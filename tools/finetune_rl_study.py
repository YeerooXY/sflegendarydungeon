"""Run a declared continuation study without reusing the previous final cohort.

Three fresh-seed fine-tunes, a validation-selected longer continuation, then a
frozen comparison against the parent. Simulator rules stay unchanged.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

import torch
import train_rl

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = [0, 20, 50, 100, 500]
MASK = (1 << 64) - 1


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def once(path, value):
    path = Path(path)
    if path.exists():
        if read(path) != value:
            raise RuntimeError(f'Refusing to replace frozen artifact: {path}')
    else:
        train_rl.write_json(path, value)


def copy_checked(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(source, target)
    if train_rl.sha(source) != train_rl.sha(target):
        raise RuntimeError(f'Artifact changed: {target}')


def fingerprint(seed):
    value = (seed + 0x9e3779b97f4a7c15) & MASK
    value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & MASK
    value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & MASK
    return (value ^ (value >> 31)) & 0xffffffff


def prepare(out):
    parent = ROOT / 'docs/rl-study-2026-10-09/selected-checkpoint.pt'
    variants = [
        dict(name='small-updates', seed=9301001, steps=10485760, learning_rate=1e-4,
             entropy_coefficient=.003, gae_lambda=.95, rollout_steps=128, target_kl=.015),
        dict(name='long-credit', seed=9301002, steps=10485760, learning_rate=1e-4,
             entropy_coefficient=.01, gae_lambda=.99, rollout_steps=256, target_kl=.025),
        dict(name='exploration', seed=9301003, steps=10485760, learning_rate=3e-4,
             entropy_coefficient=.02, gae_lambda=.98, rollout_steps=128, target_kl=.03),
    ]
    plan = dict(schema='sfld-ppo-finetune-plan-v1', budgets=BUDGETS,
        parent_checkpoint_sha256=train_rl.sha(parent),
        library_sha256=train_rl.sha(ROOT / 'build/Release/sfld_rl.dll'),
        profile_sha256=train_rl.sha(ROOT / 'profiles/synthetic.profile'),
        variants=variants, extension=dict(seed=9301004, steps=20971520),
        common_validation=dict(seed=9302001, events=512),
        selection_validation=dict(seed=9302002, events=1024),
        final=dict(seed=9303001, events=5000),
        training_validation_events=256, n_envs=256,
        selection_rule='Equal mean weekly completions over five budgets; exact ties keep first candidate (parent first).',
        extension_rule='Use first-stage winner and its settings; if parent wins use small-updates settings. Fresh seed, transferred Adam moments.',
        objective='Completed runs in 168 hours; gamma one; unchanged profile, random offered stones, all legal actions.')
    seeds = [v['seed'] for v in variants] + [plan[k]['seed'] for k in
        ('extension', 'common_validation', 'selection_validation', 'final')]
    # Include previous study roots as an additional collision check.
    seeds += [9011001, 9011002, 9011003, 9100001, 9100002, 9200001, 9200002, 9200003]
    if len({fingerprint(s) for s in seeds}) != len(seeds):
        raise RuntimeError('Compressed seed roots overlap')
    out.mkdir(parents=True, exist_ok=True)
    once(out / 'study-plan.json', plan)
    copy_checked(parent, out / 'parent-checkpoint.pt')
    copy_checked(ROOT / 'build/Release/sfld_rl.dll', out / 'sfld_rl.dll')
    saved, model = train_rl.load_checkpoint(parent, torch.device('cpu'))
    del model
    sources = list(saved['metadata']['sources']) + ['tools/finetune_rl_study.py', 'profiles/synthetic.profile',
        'docs/rule-strategy-2026-10-09/rules/score-free.rules',
        'docs/rule-strategy-2026-10-09/rules/score-paid.rules']
    hashes = {p: train_rl.sha(ROOT / p) for p in sources}
    manifest = out / 'source-manifest.json'
    if manifest.exists() and read(manifest) != hashes:
        original = read(manifest)
        changes = {p for p in set(original) | set(hashes) if original.get(p) != hashes.get(p)}
        script = 'tools/finetune_rl_study.py'
        revision = read(out / 'orchestrator-revision.json')
        if changes != {script} or revision['previous_sha256'] != original[script] or revision['current_sha256'] != hashes[script]:
            raise RuntimeError('Training sources changed or orchestration revision was not recorded')
        copy_checked(ROOT / script, out / 'orchestrator-revisions' / hashes[script] / 'finetune_rl_study.py')
        sources.remove(script)  # Preserve the initial snapshot; publish both versions.
    else:
        once(manifest, hashes)
    for path in sources:
        copy_checked(ROOT / path, out / 'source' / path)
    return plan


def train_job(out, plan, job, parent):
    name = job['name']
    command = [sys.executable, 'tools/train_rl.py', '--mode', 'train', '--finetune-from', str(parent),
        '--budgets', ','.join(map(str, BUDGETS)), '--n-envs', '256', '--rollout-steps', str(job['rollout_steps']),
        '--total-steps', str(job['steps']), '--validation-events', '256',
        '--validate-every', str(8192 // job['rollout_steps']),
        '--checkpoint-every', str(8192 // job['rollout_steps']), '--device', 'cuda', '--torch-threads', '2',
        '--seed', str(job['seed']), '--learning-rate', str(job['learning_rate']),
        '--entropy-coefficient', str(job['entropy_coefficient']), '--gae-lambda', str(job['gae_lambda']),
        '--target-kl', str(job['target_kl']), '--output', str(out / name)]
    invocation = dict(job=job, command=command, parent_checkpoint_sha256=train_rl.sha(parent),
                      trainer_sha256=train_rl.sha(ROOT / 'tools/train_rl.py'))
    once(out / f'{name}-invocation.json', invocation)
    status_path = out / f'{name}-status.json'
    if status_path.exists():
        if read(status_path)['exit_code'] != 0:
            raise RuntimeError(f'Prior training failed: {name}')
    else:
        print(json.dumps({'starting_training': name, 'new_decisions': job['steps']}), flush=True)
        started = time.monotonic()
        with (out / f'{name}.log').open('x', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        once(status_path, dict(exit_code=result.returncode, wall_seconds=time.monotonic() - started))
        if result.returncode:
            raise RuntimeError(f'Training failed; see {name}.log')
    summary = read(out / name / 'training-summary.json')
    if summary['state']['steps'] != job['steps']:
        raise RuntimeError('Training decision count differs from plan')
    pointer = read(out / name / 'best.json')
    checkpoint = out / name / pointer['checkpoint']
    if checkpoint.parent.resolve() != (out / name).resolve() or train_rl.sha(checkpoint) != pointer['sha256']:
        raise RuntimeError('Invalid training checkpoint pointer')
    print(json.dumps({'finished_training': name, 'best_steps': pointer['steps'],
                      'validation_score': pointer['selection_score']}), flush=True)
    return dict(name=name, checkpoint=str(checkpoint.relative_to(out).as_posix()),
                checkpoint_sha256=pointer['sha256'], config=job)


def validate_evaluation(result, plan, seed, namespace, events, checkpoint_sha):
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def count(value):
        return finite(value) and value >= 0 and value == int(value)

    if (result['root_seed'] != str(seed) or result['namespace'] != namespace or
        result['namespace_byte'] != train_rl.DOMAINS[namespace] or
        result['checkpoint_sha256'] != checkpoint_sha or
        result['library_sha256'] != plan['library_sha256'] or
        result['profile_sha256'] != plan['profile_sha256'] or result['action_policy'] != 'greedy'):
        raise RuntimeError('Evaluation provenance differs from plan')
    metadata = result.get('environment_metadata', [])
    signature = result.get('checkpoint_training_metadata', {}).get('environment_signature')
    if len(metadata) != len(BUDGETS) or not isinstance(signature, dict):
        raise RuntimeError('Evaluation lacks complete environment provenance')
    by_budget = {}
    for item in metadata:
        budgets = item.get('budgets', [])
        profiles = item.get('profiles', [])
        if (len(budgets) != 1 or budgets[0] not in BUDGETS or budgets[0] in by_budget or
            item.get('library_sha256') != plan['library_sha256'] or len(profiles) != 1 or
            profiles[0].get('sha256') != plan['profile_sha256'] or not profiles[0].get('path') or
            not finite(item.get('deadline_hours')) or item['deadline_hours'] != 168 or
            not count(item.get('max_actions')) or item['max_actions'] != plan.get('max_actions', 100000) or
            item.get('seed_namespace') != train_rl.DOMAINS[namespace] or item.get('auto_reset') is not False or
            item.get('n_envs') != min(plan.get('n_envs', 256), events)):
            raise RuntimeError('Nested evaluation environment differs from plan')
        try:
            train_rl.environment_changes(signature, item, resume=True)
        except ValueError as error:
            raise RuntimeError('Evaluation environment differs from checkpoint') from error
        by_budget[budgets[0]] = item
    rows = result['outcomes']
    expected = {(b, i) for b in BUDGETS for i in range(events)}
    numeric = ('budget', 'event_index', 'completions', 'mushrooms', 'recovery_mushrooms',
               'reroll_mushrooms', 'actions', 'invalid_actions')
    if any(any(not count(row.get(key)) for key in numeric) for row in rows):
        raise RuntimeError('Evaluation counters must be finite nonnegative integers')
    if len(rows) != len(expected) or {(r['budget'], r['event_index']) for r in rows} != expected:
        raise RuntimeError('Incomplete or duplicated evaluation cohort')
    for row in rows:
        item = by_budget[row['budget']]
        if (row.get('truncated') is not False or row['invalid_actions'] != 0 or row.get('terminated') is not True or
            row['mushrooms'] > row['budget'] or row['mushrooms'] != row['recovery_mushrooms'] + row['reroll_mushrooms'] or
            not 0 < row['actions'] < item['max_actions'] or row['completions'] > row['actions'] or
            not finite(row.get('elapsed_hours')) or not 0 < row['elapsed_hours'] <= 168 + 1e-8 or
            not isinstance(row.get('profile'), str) or
            Path(row['profile']).resolve() != Path(item['profiles'][0]['path']).resolve()):
            raise RuntimeError('Invalid, incomplete or over-budget evaluation')
    score = statistics.mean(r['completions'] for r in rows)
    if not finite(result.get('selection_score')) or abs(score - result['selection_score']) > 1e-10:
        raise RuntimeError('Evaluation score does not match outcomes')
    summaries = result.get('summaries', [])
    if len(summaries) != len(BUDGETS) or {s.get('budget') for s in summaries} != set(BUDGETS):
        raise RuntimeError('Evaluation summaries do not cover every declared budget')
    for summary in summaries:
        selected = [r for r in rows if r['budget'] == summary['budget']]
        actual = train_rl.interval([r['completions'] for r in selected])
        reported = summary.get('completions') or {}
        if (summary.get('events') != events or summary.get('failed_events') != 0 or
            summary.get('invalid_actions') != 0 or summary.get('truncations') != 0 or
            any(not finite(reported.get(k)) or abs(reported[k] - actual[k]) > 1e-10 for k in ('mean', 'standard_error'))):
            raise RuntimeError('Evaluation summaries do not match indexed outcomes')
        interval = reported.get('normal_95_ci', [])
        if len(interval) != 2 or any(not finite(a) or abs(a - b) > 1e-10 for a, b in zip(interval, actual['normal_95_ci'])):
            raise RuntimeError('Evaluation interval does not match indexed outcomes')
        for key, source in (('mean_mushrooms', 'mushrooms'), ('mean_recovery_mushrooms', 'recovery_mushrooms'),
                            ('mean_reroll_mushrooms', 'reroll_mushrooms')):
            if not finite(summary.get(key)) or abs(summary[key] - statistics.mean(r[source] for r in selected)) > 1e-10:
                raise RuntimeError('Evaluation spending summary does not match indexed outcomes')
    return score


def evaluate(out, plan, candidate, cohort, namespace, filename):
    checkpoint = out / candidate['checkpoint']
    checksum = train_rl.sha(checkpoint)
    if checksum != candidate['checkpoint_sha256']:
        raise RuntimeError('Candidate changed')
    path = out / filename
    if path.exists():
        result = read(path)
    else:
        args = train_rl.parse(['--mode', 'evaluate', '--output', str(path), '--checkpoint', str(checkpoint),
            '--budgets', ','.join(map(str, BUDGETS)), '--seed', str(cohort['seed']), '--events', str(cohort['events']),
            '--n-envs', '256', '--device', 'cuda', '--eval-action', 'greedy'])
        saved, model = train_rl.load_checkpoint(checkpoint, torch.device('cuda'))
        env = train_rl.make_env(args, n_envs=1, namespace=namespace)
        try:
            env.reset()
            train_rl.environment_changes(saved['metadata']['environment_signature'], env.metadata, resume=True)
        finally:
            env.close()
        print(json.dumps({'starting_evaluation': filename, **cohort}), flush=True)
        result = train_rl.evaluate_model(model, args, torch.device('cuda'), namespace=namespace, events=cohort['events'])
        result.update(schema='sfld-ppo-evaluation-v1', checkpoint=candidate['checkpoint'], checkpoint_sha256=checksum,
            library_sha256=plan['library_sha256'], profile_sha256=plan['profile_sha256'],
            checkpoint_training_metadata=saved['metadata'], evaluator_sha256=train_rl.sha(Path(__file__)))
        once(path, result)
        del model, saved
        torch.cuda.empty_cache()
    score = validate_evaluation(result, plan, cohort['seed'], namespace, cohort['events'], checksum)
    print(json.dumps({'evaluated': filename, 'score': score,
                      'means': {str(s['budget']): s['completions']['mean'] for s in result['summaries']}}), flush=True)
    return {**candidate, 'selection_score': score, 'evaluation_file': filename}


def select(out, plan, candidates, cohort, prefix, path):
    scored = [evaluate(out, plan, c, cohort, 'validation', f'{prefix}-{c["name"]}.json') for c in candidates]
    best = max(scored, key=lambda c: c['selection_score'])
    result = dict(schema='sfld-ppo-finetune-selection-v1', selection_rule=plan['selection_rule'],
                  candidates=scored, selected=best, cohort=cohort)
    if path == 'selection.json':
        copy_checked(out / best['checkpoint'], out / 'selected-checkpoint.pt')
        result['selected'] = {**best, 'original_checkpoint': best['checkpoint'], 'checkpoint': 'selected-checkpoint.pt'}
        result['final_protocol'] = plan['final']
    once(out / path, result)
    print(json.dumps({'selection': path, 'winner': best['name'], 'score': best['selection_score']}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'out/rl-finetune-2026-10-10')
    parser.add_argument('--phase', choices=['prepare', 'branches', 'extension', 'final', 'all'], default='all')
    args = parser.parse_args()
    os.chdir(ROOT)
    torch.set_num_threads(2)
    out = args.output.resolve()
    plan = prepare(out)
    if args.phase == 'prepare':
        return
    if not torch.cuda.is_available():
        raise RuntimeError('This declared study requires CUDA')
    parent = dict(name='parent', checkpoint='parent-checkpoint.pt', checkpoint_sha256=plan['parent_checkpoint_sha256'])
    candidates = [parent]
    for job in plan['variants']:
        candidates.append(train_job(out, plan, job, out / 'parent-checkpoint.pt'))
    if args.phase == 'branches':
        return
    common = select(out, plan, candidates, plan['common_validation'], 'common', 'common-validation.json')
    winner = common['selected']
    job = {**winner.get('config', plan['variants'][0]), **plan['extension'], 'name': 'extension'}
    candidates.append(train_job(out, plan, job, out / winner['checkpoint']))
    if args.phase == 'extension':
        return
    selection = select(out, plan, candidates, plan['selection_validation'], 'selection', 'selection.json')
    totals = []
    for job in [*plan['variants'], dict(name='extension')]:
        summary = read(out / job['name'] / 'training-summary.json')
        state = summary['state']
        totals.append(dict(name=job['name'], steps=state['steps'], completed_training_events=state['completed_training_events'],
                           wall_seconds=summary['wall_seconds_this_invocation']))
    once(out / 'training-totals.json', dict(new_ppo_steps=sum(j['steps'] for j in totals),
        completed_training_events=sum(j['completed_training_events'] for j in totals),
        wall_seconds=sum(j['wall_seconds'] for j in totals), jobs=totals))
    evaluate(out, plan, selection['selected'], plan['final'], 'final', 'final-learner.json')
    evaluate(out, plan, parent, plan['final'], 'final', 'final-parent.json')
    print(json.dumps({'study_complete': str(out), 'selected': selection['selected']['name']}), flush=True)


if __name__ == '__main__':
    main()
