"""Explicit, lossless input expansion for the offered-stone history schema.

This is a transfer to the corrected engine, not a resume of the old experiment.
Ordinary train_rl compatibility checks remain strict. Original provenance is
retained, and neither simulated decisions nor training are claimed here.
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import torch

import train_rl
from sfld_rl.env import VectorEnv


def check_schema(old, new):
    prefix = old['observation_names']
    expected = ['offered_gem.' + n.removeprefix('owned_gem.')
                for n in prefix if n.startswith('owned_gem.')]
    if len(prefix) != 437 or len(expected) != 23 or new['observation_names'] != prefix + expected:
        raise ValueError('Migration requires the exact original prefix plus 23 offered-gem features')
    for key in set(old) | set(new):
        if key not in ('engine', 'observation_names') and old.get(key) != new.get(key):
            raise ValueError(f'Migration changes an unrelated schema field: {key}')
    return expected


def expand_checkpoint(saved, new_schema):
    """Preserve every learned coefficient/moment; zero only the new columns."""
    old_schema = saved['metadata']['environment_signature']['schema']
    extra = check_schema(old_schema, new_schema)
    result = copy.deepcopy(saved)
    old_dim = len(old_schema['observation_names'])
    new_dim = len(new_schema['observation_names'])
    if saved['architecture']['observation_size'] != old_dim:
        raise ValueError('Parent network does not match its observation schema')
    result['architecture']['observation_size'] = new_dim
    key = 'trunk.0.weight'
    weight = saved['model'][key]
    if weight.ndim != 2 or weight.shape[1] != old_dim:
        raise ValueError('Unexpected first-layer shape')
    def padded(value):
        return torch.cat((value.clone(), value.new_zeros((value.shape[0], len(extra)))), dim=1)
    result['model'][key] = padded(weight)
    names = list(saved['model'])
    ids = [i for group in saved['optimizer']['param_groups'] for i in group['params']]
    if len(names) != len(ids) or len(set(ids)) != len(ids):
        raise ValueError('Optimizer parameters cannot be mapped unambiguously')
    moments = result['optimizer']['state'].get(ids[names.index(key)], {})
    for field in ('exp_avg', 'exp_avg_sq', 'max_exp_avg_sq'):
        if field in moments:
            if moments[field].shape != weight.shape:
                raise ValueError('Unexpected Adam first-layer moment shape')
            moments[field] = padded(moments[field])
    return result


def migrate(parent, output, args):
    parent, output = Path(parent), Path(output)
    checksum = train_rl.sha(parent)
    original, _ = train_rl.load_checkpoint(parent, torch.device('cpu'))
    env = train_rl.make_env(args, n_envs=1)
    try:
        transferred = expand_checkpoint(original, env.schema)
        metadata = train_rl.source_metadata(args, env)
    finally:
        env.close()
    # Keep the parent's counts and ancestor list exactly once. The next actual
    # fine-tune accumulates these counts through the existing lineage logic.
    metadata['seed'] = original['metadata']['seed']
    if 'finetune' in original['metadata']:
        metadata['finetune'] = copy.deepcopy(original['metadata']['finetune'])
    changes = {key: {'parent': original['metadata']['sources'].get(key), 'current': value}
               for key, value in metadata['sources'].items()
               if original['metadata']['sources'].get(key) != value}
    metadata['migration'] = {
        'schema': 'sfld-offered-history-transfer-v1',
        'parent_checkpoint': str(parent.resolve()), 'parent_sha256': checksum,
        'original_metadata': original['metadata'],
        'script_sha256': train_rl.sha(__file__), 'dependency_changes': changes,
        'old_observations': 437, 'new_observations': 460,
        'mechanics': 'Transfer from held-only training to all-offered exclusion; rules frozen for subsequent training and evaluation.',
        'weights': 'Original coefficients unchanged; 23 new input columns zero.',
        'optimizer': 'Adam old moments and scalar steps unchanged; new columns have zero moments.',
        'new_training_decisions': 0,
    }
    transferred['metadata'] = metadata
    transferred['state']['migration_only'] = True
    if output.exists():
        raise FileExistsError(output)
    if train_rl.sha(parent) != checksum:
        raise RuntimeError('Parent changed during migration')
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(transferred, output)
    train_rl.write_json(output.with_suffix('.migration.json'), metadata['migration'])
    return train_rl.sha(output)


def verify_live_identity(parent, transferred, old_library, new_library, profile,
                         seed=9700001, events=64, budgets=(0, 20, 50, 100, 500)):
    """Match the corrected 437-input engine to its history-observing successor."""
    _, old_model = train_rl.load_checkpoint(parent, torch.device('cpu'))
    _, new_model = train_rl.load_checkpoint(transferred, torch.device('cpu'))
    comparisons, summaries = 0, []
    for budget in budgets:
        args = dict(profiles=[profile], n_envs=events, budgets=[budget], seed=seed,
                    seed_namespace=0xD9, auto_reset=False)
        old_env = VectorEnv(old_library, **args)
        new_env = VectorEnv(new_library, **args)
        finished = set()
        try:
            check_schema(old_env.schema, new_env.schema)
            a, ma = old_env.reset(); b, mb = new_env.reset()
            while len(finished) < events:
                np.testing.assert_array_equal(a, b[:, :437])
                np.testing.assert_array_equal(ma, mb)
                with torch.no_grad():
                    logits_a, values_a = old_model(torch.from_numpy(a))
                    logits_b, values_b = new_model(torch.from_numpy(b))
                    torch.testing.assert_close(logits_a, logits_b, atol=2e-5, rtol=2e-5)
                    torch.testing.assert_close(values_a, values_b, atol=2e-5, rtol=2e-5)
                    ca = old_model.actions(torch.from_numpy(a), torch.from_numpy(ma), greedy=True)[0].numpy()
                    cb = new_model.actions(torch.from_numpy(b), torch.from_numpy(mb), greedy=True)[0].numpy()
                np.testing.assert_array_equal(ca, cb)
                a, ma, ra, da, ta, ia = old_env.step(ca)
                b, mb, rb, db, tb, ib = new_env.step(cb)
                for left, right in ((ra, rb), (da, db), (ta, tb)):
                    np.testing.assert_array_equal(left, right)
                if np.any(ta):
                    raise RuntimeError('Identity test hit a safety limit')
                for key in ia:
                    if isinstance(ia[key], np.ndarray):
                        # Terminal observations have the same added history
                        # columns as ordinary observations; compare the prefix.
                        right = ib[key][:, :437] if ia[key].shape == (events, 437) else ib[key]
                        np.testing.assert_array_equal(ia[key], right)
                ended = np.flatnonzero(da)
                for slot in ended:
                    event = int(ia['episode_index'][slot])
                    if event < events:
                        finished.add(event)
                comparisons += events
                if len(ended):
                    a, ma = old_env.reset_done(ended); b, mb = new_env.reset_done(ended)
            summaries.append({'budget': budget, 'matched_events': len(finished)})
        finally:
            old_env.close(); new_env.close()
    return {'schema': 'sfld-history-transfer-identity-v1', 'passed': True,
            'seed': seed, 'namespace': 0xD9, 'summaries': summaries,
            'matched_decision_rows_including_unused_tails': comparisons,
            'parent_sha256': train_rl.sha(parent), 'transferred_sha256': train_rl.sha(transferred),
            'old_library_sha256': train_rl.sha(old_library), 'new_library_sha256': train_rl.sha(new_library),
            'profile_sha256': train_rl.sha(profile)}
