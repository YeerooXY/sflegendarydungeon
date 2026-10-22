"""Repeatable simulator/policy timings; performance results are not strategy evidence.

Make a frozen legal action trace before changing the code, then replay that same
trace against each library. Timings exclude hashing and setup. Outputs include
hashes so a faster replay with different outcomes cannot pass as an improvement.
"""
from __future__ import annotations
import argparse
import cProfile
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import pstats
import statistics
import time
from types import SimpleNamespace

import numpy as np
import torch
import train_rl
from sfld_rl.env import VectorEnv

ROOT = Path(__file__).resolve().parents[1]
SEED = 12231001
BUDGETS = (0, 20, 50, 100, 500, 1000)


def environment(args, count):
    return VectorEnv(args.library, [args.profile], count, budgets=BUDGETS,
                     seed=SEED, seed_namespace=246, auto_reset=True)


def make_trace(args):
    if args.trace.exists():
        raise FileExistsError(args.trace)
    _, model = train_rl.load_checkpoint(args.checkpoint, torch.device('cpu'))
    model.eval()
    actions, observations, masks = [], [], []
    profiler = cProfile.Profile()
    with environment(args, args.n_envs) as env, torch.inference_mode():
        obs, mask = env.reset()
        profiler.enable()
        for step in range(args.steps):
            if step % max(1, args.steps // 16) == 0:
                observations.append(obs.copy()); masks.append(mask.copy())
            a = model.actions(torch.from_numpy(obs), torch.from_numpy(mask), greedy=True)[0].numpy()
            actions.append(a.copy())
            obs, mask, _, _, truncated, _ = env.step(a)
            if truncated.any(): raise RuntimeError('Unexpected safety truncation')
        profiler.disable()
    args.trace.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.trace, actions=np.stack(actions), observations=np.stack(observations),
                        masks=np.stack(masks), profile_sha256=train_rl.sha(args.profile),
                        checkpoint_sha256=train_rl.sha(args.checkpoint), seed=SEED)
    with args.trace.with_suffix('.profile.txt').open('w', encoding='utf-8') as out:
        pstats.Stats(profiler, stream=out).sort_stats('cumtime').print_stats(45)
    print(json.dumps({'trace': str(args.trace), 'decisions': len(actions) * args.n_envs}), flush=True)


def native(args, trace):
    acts = trace['actions']; times = []; digests = []
    for rep in range(args.repeats):
        digest = hashlib.sha256(); elapsed = 0.
        with environment(args, acts.shape[1]) as env:
            env.reset()
            for a in acts:
                started = time.perf_counter(); result = env.step(a)
                elapsed += time.perf_counter() - started
                for array in result[:5]: digest.update(array.tobytes())
                for key in sorted(result[5]): digest.update(result[5][key].tobytes())
            digest.update(env.reward_totals()['gold_units'].tobytes())
        times.append(elapsed); digests.append(digest.hexdigest())
    if len(set(digests)) != 1: raise RuntimeError('Nondeterministic replay')
    return {'seconds': times, 'median_seconds': statistics.median(times),
            'decisions': acts.size, 'decisions_per_second': acts.size / statistics.median(times),
            'all_outputs_sha256': digests[0]}


def policy(args, trace, device):
    _, model = train_rl.load_checkpoint(args.checkpoint, torch.device(device)); model.eval()
    if getattr(args, 'reference_shop', None):
        model = reference_model(model, args.reference_shop)
    obs = [torch.tensor(x, device=device) for x in trace['observations']]
    masks = [torch.tensor(x, device=device) for x in trace['masks']]
    sync = (lambda: torch.cuda.synchronize()) if device == 'cuda' else (lambda: None)
    functions = {'network': lambda i: model(obs[i]),
                 'shop_masks': lambda i: model.constrained_masks(obs[i], masks[i]),
                 'greedy_actions': lambda i: model.actions(obs[i], masks[i], greedy=True)}
    result = {}
    with torch.inference_mode():
        for label, fn in functions.items():
            for i in range(16): fn(i % len(obs))
            times = []
            for _ in range(args.repeats):
                sync(); start = time.perf_counter()
                for i in range(args.policy_steps): fn(i % len(obs))
                sync(); times.append(time.perf_counter() - start)
            result[label] = {'seconds': times, 'median_ms_per_batch': statistics.median(times)*1000/args.policy_steps}
        digest = hashlib.sha256()
        for o, m in zip(obs, masks):
            a, logp, value = model.actions(o, m, greedy=True)
            digest.update(a.cpu().numpy().tobytes())
            digest.update(model.constrained_masks(o, m).cpu().numpy().tobytes())
        result['action_and_mask_sha256'] = digest.hexdigest()
    return result


