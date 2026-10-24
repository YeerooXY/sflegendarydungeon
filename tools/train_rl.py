"""Train masked PPO on visible dungeon state, or evaluate frozen checkpoints.

Reward is completed runs only, gamma=1. Death and room 100 do not end a
seven-day episode. Validation selects checkpoints; final seeds stay untouched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np
import torch

from sfld_rl.ppo import ActorCritic, PPOConfig, advantages, cloning_update, ppo_update
from sfld_rl.stone_priority import MODES as STONE_PRIORITIES, with_priority
from sfld_rl.shop_policy import MODES as SHOP_POLICIES, ShopPolicyActor, with_shop, profile_config

DOMAINS = {'train': 0xA1, 'cloning': 0xA2, 'validation': 0xB2, 'final': 0xC3}
ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def tensor(array, device, dtype=torch.float32):
    return torch.as_tensor(array, dtype=dtype, device=device)


def policy_actions(model, observations, masks, device, *, greedy=False):
    """Constrain host-resident shop rows before their first GPU transfer.

    Only the exact standard shop wrapper takes this path. Subclasses may
    change their distribution (for example exploration temperature) and must
    retain their own actions implementation.
    """
    if type(model) is ShopPolicyActor and torch.device(device).type == 'cuda':
        constrained = model.numpy_masks(observations, masks)
        distribution, values = model.ppo_distribution(
            tensor(observations, device), tensor(constrained, device, torch.bool))
        actions = distribution.logits.argmax(dim=-1) if greedy else distribution.sample()
        return actions, distribution.log_prob(actions), values
    return model.actions(tensor(observations, device), tensor(masks, device, torch.bool), greedy=greedy)


def make_env(args, *, n_envs=None, budgets=None, profiles=None, namespace='train', initial_index=0):
    from sfld_rl.env import VectorEnv
    return VectorEnv(args.library, profiles or args.profiles, n_envs or args.n_envs,
                     budgets=tuple(args.budgets if budgets is None else budgets), seed=args.seed,
                     seed_namespace=DOMAINS[namespace], deadline_hours=args.deadline_hours,
                     max_actions=args.max_actions, auto_reset=False, initial_episode_index=initial_index)


def teacher_actions(env, args):
    budgets = np.asarray(env.episode_budgets)
    actions = np.empty(len(budgets), dtype=np.int64)
    for free, kind, rules in [(True, 'legacy', args.teacher_free_rules),
                              (False, 'survival', args.teacher_paid_rules)]:
        selected = (budgets == 0) if free else (budgets > 0)
        if selected.any():
            labels = np.asarray(env.teacher_actions(kind=kind, rules_path=rules), dtype=np.int64)
            actions[selected] = labels[selected]
    if (actions < 0).any():
        raise RuntimeError('Teacher action is unavailable in the RL action schema')
    return actions


def transition_guard(rewards, terminated, truncated, infos, previous_completions):
    """Fail instead of training on a safety cap, illegal move or shaped reward."""
    rewards = np.asarray(rewards)
    if not np.isfinite(rewards).all():
        raise RuntimeError('Nonfinite environment reward')
    if np.asarray(truncated).any():
        raise RuntimeError(f'Safety truncation in {int(np.asarray(truncated).sum())} environments; not a deadline terminal')
    if np.asarray(infos['invalid_actions']).any():
        raise RuntimeError('Environment reported invalid actions')
    actual = np.asarray(infos['completions']) - previous_completions
    if not np.allclose(rewards, actual, rtol=0, atol=1e-6):
        raise RuntimeError('Reward must equal the change in completed runs, without shaping')
    if np.any(np.asarray(terminated) & (np.asarray(infos['elapsed_hours']) <= 0)):
        raise RuntimeError('Invalid episode terminal metadata')


def compact_metadata(metadata):
    # The schema and binary/profile content establish compatibility. Preserve
    # full metadata too; runtime counters and paths need not match on resume.
    result = {key: value for key, value in metadata.items()
              if 'sha256' in key or 'schema' in key or key in
              ('observation_names', 'action_names', 'deadline_hours', 'max_actions')}
    result['profile_sha256'] = [p['sha256'] for p in metadata.get('profiles', [])]
    return result


def validate_resume_settings(args, metadata):
    if metadata.get('shop_policy') != profile_config(getattr(args, 'profiles', []), getattr(args, 'shop_policy', 'learned')):
        raise ValueError('Resume changes frozen training setting: shop_policy')
    if metadata.get('stone_priority', 'learned') != getattr(args, 'stone_priority', 'learned'):
        raise ValueError('Resume changes frozen training setting: stone_priority')
    for key in ('seed', 'budgets', 'n_envs', 'rollout_steps', 'validation_events', 'eval_action'):
        expected = str(args.seed) if key == 'seed' else getattr(args, key)
        if metadata[key] != expected:
            raise ValueError(f'Resume changes frozen training setting: {key}')


def environment_changes(saved_signature, current_metadata, *, resume=False):
    current = compact_metadata(current_metadata)
    changed = {key: {'training': saved_signature.get(key), 'current': current.get(key)}
               for key in sorted(set(saved_signature) | set(current))
               if saved_signature.get(key) != current.get(key)}
    if resume and changed:
        raise ValueError('Resume environment schema, profile or library differs: ' + ', '.join(changed))
    for key in changed:
        if 'schema' in key or key in ('observation_names', 'action_names', 'library_sha256'):
            raise ValueError(f'Checkpoint environment mismatch: {key}')
    return changed


def environment_transfer(saved_signature, current_metadata):
    """Changes allowed by --transfer-environment: a new engine build, library or
    profile whose observation and action layouts are exactly the parent's."""
    old, new = saved_signature.get('schema') or {}, current_metadata.get('schema') or {}
    for key in set(old) | set(new):
        if key != 'engine' and old.get(key) != new.get(key):
            raise ValueError(f'Environment transfer changes the observation/action schema: {key}')
    current = compact_metadata(current_metadata)
    changed = {key: {'parent': saved_signature.get(key), 'new': current.get(key)}
               for key in ('library_sha256', 'profile_sha256', 'schema_sha256')
               if saved_signature.get(key) != current.get(key)}
    if old.get('engine') != new.get('engine'):
        changed['engine'] = {'parent': old.get('engine'), 'new': new.get('engine')}
    return changed


