"""Frozen offered-history PPO study: three branches, continuation, fresh test.

The baseline remains eligible. Global and budget-specific winners are selected
only on validation; final comparisons are never used to change the selection.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

import torch

import train_rl
from finetune_rl_study import read, once, copy_checked, validate_evaluation
from migrate_rl_history import migrate, verify_live_identity

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = [0, 20, 50, 100, 500]
OLD_SEEDS = [9010001, 9011001, 9011002, 9011003, 9100001, 9100002,
    9200001, 9200002, 9200003, 9301001, 9301002, 9301003, 9301004,
    9302001, 9302002, 9303001, 9401001, 9500001, 9501001, 9501002,
    9502001, 9601001]


def frozen_args(out):
    return ['--library', str(out / 'sfld_rl.dll'), '--profiles', str(out / 'synthetic.profile'),
            '--budgets', ','.join(map(str, BUDGETS)), '--n-envs', '256',
            '--device', 'cuda', '--torch-threads', '2']


def prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    parent = ROOT / 'docs/rl-finetune-2026-10-10/selected-checkpoint.pt'
    old_library = ROOT / 'docs/stone-exclusion-2026-10-10/sfld_rl.dll'
    for source, target in [(parent, 'original-checkpoint.pt'),
                           (ROOT / 'build/Release/sfld_rl.dll', 'sfld_rl.dll'),
                           (ROOT / 'profiles/synthetic.profile', 'synthetic.profile')]:
        copy_checked(source, out / target)
    sources = [ROOT / 'tools/train_rl.py', ROOT / 'tools/sfld_rl/env.py', ROOT / 'tools/sfld_rl/ppo.py',
               Path(__file__), ROOT / 'tools/migrate_rl_history.py', ROOT / 'tools/finetune_rl_study.py',
               ROOT / 'CMakeLists.txt']
    sources += sorted(p for d in ('src', 'include') for p in (ROOT / d).rglob('*')
                      if p.is_file() and p.suffix in ('.cpp', '.hpp', '.h', '.c'))
    hashes = {str(p.relative_to(ROOT).as_posix()): train_rl.sha(p) for p in sources}
    once(out / 'source-manifest.json', hashes)
    for p in sources:
        copy_checked(p, out / 'source' / p.relative_to(ROOT))
    variants = [
        dict(name='steady', seed=9701001, steps=16777216, learning_rate=1e-4,
             entropy_coefficient=.003, gae_lambda=.95, rollout_steps=128, target_kl=.015),
        dict(name='long-credit', seed=9701002, steps=16777216, learning_rate=7.5e-5,
             entropy_coefficient=.003, gae_lambda=.99, rollout_steps=256, target_kl=.02),
        dict(name='exploration', seed=9701003, steps=16777216, learning_rate=1.5e-4,
             entropy_coefficient=.01, gae_lambda=.98, rollout_steps=256, target_kl=.025),
    ]
    plan = dict(schema='sfld-history-training-plan-v1', budgets=BUDGETS,
        original_checkpoint_sha256=train_rl.sha(parent), library_sha256=train_rl.sha(out / 'sfld_rl.dll'),
        profile_sha256=train_rl.sha(out / 'synthetic.profile'),
        identity=dict(seed=9700001, events=64, namespace=0xD9),
        variants=variants, extension=dict(seed=9701004, steps=33554432),
        common_validation=dict(seed=9702001, events=768),
        selection_validation=dict(seed=9702002, events=1536),
        final=dict(seed=9703001, events=5000), diagnostic_seed=9704001,
        training_validation_events=256, n_envs=256, max_actions=100000,
        selection_rule='Global: equal mean weekly completions over five caps. Per-cap: highest validation completions at that cap. Exact ties keep earlier candidate, baseline first.',
        extension_rule='Continue common-validation global winner with its settings, or steady settings if baseline wins.',
        final_rule='Freeze global and per-cap selections before final evaluation. No reselection on final results.',
        objective='Maximum completed runs in 168 hours, gamma one, unshaped completion-only reward; random stones, all-offered exclusion, visible offered history.')
    seeds = OLD_SEEDS + [v['seed'] for v in variants] + [plan[k]['seed'] for k in
        ('identity', 'extension', 'common_validation', 'selection_validation', 'final')] + [plan['diagnostic_seed']]
    if len(set(map(train_rl.seed_fingerprint, seeds))) != len(seeds):
        raise RuntimeError('Compressed root-seed fingerprints overlap')
    migrated = out / 'parent-checkpoint.pt'
    if not migrated.exists():
        args = train_rl.parse(['--mode', 'train', '--output', str(out / 'unused'), *frozen_args(out)])
        migrate(parent, migrated, args)
    plan['parent_checkpoint_sha256'] = train_rl.sha(migrated)
    once(out / 'study-plan.json', plan)
    identity = out / 'migration-identity.json'
    if not identity.exists():
        print(json.dumps({'checking': 'migration identity, 64 events per cap'}), flush=True)
        once(identity, verify_live_identity(parent, migrated, old_library, out / 'sfld_rl.dll',
             out / 'synthetic.profile', seed=plan['identity']['seed'], events=plan['identity']['events']))
    verified = read(identity)
    if not verified['passed'] or verified['transferred_sha256'] != plan['parent_checkpoint_sha256']:
        raise RuntimeError('Transfer identity is not verified')
    return plan


def train_job(out, plan, job, parent):
    name = job['name']
    destination = out / name
    command = [sys.executable, 'tools/train_rl.py', '--mode', 'train', '--finetune-from', str(parent),
        *frozen_args(out), '--rollout-steps', str(job['rollout_steps']), '--total-steps', str(job['steps']),
        '--validation-events', str(plan['training_validation_events']),
        '--validate-every', str(8192 // job['rollout_steps']),
        '--checkpoint-every', str(8192 // job['rollout_steps']),
        '--seed', str(job['seed']), '--learning-rate', str(job['learning_rate']),
        '--entropy-coefficient', str(job['entropy_coefficient']), '--gae-lambda', str(job['gae_lambda']),
        '--target-kl', str(job['target_kl']), '--output', str(destination)]
    once(out / f'{name}-invocation.json', dict(job=job, command=command,
        parent_checkpoint_sha256=train_rl.sha(parent), trainer_sha256=train_rl.sha(ROOT / 'tools/train_rl.py')))
    status = out / f'{name}-status.json'
    if not status.exists():
        print(json.dumps({'starting_training': name, 'new_decisions': job['steps']}), flush=True)
        started = time.monotonic()
        with (out / f'{name}.log').open('x', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        once(status, dict(exit_code=result.returncode, wall_seconds=time.monotonic() - started))
    if read(status)['exit_code']:
        raise RuntimeError(f'Training failed; see {name}.log')
    summary = read(destination / 'training-summary.json')
    if summary['state']['steps'] != job['steps']:
        raise RuntimeError('Wrong training decision count')
    best = read(destination / 'best.json')
    path = destination / best['checkpoint']
    if path.parent.resolve() != destination.resolve() or train_rl.sha(path) != best['sha256']:
        raise RuntimeError('Invalid checkpoint pointer')
    print(json.dumps({'finished_training': name, 'best_steps': best['steps'],
                      'internal_validation_score': best['selection_score']}), flush=True)
    return dict(name=name, checkpoint=path.relative_to(out).as_posix(), checkpoint_sha256=best['sha256'], config=job)


def evaluate(out, plan, candidate, cohort, namespace, filename):
    path = out / filename
    checkpoint = out / candidate['checkpoint']
    if train_rl.sha(checkpoint) != candidate['checkpoint_sha256']:
        raise RuntimeError('Candidate changed after selection')
    if not path.exists():
        args = train_rl.parse(['--mode', 'evaluate', '--output', str(path), '--checkpoint', str(checkpoint),
            *frozen_args(out), '--seed', str(cohort['seed']), '--events', str(cohort['events']), '--eval-action', 'greedy'])
        saved, model = train_rl.load_checkpoint(checkpoint, torch.device('cuda'))
        env = train_rl.make_env(args, n_envs=1, namespace=namespace)
        try:
            train_rl.environment_changes(saved['metadata']['environment_signature'], env.metadata, resume=True)
        finally:
            env.close()
        print(json.dumps({'starting_evaluation': filename, **cohort}), flush=True)
        result = train_rl.evaluate_model(model, args, torch.device('cuda'), namespace=namespace, events=cohort['events'])
        result.update(schema='sfld-ppo-evaluation-v1', checkpoint=candidate['checkpoint'],
            checkpoint_sha256=candidate['checkpoint_sha256'], library_sha256=plan['library_sha256'],
            profile_sha256=plan['profile_sha256'], checkpoint_training_metadata=saved['metadata'],
            evaluator_sha256=train_rl.sha(__file__))
        once(path, result)
        del saved, model
        torch.cuda.empty_cache()
    result = read(path)
    score = validate_evaluation(result, plan, cohort['seed'], namespace, cohort['events'], candidate['checkpoint_sha256'])
    means = {str(s['budget']): s['completions']['mean'] for s in result['summaries']}
    print(json.dumps({'evaluated': filename, 'score': score, 'means': means}), flush=True)
    return {**candidate, 'selection_score': score, 'budget_means': means, 'evaluation_file': filename}


def select_candidates(scored):
    return {'global': max(scored, key=lambda c: c['selection_score']),
            'per_budget': {str(b): max(scored, key=lambda c: c['budget_means'][str(b)]) for b in BUDGETS}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'out/rl-history-2026-10-11')
    parser.add_argument('--phase', choices=['prepare', 'branches', 'all'], default='all')
    args = parser.parse_args()
    os.chdir(ROOT); torch.set_num_threads(2)
    out = args.output.resolve()
    plan = prepare(out)
    if args.phase == 'prepare':
        print(json.dumps({'prepared': str(out)}), flush=True); return
    if not torch.cuda.is_available():
        raise RuntimeError('Declared study requires CUDA')
    parent = dict(name='parent', checkpoint='parent-checkpoint.pt', checkpoint_sha256=plan['parent_checkpoint_sha256'])
    candidates = [parent]
    for job in plan['variants']:
        candidates.append(train_job(out, plan, job, out / parent['checkpoint']))
    if args.phase == 'branches':
        return
    common = [evaluate(out, plan, c, plan['common_validation'], 'validation', f'common-{c["name"]}.json') for c in candidates]
    winner = select_candidates(common)['global']
    once(out / 'common-selection.json', dict(candidates=common, selected=winner))
    job = {**winner.get('config', plan['variants'][0]), **plan['extension'], 'name': 'extension'}
    candidates.append(train_job(out, plan, job, out / winner['checkpoint']))
    scored = [evaluate(out, plan, c, plan['selection_validation'], 'validation', f'selection-{c["name"]}.json') for c in candidates]
    selection = dict(schema='sfld-history-selection-v1', candidates=scored, **select_candidates(scored),
                     selection_rule=plan['selection_rule'], final_protocol=plan['final'])
    once(out / 'selection.json', selection)
    chosen = {c['name']: c for c in [parent, selection['global'], *selection['per_budget'].values()]}
    for c in chosen.values():
        copy_checked(out / c['checkpoint'], out / 'selected' / (c['name'] + '.pt'))
    jobs = []
    for name in [v['name'] for v in plan['variants']] + ['extension']:
        summary = read(out / name / 'training-summary.json')
        jobs.append(dict(name=name, steps=summary['state']['steps'],
                         completed_training_events=summary['state']['completed_training_events'],
                         wall_seconds=summary['wall_seconds_this_invocation']))
    once(out / 'training-totals.json', dict(new_ppo_steps=sum(j['steps'] for j in jobs),
        completed_training_events=sum(j['completed_training_events'] for j in jobs), jobs=jobs))
    print(json.dumps({'frozen_selection': {b: c['name'] for b, c in selection['per_budget'].items()},
                      'global': selection['global']['name']}), flush=True)
    for c in chosen.values():
        evaluate(out, plan, c, plan['final'], 'final', f'final-{c["name"]}.json')
    print(json.dumps({'study_complete': str(out)}), flush=True)


if __name__ == '__main__':
    main()