def reference_model(model, path):
    spec = importlib.util.spec_from_file_location('sfld_rl._runtime_reference_shop', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    schema = {'observation_names': list(model.oi), 'action_names': list(model.ai)}
    return module.ShopPolicyActor(model, schema, model.shop_config).eval()


def closed_loop(args, trace, device):
    _, model = train_rl.load_checkpoint(args.checkpoint, torch.device(device)); model.eval()
    if getattr(args, 'reference_shop', None): model = reference_model(model, args.reference_shop)
    digest = hashlib.sha256(); elapsed = 0.
    with environment(args, trace['actions'].shape[1]) as env, torch.inference_mode():
        obs, masks = env.reset()
        for _ in range(16):
            model.actions(torch.as_tensor(obs, device=device), torch.as_tensor(masks, device=device), greedy=True)
        if device == 'cuda': torch.cuda.synchronize()
        for _ in trace['actions']:
            started = time.perf_counter()
            actions = model.actions(torch.as_tensor(obs, device=device), torch.as_tensor(masks, device=device), greedy=True)[0].cpu().numpy()
            result = env.step(actions)
            elapsed += time.perf_counter() - started
            digest.update(actions.tobytes())
            for array in result[:5]: digest.update(array.tobytes())
            for key in sorted(result[5]): digest.update(result[5][key].tobytes())
            obs, masks = result[:2]
    return {'seconds': elapsed, 'all_outputs_sha256': digest.hexdigest()}


def comparison(args, trace):
    if not args.reference_shop: raise ValueError('A frozen shop implementation is required for comparison')
    settings = {
        'before': SimpleNamespace(**{**vars(args), 'library': args.compare_library, 'repeats': 1}),
        'after': SimpleNamespace(**{**vars(args), 'reference_shop': None, 'repeats': 1})}
    devices = ['cpu'] + (['cuda'] if torch.cuda.is_available() else [])
    output = {label: {'native': [], 'policy': {d: [] for d in devices},
                      'closed_loop': {d: [] for d in devices}} for label in settings}
    # Reverse execution order on alternate repetitions to limit clock/load bias.
    for rep in range(args.repeats):
        for label in (('before', 'after') if rep % 2 == 0 else ('after', 'before')):
            s = settings[label]; row = output[label]
            row['native'].append(native(s, trace))
            for d in devices:
                row['policy'][d].append(policy(s, trace, d))
                row['closed_loop'][d].append(closed_loop(s, trace, d))
            print(json.dumps({'repetition': rep+1, 'variant': label, 'closed_loop': {
                d: row['closed_loop'][d][-1]['seconds'] for d in devices}}), flush=True)
    for kind in ('native', 'closed_loop', 'policy'):
        for d in ([None] if kind == 'native' else devices):
            hashes = []
            for label in settings:
                rows = output[label][kind] if d is None else output[label][kind][d]
                hashes += [r['action_and_mask_sha256' if kind == 'policy' else 'all_outputs_sha256'] for r in rows]
            if len(set(hashes)) != 1: raise RuntimeError(f'Changed outputs: {kind} {d}')
    speedups = {}
    for d in devices:
        old = statistics.median(r['seconds'] for r in output['before']['closed_loop'][d])
        new = statistics.median(r['seconds'] for r in output['after']['closed_loop'][d])
        speedups[d] = {'before_seconds': old, 'after_seconds': new, 'speedup': old/new,
                       'decisions_per_second': trace['actions'].size/new}
    return {'variants': output, 'closed_loop_speedup': speedups, 'identical_outputs': True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--profile', type=Path, default=ROOT/'profiles/synthetic.profile')
    p.add_argument('--checkpoint', type=Path, default=ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt')
    p.add_argument('--trace', type=Path, required=True)
    p.add_argument('--make-trace', action='store_true')
    p.add_argument('--output', type=Path)
    p.add_argument('--n-envs', type=int, default=512)
    p.add_argument('--steps', type=int, default=1024)
    p.add_argument('--policy-steps', type=int, default=128)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--skip-policy', action='store_true')
    p.add_argument('--compare-library', type=Path)
    p.add_argument('--reference-shop', type=Path)
    args = p.parse_args(); torch.set_num_threads(2)
    if args.make_trace: make_trace(args)
    if not args.output: return
    if args.output.exists(): raise FileExistsError(args.output)
    trace = np.load(args.trace)
    if str(trace['profile_sha256']) != train_rl.sha(args.profile): raise ValueError('Trace profile changed')
    if str(trace['checkpoint_sha256']) != train_rl.sha(args.checkpoint): raise ValueError('Trace checkpoint changed')
    result = {'platform': platform.platform(), 'processor': platform.processor(),
              'torch': torch.__version__, 'threads': torch.get_num_threads(),
              'library_sha256': train_rl.sha(args.library), 'trace_sha256': train_rl.sha(args.trace),
              'profile_sha256': train_rl.sha(args.profile), 'checkpoint_sha256': train_rl.sha(args.checkpoint),
              'sources': {n: train_rl.sha(ROOT/n) for n in ('src/rl_env.cpp', 'tools/sfld_rl/env.py',
                          'tools/sfld_rl/ppo.py', 'tools/sfld_rl/shop_policy.py', 'tools/benchmark_runtime.py')},
              'decisions_per_replay': int(trace['actions'].size)}
    if args.compare_library:
        result['baseline_library_sha256'] = train_rl.sha(args.compare_library)
        result['reference_shop_sha256'] = train_rl.sha(args.reference_shop)
        result['comparison'] = comparison(args, trace)
    else:
        result['native'] = native(args, trace)
    if not args.skip_policy and not args.compare_library:
        result['policy'] = {d: policy(args, trace, d) for d in ('cpu', 'cuda') if d == 'cpu' or torch.cuda.is_available()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__': main()