@contextmanager
def evaluation_rng(device, seed):
    devices = [device.index if device.index is not None else torch.cuda.current_device()] if device.type == 'cuda' else []
    with torch.random.fork_rng(devices=devices):
        torch.manual_seed(seed & ((1 << 64) - 1))
        yield


def interval(values):
    mean = statistics.mean(values)
    se = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.
    return {'mean': mean, 'standard_error': se, 'normal_95_ci': [mean - 1.96 * se, mean + 1.96 * se]}


def evaluate_model(model, args, device, *, namespace, events, teacher=False, profiles=None):
    """Exactly indices 0..events-1 per budget/profile, independent of finish order."""
    summaries, outcomes, metadata = [], [], []
    was_training = model.training if model is not None else False
    if model is not None:
        model.eval()
    started = time.monotonic()
    try:
        with evaluation_rng(device, args.seed + DOMAINS[namespace]):
            for profile in profiles or args.profiles:
                for budget in args.budgets:
                    env = make_env(args, n_envs=min(args.n_envs, events), budgets=[budget],
                                   profiles=[profile], namespace=namespace)
                    collected = {}
                    try:
                        obs, masks = env.reset()
                        metadata.append(env.metadata)
                        while len(collected) < events:
                            if teacher:
                                actions = teacher_actions(env, args)
                            else:
                                with torch.no_grad():
                                    actions = policy_actions(model, obs, masks, device,
                                                             greedy=args.eval_action == 'greedy')[0].cpu().numpy()
                            obs, masks, rewards, terminated, truncated, info = env.step(actions)
                            ended = np.flatnonzero(np.asarray(terminated) | np.asarray(truncated))
                            for slot in ended:
                                index = int(info['episode_index'][slot])
                                if index < events:
                                    if index in collected:
                                        raise RuntimeError('Evaluation repeated an event index')
                                    row = {'profile': str(profile), 'budget': budget, 'event_index': index,
                                           'terminated': bool(terminated[slot]), 'truncated': bool(truncated[slot])}
                                    for key in ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms',
                                                'invalid_actions', 'elapsed_hours', 'actions'):
                                        value = float(info[key][slot])
                                        row[key] = value if key == 'elapsed_hours' else int(value)
                                    if row['mushrooms'] > budget:
                                        raise RuntimeError('Evaluation exceeded its gross mushroom budget')
                                    collected[index] = row
                            if len(ended):
                                obs, masks = env.reset_done(ended)
                        rows = [collected[i] for i in range(events)]
                        bad = sum(bool(r['truncated'] or r['invalid_actions'] or not r['terminated']) for r in rows)
                        summaries.append({'profile': str(profile), 'budget': budget, 'events': events,
                                          'failed_events': bad,
                                          'completions': interval([r['completions'] for r in rows]) if not bad else None,
                                          'mean_mushrooms': statistics.mean(r['mushrooms'] for r in rows),
                                          'mean_recovery_mushrooms': statistics.mean(r['recovery_mushrooms'] for r in rows),
                                          'mean_reroll_mushrooms': statistics.mean(r['reroll_mushrooms'] for r in rows),
                                          'invalid_actions': sum(r['invalid_actions'] for r in rows),
                                          'truncations': sum(r['truncated'] for r in rows)})
                        outcomes.extend(rows)
                    finally:
                        env.close()
    finally:
        if model is not None:
            model.train(was_training)
    return {'namespace': namespace, 'namespace_byte': DOMAINS[namespace], 'root_seed': str(args.seed),
            'action_policy': 'teacher' if teacher else args.eval_action,
            'summaries': summaries, 'outcomes': outcomes, 'environment_metadata': metadata,
            'wall_seconds': time.monotonic() - started,
            'selection_score': None if any(s['failed_events'] for s in summaries) else
            statistics.mean(s['completions']['mean'] for s in summaries)}


