"""Paired fixed-checkpoint experiment on ordinary recovery health floors.

Only a selected ordinary-room reenter below the floor is replaced with a wait.
The model always receives the original observations and legal-action masks.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import sys
import time

import numpy as np
import torch

import train_rl
from sfld_rl.env import VectorEnv

ROOT = Path(__file__).resolve().parents[1]
FLOORS = (20, 40, 60, 80, 100)
COUNTERS = ('overrides', 'imposed_wait_hours', 'natural_wait_hours',
            'ordinary_reentries', 'ordinary_reentry_hp_sum',
            'boss_reentries', 'boss_reentry_hp_sum', 'decisions')


class ReentryFloor:
    def __init__(self, observation_names, action_names, threshold):
        if threshold not in FLOORS:
            raise ValueError('Health floor must be 20,40,60,80,100')
        self.threshold = threshold
        self.oi = {name: i for i, name in enumerate(observation_names)}
        self.ai = {name: i for i, name in enumerate(action_names)}
        if len(self.oi) != len(observation_names) or len(self.ai) != len(action_names):
            raise ValueError('Duplicate schema names')
        for name in ('phase.recovery', 'room_div100', 'hp', 'hours_left_div168'):
            if name not in self.oi:
                raise ValueError('Missing observation ' + name)
        for name in ('reenter', 'wait_boss_safe', 'wait_deadline',
                     *(f'wait_hp_{value}' for value in FLOORS)):
            if name not in self.ai:
                raise ValueError('Missing action ' + name)
        self.wait_ids = [self.ai[f'wait_hp_{value}'] for value in FLOORS]
        self.all_wait_ids = self.wait_ids + [self.ai['wait_boss_safe'], self.ai['wait_deadline']]

    def ordinary(self, obs):
        rooms = np.rint(obs[:, self.oi['room_div100']] * 100).astype(np.int64)
        return (obs[:, self.oi['phase.recovery']] > .5) & (rooms % 25 != 0)

    def apply(self, obs, masks, actions):
        """Return copied actions and changed-row mask; never modify NN inputs.

        Native masks remove duplicate waits after clipping to the deadline.
        If the named target is masked, the highest legal fixed-HP action below
        it is the identical clipped wait. Ordinary wait_boss_safe is illegal.
        """
        result = np.asarray(actions, dtype=np.int64).copy()
        changed = np.zeros(len(result), dtype=bool)
        if self.threshold == 20:
            return result, changed  # Explicit exact no-op control.
        eligible = (self.ordinary(obs) & (result == self.ai['reenter']) &
                    (obs[:, self.oi['hp']] + 1e-6 < self.threshold / 100))
        candidates = [self.ai[f'wait_hp_{value}'] for value in FLOORS if value <= self.threshold]
        for slot in np.flatnonzero(eligible):
            legal = [action for action in candidates if masks[slot, action]]
            if not legal:
                raise RuntimeError('No legal wait equivalent for recovery floor')
            result[slot] = legal[-1]
            changed[slot] = True
        return result, changed


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Refusing to overwrite {path}')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def validate_rows(rows, events, budget, threshold):
    if len(rows) != events or {r['event_index'] for r in rows} != set(range(events)):
        raise RuntimeError('Incomplete or duplicated indexed cohort')
    integer_fields = ('event_index', 'budget', 'completions', 'mushrooms',
                      'recovery_mushrooms', 'reroll_mushrooms', 'actions',
                      'invalid_actions', 'deaths', 'overrides', 'ordinary_reentries',
                      'boss_reentries', 'decisions')
    for row in rows:
        if any(isinstance(row[k], bool) or not isinstance(row[k], (int, float)) or
               not math.isfinite(row[k]) or row[k] < 0 or int(row[k]) != row[k]
               for k in integer_fields):
            raise RuntimeError('Invalid event counter')
        if (row['budget'] != budget or row['threshold'] != threshold or
                row['terminated'] is not True or row['truncated'] is not False or
                row['invalid_actions'] or row['mushrooms'] > budget or
                row['mushrooms'] != row['recovery_mushrooms'] + row['reroll_mushrooms'] or
                not 0 < row['actions'] < 100000 or row['actions'] != row['decisions'] or
                not 167.999 <= row['elapsed_hours'] <= 168.00000001):
            raise RuntimeError('Failed event accounting')
        for key in ('imposed_wait_hours', 'natural_wait_hours', 'ordinary_reentry_hp_sum', 'boss_reentry_hp_sum'):
            if not math.isfinite(row[key]) or row[key] < 0:
                raise RuntimeError('Invalid recovery measurement')
        if not row['imposed_wait_hours'] <= row['natural_wait_hours'] + 1e-7 <= row['elapsed_hours'] + 1e-6:
            raise RuntimeError('Invalid wait accounting')
        if not (threshold / 100 * row['ordinary_reentries'] - 1e-5 <= row['ordinary_reentry_hp_sum'] <=
                row['ordinary_reentries'] + 1e-5):
            raise RuntimeError('Reentry floor violated')
        if threshold == 20 and (row['overrides'] or row['imposed_wait_hours']):
            raise RuntimeError('20 percent control was not identity')


def summarize(rows):
    result = {'events': len(rows), 'budget': rows[0]['budget'], 'threshold': rows[0]['threshold'],
              'completions': train_rl.interval([r['completions'] for r in rows])}
    for key in ('mushrooms', 'recovery_mushrooms', 'reroll_mushrooms', 'deaths',
                'actions', 'overrides', 'imposed_wait_hours', 'natural_wait_hours',
                'ordinary_reentries', 'boss_reentries'):
        result['mean_' + key] = statistics.mean(r[key] for r in rows)
    for label in ('ordinary', 'boss'):
        denominator = sum(r[label + '_reentries'] for r in rows)
        result[label + '_mean_reentry_hp'] = (
            sum(r[label + '_reentry_hp_sum'] for r in rows) / denominator if denominator else None)
    return result


def paired(control, treatment):
    def index(rows):
        indexed = {(r['budget'], r['event_index']): r for r in rows}
        if len(indexed) != len(rows):
            raise ValueError('Duplicate pairing key')
        return indexed
    left, right = index(control), index(treatment)
    if left.keys() != right.keys():
        raise ValueError('Paired event indices differ')
    delta = [right[key]['completions'] - left[key]['completions'] for key in sorted(left)]
    stats = train_rl.interval(delta)
    stats.update(events=len(delta), wins=sum(x > 0 for x in delta), ties=delta.count(0),
                 losses=sum(x < 0 for x in delta))
    return stats


def evaluate(args, saved, model, device, budget, threshold):
    started = time.monotonic()
    env = VectorEnv(args.library, [args.profile], min(args.n_envs, args.events),
                    budgets=[budget], seed=args.seed, seed_namespace=args.namespace,
                    deadline_hours=168, max_actions=100000, auto_reset=False)
    collected = {}
    try:
        train_rl.environment_changes(saved['metadata']['environment_signature'], env.metadata, resume=True)
        floor = ReentryFloor(env.observation_names, env.action_names, threshold)
        obs, masks = env.reset()
        counters = {name: np.zeros(env.n_envs, dtype=np.float64) for name in COUNTERS}
        transitions = Counter()
        with torch.inference_mode():
            while len(collected) < args.events:
                actions = model.actions(train_rl.tensor(obs, device),
                                        train_rl.tensor(masks, device, torch.bool), greedy=True)[0].cpu().numpy()
                actual, changed = floor.apply(obs, masks, actions)
                if not masks[np.arange(env.n_envs), actual].all():
                    raise RuntimeError('Intervention produced an illegal action')
                ordinary = floor.ordinary(obs)
                entering = actual == floor.ai['reenter']
                hp = obs[:, floor.oi['hp']].astype(np.float64)
                if np.any(entering & ordinary & (hp + 1e-6 < threshold / 100)):
                    raise RuntimeError('Ordinary reentry below floor')
                counters['decisions'] += 1
                counters['overrides'] += changed
                for label, chosen in (('ordinary', entering & ordinary), ('boss', entering & ~ordinary)):
                    counters[label + '_reentries'] += chosen
                    counters[label + '_reentry_hp_sum'] += chosen * hp
                active = env.episode_indices < args.events
                for slot in np.flatnonzero(changed & active):
                    transitions[env.action_names[actual[slot]]] += 1
                waiting = np.isin(actual, floor.all_wait_ids)
                before_hours = env._episode_info['elapsed_hours'].copy()
                obs, masks, rewards, terminated, truncated, info = env.step(actual)
                duration = info['elapsed_hours'] - before_hours
                counters['imposed_wait_hours'] += duration * changed
                counters['natural_wait_hours'] += duration * waiting
                ended = np.flatnonzero(np.asarray(terminated) | np.asarray(truncated))
                for slot in ended:
                    event = int(info['episode_index'][slot])
                    if event >= args.events:
                        continue
                    if event in collected:
                        raise RuntimeError('Repeated event index')
                    row = {'event_index': event, 'budget': budget, 'threshold': threshold,
                           'terminated': bool(terminated[slot]), 'truncated': bool(truncated[slot])}
                    for key in ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms',
                                'invalid_actions', 'elapsed_hours', 'actions', 'deaths', 'first_completion_hours'):
                        value = float(info[key][slot])
                        row[key] = value if key in ('elapsed_hours', 'first_completion_hours') else int(value)
                    for key, values in counters.items():
                        value = float(values[slot])
                        row[key] = int(value) if key in ('overrides', 'ordinary_reentries', 'boss_reentries', 'decisions') else value
                    collected[event] = row
                if len(ended):
                    obs, masks = env.reset_done(ended)
                    for values in counters.values():
                        values[ended] = 0
        rows = [collected[i] for i in range(args.events)]
        validate_rows(rows, args.events, budget, threshold)
        if sum(transitions.values()) != sum(r['overrides'] for r in rows):
            raise RuntimeError('Override count differs from indexed event totals')
        return {'summary': summarize(rows), 'outcomes': rows, 'environment': env.metadata,
                'override_wait_actions': dict(sorted(transitions.items())),
                'wall_seconds': time.monotonic() - started}
    finally:
        env.close()


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    device = torch.device(args.device)
    saved, model = train_rl.load_checkpoint(args.checkpoint, device)
    model.eval()
    hashes = {label: train_rl.sha(path) for label, path in
              (('checkpoint', args.checkpoint), ('library', args.library), ('profile', args.profile),
               ('runner', Path(__file__)), ('trainer', ROOT / 'tools/train_rl.py'),
               ('adapter', ROOT / 'tools/sfld_rl/env.py'), ('ppo', ROOT / 'tools/sfld_rl/ppo.py'))}
    plan = {'schema': 'sfld-reentry-experiment-v1', 'seed': str(args.seed), 'namespace_byte': args.namespace,
            'events_per_cell': args.events, 'event_indices': [0, args.events - 1],
            'budgets': args.budgets, 'thresholds': args.thresholds, 'hashes': hashes,
            'intervention': 'Only replace ordinary recovery reenter below floor with legal natural wait; original NN masks and all other actions unchanged.',
            'boss_exclusion': 'All room numbers divisible by25, including recovery to boss door choices.',
            'control': '20 percent is explicitly identity; NN may itself choose higher recovery.',
            'selection': 'Screen best nonbaseline floor separately per budget by mean completions; ties favor lower floor. Confirm on fresh seed; baseline remains eligible recommendation.',
            'caveats': ['Frozen NN, no retraining for higher health.',
                        'Later decisions and spending can change because the observed state changes.',
                        'Imposed wait hours measure the override itself, not counterfactual extra total recovery.',
                        'Same seeds pair events; divergent actions need not consume identical RNG draws.',
                        'Simulator uncertainty is outside these sampling intervals.'],
            'n_envs': min(args.n_envs, args.events), 'device': str(device)}
    plan_path = args.output / 'plan.json'
    if plan_path.exists():
        if json.loads(plan_path.read_text()) != plan:
            raise RuntimeError('Existing experiment has a different plan')
    else:
        write_once(plan_path, plan)
        (args.output / 'benchmark_rl_reentry.py').write_bytes(Path(__file__).read_bytes())
    cells = {}
    for budget in args.budgets:
        for threshold in args.thresholds:
            path = args.output / f'cap-{budget}-hp-{threshold}.json'
            if path.exists():
                cell = json.loads(path.read_text())
                if cell.get('plan') != plan:
                    raise RuntimeError('Cached cell has different provenance')
                validate_rows(cell['outcomes'], args.events, budget, threshold)
                if cell['summary'] != summarize(cell['outcomes']):
                    raise RuntimeError('Cached summary differs from outcomes')
            else:
                cell = evaluate(args, saved, model, device, budget, threshold)
                cell['plan'] = plan
                write_once(path, cell)
            cells[budget, threshold] = cell
            print(json.dumps({'budget': budget, 'threshold': threshold,
                              **cell['summary'], 'wall_seconds': cell['wall_seconds']}), flush=True)
    comparisons, selection = [], {}
    if 20 in args.thresholds:
        for budget in args.budgets:
            for threshold in args.thresholds:
                if threshold != 20:
                    comparisons.append({'budget': budget, 'threshold': threshold,
                                        'paired_delta': paired(cells[budget, 20]['outcomes'], cells[budget, threshold]['outcomes'])})
            higher = [t for t in args.thresholds if t > 20]
            if higher:
                selection[str(budget)] = max(higher, key=lambda t: (cells[budget, t]['summary']['completions']['mean'], -t))
    result = {'plan': plan, 'summaries': [c['summary'] for c in cells.values()],
              'comparisons': comparisons, 'best_higher_floor_by_budget': selection}
    summary_path = args.output / 'summary.json'
    if summary_path.exists():
        if json.loads(summary_path.read_text()) != result:
            raise RuntimeError('Existing summary differs')
    else:
        write_once(summary_path, result)
    for label, path in (('checkpoint', args.checkpoint), ('library', args.library), ('profile', args.profile), ('runner', Path(__file__))):
        if train_rl.sha(path) != hashes[label]:
            raise RuntimeError('Input changed during experiment: ' + label)
    return result


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=ROOT / 'docs/rl-finetune-2026-10-10/selected-checkpoint.pt')
    parser.add_argument('--library', type=Path, default=ROOT / 'docs/rl-finetune-2026-10-10/sfld_rl.dll')
    parser.add_argument('--profile', type=Path, default=ROOT / 'profiles/synthetic.profile')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budgets', default='0,20,50,100,500')
    parser.add_argument('--thresholds', default='20,40,60,80,100')
    parser.add_argument('--events', type=int, default=1024)
    parser.add_argument('--n-envs', type=int, default=256)
    parser.add_argument('--seed', type=int, default=9501001)
    parser.add_argument('--namespace', type=lambda value: int(value, 0), default=0xD6)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    args = parser.parse_args(argv)
    try:
        args.budgets = [int(x) for x in args.budgets.split(',')]
        args.thresholds = [int(x) for x in args.thresholds.split(',')]
    except ValueError:
        parser.error('Budgets and thresholds require comma-separated integers')
    if (not args.budgets or len(set(args.budgets)) != len(args.budgets) or any(b < 0 or b > 1000000 for b in args.budgets) or
            not args.thresholds or len(set(args.thresholds)) != len(args.thresholds) or any(t not in FLOORS for t in args.thresholds) or
            not 2 <= args.events < 2**24 or not 1 <= args.n_envs <= 4096 or
            not 0 <= args.seed < 2**64 or not 0 <= args.namespace <= 255):
        parser.error('Invalid cohort or intervention configuration')
    return args


if __name__ == '__main__':
    run(parse())
