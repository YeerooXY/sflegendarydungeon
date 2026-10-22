"""Describe a frozen learner's decisions on fresh diagnostic trajectories.

The scorer teacher sees exactly the same states, but never controls the rollout.
Counts describe behavior on learner-visited states, not causal action values,
optimal rules, a checkpoint-selection score, or an independent performance test.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

import numpy as np
import torch

import train_rl
from sfld_rl.env import VectorEnv

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = 0xD5
SCHEMA = 'sfld-ppo-behavior-audit-v1'


def hp_bin(hp):
    # Rounded percentage prevents float32 encodings of exactly 20% falling
    # into different bins from their double-precision simulator counterpart.
    percentage = round(float(hp) * 100, 4)
    for end in (20, 40, 60, 80):
        if percentage < end:
            return f'{end - 20}-{end}%'
    return '80-100%'


def key_bin(keys):
    return '0' if keys == 0 else '1-3' if keys < 4 else '4-7' if keys < 8 else '8-11' if keys < 12 else '12+'


class Decoder:
    """Decode the declared schema by name, never by an assumed numeric offset."""
    def __init__(self, names, actions):
        self.index = {name: i for i, name in enumerate(names)}
        if len(self.index) != len(names):
            raise ValueError('Duplicate observation names')
        self.actions = list(actions)
        self.groups = {}
        for name, index in self.index.items():
            if '.' in name:
                prefix, suffix = name.rsplit('.', 1)
                self.groups.setdefault(prefix, []).append((suffix, index))
        for name in ('hp', 'keys_div20', 'room_div100', 'run_number_div10', 'first_event_run', 'budget_div500'):
            if name not in self.index:
                raise ValueError(f'Missing audit observation: {name}')

    def value(self, row, name):
        return float(row[self.index[name]])

    def category(self, row, prefix):
        selected = [name for name, index in self.groups.get(prefix, []) if row[index] > .5]
        if len(selected) > 1:
            raise ValueError(f'Observation has multiple {prefix} values')
        return selected[0] if selected else None

    def owned(self, row):
        return sorted(name for name, index in self.groups['owned_gem'] if row[index] > .5)

    def active(self, row, kind):
        prefix = 'curses' if kind in ('broken_armor', 'poison', 'clumsy', 'gold_hangover', 'hard_lock') else 'blessings'
        return any(self.value(row, f'{prefix}.{i}.active') > .5 and
                   self.value(row, f'{prefix}.{i}.kind.{kind}') > .5 for i in range(3))

    def offers(self, row):
        return [{'kind': self.category(row, f'offers.{i}.effect.kind'),
                 'magnitude': round(self.value(row, f'offers.{i}.effect.magnitude'), 6),
                 'remaining': round(self.value(row, f'offers.{i}.effect.remaining_div10') * 10),
                 'keys': round(self.value(row, f'offers.{i}.keys_div6') * 6)} for i in range(2)]

    def state(self, row, budget, episode_index):
        room = round(self.value(row, 'room_div100') * 100)
        result = {'budget': budget, 'episode_index': int(episode_index),
                  'phase': self.category(row, 'phase'), 'room': room,
                  'stage': min(4, (room - 1) // 25 + 1), 'hp': self.value(row, 'hp'),
                  'keys': round(self.value(row, 'keys_div20') * 20),
                  'run_number': round(self.value(row, 'run_number_div10') * 10),
                  'run_type': 'first' if self.value(row, 'first_event_run') > .5 else 'later'}
        if result['phase'] in ('shop', 'curse_shop'):
            result['offers'] = self.offers(row)
        if result['phase'] in ('encounter', 'gems'):
            result['owned_gems'] = self.owned(row)
        if result['phase'] == 'encounter':
            result['encounter'] = self.category(row, 'encounter')
            result['one_hit'] = self.active(row, 'one_hit')
        if result['phase'] == 'gems':
            result['gem_offers'] = [self.category(row, f'gem_offers.{i}') for i in range(3)]
        if result['phase'] == 'doors':
            result['doors'] = [{'kind': self.category(row, f'doors.{i}.kind'),
                                'trap': self.value(row, f'doors.{i}.trap') > .5} for i in range(2)]
        return result


class Counts:
    def __init__(self):
        self.rows = {}

    def add(self, dimensions, state, action, teacher):
        key = json.dumps(dimensions, sort_keys=True, separators=(',', ':'))
        if key not in self.rows:
            self.rows[key] = {**dimensions, 'decisions': 0, 'disagreements': 0, 'teacher_decisions': 0,
                              'actions': Counter(), 'teacher_actions': Counter(),
                              '_hp_sum': 0., '_keys_sum': 0., 'min_hp': 1., 'max_hp': 0.,
                              'min_room': 101, 'max_room': 0, 'min_keys': state['keys'], 'max_keys': state['keys']}
        row = self.rows[key]
        row['decisions'] += 1
        row['actions'][action] += 1
        if teacher is not None:
            row['teacher_decisions'] += 1
            row['disagreements'] += action != teacher
            row['teacher_actions'][teacher] += 1
        row['_hp_sum'] += state['hp']; row['_keys_sum'] += state['keys']
        for field in ('hp', 'room', 'keys'):
            row[f'min_{field}'] = min(row[f'min_{field}'], state[field])
            row[f'max_{field}'] = max(row[f'max_{field}'], state[field])
        return row

    def export(self):
        result = []
        for _, original in sorted(self.rows.items()):
            row = original.copy()
            row['mean_hp'] = row.pop('_hp_sum') / row['decisions']
            row['mean_keys'] = row.pop('_keys_sum') / row['decisions']
            row['disagreement_rate'] = row['disagreements'] / row['teacher_decisions'] if row['teacher_decisions'] else None
            for key, value in row.items():
                if isinstance(value, Counter):
                    row[key] = dict(sorted(value.items()))
            result.append(row)
        return result


class BehaviorAudit:
    def __init__(self):
        self.summary = Counts()
        self.tables = {name: Counts() for name in ('phases', 'shops', 'combat', 'barrels', 'gems')}

    def record(self, profile, state, action, teacher):
        common = {'profile': profile, 'budget': state['budget']}
        self.summary.add(common, state, action, teacher)
        self.tables['phases'].add({**common, 'phase': state['phase']}, state, action, teacher)
        if state['phase'] in ('shop', 'curse_shop'):
            self.tables['shops'].add({**common, 'phase': state['phase'], 'keys': state['keys'],
                                     'key_bin': key_bin(state['keys']), 'offers': state['offers']}, state, action, teacher)
        if state['phase'] == 'encounter' and action in ('fight', 'flee'):
            self.tables['combat'].add({**common, 'stage': state['stage'], 'encounter': state['encounter'],
                                      'hp_bin': hp_bin(state['hp']), 'key_bin': key_bin(state['keys']),
                                      'one_hit': state['one_hit']}, state, action, teacher)
        if state['phase'] == 'encounter' and state['encounter'] == 'barrel' and action in ('interact', 'skip'):
            self.tables['barrels'].add({**common, 'hp_bin': hp_bin(state['hp']),
                                       'owned_gems': state['owned_gems']}, state, action, teacher)
        if state['phase'] == 'gems' and action in ('gem_0', 'gem_1', 'gem_2'):
            # A heal can revisit the same offers. Count exposure only when a
            # stone is actually chosen, rather than treating that as a new roll.
            row = self.tables['gems'].add({**common, 'stage': state['stage'], 'run_type': state['run_type'],
                                          'owned_gems': state['owned_gems']}, state, action, teacher)
            for field in ('offered', 'picked', 'teacher_picked'):
                row.setdefault(field, Counter())
            row['offered'].update(state['gem_offers'])
            row['picked'][state['gem_offers'][int(action[-1])]] += 1
            if teacher in ('gem_0', 'gem_1', 'gem_2'):
                row['teacher_picked'][state['gem_offers'][int(teacher[-1])]] += 1

    def export(self):
        return {'summaries': self.summary.export(), 'tables': {name: counts.export() for name, counts in self.tables.items()}}


def resolved(path):
    path = Path(path)
    return (path if path.is_absolute() else ROOT / path).resolve(strict=True)


def run(args):
    if args.output.exists() or (args.samples and args.samples.exists()):
        raise FileExistsError('Refusing to overwrite an existing audit or sample file')
    if args.samples and args.samples.resolve() == args.output.resolve():
        raise ValueError('Report and sample paths must differ')
    torch.set_num_threads(args.torch_threads)
    device = torch.device(args.device)
    if device.type == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA is unavailable')
    saved, model = train_rl.load_checkpoint(args.checkpoint, device)
    checkpoint_hash = train_rl.sha(args.checkpoint)
    model.eval()
    training = saved['metadata']
    library = resolved(args.library or training['environment']['library'])
    profiles = [resolved(p) for p in (args.profiles or training['profiles'])]
    if len(set(profiles)) != len(profiles):
        raise ValueError('Duplicate audit profiles')
    teachers = {}
    for label, override in (('free', args.teacher_free_rules), ('paid', args.teacher_paid_rules)):
        if getattr(args, 'no_teacher', False): continue
        expected = training['teacher'][label]
        path = resolved(override or expected['rules'])
        if train_rl.sha(path) != expected['sha256']:
            raise ValueError(f'{label} scorer stone rules differ from the checkpoint teacher')
        teachers[label] = {'path': str(path), 'sha256': expected['sha256'], 'kind': expected['kind']}
    # Encoder and network code are frozen alongside the binary/schema. A new
    # diagnostic tool is allowed; silently changing either imported module is not.
    for source in ('tools/sfld_rl/env.py', 'tools/sfld_rl/ppo.py'):
        expected = training['sources'].get(source, training['sources'].get(source.replace('/', '\\')))
        if expected is None or train_rl.sha(ROOT / source) != expected:
            raise ValueError(f'Checkpoint runtime source mismatch: {source}')
    audit, environment, episodes = BehaviorAudit(), [], []
    sample_count, sample_stream, partial = 0, None, None
    started = time.monotonic()
    try:
        if args.samples:
            args.samples.parent.mkdir(parents=True, exist_ok=True)
            partial = args.samples.with_name(args.samples.name + '.partial')
            sample_stream = partial.open('x', encoding='utf-8')
        for profile in profiles:
            for budget in args.budgets:
                env = VectorEnv(library, [profile], min(args.n_envs, args.events), budgets=[budget], seed=args.seed,
                                seed_namespace=NAMESPACE, deadline_hours=training['environment']['deadline_hours'],
                                max_actions=training['environment']['max_actions'], auto_reset=False)
                with env:
                    obs, masks = env.reset()
                    changes = train_rl.environment_changes(training['environment_signature'], env.metadata)
                    if model.architecture['observation_size'] != env.obs_dim or model.architecture['action_size'] != env.action_dim:
                        raise ValueError('Checkpoint observation/action dimensions differ')
                    environment.append({'profile': str(profile), 'budget': budget, 'metadata': env.metadata,
                                        'changes_from_training': changes})
                    decoder = Decoder(env.observation_names, env.action_names)
                    teacher = teachers.get('free' if budget == 0 else 'paid')
                    complete, previous = set(), np.zeros(env.n_envs, np.float64)
                    while len(complete) < args.events:
                        indices = env.episode_indices
                        with torch.no_grad():
                            selected = model.actions(torch.as_tensor(obs, device=device),
                                                     torch.as_tensor(masks, device=device), greedy=True)[0].cpu().numpy()
                        labels = env.teacher_actions(kind=teacher['kind'], rules_path=teacher['path']) if teacher else None
                        if labels is not None and ((labels < 0).any() or not masks[np.arange(env.n_envs), labels].all()):
                            raise RuntimeError('Teacher produced an unavailable action')
                        for slot in np.flatnonzero(indices < args.events):
                            state = decoder.state(obs[slot], budget, indices[slot])
                            if round(decoder.value(obs[slot], 'budget_div500') * 500) != budget:
                                raise RuntimeError('Observed budget differs from declared cohort')
                            action, reference = env.action_names[selected[slot]], env.action_names[labels[slot]] if labels is not None else None
                            audit.record(str(profile), state, action, reference)
                            if sample_stream and sample_count < args.sample_limit:
                                sample_stream.write(json.dumps({'profile': str(profile), **state, 'action': action,
                                                                'teacher_action': reference, 'disagrees': action != reference if reference is not None else None},
                                                               separators=(',', ':'), allow_nan=False) + '\n')
                                sample_count += 1
                        obs, masks, reward, done, truncated, info = env.step(selected)
                        train_rl.transition_guard(reward, done, truncated, info, previous)
                        previous[:] = info['completions']
                        ended = np.flatnonzero(done)
                        for slot in ended:
                            index = int(info['episode_index'][slot])
                            if index >= args.events:
                                continue
                            if index in complete:
                                raise RuntimeError('Duplicate diagnostic episode index')
                            if info['mushrooms'][slot] > budget:
                                raise RuntimeError('Diagnostic rollout exceeded its mushroom budget')
                            complete.add(index)
                            episodes.append({'profile': str(profile), 'budget': budget, 'event_index': index,
                                             'actions': int(info['actions'][slot]), 'completions': int(info['completions'][slot]),
                                             'mushrooms': int(info['mushrooms'][slot])})
                        if len(ended):
                            obs, masks = env.reset_done(ended)
                            previous[ended] = 0
                    if complete != set(range(args.events)):
                        raise RuntimeError('Diagnostic cohort is incomplete')
        exported = audit.export()
        for summary in exported['summaries']:
            rows = [row for row in episodes if row['profile'] == summary['profile'] and row['budget'] == summary['budget']]
            if sum(row['actions'] for row in rows) != summary['decisions']:
                raise RuntimeError('Behavior decision count does not match completed episode actions')
            summary['events'] = len(rows)
            summary.update(failed_events=0, invalid_actions=0, truncations=0)
        result = {'schema': SCHEMA, 'purpose': 'Descriptive policy behavior on learner-visited diagnostic states only',
                  'caveats': ['Teacher labels compare identical visible states; the teacher does not control these trajectories.',
                              'Correlations and disagreements do not establish causal action value or optimal rules.',
                              'This diagnostic cohort is not used for checkpoint selection or reported final performance.',
                              'Shop counts are action decisions, including repeated rerolls/heals; they are not unique shop visits.',
                              'Combat/barrel/stone tables count committed choices; earlier heals remain in phase counts.',
                              'Optional samples are the first capped decisions in traversal order, not a random sample.'],
                  'checkpoint': {'path': str(args.checkpoint.resolve()), 'sha256': checkpoint_hash,
                                 'training_steps': saved['state']['steps']},
                  'cohort': {'seed': str(args.seed), 'namespace_byte': NAMESPACE, 'events_per_budget': args.events,
                             'budgets': args.budgets, 'event_indices': [0, args.events - 1], 'policy': 'greedy'},
                  'teacher_comparison': not getattr(args, 'no_teacher', False),
                  'teacher_rules': teachers, 'environment_metadata': environment, **exported,
                  'accounting': {'completed_events': len(episodes), 'decisions': sum(x['decisions'] for x in exported['summaries']),
                                 'illegal_actions': 0, 'truncations': 0, 'episode_actions_match': True},
                  'samples': {'path': str(args.samples) if args.samples else None, 'count': sample_count, 'cap': args.sample_limit},
                  'sources': {str(path.relative_to(ROOT)): train_rl.sha(path) for path in
                              (Path(__file__).resolve(), ROOT / 'tools/train_rl.py', ROOT / 'tools/sfld_rl/env.py', ROOT / 'tools/sfld_rl/ppo.py')},
                  'device': str(device), 'torch': str(torch.__version__), 'numpy': str(np.__version__),
                  'wall_seconds': time.monotonic() - started}
        if train_rl.sha(args.checkpoint) != checkpoint_hash:
            raise RuntimeError('Checkpoint changed during diagnostic rollout')
        if sample_stream:
            sample_stream.close(); sample_stream = None
            if args.samples.exists():
                raise FileExistsError('Sample destination appeared while auditing')
            partial.rename(args.samples)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
        print(json.dumps({'output': str(args.output), **result['accounting']}, allow_nan=False), flush=True)
        return result
    finally:
        if sample_stream:
            sample_stream.close()


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--library', type=Path)
    parser.add_argument('--profiles', type=Path, nargs='+')
    parser.add_argument('--budgets', default='0,20,50,100,500')
    parser.add_argument('--events', type=int, default=256, help='Complete indexed episodes per budget/profile')
    parser.add_argument('--n-envs', type=int, default=128)
    parser.add_argument('--seed', type=int, default=9200001)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--torch-threads', type=int, default=1)
    parser.add_argument('--teacher-free-rules', type=Path)
    parser.add_argument('--teacher-paid-rules', type=Path)
    parser.add_argument('--no-teacher', action='store_true', help='Describe learned decisions without requesting legacy teacher labels')
    parser.add_argument('--samples', type=Path, help='Optional capped decision JSONL')
    parser.add_argument('--sample-limit', type=int, default=2000)
    args = parser.parse_args(argv)
    try:
        args.budgets = [int(x) for x in args.budgets.split(',')]
    except ValueError:
        parser.error('Budgets must be comma-separated integers')
    if not args.budgets or len(set(args.budgets)) != len(args.budgets) or not all(0 <= b <= 1000000 for b in args.budgets):
        parser.error('Budgets must be distinct integers in 0..1000000')
    if not 1 <= args.events < 2**24 or not 1 <= args.n_envs <= 4096 or not 0 <= args.seed < 2**64:
        parser.error('Invalid event count, vector size, or root seed')
    if not 1 <= args.torch_threads <= 32 or not 0 <= args.sample_limit <= 1000000:
        parser.error('Invalid thread count or sample cap')
    return args


if __name__ == '__main__':
    try:
        run(parse())
    except (ValueError, RuntimeError, OSError) as error:
        print(f'audit_rl_policy: {error}', file=sys.stderr)
        raise SystemExit(1)