def checkpoint(path, model, optimizer, state, metadata, config):
    path = Path(path)
    if path.exists():
        raise FileExistsError(f'Checkpoint already exists: {path}')
    contents = {'schema': 'sfld-ppo-v1', 'architecture': model.architecture,
                'model': model.state_dict(), 'optimizer': optimizer.state_dict(),
                'state': state, 'metadata': metadata, 'ppo_config': asdict(config),
                'torch_rng': torch.get_rng_state(),
                'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    torch.save(contents, temporary)
    temporary.replace(path)


def load_checkpoint(path, device):
    saved = torch.load(path, map_location='cpu', weights_only=True)
    if saved.get('schema') != 'sfld-ppo-v1':
        raise ValueError('Unsupported RL checkpoint schema')
    model = ActorCritic(**saved['architecture']).to(device)
    model.load_state_dict(saved['model'])
    priority = saved['metadata'].get('stone_priority', 'learned')
    if priority != 'learned':
        model = with_priority(model, priority, saved['metadata']['environment_signature']['schema'])
    if saved['metadata'].get('shop_policy') is not None:
        model = with_shop(model, saved['metadata']['environment_signature']['schema'], saved['metadata']['shop_policy'])
    return saved, model


def source_metadata(args, env):
    source_paths = [Path(__file__), ROOT / 'tools/sfld_rl/ppo.py', ROOT / 'tools/sfld_rl/env.py',
                    ROOT / 'tools/sfld_rl/stone_priority.py', ROOT / 'tools/sfld_rl/shop_policy.py',
                    ROOT / 'tools/sfld_rl/player_ideas.py']
    source_paths += [ROOT / 'CMakeLists.txt']
    source_paths += sorted(p for directory in ('src', 'include') for p in (ROOT / directory).rglob('*')
                           if p.is_file() and p.suffix in ('.cpp', '.hpp', '.h', '.c'))
    if getattr(args, 'effect_curriculum', False):
        source_paths.append(ROOT / 'tools/sfld_rl/effect_lab.py')
    return {'environment': env.metadata, 'environment_signature': compact_metadata(env.metadata),
            'sources': {str(p.relative_to(ROOT)): sha(p) for p in source_paths},
            'python': platform.python_version(), 'torch': str(torch.__version__), 'numpy': str(np.__version__),
            'device': str(args.device), 'torch_threads': args.torch_threads,
            'seed': str(args.seed), 'seed_domains': DOMAINS,
            'budgets': args.budgets, 'profiles': [str(p) for p in args.profiles],
            'n_envs': args.n_envs, 'rollout_steps': args.rollout_steps,
            'validation_events': args.validation_events, 'eval_action': args.eval_action,
            'stone_priority': getattr(args, 'stone_priority', 'learned'),
            'shop_policy': profile_config(args.profiles, getattr(args, 'shop_policy', 'learned')),
            'effect_curriculum': bool(getattr(args, 'effect_curriculum', False)),
            'curriculum_cases': ({'path': str(args.curriculum_cases), 'sha256': sha(args.curriculum_cases)}
                                 if getattr(args, 'curriculum_cases', None) else None),
            'reward': 'delta completed_runs only', 'gamma': 1.,
            'validation_selection': 'equal mean completions over declared budgets and profiles; exact ties keep earlier checkpoint',
            'teacher': {'free': {'kind': 'legacy', 'rules': str(args.teacher_free_rules), 'sha256': sha(args.teacher_free_rules)},
                        'paid': {'kind': 'survival', 'rules': str(args.teacher_paid_rules), 'sha256': sha(args.teacher_paid_rules)}},
            'resume_semantics': 'Policy/optimizer/RNG restored; in-flight events abandoned and fresh unused training episode indices allocated; not bit-identical simulator continuation',
            'limitations': ['Synthetic mechanics; finite training does not establish optimality.',
                            'Validation selects a checkpoint and is not an independent final result.']}


def seed_fingerprint(seed):
    """Native episode masters retain the low 32 bits of SplitMix64(root seed)."""
    mask = (1 << 64) - 1
    value = (int(seed) + 0x9e3779b97f4a7c15) & mask
    value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & mask
    value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & mask
    return (value ^ (value >> 31)) & 0xffffffff


def initialize_finetune(args, env, config, device):
    """Transfer weights/Adam state, but allocate a fresh experiment and RNG."""
    if args.bc_steps:
        raise ValueError('Fine-tuning cannot repeat teacher cloning')
    parent_path = Path(args.finetune_from).resolve()
    parent_sha = sha(parent_path)
    saved, model = load_checkpoint(parent_path, device)
    parent_metadata = saved['metadata']
    parent_lineage = parent_metadata.get('finetune', {})
    ancestor_seeds = parent_lineage.get('ancestor_root_seeds', []) + [str(parent_metadata['seed'])]
    if any(seed_fingerprint(seed) == seed_fingerprint(args.seed) for seed in ancestor_seeds):
        raise ValueError('Fine-tuning requires a fresh root-seed fingerprint distinct from every parent run')
    transfer = getattr(args, 'transfer_environment', False)
    if transfer:
        transferred = environment_transfer(parent_metadata['environment_signature'], env.metadata)
    else:
        environment_changes(parent_metadata['environment_signature'], env.metadata, resume=True)
    priority = getattr(args, 'stone_priority', 'learned')
    shop_config = profile_config(args.profiles, getattr(args, 'shop_policy', 'learned'))
    if shop_config != parent_metadata.get('shop_policy'):
        model = with_shop(model, env.metadata['schema'], None)
    if priority != parent_metadata.get('stone_priority', 'learned'):
        model = with_priority(model, priority, env.metadata['schema'])
    if shop_config is not None:
        model = with_shop(model, env.metadata['schema'], shop_config)
    checked = {};source_changes = {}
    for path, fingerprint in parent_metadata['sources'].items():
        # The trainer implements the intentional new fine-tuning workflow.
        # Native sources, the environment adapter and PPO math stay frozen.
        if path.replace('\\', '/') == 'tools/train_rl.py':
            continue
        current = sha(ROOT / path) if (ROOT / path).exists() else None
        if current != fingerprint:
            if not transfer:
                raise ValueError(f'Fine-tuning dependency source changed: {path}')
            # A transfer is to the current engine and adapter; record what moved.
            source_changes[path] = {'parent': fingerprint, 'new': current};continue
        checked[path] = fingerprint
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, eps=1e-5)
    parent_rates = [group['lr'] for group in saved['optimizer']['param_groups']]
    optimizer.load_state_dict(saved['optimizer'])
    # load_state_dict restores the parent's LR too: explicitly replace it only
    # after loading so the requested fine-tuning rate actually takes effect.
    for group in optimizer.param_groups:
        group['lr'] = config.learning_rate
        if 'initial_lr' in group:
            group['initial_lr'] = config.learning_rate
    counters = ('steps', 'updates', 'bc_steps', 'bc_completed_events', 'completed_training_events')
    parent_counts = {key: int(saved['state'].get(key, 0)) for key in counters}
    inherited = parent_lineage.get('cumulative_parent_counts', {})
    cumulative = {key: parent_counts[key] + int(inherited.get(key, 0)) for key in counters}
    changes = {key: {'parent': saved['ppo_config'][key], 'new': value}
               for key, value in asdict(config).items() if saved['ppo_config'][key] != value}
    settings = ('budgets', 'n_envs', 'rollout_steps', 'validation_events', 'eval_action')
    setting_changes = {key: {'parent': parent_metadata[key], 'new': getattr(args, key)}
                       for key in settings if parent_metadata[key] != getattr(args, key)}
    setting_changes['seed'] = {'parent': str(parent_metadata['seed']), 'new': str(args.seed)}
    if shop_config != parent_metadata.get('shop_policy'):
        setting_changes['shop_policy'] = {'parent': parent_metadata.get('shop_policy'), 'new': shop_config}
    if priority != parent_metadata.get('stone_priority', 'learned'):
        setting_changes['stone_priority'] = {'parent': parent_metadata.get('stone_priority', 'learned'), 'new': priority}
    if sha(parent_path) != parent_sha:
        raise ValueError('Parent checkpoint changed while loading fine-tuning inputs')
    provenance = {'parent_checkpoint': str(parent_path), 'parent_sha256': parent_sha,
                  'ancestor_root_seeds': ancestor_seeds, 'parent_counts': parent_counts,
                  'cumulative_parent_counts': cumulative,
                  'changed_ppo_hyperparameters': changes, 'changed_experiment_settings': setting_changes,
                  'optimizer_transfer': {'kind': 'Adam', 'moments': 'preserved', 'step_counters': 'preserved',
                                         'parent_learning_rates': parent_rates,
                                         'applied_learning_rates': [group['lr'] for group in optimizer.param_groups]},
                  'verified_dependency_sources': checked,
                  **({'environment_transfer': {'changed': transferred, 'changed_sources': source_changes,
                                               'note': 'Weights moved to a new engine build or profile with an identical '
                                                       'observation/action schema; not a continuation of the parent environment.'}}
                     if transfer else {}),
                  'architecture': 'Inherited exactly from parent; --hidden applies only to new models.',
                  'fresh_experiment': 'New root RNG, event indices starting at zero, new step/event counts and validation selection; no parent best score carried over.'}
    # Never restore the parent RNG. Model reconstruction consumes randomness,
    # so explicitly restart the requested stream after transferring its state.
    torch.manual_seed(args.seed)
    np.random.seed(args.seed & 0xffffffff)
    return model, optimizer, provenance


