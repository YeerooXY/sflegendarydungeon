"""Paired seven-day evaluation of held-only versus all-offered stone exclusion.

The frozen feed-forward policy sees the original observation/action schema.
There is no retraining, memory feature, action override, or recovery-floor change.
An archived-DLL identity check must pass before the main experiment starts.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time

import numpy as np
import torch

import train_rl
from sfld_rl.env import VectorEnv

ROOT = Path(__file__).resolve().parents[1]
PRIOR_ROOTS = (9010001, 9011001, 9011002, 9011003, 9100001, 9100002,
               9200001, 9200002, 9200003, 9301001, 9301002, 9301003,
               9301004, 9302001, 9302002, 9303001, 9401001, 9500001, 9501001, 9501002, 9502001)
SCHEMA = 'sfld-stone-exclusion-experiment-v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def once(path, value):
    path = Path(path)
    if path.exists():
        if read(path) != value:
            raise RuntimeError(f'Frozen artifact differs: {path}')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        train_rl.write_json(path, value)


def copy_once(path, content):
    path = Path(path)
    if path.exists() and path.read_bytes() != content:
        raise RuntimeError(f'Frozen input differs: {path}')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def settings(text):
    result = {}
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        key, value = (x.strip() for x in line.split('=', 1))
        if key in result:
            raise ValueError('Duplicate profile key: ' + key)
        result[key] = value
    return result


def arm_profiles(base, archived):
    current, historical = settings(base), settings(archived)
    for value in (current, historical):
        value.pop('exclude_offered_gems', None)
    if current != historical:
        raise ValueError('Current and archived profiles differ beyond stone exclusion')
    lines = [line for line in base.splitlines()
             if line.split('#', 1)[0].split('=', 1)[0].strip() != 'exclude_offered_gems']
    prefix = '\n'.join(lines).rstrip() + '\n'
    result = {arm: prefix + f'exclude_offered_gems={flag}\n'
              for arm, flag in [('held_only', 'false'), ('all_offered', 'true')]}
    differences = [key for key in settings(result['held_only'])
                   if settings(result['held_only'])[key] != settings(result['all_offered'])[key]]
    if differences != ['exclude_offered_gems']:
        raise AssertionError('Intervention changes more than one profile field')
    return result


def fingerprint(seed):
    mask = (1 << 64) - 1
    value = (seed + 0x9e3779b97f4a7c15) & mask
    value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & mask
    value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & mask
    return (value ^ (value >> 31)) & 0xffffffff


def target_probabilities(n):
    if n < 9:
        raise ValueError('Three disjoint three-stone menus require at least nine stones')
    missing = Fraction(1)
    for i in range(3):
        missing *= 1 - Fraction(3, n - i)
    old, new = 1 - missing, Fraction(9, n)
    return {'eligible_pool': n, 'held_only': float(old), 'all_offered': float(new),
            'held_only_exact': str(old), 'all_offered_exact': str(new),
            'assumptions': 'Uniform eligible stones; all three menus reached; take target when first offered. These are analytic chances, not gameplay-conditioned frequencies.'}


def compatibility(saved, current):
    """Allow the declared engine intervention, never a changed neural interface."""
    original = saved['metadata']['environment_signature']
    now = train_rl.compact_metadata(current)
    before_schema, after_schema = copy.deepcopy(original['schema']), copy.deepcopy(now['schema'])
    before_version = before_schema.pop('engine', None)
    after_version = after_schema.pop('engine', None)
    if before_schema != after_schema:
        raise ValueError('Checkpoint observation/action/metric schema changed')
    for key in ('observation_dim', 'action_dim'):
        expected = saved['architecture']['observation_size' if key == 'observation_dim' else 'action_size']
        if current[key] != expected:
            raise ValueError('Checkpoint dimension changed: ' + key)
    allowed = {'library_sha256', 'profile_sha256', 'schema', 'schema_sha256'}
    changes = {key: {'training': original.get(key), 'current': now.get(key)}
               for key in set(original) | set(now) if original.get(key) != now.get(key)}
    if set(changes) - allowed:
        raise ValueError('Undeclared environment difference: ' + ', '.join(sorted(set(changes) - allowed)))
    return {'training_engine': before_version, 'evaluation_engine': after_version,
            'changes': changes, 'identical_neural_interface': True,
            'history_features_added': False, 'retrained': False}


def interval(values):
    result = train_rl.interval(values)
    mean, se = result['mean'], result['standard_error']
    result['normal_99_ci'] = [mean - 2.5758293035489004 * se, mean + 2.5758293035489004 * se]
    return result


class OfferTelemetry:
    """Observe real run labels; deduplicate a menu revisited after a paid heal."""
    def __init__(self, names, action_names, n_envs):
        self.oi = {name: i for i, name in enumerate(names)}
        self.actions = action_names
        self.owned = [i for name, i in self.oi.items() if name.startswith('owned_gem.')]
        self.options = [[(name.rsplit('.', 1)[1], i) for name, i in self.oi.items()
                         if name.startswith(f'gem_offers.{j}.')] for j in range(3)]
        self.runs = [[] for _ in range(n_envs)]
        self.last_run = np.full(n_envs, -1, np.int64)

    def record(self, obs, actions, active):
        numbers = np.rint(obs[:, self.oi['run_number_div10']] * 10).astype(np.int64)
        for slot in np.flatnonzero(active & (numbers != self.last_run)):
            first = bool(obs[slot, self.oi['first_event_run']] > .5)
            self.runs[slot].append({'run_number': int(numbers[slot]),
                                   'run_class': 'first' if first else 'later', 'menus': []})
            self.last_run[slot] = numbers[slot]
        for slot in np.flatnonzero(active & (obs[:, self.oi['phase.gems']] > .5)):
            index = int(np.count_nonzero(obs[slot, self.owned] > .5)) + 1
            offers = []
            for group in self.options:
                names = [name for name, i in group if obs[slot, i] > .5]
                if len(names) != 1:
                    raise RuntimeError('Ambiguous visible stone offer')
                offers.append(names[0])
            menus = self.runs[slot][-1]['menus']
            if len(menus) < index:
                if len(menus) != index - 1:
                    raise RuntimeError('Missing observed stone selection')
                menus.append({'selection': index, 'offered': offers, 'chosen': None})
            if menus[index - 1]['offered'] != offers:
                raise RuntimeError('A visible stone menu changed before selection')
            action = self.actions[int(actions[slot])]
            if action in ('gem_0', 'gem_1', 'gem_2'):
                menu = menus[index - 1]
                if menu['chosen'] is not None:
                    raise RuntimeError('Stone choice counted twice')
                menu['chosen'] = offers[int(action[-1])]

    def reset(self, slots):
        for slot in slots:
            self.runs[slot] = []
            self.last_run[slot] = -1


def validate_rows(rows, events, budget, arm):
    if len(rows) != events or {r['event_index'] for r in rows} != set(range(events)):
        raise ValueError('Incomplete or duplicated indexed event cohort')
    integer_fields = ('event_index', 'budget', 'completions', 'mushrooms', 'recovery_mushrooms',
                      'reroll_mushrooms', 'actions', 'invalid_actions', 'deaths', 'decisions')
    for row in rows:
        for key in integer_fields:
            v = row[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or int(v) != v:
                raise ValueError('Invalid event counter: ' + key)
        if (row['budget'] != budget or row['terminated'] is not True or row['truncated'] is not False or
                row['invalid_actions'] or row['mushrooms'] > budget or
                row['mushrooms'] != row['recovery_mushrooms'] + row['reroll_mushrooms'] or
                not 0 < row['actions'] < 100000 or row['actions'] != row['decisions'] or
                not math.isfinite(row['elapsed_hours']) or not 0 < row['elapsed_hours'] <= 168.00000001):
            raise ValueError('Failed event budget/time/terminal accounting')
        first = row['first_completion_hours']
        if not math.isfinite(first) or (row['completions'] and not 0 < first <= row['elapsed_hours']):
            raise ValueError('Invalid first completion time')
        previous = 0
        for run in row['stone_runs']:
            if run['run_number'] != previous + 1 or run['run_class'] not in ('first', 'later'):
                raise ValueError('Invalid observed run sequence')
            previous = run['run_number']
            seen, held = set(), set()
            if len(run['menus']) > 3:
                raise ValueError('Too many stone menus')
            for i, menu in enumerate(run['menus'], 1):
                offers = menu['offered']
                if (menu['selection'] != i or len(offers) != 3 or len(set(offers)) != 3 or
                        set(offers) & held or menu['chosen'] is not None and menu['chosen'] not in offers):
                    raise ValueError('Invalid stone offer/choice')
                if arm == 'all_offered' and set(offers) & seen:
                    raise ValueError('All-offered arm recycled a stone')
                if i < len(run['menus']) and menu['chosen'] is None:
                    raise ValueError('Advanced past an unchosen stone menu')
                seen.update(offers)
                if menu['chosen'] is not None:
                    held.add(menu['chosen'])


def offer_summary(rows):
    result = []
    for label in ('first', 'later'):
        runs = [run for row in rows for run in row['stone_runs'] if run['run_class'] == label]
        reached = [run for run in runs if len(run['menus']) == 3]
        ever = Counter(stone for run in runs for stone in {g for m in run['menus'] for g in m['offered']})
        third = Counter(stone for run in reached for stone in {g for m in run['menus'] for g in m['offered']})
        picked = Counter(m['chosen'] for run in runs for m in run['menus'] if m['chosen'] is not None)
        result.append({'run_class': label, 'observed_runs': len(runs),
                       'runs_with_offer': sum(bool(run['menus']) for run in runs),
                       'runs_reaching_third_menu': len(reached),
                       'menus': sum(len(run['menus']) for run in runs),
                       'ever_offered_run_counts': dict(sorted(ever.items())),
                       'ever_offered_by_third_menu_run_counts': dict(sorted(third.items())),
                       'chosen_counts': dict(sorted(picked.items())),
                       'conditioning_warning': 'Visited-run telemetry, classified by the observed first_event_run flag. Reaching later menus depends on gameplay and stone choices; these counts do not estimate the unconditional analytic offer probability.'})
    return result


def summarize(rows):
    result = {'events': len(rows), 'budget': rows[0]['budget']}
    for key in ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms', 'deaths', 'actions'):
        result[key] = interval([row[key] for row in rows])
    result['completion_distribution'] = {str(k): v for k, v in sorted(Counter(r['completions'] for r in rows).items())}
    return result


def paired(old, new):
    def index(rows):
        result = {(r['budget'], r['event_index']): r for r in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate pairing key')
        return result
    left, right = index(old), index(new)
    if left.keys() != right.keys():
        raise ValueError('Paired event indices differ')
    keys = sorted(left)
    result = {'events': len(keys), 'direction': 'all_offered minus held_only'}
    for metric in ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms', 'deaths'):
        differences = [right[key][metric] - left[key][metric] for key in keys]
        result[metric] = interval(differences)
        if metric == 'completions':
            result.update(wins=sum(x > 0 for x in differences), ties=differences.count(0),
                          losses=sum(x < 0 for x in differences),
                          difference_distribution={str(k): v for k, v in sorted(Counter(differences).items())})
    return result


def evaluate(plan, saved, model, device, profile, library, budget, arm, events):
    started = time.monotonic()
    env = VectorEnv(library, [profile], min(plan['n_envs'], events), budgets=[budget],
                    seed=int(plan['seed']), seed_namespace=plan['namespace_byte'],
                    deadline_hours=168, max_actions=100000, auto_reset=False)
    collected = {}
    try:
        changes = compatibility(saved, env.metadata)
        obs, masks = env.reset()
        telemetry = OfferTelemetry(env.observation_names, env.action_names, env.n_envs)
        decisions = np.zeros(env.n_envs, np.int64)
        with torch.inference_mode():
            while len(collected) < events:
                actions = model.actions(train_rl.tensor(obs, device),
                    train_rl.tensor(masks, device, torch.bool), greedy=True)[0].cpu().numpy()
                if not masks[np.arange(env.n_envs), actions].all():
                    raise RuntimeError('Frozen policy emitted an illegal action')
                telemetry.record(obs, actions, env.episode_indices < events)
                previous = env._episode_info['completions'].copy()
                decisions += 1
                obs, masks, rewards, terminated, truncated, info = env.step(actions)
                train_rl.transition_guard(rewards, terminated, truncated, info, previous)
                ended = np.flatnonzero(terminated | truncated)
                for slot in ended:
                    event = int(info['episode_index'][slot])
                    if event >= events:
                        continue
                    if event in collected:
                        raise RuntimeError('Repeated event index')
                    row = {'event_index': event, 'budget': budget, 'terminated': bool(terminated[slot]),
                           'truncated': bool(truncated[slot]), 'decisions': int(decisions[slot]),
                           'stone_runs': telemetry.runs[slot]}
                    for key in ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms',
                                'invalid_actions', 'elapsed_hours', 'actions', 'deaths', 'first_completion_hours'):
                        value = float(info[key][slot])
                        if not math.isfinite(value) or key not in ('elapsed_hours', 'first_completion_hours') and value != int(value):
                            raise RuntimeError('Nonfinite or fractional native counter')
                        row[key] = value if key in ('elapsed_hours', 'first_completion_hours') else int(value)
                    collected[event] = row
                if len(ended):
                    obs, masks = env.reset_done(ended)
                    decisions[ended] = 0
                    telemetry.reset(ended)
        rows = [collected[i] for i in range(events)]
        validate_rows(rows, events, budget, arm)
        return {'summary': summarize(rows), 'outcomes': rows, 'stone_offers': offer_summary(rows),
                'environment': env.metadata, 'checkpoint_compatibility': changes,
                'wall_seconds': time.monotonic() - started}
    finally:
        env.close()


def cell_path(output, phase, arm, budget):
    return Path(output) / f'{phase}-{arm}-cap-{budget}.json'


def cell_configuration(plan, phase, arm, budget):
    legacy = arm == 'legacy'
    return {'plan_sha256': plan['plan_content_sha256'], 'phase': phase, 'arm': arm, 'budget': budget,
            'events': plan['identity_events'] if phase == 'identity' else plan['events_per_arm_budget'],
            'library': plan['legacy_library'] if legacy else plan['library'],
            'library_sha256': plan['hashes']['legacy_library' if legacy else 'library'],
            'profile': plan['archived_profile'] if legacy else plan['profiles'][arm]['path'],
            'profile_sha256': plan['hashes']['archived_profile'] if legacy else plan['profiles'][arm]['sha256'],
            'checkpoint_sha256': plan['hashes']['checkpoint']}


def validate_cell(cell, plan, config):
    if cell['configuration'] != config:
        raise ValueError('Cached cell provenance differs')
    validate_rows(cell['outcomes'], config['events'], config['budget'], config['arm'])
    if cell['summary'] != summarize(cell['outcomes']) or cell['stone_offers'] != offer_summary(cell['outcomes']):
        raise ValueError('Cached aggregate differs from indexed outcomes')
    meta = cell['environment']
    if (meta['library_sha256'] != config['library_sha256'] or
            meta['profiles'] != [{'path': config['profile'], 'sha256': config['profile_sha256']}] or
            meta['deadline_hours'] != 168 or meta['max_actions'] != 100000 or meta['auto_reset'] is not False or
            meta['seed_namespace'] != plan['namespace_byte'] or meta['budgets'] != [config['budget']] or
            meta['initial_episode_index'] != 0 or meta['n_envs'] != min(plan['n_envs'], config['events'])):
        raise ValueError('Cached native environment differs')


def verify_inputs(plan):
    for label, path in plan['input_paths'].items():
        if train_rl.sha(path) != plan['hashes'][label]:
            raise RuntimeError('Input changed during experiment: ' + label)
    for value in plan['profiles'].values():
        if train_rl.sha(value['path']) != value['sha256']:
            raise RuntimeError('Arm profile changed')


def worker(args):
    plan = read(args.output / 'plan.json')
    verify_inputs(plan)
    torch.set_num_threads(2)
    device = torch.device(plan['device'])
    saved, model = train_rl.load_checkpoint(plan['checkpoint'], device)
    model.eval()
    for budget in plan['budgets']:
        config = cell_configuration(plan, args.worker_phase, args.worker_arm, budget)
        path = cell_path(args.output, args.worker_phase, args.worker_arm, budget)
        if path.exists():
            cell = read(path)
        else:
            cell = evaluate(plan, saved, model, device, config['profile'], config['library'],
                            budget, args.worker_arm, config['events'])
            cell['configuration'] = config
            once(path, cell)
        validate_cell(cell, plan, config)
        print(json.dumps({'phase': args.worker_phase, 'arm': args.worker_arm,
                          **cell['summary'], 'wall_seconds': cell['wall_seconds']}), flush=True)
    verify_inputs(plan)


def prepare(args):
    args.output.mkdir(parents=True, exist_ok=True)
    generated = arm_profiles(args.profile.read_text(encoding='utf-8-sig'),
                             args.archived_profile.read_text(encoding='utf-8-sig'))
    profiles = {}
    for arm, text in generated.items():
        path = (args.output / (arm + '.profile')).resolve()
        copy_once(path, text.encode('utf-8'))
        profiles[arm] = {'path': str(path), 'sha256': train_rl.sha(path)}
    fp = fingerprint(args.seed)
    if fp in {fingerprint(seed) for seed in PRIOR_ROOTS}:
        raise ValueError('Compressed seed root overlaps a previous study')
    paths = {key: str(getattr(args, key).resolve(strict=True)) for key in
             ('checkpoint', 'library', 'legacy_library', 'profile', 'archived_profile')}
    paths.update(runner=str(Path(__file__).resolve()), trainer=str(ROOT / 'tools/train_rl.py'),
                 adapter=str(ROOT / 'tools/sfld_rl/env.py'), ppo=str(ROOT / 'tools/sfld_rl/ppo.py'))
    hashes = {key: train_rl.sha(path) for key, path in paths.items()}
    plan = {'schema': SCHEMA, 'seed': str(args.seed), 'namespace_byte': args.namespace,
            'compressed_root_fingerprint': fp,
            'packed_master_hex': f'{(args.namespace << 56) | (fp << 24):016x}',
            'prior_root_fingerprints': {str(s): fingerprint(s) for s in PRIOR_ROOTS},
            'budgets': args.budgets, 'events_per_arm_budget': args.events, 'identity_events': args.identity_events,
            'event_indices': [0, args.events - 1], 'n_envs': args.n_envs, 'device': args.device,
            'checkpoint': paths['checkpoint'], 'library': paths['library'], 'legacy_library': paths['legacy_library'],
            'archived_profile': paths['archived_profile'], 'profiles': profiles, 'hashes': hashes, 'input_paths': paths,
            'objective': 'Completions in a full 168-hour event, with independent budget caps. No early stop after completing a run.',
            'intervention': 'Only exclude_offered_gems false versus true, under the same new binary and frozen neural network.',
            'policy': 'Greedy original masked network, no retraining, no added observation/history features, no action override. Original recovery decisions and minimum 20% reentry remain.',
            'identity': 'Exact indexed outcomes and observed stone trajectories of archived engine versus new held-only control, checked before main results.',
            'intervals': 'Normal paired 95% and 99% sampling intervals. The five 99% completion intervals give an approximate Bonferroni familywise 95% bound across budgets; simulator uncertainty is separate.',
            'caveats': ['Common indexed seeds pair arms; changing offers can change later choices and RNG consumption.',
                        'The frozen policy was trained under held-only exclusion and has no rejected-offer history input; this is transfer performance, not an optimum after retraining.',
                        'Stone telemetry is conditioned on actually observed runs and reached menus, and is not an unbiased test of analytic probabilities.',
                        'Identity events are a prespecified prefix of the main cohort, used only to validate implementation, not tune or select policies.'],
            'analytic_target_probabilities': [target_probabilities(n) for n in (11, 20)]}
    plan['plan_content_sha256'] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    once(args.output / 'plan.json', plan)
    copy_once(args.output / 'benchmark_stone_exclusion.py', Path(__file__).read_bytes())
    return plan


def run(args):
    plan = prepare(args)
    def launch(phase, arm):
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--output', str(args.output),
                        '--worker-phase', phase, '--worker-arm', arm], cwd=ROOT, check=True)
    launch('identity', 'legacy')
    launch('identity', 'held_only')
    for budget in plan['budgets']:
        left = read(cell_path(args.output, 'identity', 'legacy', budget))
        right = read(cell_path(args.output, 'identity', 'held_only', budget))
        for arm, cell in [('legacy', left), ('held_only', right)]:
            validate_cell(cell, plan, cell_configuration(plan, 'identity', arm, budget))
        if left['outcomes'] != right['outcomes']:
            different = [a['event_index'] for a, b in zip(left['outcomes'], right['outcomes']) if a != b]
            raise RuntimeError(f'Archived/new held-only identity failed at budget {budget}: {different[:10]}')
    once(args.output / 'identity.json', {'schema': SCHEMA, 'plan_sha256': plan['plan_content_sha256'],
         'events_per_budget': plan['identity_events'], 'budgets': plan['budgets'], 'exact_outcomes_equal': True,
         'includes': 'All indexed completion, spending, death, action, timing counters and observed stone menu/choice trajectories.'})
    print('Archived/new held-only control identity passed.', flush=True)
    for arm in ('held_only', 'all_offered'):
        launch('main', arm)
    comparisons, summaries, artifacts = [], [], []
    for budget in plan['budgets']:
        cells = {}
        for arm in ('held_only', 'all_offered'):
            path = cell_path(args.output, 'main', arm, budget)
            cell = read(path)
            validate_cell(cell, plan, cell_configuration(plan, 'main', arm, budget))
            cells[arm] = cell
            summaries.append({'arm': arm, **cell['summary'], 'stone_offers': cell['stone_offers']})
            artifacts.append({'path': path.name, 'sha256': train_rl.sha(path)})
        comparisons.append({'budget': budget, **paired(cells['held_only']['outcomes'], cells['all_offered']['outcomes'])})
    result = {'schema': SCHEMA, 'plan_sha256': plan['plan_content_sha256'],
              'summaries': summaries, 'comparisons': comparisons, 'indexed_artifacts': artifacts,
              'analytic_target_probabilities': plan['analytic_target_probabilities']}
    verify_inputs(plan)
    once(args.output / 'summary.json', result)
    print(json.dumps({'comparisons': comparisons}), flush=True)
    return result


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=ROOT / 'docs/rl-finetune-2026-10-10/selected-checkpoint.pt')
    parser.add_argument('--library', type=Path, default=ROOT / 'out/stone-exclusion-2026-10-10/sfld_rl.dll')
    parser.add_argument('--legacy-library', type=Path, default=ROOT / 'docs/rl-finetune-2026-10-10/sfld_rl.dll')
    parser.add_argument('--profile', type=Path, default=ROOT / 'profiles/synthetic.profile')
    parser.add_argument('--archived-profile', type=Path, default=ROOT / 'out/stone-exclusion-2026-10-10/baseline-original.profile')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budgets', default='0,20,50,100,500')
    parser.add_argument('--events', type=int, default=5000)
    parser.add_argument('--identity-events', type=int, default=64)
    parser.add_argument('--n-envs', type=int, default=256)
    parser.add_argument('--seed', type=int, default=9601001)
    parser.add_argument('--namespace', type=lambda value: int(value, 0), default=0xD8)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    parser.add_argument('--worker-phase', choices=('identity', 'main'), help=argparse.SUPPRESS)
    parser.add_argument('--worker-arm', choices=('legacy', 'held_only', 'all_offered'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        args.budgets = [int(x) for x in args.budgets.split(',')]
    except ValueError:
        parser.error('Budgets must be comma-separated integers')
    if (not args.budgets or len(set(args.budgets)) != len(args.budgets) or any(not 0 <= b <= 1000000 for b in args.budgets) or
            not 2 <= args.events < 2**24 - args.n_envs or not 2 <= args.identity_events <= args.events or
            not 1 <= args.n_envs <= 4096 or not 0 <= args.seed < 2**64 or not 0 <= args.namespace <= 255 or
            bool(args.worker_phase) != bool(args.worker_arm)):
        parser.error('Invalid experiment configuration')
    args.output = args.output.resolve()
    return args


if __name__ == '__main__':
    arguments = parse()
    worker(arguments) if arguments.worker_phase else run(arguments)
