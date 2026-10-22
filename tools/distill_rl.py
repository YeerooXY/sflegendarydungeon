"""Imitate a frozen neural dungeon policy with small, phase-specific rule trees.

Fit and agreement labels come from different event namespaces. The final test
plays complete events with both policies; decision agreement is not a substitute
for measured completion loss. Tree artifacts are JSON, never pickled estimators.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
from types import SimpleNamespace

import numpy as np
import torch
from torch import nn
from sklearn.tree import DecisionTreeClassifier
import sklearn

import train_rl
from sfld_rl.env import VectorEnv

ROOT = Path(__file__).resolve().parents[1]
PHASES = ('doors', 'encounter', 'shop', 'curse_shop', 'gems', 'recovery', 'complete')
DOMAINS = {'distill_fit': 0xD6, 'distill_agreement': 0xD8, 'distill_gameplay': 0xD7}
SCHEMA = 'sfld-phase-trees-v1'


def save_json(path, value):
    if Path(path).exists():
        raise FileExistsError(f'Refusing to overwrite distillation artifact: {path}')
    train_rl.write_json(path, value)


def phase_indices(names):
    return [names.index('phase.' + phase) for phase in PHASES]


def phases_of(observations, indices):
    values = observations[:, indices]
    if not np.isfinite(values).all() or not np.all((values == 0) | (values == 1)) or not np.all(values.sum(axis=1) == 1):
        raise ValueError('Every observation must contain exactly one active phase')
    return values.argmax(axis=1)


def balanced_quotas(samples, budgets):
    if samples < len(budgets):
        raise ValueError('Need at least one sample per declared budget')
    return [samples // len(budgets) + (i < samples % len(budgets)) for i in range(len(budgets))]


def reservoir_insert(storage, batch, seen, random):
    """Algorithm R, retaining the last replacement when a batch hits a slot twice."""
    capacity = len(storage['actions']); count = len(batch['actions'])
    fill = min(count, max(0, capacity - seen))
    if fill:
        for key in storage:
            storage[key][seen:seen + fill] = batch[key][:fill]
    if fill < count:
        positions = np.arange(fill, count)
        targets = random.integers(0, seen + positions + 1)
        selected = targets < capacity
        targets, positions = targets[selected], positions[selected]
        # Advanced assignment with repeated indices is not a portable ordering
        # promise. Select the last candidate explicitly before copying any row.
        _, last_reversed = np.unique(targets[::-1], return_index=True)
        last = len(targets) - 1 - last_reversed
        for key in storage:
            storage[key][targets[last]] = batch[key][positions[last]]
    return seen + count


def collect_labels(actor, saved, args, device, *, samples, seed, namespace):
    """Balanced reservoirs; event domains stay separate and late play is sampled."""
    blocks, metadata, reports = [], [], []
    started = time.monotonic()
    for budget, quota in zip(args.budgets, balanced_quotas(samples, args.budgets)):
        with VectorEnv(args.library, [args.profile], min(args.n_envs, quota), budgets=[budget], seed=seed,
                       seed_namespace=DOMAINS[namespace], deadline_hours=args.deadline_hours,
                       max_actions=args.max_actions, auto_reset=False) as env:
            obs, masks = env.reset()
            train_rl.environment_changes(saved['metadata']['environment_signature'], env.metadata, resume=True)
            if obs.shape[1] != actor.architecture['observation_size'] or masks.shape[1] != actor.architecture['action_size']:
                raise ValueError('Checkpoint and environment dimensions differ')
            data = {'observations': np.empty((quota, obs.shape[1]), np.float32),
                    'masks': np.empty((quota, masks.shape[1]), bool), 'actions': np.empty(quota, np.int64),
                    'event_indices': np.empty(quota, np.int64), 'budgets': np.full(quota, budget, np.int32)}
            seen = executed = completed = 0
            reservoir_seed = (seed ^ (DOMAINS[namespace] << 48) ^ (budget << 16) ^ 0x392bbe91) & ((1 << 64) - 1)
            random = np.random.default_rng(reservoir_seed)
            previous = np.zeros(env.n_envs)
            while seen < quota or completed < args.min_collection_events:
                with torch.no_grad():
                    labels = actor.actions(train_rl.tensor(obs, device), train_rl.tensor(masks, device, torch.bool), greedy=True)[0].cpu().numpy()
                batch = {'observations': obs, 'masks': masks, 'actions': labels,
                         'event_indices': env.episode_indices, 'budgets': np.full(env.n_envs, budget, np.int32)}
                seen = reservoir_insert(data, batch, seen, random)
                obs, masks, reward, done, truncated, info = env.step(labels)
                train_rl.transition_guard(reward, done, truncated, info, previous)
                previous[:] = info['completions']
                executed += env.n_envs
                ended = np.flatnonzero(done)
                if len(ended):
                    completed += len(ended); obs, masks = env.reset_done(ended); previous[ended] = 0
            blocks.append(data); metadata.append(env.metadata)
            reports.append({'budget': budget, 'labels': quota, 'executed_transitions': executed,
                            'completed_collection_events': completed,
                            'reservoir_seed': str(reservoir_seed),
                            'distinct_labeled_events': len(np.unique(data['event_indices'])),
                            'last_event_index': int(data['event_indices'].max())})
            print(json.dumps({'collected': namespace, **reports[-1]}), flush=True)
    return {key: np.concatenate([block[key] for block in blocks]) for key in blocks[0]}, {
        'root_seed': str(seed), 'namespace': namespace, 'namespace_byte': DOMAINS[namespace],
        'requested_labels': samples, 'budgets': reports, 'environment_metadata': metadata,
        'wall_seconds': time.monotonic() - started,
        'minimum_completed_events_per_budget': args.min_collection_events,
        'sampling': 'Equal-size uniform transition reservoirs by budget, after enough full actor events to include late play. Continuing vector rows may be partial at the stop. Fit and held-out labels use separate event domains; no row-level train/validation split.'}


def fit_trees(data, observation_names, action_names, *, max_depth=6, max_leaves=32, min_leaf=20, seed=1):
    x = np.asarray(data['observations'], np.float32); y = np.asarray(data['actions'], np.int64)
    masks = np.asarray(data['masks'], bool)
    if x.ndim != 2 or x.shape[1] != len(observation_names) or y.shape != (len(x),) or masks.shape != (len(x), len(action_names)):
        raise ValueError('Inconsistent distillation dataset dimensions')
    if not len(x) or not np.isfinite(x).all() or np.any(y < 0) or np.any(y >= len(action_names)) or not masks[np.arange(len(y)), y].all():
        raise ValueError('Distillation labels must be finite and legal')
    if not 1 <= max_depth <= 6 or not 2 <= max_leaves <= 32 or min_leaf < 1:
        raise ValueError('Trees require depth1..6, leaves2..32 and a positive minimum leaf count')
    phase = phases_of(x, phase_indices(observation_names))
    trees = {}
    for phase_id, name in enumerate(PHASES):
        selected = phase == phase_id
        counts = np.bincount(y[selected], minlength=len(action_names)).astype(int).tolist()
        nodes = []
        if selected.any():
            estimator = DecisionTreeClassifier(max_depth=max_depth, max_leaf_nodes=max_leaves,
                                               min_samples_leaf=min_leaf, random_state=seed)
            estimator.fit(x[selected], y[selected])
            tree = estimator.tree_
            for i in range(tree.node_count):
                values = np.zeros(len(action_names))
                empirical = tree.value[i, 0]
                values[estimator.classes_.astype(int)] = empirical / empirical.sum() * tree.weighted_n_node_samples[i]
                leaf = tree.children_left[i] == tree.children_right[i]
                node = {'samples': int(tree.n_node_samples[i]), 'action_counts': np.rint(values).astype(int).tolist()}
                if not leaf:
                    node.update(feature=int(tree.feature[i]), threshold=float(tree.threshold[i]),
                                left=int(tree.children_left[i]), right=int(tree.children_right[i]))
                nodes.append(node)
        trees[name] = {'samples': int(selected.sum()), 'action_counts': counts, 'nodes': nodes}
    return {'schema': SCHEMA, 'observation_names': list(observation_names), 'action_names': list(action_names),
            'phase_names': list(PHASES), 'global_action_counts': np.bincount(y, minlength=len(action_names)).astype(int).tolist(),
            'trees': trees, 'fit': {'max_depth': max_depth, 'max_leaves': max_leaves, 'min_leaf': min_leaf, 'random_state': seed},
            'fallback': 'Mask illegal actions, then choose the most frequent legal leaf action. If none was observed, use phase counts, then global counts, then the first legal action. Ties use schema order.'}


class TreePolicy(nn.Module):
    """Inference needs only JSON and visible observations plus legal masks."""
    def __init__(self, document):
        super().__init__()
        self.document = document
        if document.get('schema') != SCHEMA or document.get('phase_names') != list(PHASES):
            raise ValueError('Unsupported tree policy schema')
        self.observation_names = document['observation_names']; self.action_names = document['action_names']
        self.phase_columns = phase_indices(self.observation_names)
        self.architecture = {'observation_size': len(self.observation_names), 'action_size': len(self.action_names)}
        if not self.action_names or len(set(self.action_names)) != len(self.action_names) or len(set(self.observation_names)) != len(self.observation_names):
            raise ValueError('Invalid feature/action schema')
        self.global_counts = self._counts(document['global_action_counts'])
        self.tables = {}
        for phase in PHASES:
            tree = document['trees'][phase]; nodes = tree['nodes']; prior = self._counts(tree['action_counts'])
            if len(nodes) > 127:
                raise ValueError('Tree exceeds supported depth/size')
            seen = set()
            def visit(index, depth):
                if not isinstance(index, int) or index < 0 or index >= len(nodes) or index in seen or depth > 6:
                    raise ValueError('Tree contains an invalid edge, cycle or excessive depth')
                seen.add(index); node = nodes[index]; self._counts(node['action_counts'])
                if 'feature' in node:
                    if not isinstance(node['feature'], int) or not 0 <= node['feature'] < len(self.observation_names) or not math.isfinite(node['threshold']):
                        raise ValueError('Tree split is invalid')
                    visit(node['left'], depth + 1); visit(node['right'], depth + 1)
            if nodes:
                visit(0, 0)
                if len(seen) != len(nodes) or sum('feature' not in node for node in nodes) > 32:
                    raise ValueError('Tree has unreachable nodes or too many leaves')
            self.tables[phase] = {'prior': prior, 'feature': np.array([n.get('feature', -1) for n in nodes], np.int64),
                                  'threshold': np.array([n.get('threshold', 0) for n in nodes]),
                                  'left': np.array([n.get('left', 0) for n in nodes], np.int64),
                                  'right': np.array([n.get('right', 0) for n in nodes], np.int64),
                                  'counts': np.array([n['action_counts'] for n in nodes], np.float64)}
        self.reset_diagnostics()

    def _counts(self, values):
        result = np.asarray(values, np.float64)
        if result.shape != (len(self.action_names),) or not np.isfinite(result).all() or np.any(result < 0):
            raise ValueError('Invalid empirical action counts')
        return result

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    def reset_diagnostics(self):
        self.diagnostics = {'decisions': 0, 'masked_top_action': 0, 'phase_fallback': 0, 'global_fallback': 0, 'unobserved_legal_fallback': 0}

    def predict_numpy(self, observations, masks):
        x = np.asarray(observations, np.float32); masks = np.asarray(masks, bool)
        if x.ndim != 2 or x.shape[1] != len(self.observation_names) or masks.shape != (len(x), len(self.action_names)) or not np.isfinite(x).all():
            raise ValueError('Invalid tree observation/mask dimensions or values')
        if not masks.any(axis=1).all():
            raise ValueError('Every active tree observation needs a legal action')
        phase_ids = phases_of(x, self.phase_columns); actions = np.empty(len(x), np.int64)
        for phase_id, name in enumerate(PHASES):
            rows = np.flatnonzero(phase_ids == phase_id)
            if not len(rows):
                continue
            table = self.tables[name]; legal = masks[rows]
            if len(table['feature']):
                node = np.zeros(len(rows), np.int64)
                for _ in range(7):
                    active = table['feature'][node] >= 0
                    if not active.any():
                        break
                    positions = np.flatnonzero(active); current = node[positions]
                    go_left = x[rows[positions], table['feature'][current]] <= table['threshold'][current]
                    node[positions] = np.where(go_left, table['left'][current], table['right'][current])
                counts = table['counts'][node].copy()
            else:
                counts = np.zeros((len(rows), len(self.action_names)))
            self.diagnostics['masked_top_action'] += int((~legal[np.arange(len(rows)), counts.argmax(axis=1)] & (counts.sum(axis=1) > 0)).sum())
            counts *= legal
            for fallback, prior in [('phase_fallback', table['prior']), ('global_fallback', self.global_counts),
                                    ('unobserved_legal_fallback', np.ones(len(self.action_names)))]:
                missing = counts.sum(axis=1) <= 0
                self.diagnostics[fallback] += int(missing.sum())
                counts[missing] = prior * legal[missing]
            actions[rows] = counts.argmax(axis=1)
        self.diagnostics['decisions'] += len(x)
        return actions

    def actions(self, observations, masks, greedy=True):
        result = self.predict_numpy(observations.detach().cpu().numpy(), masks.detach().cpu().numpy())
        action = torch.as_tensor(result, device=observations.device)
        zero = torch.zeros(len(result), device=observations.device)
        return action, zero, zero

    def values(self, observations):
        # No learned or heuristic value enters full-event gameplay evaluation.
        return torch.zeros(len(observations), device=observations.device)


def agreement(policy, data):
    policy.reset_diagnostics()
    predicted = policy.predict_numpy(data['observations'], data['masks'])
    correct = predicted == data['actions']; phases = phases_of(data['observations'], policy.phase_columns)
    result = {'samples': len(correct), 'agreement': float(correct.mean()), 'diagnostics': dict(policy.diagnostics), 'by_phase': {}, 'by_budget': {}}
    for i, phase in enumerate(PHASES):
        take = phases == i
        result['by_phase'][phase] = {'samples': int(take.sum()), 'agreement': float(correct[take].mean()) if take.any() else None}
    for budget in np.unique(data['budgets']):
        take = data['budgets'] == budget
        result['by_budget'][str(int(budget))] = {'samples': int(take.sum()), 'agreement': float(correct[take].mean()),
                                                'distinct_events': len(np.unique(data['event_indices'][take]))}
    result['interpretation'] = 'Agreement on independent teacher trajectories; transitions within an event are correlated. Gameplay loss must be measured separately.'
    return result


def condition_text(name, threshold, left):
    operator = '<=' if left else '>'
    if name == 'hp':
        return f'health {operator} {threshold * 100:.8g}%'
    match = re.search(r'_div(\d+)$', name)
    if match:
        return f'{name[:match.start()]} {operator} {threshold * int(match.group(1)):.8g}'
    boolean = name.startswith(('phase.', 'resume_phase.', 'stage.', 'owned_gem.', 'shops_seen.', 'encounter.', 'gem_offers.', 'donated_resource.')) or '.kind.' in name or '.clock.' in name or name.endswith(('.trap', '.cursed_trap', '.available', '.present', '.active')) or name in ('terminal', 'first_event_run', 'trial_seen')
    if boolean and 0 <= threshold < 1:
        return name + (' is false' if left else ' is true')
    return f'{name} {operator} {threshold:.8g}'


def readable_rules(document):
    lines = ['Approximate imitation of a frozen neural policy.',
             'Follow the rules for the current phase. All conditions within a rule must hold.',
             'At a leaf, choose the highest-frequency LEGAL action. If no observed leaf action is legal, use phase frequencies, then global frequencies, then the first legal action.',
             'Action ties use the listed action schema order. Percentages are empirical training frequencies, not success odds.',
             'Display thresholds are rounded; the JSON contains exact thresholds. Small trees may lose completions; inspect the gameplay comparison.',
             'Action schema: ' + ', '.join(document['action_names']), '']
    def ranking(counts):
        total = sum(counts)
        return ', '.join(f'{document["action_names"][i]} ({counts[i] / total:.1%})' for i in sorted(range(len(counts)), key=lambda j: (-counts[j], j)) if counts[i]) if total else 'no observed actions'
    lines.append('Global fallback frequencies: ' + ranking(document['global_action_counts']))
    for phase in PHASES:
        tree = document['trees'][phase]; lines.extend(['', phase.upper(), 'Phase fallback frequencies: ' + ranking(tree['action_counts'])])
        nodes = tree['nodes']
        if not nodes:
            lines.append('No fitting examples in this phase; use the declared fallback.')
            continue
        def visit(index, conditions):
            node = nodes[index]
            if 'feature' not in node:
                lines.append('IF ' + (' AND '.join(conditions) if conditions else 'always') + ' THEN ' + ranking(node['action_counts']) + f' [n={node["samples"]}]')
                return
            name = document['observation_names'][node['feature']]
            visit(node['left'], conditions + [condition_text(name, node['threshold'], True)])
            visit(node['right'], conditions + [condition_text(name, node['threshold'], False)])
        visit(0, [])
    return '\n'.join(lines) + '\n'


def paired_gameplay(neural, tree):
    for field in ('root_seed', 'namespace_byte'):
        if neural[field] != tree[field]:
            raise ValueError('Cannot pair different gameplay cohorts')
    left_metadata = [train_rl.compact_metadata(m) for m in neural.get('environment_metadata', [])]
    right_metadata = [train_rl.compact_metadata(m) for m in tree.get('environment_metadata', [])]
    if left_metadata != right_metadata:
        raise ValueError('Cannot pair different libraries, profiles or event horizons')
    def indexed(result):
        rows = {}
        for row in result['outcomes']:
            key = (row['profile'], row['budget'], row['event_index'])
            if key in rows or row['truncated'] or row['invalid_actions'] or not row['terminated'] or row['mushrooms'] > row['budget']:
                raise ValueError('Invalid or repeated gameplay outcome; completion estimates withheld')
            rows[key] = row
        return rows
    actor_rows, tree_rows = indexed(neural), indexed(tree)
    if not actor_rows or actor_rows.keys() != tree_rows.keys():
        raise ValueError('Cannot pair incomplete or different gameplay event sets')
    result = []
    for profile, budget in sorted({(key[0], key[1]) for key in actor_rows}):
        keys = sorted(key for key in actor_rows if key[:2] == (profile, budget))
        if [key[2] for key in keys] != list(range(len(keys))):
            raise ValueError('Gameplay event indices must form a complete cohort starting at zero')
        losses = [actor_rows[key]['completions'] - tree_rows[key]['completions'] for key in keys]
        result.append({'profile': profile, 'budget': budget, 'events': len(keys),
                       'neural_completions_mean': statistics.mean(actor_rows[key]['completions'] for key in keys),
                       'tree_completions_mean': statistics.mean(tree_rows[key]['completions'] for key in keys),
                       'paired_completion_loss': train_rl.interval(losses),
                       'interpretation': 'Positive loss means fewer completions with the tree policy.'})
    return result


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--library', type=Path, default=ROOT / 'build/Release/sfld_rl.dll')
    parser.add_argument('--profile', type=Path, default=ROOT / 'profiles/synthetic.profile')
    parser.add_argument('--budgets', default='0,20,50,100,500')
    parser.add_argument('--samples', type=int, default=262144)
    parser.add_argument('--agreement-samples', type=int, default=32768)
    parser.add_argument('--min-collection-events', type=int, default=64)
    parser.add_argument('--events', type=int, default=256)
    parser.add_argument('--n-envs', type=int, default=128)
    parser.add_argument('--max-depth', type=int, default=6)
    parser.add_argument('--max-leaves', type=int, default=32)
    parser.add_argument('--min-leaf', type=int, default=20)
    parser.add_argument('--fit-seed', type=int, default=9200001)
    parser.add_argument('--agreement-seed', type=int, default=9200003)
    parser.add_argument('--gameplay-seed', type=int, default=9200002)
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    parser.add_argument('--deadline-hours', type=float, default=168)
    parser.add_argument('--max-actions', type=int, default=100000)
    args = parser.parse_args(argv)
    try:
        args.budgets = [int(x) for x in args.budgets.split(',')]
    except ValueError:
        parser.error('Budgets must be comma-separated integers')
    if not args.budgets or len(set(args.budgets)) != len(args.budgets) or min(args.budgets) < 0 or max(args.budgets) > 1000000:
        parser.error('Budgets must be distinct integers0..1000000')
    if not 1 <= args.n_envs <= 4096 or min(args.samples, args.agreement_samples) < len(args.budgets) or args.events < 2 or args.min_collection_events < 1:
        parser.error('Invalid sample count, event count or vector size')
    if not 1 <= args.max_depth <= 6 or not 2 <= args.max_leaves <= 32 or args.min_leaf < 1:
        parser.error('Trees require depth1..6, leaves2..32 and positive minimum leaf count')
    if any(not 0 <= seed < 2**64 for seed in (args.fit_seed, args.agreement_seed, args.gameplay_seed)):
        parser.error('Seeds must fit unsigned64-bit integers')
    if not math.isfinite(args.deadline_hours) or args.deadline_hours <= 0 or args.max_actions < 1:
        parser.error('Invalid event horizon or action cap')
    return args


def main(argv=None):
    args = parse(argv); args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(2); device = torch.device(args.device)
    if device.type == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable')
    train_rl.DOMAINS.update(DOMAINS)  # Process-local names only; frozen training sources stay untouched.
    started = time.monotonic()
    checkpoint_hash = train_rl.sha(args.checkpoint)
    saved, actor = train_rl.load_checkpoint(args.checkpoint, device); actor.eval()
    provenance = {'checkpoint': str(args.checkpoint.resolve()), 'checkpoint_sha256': checkpoint_hash,
                  'library_sha256': train_rl.sha(args.library), 'profile_sha256': train_rl.sha(args.profile),
                  'script_sha256': train_rl.sha(Path(__file__)), 'torch': str(torch.__version__),
                  'numpy': str(np.__version__), 'scikit_learn': sklearn.__version__,
                  'device': str(device), 'budgets': args.budgets, 'deadline_hours': args.deadline_hours,
                  'max_actions': args.max_actions, 'n_envs': args.n_envs, 'seed_domains': DOMAINS}
    save_json(args.output / 'plan.json', {'schema': 'sfld-distillation-plan-v1', **provenance,
        'fit_seed': str(args.fit_seed), 'agreement_seed': str(args.agreement_seed), 'gameplay_seed': str(args.gameplay_seed),
        'samples': args.samples, 'agreement_samples': args.agreement_samples, 'events_per_budget': args.events,
        'min_collection_events_per_budget': args.min_collection_events,
        'max_depth': args.max_depth, 'max_leaves': args.max_leaves, 'min_leaf': args.min_leaf})
    fit, fit_info = collect_labels(actor, saved, args, device, samples=args.samples, seed=args.fit_seed, namespace='distill_fit')
    metadata = fit_info['environment_metadata'][0]; schema = metadata['schema']
    document = fit_trees(fit, schema['observation_names'], schema['action_names'], max_depth=args.max_depth,
                         max_leaves=args.max_leaves, min_leaf=args.min_leaf, seed=args.fit_seed & 0xffffffff)
    document['provenance'] = provenance
    tree_path = args.output / 'policy-trees.json'; save_json(tree_path, document)
    rules_path = args.output / 'rules.txt'; rules_path.write_text(readable_rules(document), encoding='utf-8')
    policy = TreePolicy.load(tree_path)
    fitting_agreement = agreement(policy, fit)
    np.savez_compressed(args.output / 'fit-labels.npz', **fit); del fit
    held, held_info = collect_labels(actor, saved, args, device, samples=args.agreement_samples,
                                    seed=args.agreement_seed, namespace='distill_agreement')
    held_agreement = agreement(policy, held)
    np.savez_compressed(args.output / 'agreement-labels.npz', **held); del held
    agreement_report = {'schema': 'sfld-distillation-agreement-v1', 'fit_collection': fit_info,
                        'held_out_collection': held_info, 'fitting': fitting_agreement, 'held_out': held_agreement}
    save_json(args.output / 'agreement.json', agreement_report)
    eval_args = SimpleNamespace(library=args.library, profiles=[args.profile], budgets=args.budgets, seed=args.gameplay_seed,
                               n_envs=args.n_envs, deadline_hours=args.deadline_hours, max_actions=args.max_actions, eval_action='greedy')
    print(json.dumps({'evaluating_gameplay': True, 'events_per_budget': args.events}), flush=True)
    neural = train_rl.evaluate_model(actor, eval_args, device, namespace='distill_gameplay', events=args.events)
    neural.update(schema='sfld-ppo-evaluation-v1', checkpoint_sha256=checkpoint_hash, policy_type='neural')
    save_json(args.output / 'gameplay-neural.json', neural)
    policy.reset_diagnostics()
    tree = train_rl.evaluate_model(policy, eval_args, torch.device('cpu'), namespace='distill_gameplay', events=args.events)
    tree.update(schema='sfld-ppo-evaluation-v1', checkpoint_sha256=checkpoint_hash, policy_type='phase_trees',
                tree_sha256=train_rl.sha(tree_path), inference_diagnostics=dict(policy.diagnostics),
                inference_diagnostics_scope='All stepped vector rows, including auxiliary events outside the indexed reported cohort.')
    save_json(args.output / 'gameplay-tree.json', tree)
    paired = paired_gameplay(neural, tree)
    summary = {'schema': 'sfld-distillation-summary-v1', **provenance,
               'trees': {'file': tree_path.name, 'sha256': train_rl.sha(tree_path)},
               'readable_rules': {'file': rules_path.name, 'sha256': train_rl.sha(rules_path)},
               'agreement': {'file': 'agreement.json', 'sha256': train_rl.sha(args.output / 'agreement.json'), 'held_out': held_agreement},
               'label_datasets': {'fit': {'file': 'fit-labels.npz', 'sha256': train_rl.sha(args.output / 'fit-labels.npz')},
                                  'held_out': {'file': 'agreement-labels.npz', 'sha256': train_rl.sha(args.output / 'agreement-labels.npz')}},
               'neural_evaluation': {'file': 'gameplay-neural.json', 'sha256': train_rl.sha(args.output / 'gameplay-neural.json')},
               'tree_evaluation': {'file': 'gameplay-tree.json', 'sha256': train_rl.sha(args.output / 'gameplay-tree.json')},
               'gameplay_cohort': {'root_seed': str(args.gameplay_seed), 'namespace_byte': DOMAINS['distill_gameplay'], 'events_per_budget': args.events},
               'paired_gameplay': paired, 'wall_seconds': time.monotonic() - started,
               'interpretation': 'Approximate imitation, not optimal rules. Positive paired loss means the tree completes fewer runs. Held-out gameplay must decide whether this simplification is useful; retain the neural policy or existing guide when loss is unacceptable.'}
    save_json(args.output / 'distillation-summary.json', summary)
    print(json.dumps({'output': str(args.output), 'held_out_agreement': held_agreement['agreement'], 'paired_gameplay': paired}), flush=True)


if __name__ == '__main__':
    main()