def training(args, device):
    output = Path(args.output)
    if args.resume:
        saved, model = load_checkpoint(args.resume, device)
        if saved['metadata'].get('effect_curriculum') or saved['metadata'].get('effect_exploration'):
            raise ValueError('Effect-curriculum/exploration checkpoints cannot resume their sampling state; start an explicit new fine-tune')
        state = saved['state'].copy()
        config = PPOConfig(**saved['ppo_config'])
        # Deliberately reuse original hyperparameters when resuming.
        validate_resume_settings(args, saved['metadata'])
        for path, fingerprint in saved['metadata']['sources'].items():
            if sha(ROOT / path) != fingerprint:
                raise ValueError(f'Resume training source changed: {path}')
        env = make_env(args, initial_index=state['next_episode_index'])
        obs, masks = env.reset()
        try:
            environment_changes(saved['metadata']['environment_signature'], env.metadata, resume=True)
        except ValueError:
            env.close()
            raise
        optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, eps=1e-5)
        optimizer.load_state_dict(saved['optimizer'])
        torch.set_rng_state(saved['torch_rng'])
        if device.type == 'cuda' and saved['cuda_rng']:
            torch.cuda.set_rng_state_all(saved['cuda_rng'])
        metadata = saved['metadata']
        state['abandoned_on_resume'] = state.get('abandoned_on_resume', 0) + args.n_envs
        state['resume_count'] = state.get('resume_count', 0) + 1
        output.mkdir(parents=True, exist_ok=True)
        manifest = output / 'run.json'
        if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8'))['metadata'] != metadata:
            env.close()
            raise ValueError('Resume destination belongs to another training run')
        if not manifest.exists():
            write_json(manifest, {'schema': 'sfld-ppo-run-v1', 'metadata': metadata,
                                  'resumed_from': str(args.resume), 'resumed_checkpoint_sha256': sha(args.resume),
                                  'ppo_config': asdict(config), 'architecture': model.architecture})
    else:
        if output.exists():
            raise FileExistsError(output)
        config = PPOConfig(args.learning_rate, args.gae_lambda, args.clip, args.value_coefficient,
                           args.entropy_coefficient, args.max_grad_norm, args.target_kl,
                           args.epochs, args.minibatch_size)
        config.validate()
        env = make_env(args, initial_index=0)
        obs, masks = env.reset()
        try:
            finetune = None
            if args.finetune_from:
                model, optimizer, finetune = initialize_finetune(args, env, config, device)
                if (obs.shape[1], masks.shape[1]) != (model.architecture['observation_size'], model.architecture['action_size']):
                    raise ValueError('Fine-tuning checkpoint dimensions differ from environment')
            else:
                model = ActorCritic(obs.shape[1], masks.shape[1], args.hidden).to(device)
                model = with_priority(model, getattr(args, 'stone_priority', 'learned'), env.metadata['schema'])
                model = with_shop(model, env.metadata['schema'], profile_config(args.profiles, getattr(args, 'shop_policy', 'learned')))
                optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, eps=1e-5)
            metadata = source_metadata(args, env)
            if finetune is not None:
                metadata['finetune'] = finetune
            output.mkdir(parents=True, exist_ok=False)
        except Exception:
            env.close()
            raise
        state = {'steps': 0, 'updates': 0, 'bc_steps': 0, 'bc_completed_events': 0, 'next_episode_index': env.next_episode_index,
                 'best_score': None, 'completed_training_events': 0, 'resume_count': 0,
                 'abandoned_on_resume': 0}
        write_json(output / 'run.json', {'schema': 'sfld-ppo-run-v1', 'metadata': metadata,
                                       'ppo_config': asdict(config), 'architecture': model.architecture,
                                       'requested_total_steps': args.total_steps, 'requested_bc_steps': args.bc_steps,
                                       'validation_events': args.validation_events, 'validation_every': args.validate_every,
                                       'evaluation_policy': args.eval_action})
    curve = output / 'curve.jsonl'
    started = time.monotonic()
    previous = np.zeros(args.n_envs, dtype=np.float64)
    curriculum = None
    if getattr(args, 'effect_curriculum', False):
        if args.resume or args.bc_steps:
            raise ValueError('Effect curriculum currently requires a fresh PPO fine-tune')
        from sfld_rl.effect_lab import EffectCurriculum
        cases = json.loads(args.curriculum_cases.read_text(encoding='utf-8')) if args.curriculum_cases else None
        curriculum = EffectCurriculum(env, args.seed, cases=cases)
        obs, masks = curriculum.restore_resets(np.arange(args.n_envs))
    try:
        if args.bc_steps and not args.resume:
            bc_env = make_env(args, namespace='cloning')
            bc_previous = np.zeros(args.n_envs, dtype=np.float64)
            try:
                bc_obs, bc_masks = bc_env.reset()
                while state['bc_steps'] < args.bc_steps:
                    data, legal, labels = [], [], []
                    completed_scores = []
                    length = min(args.rollout_steps, math.ceil((args.bc_steps - state['bc_steps']) / args.n_envs))
                    for _ in range(length):
                        actions = teacher_actions(bc_env, args)
                        data.append(bc_obs.copy()); legal.append(bc_masks.copy()); labels.append(actions.copy())
                        bc_obs, bc_masks, reward, done, trunc, info = bc_env.step(actions)
                        transition_guard(reward, done, trunc, info, bc_previous)
                        bc_previous[:] = info['completions']
                        ended = np.flatnonzero(done)
                        if len(ended):
                            completed_scores.extend(float(info['completions'][i]) for i in ended)
                            state['bc_completed_events'] += len(ended)
                            bc_obs, bc_masks = bc_env.reset_done(ended)
                            bc_previous[ended] = 0
                        state['bc_steps'] += args.n_envs
                    loss = cloning_update(model, optimizer, np.concatenate(data), np.concatenate(legal),
                                          np.concatenate(labels), device, args.minibatch_size)
                    row = {'phase': 'cloning', 'steps': state['bc_steps'], 'loss': loss,
                           'completed_events': len(completed_scores),
                           'total_completed_events': state['bc_completed_events'],
                           'mean_completed_event_runs': statistics.mean(completed_scores) if completed_scores else None,
                           'wall_seconds': time.monotonic() - started}
                    with curve.open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(row, allow_nan=False) + '\n')
                    print(json.dumps(row, allow_nan=False), flush=True)
            finally:
                bc_env.close()

        if not args.resume:
            # Keep the cloned/from-scratch initializer eligible: PPO is allowed
            # to deviate, but a later regression must not erase a good teacher.
            validation = evaluate_model(model, args, device, namespace='validation', events=args.validation_events)
            state['best_score'] = validation['selection_score']
            path = output / 'checkpoint-000000000000.pt'
            checkpoint(path, model, optimizer, state.copy(), metadata, config)
            pointer = {'checkpoint': path.name, 'sha256': sha(path), 'steps': 0,
                       'selection_score': validation['selection_score']}
            write_json(output / 'latest.json', pointer)
            if validation['selection_score'] is not None:
                write_json(output / 'best.json', {**pointer, 'validation': validation['summaries']})
            row = {'phase': 'initial_validation', 'steps': 0, 'bc_steps': state['bc_steps'],
                   'validation_score': validation['selection_score'], 'validation_summaries': validation['summaries']}
            with curve.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, allow_nan=False) + '\n')
            print(json.dumps(row, allow_nan=False), flush=True)

        while state['steps'] < args.total_steps:
            length = min(args.rollout_steps, math.ceil((args.total_steps - state['steps']) / args.n_envs))
            shape = (length, args.n_envs)
            observations = np.empty((*shape, obs.shape[1]), np.float32)
            legal = np.empty((*shape, masks.shape[1]), bool)
            actions_all = np.empty(shape, np.int64)
            log_probs = np.empty(shape, np.float32)
            values, next_values, rewards = [np.empty(shape, np.float32) for _ in range(3)]
            terminated, truncated = [np.empty(shape, bool) for _ in range(2)]
            episode_scores = []
            model.eval()
            for t in range(length):
                observations[t] = obs; legal[t] = masks
                with torch.no_grad():
                    actions, logs, value = policy_actions(model, obs, masks, device)
                actions_all[t] = actions.cpu().numpy(); log_probs[t] = logs.cpu().numpy(); values[t] = value.cpu().numpy()
                obs, masks, reward, done, trunc, info = env.step(actions_all[t])
                transition_guard(reward, done, trunc, info, previous)
                previous[:] = info['completions']
                rewards[t] = reward; terminated[t] = done; truncated[t] = trunc
                ended = np.flatnonzero(done)
                if len(ended):
                    episode_scores.extend(float(info['completions'][i]) for i in ended)
                    state['completed_training_events'] += len(ended)
                    if curriculum is not None:
                        curriculum.completed(ended)
                    obs, masks = env.reset_done(ended)
                    if curriculum is not None:
                        obs, masks = curriculum.restore_resets(ended)
                    previous[ended] = 0
                state['steps'] += args.n_envs
            next_values[:-1] = values[1:]
            with torch.no_grad():
                next_values[-1] = model.values(tensor(obs, device)).cpu().numpy()
            # Values after resets are ignored by GAE at true terminal steps.
            adv, returns = advantages(rewards, values, next_values, terminated, truncated, config.gae_lambda)
            batch = {'observations': observations.reshape(-1, obs.shape[1]), 'masks': legal.reshape(-1, masks.shape[1]),
                     'actions': actions_all.reshape(-1), 'log_probs': log_probs.reshape(-1),
                     'advantages': adv.reshape(-1), 'returns': returns.reshape(-1)}
            model.train()
            losses = ppo_update(model, optimizer, batch, config, device)
            state['updates'] += 1
            state['next_episode_index'] = env.next_episode_index
            if curriculum is not None:
                state.update(curriculum.counts())
                state['completed_training_events_includes_partial_scenarios'] = True
            row = {'phase': 'ppo', 'update': state['updates'], 'steps': state['steps'], **losses,
                   'completed_events': len(episode_scores),
                   'mean_completed_event_runs': statistics.mean(episode_scores) if episode_scores else None,
                   'rollout_completions': float(rewards.sum()), 'wall_seconds': time.monotonic() - started}
            last = state['steps'] >= args.total_steps
            validation = None
            improved = False
            if state['updates'] % args.validate_every == 0 or last:
                validation = evaluate_model(model, args, device, namespace='validation', events=args.validation_events)
                row['validation_score'] = validation['selection_score']
                row['validation_summaries'] = validation['summaries']
                improved = validation['selection_score'] is not None and (
                    state['best_score'] is None or validation['selection_score'] > state['best_score'])
                if improved:
                    state['best_score'] = validation['selection_score']
            if validation is not None or state['updates'] % args.checkpoint_every == 0 or last:
                path = output / f'checkpoint-{state["steps"]:012d}.pt'
                checkpoint(path, model, optimizer, state.copy(), metadata, config)
                pointer = {'checkpoint': path.name, 'sha256': sha(path), 'steps': state['steps'],
                           'selection_score': validation['selection_score'] if validation else None}
                write_json(output / 'latest.json', pointer)
                if improved:
                    write_json(output / 'best.json', {**pointer, 'validation': validation['summaries']})
            with curve.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, allow_nan=False) + '\n')
            print(json.dumps(row, allow_nan=False), flush=True)
        write_json(output / 'training-summary.json', {'state': state, 'wall_seconds_this_invocation': time.monotonic() - started,
                                                     'final_evaluation_performed': False})
    except Exception as error:
        write_json(output / f'failure-{state["steps"]:012d}.json', {'type': type(error).__name__, 'message': str(error),
                                                                  'state': state, 'invalid_or_truncated_events_are_not_terminals': True})
        raise
    finally:
        if curriculum is not None:
            curriculum.close()
        env.close()


