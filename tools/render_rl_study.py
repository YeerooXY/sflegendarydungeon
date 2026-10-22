"""Render a portable RL study report, rejecting incomplete or mismatched final pairs.

Usage: python tools/render_rl_study.py --study out/rl-2026-10-09 --output out/rl-2026-10-09/report.html
No Torch, NumPy, web server or external JavaScript dependency is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
from urllib.parse import quote


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, label, minimum=0):
    require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
            f'{label} must be an integer >= {minimum}')
    return value


def number(value, label):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
            f'{label} must be finite')
    return float(value)


def fingerprint(value, label):
    require(isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value),
            f'{label} must be a SHA256 fingerprint')
    return value


def estimate(values):
    require(len(values) >= 2, 'At least two complete events are needed for an interval')
    mean = statistics.mean(values)
    se = statistics.stdev(values) / math.sqrt(len(values))
    return {'mean': mean, 'standard_error': se, 'normal_95_ci': [mean - 1.96 * se, mean + 1.96 * se]}


def agrees(actual, expected, label):
    require(math.isclose(number(actual, label), expected, abs_tol=1e-9, rel_tol=1e-9),
            f'{label} disagrees with indexed outcomes')


def validate_evaluation(report):
    """Reconstruct all estimates from exact event indices and return canonical groups."""
    try:
        root_seed = int(report['root_seed'])
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError('Evaluation needs an unsigned root_seed') from error
    require(0 <= root_seed < 2**64, 'Evaluation root seed is outside uint64')
    namespace = integer(report['namespace_byte'], 'namespace_byte')
    require(namespace <= 255, 'Namespace byte is outside uint8')
    require(isinstance(report['namespace'], str), 'Evaluation needs namespace name')
    summaries, environments, outcomes = report['summaries'], report['environment_metadata'], report['outcomes']
    require(len(summaries) == len(environments) and bool(summaries), 'Summary/environment count mismatch')
    groups, used_rows = {}, set()
    for summary, env in zip(summaries, environments):
        budget = integer(summary['budget'], 'budget')
        n = integer(summary['events'], 'events', 2)
        require(env['budgets'] == [budget] and len(env['profiles']) == 1,
                'Evaluation metadata must identify one budget/profile per summary')
        require(env['seed_namespace'] == namespace, 'Environment/evaluation namespace mismatch')
        require(env.get('initial_episode_index', 0) == 0, 'Evaluation must start at event index zero')
        profile = fingerprint(env['profiles'][0]['sha256'], 'profile hash')
        horizon = number(env['deadline_hours'], 'deadline_hours')
        require(horizon > 0, 'Deadline must be positive')
        signature = {'root_seed': root_seed, 'namespace': report['namespace'], 'namespace_byte': namespace,
                     'profile_sha256': profile, 'library_sha256': fingerprint(env['library_sha256'], 'library hash'),
                     'schema_sha256': fingerprint(env['schema_sha256'], 'schema hash'),
                     'deadline_hours': horizon, 'max_actions': integer(env['max_actions'], 'max_actions', 1)}
        key = (profile, budget)
        require(key not in groups, 'Duplicate budget/profile evaluation group')
        indexed = {}
        for position, row in enumerate(outcomes):
            if row['profile'] != summary['profile'] or row['budget'] != budget:
                continue
            require(position not in used_rows, 'An outcome was assigned to multiple summaries')
            used_rows.add(position)
            index = integer(row['event_index'], 'event_index')
            require(index not in indexed, 'Duplicate event index')
            require(row['terminated'] is True and row['truncated'] is False,
                    'Invalid or truncated event cannot contribute to a final estimate')
            require(integer(row['invalid_actions'], 'invalid_actions') == 0, 'Invalid actions in evaluation')
            for name in ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms', 'actions'):
                integer(row[name], name)
            require(row['mushrooms'] <= budget, 'Event exceeded its mushroom budget')
            require(row['recovery_mushrooms'] + row['reroll_mushrooms'] == row['mushrooms'],
                    'Spending categories do not sum to total')
            # Game::finished also ends an event when the remaining fraction of
            # time cannot fit another ordinary action. It need not equal 168.
            require(0 < number(row['elapsed_hours'], 'elapsed_hours') <= horizon + 1e-9,
                    'Event elapsed time is outside its declared horizon')
            indexed[index] = row
        require(set(indexed) == set(range(n)), 'Missing or unexpected event indices')
        for field in ('failed_events', 'invalid_actions', 'truncations'):
            require(integer(summary[field], field) == 0, 'Failed events cannot contribute to a final estimate')
        rows = [indexed[i] for i in range(n)]
        stats = estimate([r['completions'] for r in rows])
        require(summary['completions'] is not None, 'Missing completion estimate')
        agrees(summary['completions']['mean'], stats['mean'], 'Completion mean')
        agrees(summary['completions']['standard_error'], stats['standard_error'], 'Completion standard error')
        require(len(summary['completions']['normal_95_ci']) == 2, 'Malformed completion interval')
        for actual, expected in zip(summary['completions']['normal_95_ci'], stats['normal_95_ci']):
            agrees(actual, expected, 'Completion interval')
        spending = {}
        for field in ('mushrooms', 'recovery_mushrooms', 'reroll_mushrooms'):
            spending[field] = statistics.mean(r[field] for r in rows)
            agrees(summary['mean_' + field], spending[field], 'Mean ' + field)
        groups[key] = {'profile': summary['profile'], 'budget': budget, 'events': n, 'signature': signature,
                       'rows': rows, 'completions': stats, 'spending': spending}
    require(len(used_rows) == len(outcomes), 'Unmatched outcome rows')
    score = statistics.mean(g['completions']['mean'] for g in groups.values())
    agrees(report['selection_score'], score, 'Selection score')
    return groups


def paired_results(learner, teacher):
    left, right = validate_evaluation(learner), validate_evaluation(teacher)
    require(set(left) == set(right), 'Paired budget/profile groups differ')
    result = []
    for key in sorted(left, key=lambda k: (k[0], k[1])):
        a, b = left[key], right[key]
        require(a['signature'] == b['signature'], 'Paired seed, namespace, profile, horizon or library mismatch')
        require(a['events'] == b['events'], 'Paired event counts differ')
        differences = [x['completions'] - y['completions'] for x, y in zip(a['rows'], b['rows'])]
        result.append({'profile': a['profile'], 'profile_sha256': key[0], 'budget': a['budget'], 'events': a['events'],
                       'learner': a['completions'], 'teacher': b['completions'], 'paired_difference': estimate(differences),
                       'learner_spending': a['spending'], 'teacher_spending': b['spending'], 'cohort': a['signature']})
    return result


class Artifacts:
    def __init__(self, output):
        self.output = Path(output)
        self.rows = {}

    def read(self, path, *, expected=None, jsonl=False, plain_text=False):
        path = Path(path).resolve()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if expected is not None:
            require(digest == fingerprint(expected, 'artifact hash'), f'Artifact hash mismatch: {path}')
        self.rows[str(path)] = {'path': str(path), 'sha256': digest,
                               'href': quote(os.path.relpath(path, self.output.parent).replace('\\', '/'), safe='/.:')}
        if plain_text:
            return data.decode('utf-8-sig')
        if jsonl:
            # A renderer may run while training appends a record. Never accept a
            # malformed complete line; only an unfinished last line is omitted.
            text = data.decode('utf-8-sig')
            lines = text.splitlines()
            if text and not text.endswith('\n'):
                lines = lines[:-1]
            return [json.loads(line) for line in lines if line.strip()]
        return json.loads(data.decode('utf-8-sig'))

    def verify(self, path, expected):
        path = Path(path).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest == fingerprint(expected, 'checkpoint hash'), f'Checkpoint hash mismatch: {path}')
        self.rows[str(path)] = {'path': str(path), 'sha256': digest,
                               'href': quote(os.path.relpath(path, self.output.parent).replace('\\', '/'), safe='/.:')}


def relative_file(base, value):
    path = Path(value)
    return path if path.is_absolute() else Path(base) / path


def read_run(study, job, artifacts):
    directory = study / job['name']
    require(directory.parent == study and directory.name not in ('', '.', '..'), 'Invalid study job name')
    result = {'name': job['name'], 'planned_bc_steps': job['cloning_steps'], 'planned_ppo_steps': job['ppo_steps'],
              'seed': job['seed'], 'status': 'pending', 'points': [], 'cloning': [], 'bc_steps': 0,
              'ppo_steps': 0, 'bc_events': 0, 'ppo_events': 0, 'sources': {}, 'best': None}
    if not (directory / 'run.json').exists():
        return result
    manifest = artifacts.read(directory / 'run.json')
    metadata = manifest['metadata']
    require(int(metadata['seed']) == job['seed'], 'Job and training seed differ')
    result.update(status='running', sources=metadata['sources'], metadata=metadata)
    if (directory / 'curve.jsonl').exists():
        ppo_events = 0
        previous = {'cloning': -1, 'ppo': -1}
        for row in artifacts.read(directory / 'curve.jsonl', jsonl=True):
            phase = row['phase']
            if phase in previous:
                require(row['steps'] > previous[phase], 'Repeated or reordered training steps')
                previous[phase] = row['steps']
            if phase == 'cloning':
                result['bc_steps'] = integer(row['steps'], 'BC steps')
                result['bc_events'] += integer(row['completed_events'], 'BC completed events')
                require(row['total_completed_events'] == result['bc_events'], 'BC event count mismatch')
                result['cloning'].append({'steps': row['steps'], 'loss': number(row['loss'], 'BC loss'),
                                          'events': result['bc_events']})
            elif phase == 'ppo':
                result['ppo_steps'] = integer(row['steps'], 'PPO steps')
                ppo_events += integer(row['completed_events'], 'PPO completed events')
                result['ppo_events'] = ppo_events
            elif phase != 'initial_validation':
                raise ValueError('Unknown curve phase: ' + phase)
            for summary in row.get('validation_summaries', []):
                failed = bool(summary.get('failed_events') or summary.get('invalid_actions') or summary.get('truncations'))
                stats = summary.get('completions')
                require(not failed or stats is None, 'Invalid validation estimate was accepted')
                result['points'].append({'steps': row['steps'], 'events': ppo_events, 'budget': summary['budget'],
                                         'profile': summary['profile'], 'estimate': stats, 'failed': failed,
                                         'validation_events': summary['events']})
    if (directory / 'training-summary.json').exists():
        summary = artifacts.read(directory / 'training-summary.json')
        state = summary['state']
        for key, state_key in [('bc_steps', 'bc_steps'), ('ppo_steps', 'steps'),
                               ('bc_events', 'bc_completed_events'), ('ppo_events', 'completed_training_events')]:
            require(result[key] == state[state_key], 'Training summary and curve disagree: ' + key)
        result['status'] = 'completed'
    if (directory / 'best.json').exists():
        best = artifacts.read(directory / 'best.json')
        artifacts.verify(relative_file(directory, best['checkpoint']), best['sha256'])
        result['best'] = best
    failures = list(directory.glob('failure-*.json'))
    if failures:
        result['status'] = 'failed'
        result['failures'] = [artifacts.read(path) for path in failures]
    return result


def read_common(path, artifacts):
    common = artifacts.read(path)
    require(common['schema'] == 'sfld-ppo-common-validation-v1', 'Unknown common-validation schema')
    candidates, groups = [], None
    names = set()
    for candidate in common['candidates']:
        require(candidate['name'] not in names, 'Duplicate common-validation candidate')
        names.add(candidate['name'])
        report = artifacts.read(relative_file(path.parent, candidate['validation_file']), expected=candidate['validation_sha256'])
        current = validate_evaluation(report)
        if groups is not None:
            require(set(current) == set(groups), 'Common validation candidates have different budget/profile groups')
            for key in current:
                require(current[key]['signature'] == groups[key]['signature'] and
                        current[key]['events'] == groups[key]['events'], 'Common validation cohorts differ')
        groups = current
        require(int(report['root_seed']) == int(common['seed']) and
                report['namespace_byte'] == common['namespace_byte'] and report['namespace'] == common['namespace'],
                'Common validation manifest/cohort mismatch')
        require(all(g['events'] == common['events_per_budget'] for g in current.values()), 'Common validation size mismatch')
        require(set(g['budget'] for g in current.values()) == set(common['budgets']), 'Common validation budgets differ')
        require(report['checkpoint_sha256'] == candidate['checkpoint_sha256'], 'Candidate checkpoint hash differs')
        agrees(candidate['selection_score'], report['selection_score'], 'Candidate selection score')
        candidates.append({**candidate, 'summaries': report['summaries']})
    require(bool(candidates), 'Common validation needs candidates')
    winner = max(candidates, key=lambda c: c['selection_score'])
    selected = common['selected']
    require(selected['name'] == winner['name'] and selected['checkpoint_sha256'] == winner['checkpoint_sha256'],
            'Selected candidate is not the common-validation winner (ties keep first)')
    agrees(selected['selection_score'], winner['selection_score'], 'Selected score')
    return {**common, 'candidates': candidates,
            'cohorts': [{'budget': g['budget'], **g['signature']} for g in groups.values()]}


def validate_behavior(audit, plan, checkpoint_sha256=None):
    require(audit['schema'] == 'sfld-ppo-behavior-audit-v1', 'Unknown behavior audit schema')
    require(audit['cohort']['namespace_byte'] == 213, 'Behavior audit must use its separate diagnostic namespace')
    if checkpoint_sha256 is not None:
        require(audit['checkpoint']['sha256'] == checkpoint_sha256, 'Behavior audit uses a different checkpoint')
    for entry in audit['environment_metadata']:
        env = entry.get('metadata', entry)
        require(env['library_sha256'] == plan['library_sha256'] and env['deadline_hours'] == 168.,
                'Behavior audit library or horizon differs')
        require(env['seed_namespace'] == 213 and all(p['sha256'] == plan['profile_sha256'] for p in env['profiles']),
                'Behavior audit profile or namespace differs')
    rows = list(audit['summaries']) + [row for table in audit['tables'].values() for row in table]
    for row in rows:
        decisions = integer(row['decisions'], 'diagnostic decisions')
        disagreements = integer(row['disagreements'], 'diagnostic disagreements')
        require(disagreements <= decisions, 'More diagnostic disagreements than decisions')
        agrees(row['disagreement_rate'], disagreements / decisions if decisions else 0., 'Diagnostic disagreement rate')
        for field in ('actions', 'teacher_actions'):
            require(sum(integer(value, 'action count') for value in row[field].values()) == decisions,
                    'Diagnostic action counts do not sum to decisions')
        for field in ('failed_events', 'invalid_actions', 'truncations'):
            require(row.get(field, 0) == 0, 'Behavior audit contains failed events')
    return audit


def read_distillation(path, artifacts, plan, checkpoint_sha256, training_environment):
    summary = artifacts.read(path)
    require(summary['schema'] == 'sfld-distillation-summary-v1', 'Unknown distillation summary schema')
    require(summary['checkpoint_sha256'] == checkpoint_sha256, 'Extracted rules imitate a different checkpoint')
    for key in ('library_sha256', 'profile_sha256'):
        require(summary[key] == plan[key], 'Distillation environment differs: ' + key)
    require(summary['deadline_hours'] == training_environment['deadline_hours'], 'Distillation horizon differs')

    def linked(key, plain_text=False):
        entry = summary[key]
        return artifacts.read(relative_file(path.parent, entry['file']), expected=entry['sha256'], plain_text=plain_text)

    trees, agreement = linked('trees'), linked('agreement')
    rules = linked('readable_rules', plain_text=True)
    neural, tree = linked('neural_evaluation'), linked('tree_evaluation')
    require(trees['schema'] == 'sfld-phase-trees-v1', 'Unknown extracted tree schema')
    for key in ('checkpoint_sha256', 'library_sha256', 'profile_sha256'):
        require(trees['provenance'][key] == summary[key], 'Tree provenance mismatch: ' + key)
    require(neural['policy_type'] == 'neural' and tree['policy_type'] == 'phase_trees', 'Distillation policy labels differ')
    require(neural['checkpoint_sha256'] == tree['checkpoint_sha256'] == checkpoint_sha256,
            'Distillation gameplay used a different neural checkpoint')
    require(tree['tree_sha256'] == summary['trees']['sha256'], 'Gameplay used a different extracted tree')
    pairs = paired_results(neural, tree)
    cohort = summary['gameplay_cohort']
    require(cohort['namespace_byte'] == 215, 'Extracted-rule gameplay must use its separate held-out namespace')
    require(set(p['budget'] for p in pairs) == set(summary['budgets']), 'Distillation gameplay budgets differ')
    for pair in pairs:
        signature = pair['cohort']
        require(signature['root_seed'] == int(cohort['root_seed']) and signature['namespace_byte'] == 215 and
                signature['namespace'] == 'distill_gameplay' and pair['events'] == cohort['events_per_budget'],
                'Distillation gameplay cohort mismatch')
        require(signature['profile_sha256'] == plan['profile_sha256'], 'Distillation gameplay profile mismatch')
        for key in ('library_sha256', 'schema_sha256', 'deadline_hours', 'max_actions'):
            require(signature[key] == training_environment[key], 'Distillation gameplay environment mismatch: ' + key)
    declared = {}
    for row in summary['paired_gameplay']:
        key = (row['profile'], row['budget'])
        require(key not in declared, 'Duplicate distilled-rule gameplay summary')
        declared[key] = row
    require(set(declared) == {(p['profile'], p['budget']) for p in pairs}, 'Distillation gameplay summaries differ')
    losses = []
    for pair in pairs:
        row = declared[(pair['profile'], pair['budget'])]
        require(row['events'] == pair['events'], 'Distillation event count differs')
        agrees(row['neural_completions_mean'], pair['learner']['mean'], 'Neural completion mean')
        agrees(row['tree_completions_mean'], pair['teacher']['mean'], 'Tree completion mean')
        for key in ('mean', 'standard_error'):
            agrees(row['paired_completion_loss'][key], pair['paired_difference'][key], 'Paired tree loss ' + key)
        require(len(row['paired_completion_loss']['normal_95_ci']) == 2, 'Malformed tree loss interval')
        for actual, expected in zip(row['paired_completion_loss']['normal_95_ci'], pair['paired_difference']['normal_95_ci']):
            agrees(actual, expected, 'Paired tree loss interval')
        loss, baseline = pair['paired_difference']['mean'], pair['learner']['mean']
        relative = loss / baseline if baseline > 0 else None
        losses.append({**row, 'paired_completion_loss': pair['paired_difference'], 'relative_loss': relative,
                       'large_observed_loss': loss >= .25 or (relative is not None and relative >= .1)})

    require(agreement['schema'] == 'sfld-distillation-agreement-v1', 'Unknown distillation agreement schema')
    require(agreement['held_out'] == summary['agreement']['held_out'], 'Embedded agreement differs from artifact')
    held = agreement['held_out']
    samples = integer(held['samples'], 'Held-out agreement samples', 1)
    accuracy = number(held['agreement'], 'Held-out agreement')
    require(0 <= accuracy <= 1, 'Agreement must be a fraction')
    for group in ('by_phase', 'by_budget'):
        counts, correct = 0, 0.
        for row in held[group].values():
            n = integer(row['samples'], 'Agreement group samples')
            if n:
                value = number(row['agreement'], 'Group agreement')
                require(0 <= value <= 1, 'Group agreement must be a fraction')
                correct += n * value
            else:
                require(row['agreement'] is None, 'Empty agreement group needs a null rate')
            counts += n
        require(counts == samples, 'Agreement subgroup sample totals differ')
        agrees(accuracy, correct / samples, 'Weighted held-out agreement')
    for key, namespace in (('fit_collection', 214), ('held_out_collection', 216)):
        collection = agreement[key]
        require(collection['namespace_byte'] == namespace, 'Fit and agreement labels require separate event domains')
        for env in collection['environment_metadata']:
            require(env['seed_namespace'] == namespace and
                    all(p['sha256'] == plan['profile_sha256'] for p in env['profiles']), 'Label collection profile/domain differs')
            for name in ('library_sha256', 'schema_sha256', 'deadline_hours', 'max_actions'):
                require(env[name] == training_environment[name], 'Label collection environment differs: ' + name)
    require(agreement['held_out_collection']['requested_labels'] == samples, 'Held-out label sample total differs')
    phases = []
    for phase, entry in trees['trees'].items():
        leaves = sum('feature' not in node for node in entry['nodes'])
        phases.append({'phase': phase, 'rules': leaves, 'split_nodes': len(entry['nodes']) - leaves,
                       'fit_samples': entry['samples'], 'agreement': held['by_phase'].get(phase)})
    rules_path = relative_file(path.parent, summary['readable_rules']['file']).resolve()
    return {'checkpoint_sha256': checkpoint_sha256, 'rule_count': sum(p['rules'] for p in phases),
            'phases': phases, 'fit': trees['fit'], 'fallback': trees['fallback'], 'held_out': held,
            'label_sampling': agreement['held_out_collection'].get('sampling', ''),
            'gameplay_cohort': cohort, 'losses': losses, 'readable_rules': rules,
            'readable_rules_href': artifacts.rows[str(rules_path)]['href'],
            'large_loss_threshold': 'Observed loss of at least 0.25 completions per event, or at least 10% of the neural mean. These are descriptive flags, not a statistical test.'}


def build_report(study, output):
    study = Path(study).resolve()
    artifacts = Artifacts(Path(output).resolve())
    plan = artifacts.read(study / 'study-plan.json')
    names = [job['name'] for job in plan['jobs']]
    require(len(names) == len(set(names)), 'Repeated study job name')
    runs = [read_run(study, job, artifacts) for job in plan['jobs']]
    common_path = study / 'common-validation.json'
    common = read_common(common_path, artifacts) if common_path.exists() else None
    if common is not None:
        require(int(common['seed']) == int(plan['common_validation_seed']), 'Common validation seed differs from study plan')
        require([c['name'] for c in common['candidates']] == names, 'Common validation candidate order differs from plan')
        for run, candidate in zip(runs, common['candidates']):
            require(run['status'] == 'completed' and run['best'] is not None, 'Selection includes unfinished training')
            require(candidate['checkpoint_sha256'] == run['best']['sha256'], 'Common candidate differs from training winner')
        artifacts.verify(relative_file(study, common['selected']['checkpoint']), common['selected']['checkpoint_sha256'])
    finals = [study / 'final-learner.json', study / 'final-teacher.json']
    require(finals[0].exists() == finals[1].exists(), 'Both final evaluation files are required for a comparison')
    comparison = []
    if finals[0].exists():
        learner, teacher = [artifacts.read(path) for path in finals]
        require(teacher['action_policy'] == 'teacher' and learner['action_policy'] in ('greedy', 'sample'),
                'Final files do not identify learner and teacher policies')
        comparison = paired_results(learner, teacher)
        require(int(learner['root_seed']) == int(plan['final_seed']), 'Final seed differs from study plan')
        if common is not None:
            require(learner['checkpoint_sha256'] == common['selected']['checkpoint_sha256'],
                    'Final learner differs from the candidate frozen by common validation')
            require((int(common['seed']), common['namespace_byte']) !=
                    (int(learner['root_seed']), learner['namespace_byte']), 'Final cohort was used for selection')
    for run in runs:
        if 'metadata' not in run:
            continue
        env = run['metadata']['environment']
        require(env['library_sha256'] == plan['library_sha256'], 'Training library differs from study plan')
        require(all(p['sha256'] == plan['profile_sha256'] for p in env['profiles']),
                'Training profile differs from study plan')
        require(env['deadline_hours'] == 168., 'This seven-day report requires a 168-hour training horizon')
        for cohort in (common['cohorts'] if common else []) + [r['cohort'] for r in comparison]:
            require(cohort['profile_sha256'] == plan['profile_sha256'], 'Evaluation profile differs from training')
            for key in ('library_sha256', 'schema_sha256', 'deadline_hours', 'max_actions'):
                require(cohort[key] == env[key], 'Evaluation differs from training environment: ' + key)
    for row in comparison:
        require(row['cohort']['library_sha256'] == plan['library_sha256'] and
                row['profile_sha256'] == plan['profile_sha256'], 'Final environment differs from study plan')
    behavior_path = study / 'behavior-audit.json'
    behavior = validate_behavior(artifacts.read(behavior_path), plan,
                                 common['selected']['checkpoint_sha256'] if common else None) if behavior_path.exists() else None
    distillation_path = study / 'distillation' / 'distillation-summary.json'
    distillation = None
    if distillation_path.exists():
        require(common is not None, 'Extracted rules need a frozen common-validation winner')
        training_environment = next(r['metadata']['environment'] for r in runs if 'metadata' in r)
        distillation = read_distillation(distillation_path, artifacts, plan,
                                        common['selected']['checkpoint_sha256'], training_environment)
    return {'plan': plan, 'runs': runs, 'common': common, 'comparison': comparison,
            'behavior': behavior, 'distillation': distillation,
            'artifacts': list(artifacts.rows.values())}


TEMPLATE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Legendary Dungeon — learning study</title>
<style>
:root{color-scheme:dark;--bg:#0e1420;--panel:#172131;--line:#34445c;--text:#edf2fa;--muted:#b7c5d9;--accent:#80d4c8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,sans-serif}main{max-width:1220px;margin:auto;padding:38px 26px 80px}h1{font-size:clamp(28px,4vw,44px);line-height:1.1;margin:10px 0 20px}h2{font-size:23px;margin-top:0}p{max-width:92ch}.muted,small{color:var(--muted)}.eyebrow{letter-spacing:.1em;text-transform:uppercase;color:var(--accent);font-size:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;margin:24px 0}.controls{display:flex;flex-wrap:wrap;gap:18px;align-items:center}label{font-size:14px}select,button{background:var(--bg);color:var(--text);padding:8px 12px;border:1px solid var(--line);border-radius:7px}button{cursor:pointer}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:12px 10px}th{color:var(--muted);font-size:12px;letter-spacing:.02em}td.num{font-variant-numeric:tabular-nums;white-space:nowrap}a{color:#9ed6ff}code{overflow-wrap:anywhere;font-size:12px}summary{cursor:pointer}svg{display:block;width:100%;height:auto;min-height:240px;margin-top:18px}svg text{fill:var(--muted);font:12px system-ui}.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:14px;margin:10px 0}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:7px}.tag{color:var(--accent)}#hover{min-height:27px;color:var(--muted);font-size:14px}.note{border-left:3px solid var(--accent);padding-left:15px}.stats{display:flex;gap:28px;flex-wrap:wrap}.stats strong{font-size:28px;display:block}.stats span{font-size:13px;color:var(--muted)}@media(max-width:650px){main{padding:25px 14px}.card{padding:15px}th,td{padding:10px 7px}}
</style><main>
<div class="eyebrow">Shakes &amp; Fidget · simulator research</div><h1>What did the learning policy learn?</h1>
<p>This study trains a policy to maximize completed dungeon runs during one seven-day event. It sees the current visible game state, legal actions and remaining budget. Death and finishing room 100 continue the same event; only completed runs earn training reward.</p>
<p class="note">These are results from a simulator with uncertain mechanics. The study compares the tested policies; it does not establish the best possible strategy or predict the live game exactly.</p>
<section class="card"><h2>Training progress</h2><div id="totals" class="stats"></div><p class="muted">A transition is one action. A completed training event is a full simulated deadline, not one dungeon clear. Teacher cloning (BC) and reinforcement learning (PPO) are counted separately.</p><div class="scroll"><table id="runs"></table></div></section>
<section class="card"><h2>Validation learning curves</h2><p class="muted">Each run repeatedly uses its own fixed validation cohort to choose a checkpoint. These curves are selection data. They are not the final comparison, and differences between starts also include different validation cohorts.</p><div class="controls"><label>Mushroom budget <select id="budget"></select></label><label>Horizontal axis <select id="axis"><option value="steps">PPO transitions</option><option value="events">Completed PPO events</option></select></label><label><input type="checkbox" id="intervals" checked> Show sampling intervals</label></div><div id="legend" class="legend"></div><svg id="curve" viewBox="0 0 1100 420" role="img" aria-label="Validation completions over PPO training"></svg><div id="hover">Hover or focus a point to inspect its validation estimate.</div><p class="muted">The point at zero PPO transitions is measured after cloning, when cloning was enabled. Intervals describe sampling variation within a validation cohort; repeated checkpoint selection makes them unsuitable as evidence of final superiority.</p><details><summary>Teacher cloning curve</summary><p class="muted">Cloning loss measures agreement with the prior rule-based teacher. Lower loss is not itself evidence of more completed dungeon runs.</p><svg id="bc" viewBox="0 0 1100 340" role="img" aria-label="Cloning loss over teacher transitions"></svg></details></section>
<section class="card"><h2>Common validation selection</h2><div id="common"></div></section>
<section class="card"><h2>Independent final comparison</h2><div id="final"></div><p class="muted">Completion intervals use the mean ± 1.96 standard errors. The difference interval is computed from learner minus teacher on matched event indices. Equal seeds match the starting random stream; different actions can consume randomness differently. No invalid or safety-truncated event is accepted.</p></section>
<section class="card" id="behavior-card" hidden><h2>What the selected policy does</h2><p>These observations describe actions on states visited by the learner in a separate diagnostic cohort. The teacher is asked what it would do in those same states. They do not show whether any individual action caused better performance, or establish an optimal rule.</p><div id="behavior-summary"></div><div class="controls"><label>Decision type <select id="behavior-kind"></select></label><label>Budget <select id="behavior-budget"></select></label><label>Minimum observations <select id="behavior-min"><option>1</option><option selected>20</option><option>100</option></select></label></div><p id="behavior-count" class="muted"></p><div class="scroll"><table id="behavior-table"></table></div><p class="muted">Rows are ordered by observed decisions. Repeated shop rerolls and recovery actions can contribute several decisions from the same shop. Combat, barrel and stone tables use committed choices; their counts are not independent samples.</p></section>
<section class="card" id="distillation-card" hidden><h2>Experimental extracted rules</h2><p>Small decision trees approximate the selected neural policy using readable conditions for each game phase. High action agreement can still produce a poor strategy: one changed decision can alter the rest of an event. These rules remain experimental, rather than a recommended playing guide.</p><div id="distillation-summary"></div><div class="scroll"><table id="distillation-loss"></table></div><p id="distillation-threshold" class="muted"></p><details><summary>Agreement and rule counts by phase</summary><div class="scroll"><table id="distillation-phases"></table></div><p id="distillation-sampling" class="muted"></p></details><details><summary>Read the extracted rules</summary><p id="distillation-fallback" class="muted"></p><pre id="distillation-rules" style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px"></pre></details></section>
<section class="card"><h2>What this does and does not show</h2><ul><li>Stone offers remain random. The learner chooses among the offers it receives.</li><li>The objective is completed runs before the deadline, with separate reported mushroom budgets. Gold, item quality and legendary drops are not part of the reward.</li><li>Training observations contain no future rooms, random-generator state or event seeds.</li><li>Cloned starts initially learn the previous teacher; PPO can subsequently choose different actions. The fresh start has no cloning phase.</li><li>Final results must not be used to tune this checkpoint. Uncertain room, damage and reward assumptions require separate sensitivity checks and live-game data.</li><li>Sampling intervals omit simulator error, training randomness and the uncertainty from choosing among strategies.</li></ul></section>
<section class="card"><h2>Reproduction and evidence</h2><p class="muted">The report embeds its figures and data. The links below open the underlying local artifacts when kept alongside the study. Recorded hashes identify the exact inputs used to render this page.</p><button id="download">Download embedded report data</button><details><summary>Study plan</summary><pre id="plan" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details><details><summary>Artifact paths and SHA256 hashes</summary><div class="scroll"><table id="artifacts"></table></div></details><details><summary>Training source fingerprints</summary><div id="sources"></div></details></section>
</main><script id="study-data" type="application/json">__DATA__</script><script>
'use strict';const D=JSON.parse(document.getElementById('study-data').textContent),$=id=>document.getElementById(id),colors=['#80d4c8','#d5a6ff','#ffc576','#7db4ff','#ff929d'];
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,n=3)=>Number(x).toLocaleString(undefined,{minimumFractionDigits:n,maximumFractionDigits:n});const count=x=>Number(x).toLocaleString();
const ci=s=>`${fmt(s.mean)} <small>[${fmt(s.normal_95_ci[0])}, ${fmt(s.normal_95_ci[1])}]</small>`;
function table(id,heads,rows){$(id).innerHTML='<thead><tr>'+heads.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(x=>'<td class="num">'+x+'</td>').join('')+'</tr>').join('')+'</tbody>'}
let sum=k=>D.runs.reduce((s,r)=>s+r[k],0);$('totals').innerHTML=[['BC transitions',sum('bc_steps')],['PPO transitions',sum('ppo_steps')],['BC events completed',sum('bc_events')],['PPO events completed',sum('ppo_events')]].map(([s,n])=>`<div><strong>${count(n)}</strong><span>${s}</span></div>`).join('');
table('runs',['Start','Status','BC transitions / events','PPO transitions / events','Selected PPO step','Failed validation groups'],D.runs.map(r=>[esc(r.name),esc(r.status),`${count(r.bc_steps)} / ${count(r.bc_events)}`,`${count(r.ppo_steps)} / ${count(r.ppo_events)}`,r.best?count(r.best.steps):'Pending',count(r.points.filter(p=>p.failed).length)]));
const budgets=[...new Set(D.runs.flatMap(r=>r.points.map(p=>p.budget)))].sort((a,b)=>a-b);$('budget').innerHTML=budgets.map(x=>`<option value="${x}">${x===0?'0 — free play':x}</option>`).join('');
$('legend').innerHTML=D.runs.map((r,i)=>`<span><i class="dot" style="background:${colors[i%colors.length]}"></i>${esc(r.name)}</span>`).join('');
function plot(id,series,xlabel,ylabel,height){const svg=$(id),W=1100,H=height,L=68,R=28,T=25,B=55,pts=series.flatMap(s=>s.points);if(!pts.length){svg.innerHTML='<text x="70" y="90">No observations yet.</text>';return}let xmax=Math.max(1,...pts.map(p=>p.x)),ymin=Math.min(0,...pts.map(p=>p.low??p.y)),ymax=Math.max(.05,...pts.map(p=>p.high??p.y));ymax+=.05*(ymax-ymin||1);const X=x=>L+x/xmax*(W-L-R),Y=y=>H-B-(y-ymin)/(ymax-ymin)*(H-T-B);let out='';for(let i=0;i<=5;i++){let y=ymin+i/5*(ymax-ymin),x=i/5*xmax;out+=`<path d="M${L} ${Y(y)}H${W-R}" stroke="#34445c"/><text x="${L-10}" y="${Y(y)+4}" text-anchor="end">${fmt(y,2)}</text><text x="${X(x)}" y="${H-B+24}" text-anchor="middle">${count(Math.round(x))}</text>`}out+=`<text x="${W/2}" y="${H-8}" text-anchor="middle">${esc(xlabel)}</text><text x="${L}" y="15">${esc(ylabel)}</text>`;series.forEach(s=>{out+=`<polyline fill="none" stroke="${s.color}" stroke-width="2.5" points="${s.points.map(p=>`${X(p.x)},${Y(p.y)}`).join(' ')}"/>`;s.points.forEach(p=>{if(p.low!=null&&$('intervals').checked)out+=`<path d="M${X(p.x)} ${Y(p.low)}V${Y(p.high)}" stroke="${s.color}" opacity=".6"/>`;out+=`<circle tabindex="0" cx="${X(p.x)}" cy="${Y(p.y)}" r="5" fill="${s.color}" data-label="${esc(p.label)}"><title>${esc(p.label)}</title></circle>`})});svg.innerHTML=out;svg.querySelectorAll('circle').forEach(c=>['mouseenter','focus'].forEach(e=>c.addEventListener(e,()=>$('hover').textContent=c.dataset.label)))}
function redraw(){let b=+$('budget').value,a=$('axis').value;plot('curve',D.runs.map((r,i)=>({color:colors[i%colors.length],points:r.points.filter(p=>p.budget===b&&p.estimate&&!p.failed).map(p=>({x:p[a],y:p.estimate.mean,low:p.estimate.normal_95_ci[0],high:p.estimate.normal_95_ci[1],label:`${r.name}: ${fmt(p.estimate.mean)} clears [${p.estimate.normal_95_ci.map(v=>fmt(v)).join(', ')}], ${count(p.steps)} PPO transitions, ${count(p.events)} PPO events, ${count(p.validation_events)} validation events`}))})),a==='steps'?'PPO transitions':'Completed PPO events','Mean event completions',420);plot('bc',D.runs.map((r,i)=>({color:colors[i%colors.length],points:r.cloning.map(p=>({x:p.steps,y:p.loss,label:`${r.name}: loss ${fmt(p.loss,4)}, ${count(p.steps)} BC transitions, ${count(p.events)} BC events`}))})),'BC transitions','Cloning loss',340)}['budget','axis','intervals'].forEach(id=>$(id).addEventListener('change',redraw));redraw();
if(D.common){$('common').innerHTML=`<p>Selected <strong>${esc(D.common.selected.name)}</strong> using ${count(D.common.events_per_budget)} events per budget on a fresh common cohort. Selection seed: <code>${esc(D.common.seed)}</code>. Final outcomes were not used for this choice.</p><p class="muted">${esc(D.common.selection_rule)}</p><div class="scroll"><table id="common-table"></table></div>`;table('common-table',['Candidate','Mean selection score','Checkpoint SHA256'],D.common.candidates.map(c=>[esc(c.name),fmt(c.selection_score),'<code>'+esc(c.checkpoint_sha256)+'</code>']))}else $('common').innerHTML='<p class="muted">Common validation has not been recorded. No cross-start winner is declared.</p>';
if(D.comparison.length){let first=D.comparison[0];$('final').innerHTML=`<p>${count(first.events)} matched events per budget; final seed <code>${first.cohort.root_seed}</code>, namespace <code>${first.cohort.namespace_byte}</code>. Positive differences favor the learner.</p><div class="scroll"><table id="final-table"></table></div><h3>Mushrooms spent per event</h3><div class="scroll"><table id="spending"></table></div>`;table('final-table',['Budget','Learner completions [95% interval]','Teacher completions [95% interval]','Paired difference [95% interval]','Events'],D.comparison.map(r=>[r.budget===0?'0 — free':count(r.budget),ci(r.learner),ci(r.teacher),ci(r.paired_difference),count(r.events)]));table('spending',['Budget','Learner total','Teacher total','Learner recovery / rerolls','Teacher recovery / rerolls'],D.comparison.map(r=>[count(r.budget),fmt(r.learner_spending.mushrooms,2),fmt(r.teacher_spending.mushrooms,2),fmt(r.learner_spending.recovery_mushrooms,2)+' / '+fmt(r.learner_spending.reroll_mushrooms,2),fmt(r.teacher_spending.recovery_mushrooms,2)+' / '+fmt(r.teacher_spending.reroll_mushrooms,2)]))}else $('final').innerHTML='<p class="muted">The independent final comparison is not available yet. Validation scores are not substituted for final results.</p>';
if(D.behavior){$('behavior-card').hidden=false;const A=D.behavior;$('behavior-summary').innerHTML=`<p class="muted">Diagnostic seed <code>${esc(A.cohort.seed)}</code>, namespace <code>${A.cohort.namespace_byte}</code>; ${count(A.cohort.events_per_budget)} events per budget.</p>`;$('behavior-kind').innerHTML=Object.keys(A.tables).map(k=>`<option value="${esc(k)}">${esc(k)}</option>`).join('');$('behavior-budget').innerHTML=A.cohort.budgets.map(b=>`<option value="${b}">${b}</option>`).join('');function actionlist(map,n){return Object.entries(map).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${esc(k)}: ${fmt(100*v/Math.max(1,n),1)}% (${count(v)})`).join('<br>')}function behavior(){let kind=$('behavior-kind').value,rows=A.tables[kind].filter(r=>r.budget===+$('behavior-budget').value&&r.decisions>=+$('behavior-min').value).sort((a,b)=>b.decisions-a.decisions);$('behavior-count').textContent=`${count(rows.length)} matching groups; showing the first ${count(Math.min(500,rows.length))}.`;table('behavior-table',['Observed state','Decisions','Learner actions','Teacher actions at same states','Disagreement'],rows.slice(0,500).map(r=>{let dimensions=['phase','stage','encounter','hp_bin','keys','key_bin','one_hit','owned_gems','run_type','offers'].filter(k=>r[k]!=null).map(k=>`${esc(k)}: ${esc(typeof r[k]==='object'?JSON.stringify(r[k]):r[k])}`).join('<br>');return[dimensions||'All recorded states',count(r.decisions),actionlist(r.picked||r.actions,r.decisions),actionlist(r.teacher_picked||r.teacher_actions,r.decisions),fmt(r.disagreement_rate*100,1)+'%']}))}['behavior-kind','behavior-budget','behavior-min'].forEach(id=>$(id).addEventListener('change',behavior));behavior()}
if(D.distillation){$('distillation-card').hidden=false;const X=D.distillation,bad=X.losses.filter(r=>r.large_observed_loss);$('distillation-summary').innerHTML=`<p><strong>${count(X.rule_count)} leaf rules</strong>; ${fmt(X.held_out.agreement*100,1)}% agreement on ${count(X.held_out.samples)} held-out decisions. <a href="${esc(X.readable_rules_href)}">Open readable rules</a>.</p><p>Complete-event test: ${count(X.gameplay_cohort.events_per_budget)} matched events per budget, separate namespace <code>${X.gameplay_cohort.namespace_byte}</code>. Positive loss means the extracted rules complete fewer runs than the neural policy.</p>`+(bad.length?`<p class="note"><strong>Large observed losses at budgets ${bad.map(r=>count(r.budget)).join(', ')}.</strong> This extraction should not replace the neural policy or be presented as a recommended guide.</p>`:'<p class="muted">No loss crossed the descriptive flag below. This alone does not establish that the extracted rules preserve performance.</p>');table('distillation-loss',['Budget','Neural mean','Rules mean','Paired completion loss [95% interval]','Relative loss','Flag'],X.losses.map(r=>[count(r.budget),fmt(r.neural_completions_mean),fmt(r.tree_completions_mean),ci(r.paired_completion_loss),r.relative_loss==null?'—':fmt(r.relative_loss*100,1)+'%',r.large_observed_loss?'Large observed loss':r.paired_completion_loss.normal_95_ci[0]>0?'Measured loss':'Interval includes no loss']));$('distillation-threshold').textContent=X.large_loss_threshold+' Intervals are paired sampling intervals and omit simulator error.';table('distillation-phases',['Phase','Leaf rules','Fitting decisions','Held-out decisions','Agreement'],X.phases.map(p=>[esc(p.phase),count(p.rules),count(p.fit_samples),p.agreement?count(p.agreement.samples):'0',p.agreement&&p.agreement.agreement!=null?fmt(p.agreement.agreement*100,1)+'%':'No samples']));$('distillation-sampling').textContent=X.label_sampling+' Decisions within an event are correlated; these agreement percentages are descriptive.';$('distillation-fallback').textContent=X.fallback+' Displayed conditions may be rounded; the tree JSON retains exact thresholds. Action frequencies are imitation data, not success probabilities.';$('distillation-rules').textContent=X.readable_rules}
$('plan').textContent=JSON.stringify(D.plan,null,2);table('artifacts',['Artifact','SHA256'],D.artifacts.map(a=>[`<a href="${esc(a.href)}">${esc(a.path)}</a>`,'<code>'+esc(a.sha256)+'</code>']));$('sources').innerHTML=D.runs.filter(r=>Object.keys(r.sources).length).map(r=>`<h3>${esc(r.name)}</h3><div class="scroll"><table><thead><tr><th>Recorded source</th><th>SHA256 at training start</th></tr></thead><tbody>${Object.entries(r.sources).map(([p,h])=>'<tr><td>'+esc(p)+'</td><td><code>'+esc(h)+'</code></td></tr>').join('')}</tbody></table></div>`).join('');$('download').addEventListener('click',()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(D,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=u;a.download='rl-study-report-data.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)});
</script></html>'''


def render(data):
    # JSON is inert data, and escaping '<' also prevents </script> injection.
    encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).replace('<', '\\u003c')
    return TEMPLATE.replace('__DATA__', encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--overwrite', action='store_true', help='Replace an earlier rendered report after validating all inputs')
    args = parser.parse_args(argv)
    if args.output.exists() and not args.overwrite:
        raise FileExistsError(args.output)
    data = build_report(args.study, args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + '.tmp')
    temporary.write_text(render(data), encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({'output': str(args.output), 'runs': len(data['runs']), 'final_comparisons': len(data['comparison'])}))


if __name__ == '__main__':
    main()