def evaluation(args, device):
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f'Evaluation report exists: {output}')
    saved, model = (None, None) if args.teacher else load_checkpoint(args.checkpoint, device)
    changes = {}
    if saved is not None:
        env = make_env(args, n_envs=1, namespace='final')
        try:
            obs, masks = env.reset()
            if obs.shape[1] != model.architecture['observation_size'] or masks.shape[1] != model.architecture['action_size']:
                raise ValueError('Checkpoint observation/action dimensions do not match environment')
            changes = environment_changes(saved['metadata']['environment_signature'], env.metadata)
        finally:
            env.close()
    result = evaluate_model(model, args, device, namespace='final', events=args.events, teacher=args.teacher)
    result.update(schema='sfld-ppo-evaluation-v1', checkpoint=str(args.checkpoint) if args.checkpoint else None,
                  checkpoint_sha256=sha(args.checkpoint) if args.checkpoint else None,
                  checkpoint_training_metadata=saved['metadata'] if saved else None,
                  changes_from_training_environment=changes,
                  environment_compatibility='Same schema and native library required; alternate profiles/horizon/safety cap reported explicitly.',
                  torch=str(torch.__version__), device=str(device), n_envs=args.n_envs,
                  teacher_rules={'free': sha(args.teacher_free_rules), 'paid': sha(args.teacher_paid_rules)},
                  warning='Final results must not be used to tune this checkpoint; sampling intervals exclude model uncertainty.')
    write_json(output, result)
    print(json.dumps({'output': str(output), 'summaries': result['summaries']}, allow_nan=False), flush=True)
    if any(s['failed_events'] for s in result['summaries']):
        raise RuntimeError('Evaluation recorded invalid/truncated events; completion estimates were withheld')


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['train', 'evaluate'], required=True)
    p.add_argument('--library', type=Path, default=Path('build/Release/sfld_rl.dll'))
    p.add_argument('--profiles', '--profile', type=Path, nargs='+', default=[Path('profiles/synthetic.profile')])
    p.add_argument('--output', type=Path, required=True, help='New training directory or new evaluation JSON')
    p.add_argument('--budgets', default='0', help='Comma-separated declared event budgets')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
    p.add_argument('--torch-threads', type=int, default=2)
    p.add_argument('--n-envs', type=int, default=128)
    p.add_argument('--deadline-hours', type=float, default=168)
    p.add_argument('--max-actions', type=int, default=100000)
    p.add_argument('--total-steps', type=int, default=1000000)
    p.add_argument('--rollout-steps', type=int, default=128)
    p.add_argument('--minibatch-size', type=int, default=1024)
    p.add_argument('--epochs', type=int, default=4)
    p.add_argument('--hidden', type=int, nargs='+', default=[256, 256])
    p.add_argument('--learning-rate', type=float, default=3e-4)
    p.add_argument('--gae-lambda', type=float, default=.95)
    p.add_argument('--clip', type=float, default=.2)
    p.add_argument('--value-coefficient', type=float, default=.5)
    p.add_argument('--entropy-coefficient', type=float, default=.01)
    p.add_argument('--stone-priority', choices=STONE_PRIORITIES, default='learned',
                   help='Training-time stone constraint; saved checkpoints retain it during inference')
    p.add_argument('--shop-policy', choices=SHOP_POLICIES, default='learned',
                   help='Learn with the five-refresh shop policy active in sampling and PPO updates')
    p.add_argument('--max-grad-norm', type=float, default=.5)
    p.add_argument('--target-kl', type=float, default=.03)
    p.add_argument('--bc-steps', type=int, default=0, help='Optional teacher transitions; zero means from scratch')
    p.add_argument('--effect-curriculum', action='store_true',
                   help='Start 25%% of training episodes at effect-slot challenges; validation stays fresh full weeks')
    p.add_argument('--curriculum-cases', type=Path,
                   help='Optional declared JSON progress states for the 25%% curriculum; no action labels')
    p.add_argument('--teacher-free-rules', type=Path, default=Path('docs/rule-strategy-2026-10-09/rules/score-free.rules'))
    p.add_argument('--teacher-paid-rules', type=Path, default=Path('docs/rule-strategy-2026-10-09/rules/score-paid.rules'))
    p.add_argument('--validation-events', type=int, default=128)
    p.add_argument('--validate-every', type=int, default=10, help='PPO updates between fixed-cohort validations')
    p.add_argument('--checkpoint-every', type=int, default=10)
    starting = p.add_mutually_exclusive_group()
    starting.add_argument('--resume', type=Path, help='Continue the same frozen experiment and optimizer settings')
    starting.add_argument('--finetune-from', type=Path, help='New experiment from saved weights/Adam moments; requested PPO settings apply')
    p.add_argument('--transfer-environment', action='store_true',
                   help='With --finetune-from: allow a new engine build, library or profile when the observation and '
                        'action schema is identical (engine version aside); the changes are recorded')
    p.add_argument('--checkpoint', type=Path)
    p.add_argument('--events', type=int, default=10000)
    p.add_argument('--eval-action', choices=['greedy', 'sample'], default='greedy')
    p.add_argument('--teacher', action='store_true', help='Evaluate matched old teacher on the same final event seeds')
    args = p.parse_args(argv)
    try:
        args.budgets = [int(x) for x in args.budgets.split(',')]
    except ValueError:
        p.error('Budgets must be comma-separated integers')
    if not args.budgets or len(set(args.budgets)) != len(args.budgets) or min(args.budgets) < 0 or max(args.budgets) > 1000000:
        p.error('Budgets must be distinct integers 0..1000000')
    positive = [args.n_envs, args.total_steps, args.rollout_steps, args.epochs, args.validate_every,
                args.checkpoint_every, args.max_actions, *args.hidden]
    if min(positive) < 1 or not 1 <= args.torch_threads <= 32 or args.n_envs > 4096:
        p.error('Invalid worker, network or training dimensions')
    if args.bc_steps < 0 or min(args.events, args.validation_events, args.minibatch_size) < 2:
        p.error('Need at least two evaluation events/minibatch items and nonnegative BC steps')
    if args.effect_curriculum and (args.resume or args.bc_steps):
        p.error('Effect curriculum needs a fresh experiment without behavior cloning')
    if args.curriculum_cases and (not args.effect_curriculum or not args.curriculum_cases.is_file()):
        p.error('--curriculum-cases requires --effect-curriculum and an existing JSON file')
    if not 0 <= args.seed < (1 << 64) or not math.isfinite(args.deadline_hours) or args.deadline_hours <= 0:
        p.error('Invalid root seed or deadline')
    if args.mode == 'evaluate' and bool(args.teacher) == bool(args.checkpoint):
        p.error('Evaluation requires exactly one of --checkpoint or --teacher')
    if args.mode == 'train' and (args.checkpoint or args.teacher):
        p.error('--checkpoint and --teacher are evaluation options; use --resume or --finetune-from for training')
    if args.mode == 'evaluate' and (args.resume or args.finetune_from):
        p.error('--resume and --finetune-from are training options')
    if args.transfer_environment and not args.finetune_from:
        p.error('--transfer-environment requires --finetune-from')
    if args.finetune_from and args.bc_steps:
        p.error('--finetune-from cannot be combined with teacher cloning (--bc-steps)')
    if args.shop_policy != 'learned' and (args.stone_priority != 'learned' or args.bc_steps):
        p.error('--shop-policy requires learned stones and no teacher cloning')
    return args


def main(argv=None):
    args = parse(argv)
    torch.set_num_threads(args.torch_threads)
    device = torch.device('cuda' if args.device == 'auto' and torch.cuda.is_available() else 'cpu' if args.device == 'auto' else args.device)
    if device.type == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA was requested but is unavailable')
    args.device = str(device)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed & 0xffffffff)
    (training if args.mode == 'train' else evaluation)(args, device)


if __name__ == '__main__':
    main()
