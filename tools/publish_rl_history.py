"""Validate frozen selections and publish the offered-history training results."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics

import train_rl
from finetune_rl_study import read, once, copy_checked, validate_evaluation
from train_rl_history_study import BUDGETS, select_candidates

ROOT = Path(__file__).resolve().parents[1]
CSV_FIELDS = ['policy', 'budget', 'event_index', 'completions', 'mushrooms', 'recovery_mushrooms',
              'reroll_mushrooms', 'elapsed_hours', 'actions', 'invalid_actions', 'terminated', 'truncated']


def paired_comparison(old, new, budget, label):
    a = sorted((r for r in old['outcomes'] if r['budget'] == budget), key=lambda r: r['event_index'])
    b = sorted((r for r in new['outcomes'] if r['budget'] == budget), key=lambda r: r['event_index'])
    if not a or [r['event_index'] for r in a] != [r['event_index'] for r in b]:
        raise ValueError('Matched comparisons require identical event indices')
    delta = [y['completions'] - x['completions'] for x, y in zip(a, b)]
    change = train_rl.interval(delta)
    change['normal_99_ci'] = [change['mean'] - 2.575829 * change['standard_error'],
                              change['mean'] + 2.575829 * change['standard_error']]
    def summarize(rows):
        return {'completions': train_rl.interval([r['completions'] for r in rows]),
                **{f'mean_{k}': statistics.mean(r[k] for r in rows)
                   for k in ('mushrooms', 'recovery_mushrooms', 'reroll_mushrooms')},
                'probability_at_least': {str(n): statistics.mean(r['completions'] >= n for r in rows)
                                         for n in (1, 2, 3, 4, 5, 6, 8, 10)}}
    baseline, candidate = summarize(a), summarize(b)
    return {'budget': budget, 'candidate': label, 'events': len(a), 'baseline': baseline,
            'candidate_result': candidate, 'paired_improvement': change,
            'relative_gain_percent': 100 * change['mean'] / baseline['completions']['mean']}


def check_csv(path, results):
    expected = {(name, row['budget'], row['event_index']): row
                for name, result in results.items() for row in result['outcomes']}
    seen = set()
    with Path(path).open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError('Published CSV has missing or reordered columns')
        for row in reader:
            key = (row['policy'], int(row['budget']), int(row['event_index']))
            if key in seen or key not in expected:
                raise ValueError('Published CSV has duplicate or unexpected event indices')
            seen.add(key)
            original = expected[key]
            for field, value in row.items():
                if field == 'policy':
                    continue
                actual = original[field]
                if isinstance(actual, bool):
                    valid = value == str(actual)
                else:
                    valid = float(value) == actual
                if not valid:
                    raise ValueError(f'Published CSV differs from verified result: {field}')
    if seen != set(expected):
        raise ValueError('Published CSV is incomplete')


def verify_and_summarize(out):
    plan, selection = read(out / 'study-plan.json'), read(out / 'selection.json')
    if train_rl.sha(out / 'sfld_rl.dll') != plan['library_sha256'] or train_rl.sha(out / 'synthetic.profile') != plan['profile_sha256']:
        raise ValueError('Frozen simulator inputs changed')
    for relative, expected in read(out / 'source-manifest.json').items():
        if train_rl.sha(out / 'source' / relative) != expected:
            raise ValueError(f'Frozen source changed: {relative}')
    rescored = []
    for c in selection['candidates']:
        result = read(out / c['evaluation_file'])
        validate_evaluation(result, plan, plan['selection_validation']['seed'], 'validation',
                            plan['selection_validation']['events'], c['checkpoint_sha256'])
        if train_rl.sha(out / c['checkpoint']) != c['checkpoint_sha256']:
            raise ValueError('Candidate checkpoint changed')
        score = statistics.mean(r['completions'] for r in result['outcomes'])
        means = {str(s['budget']): s['completions']['mean'] for s in result['summaries']}
        if c['selection_score'] != score or c['budget_means'] != means:
            raise ValueError('Selection scores do not match validation')
        rescored.append(c)
    expected = select_candidates(rescored)
    if selection['global'] != expected['global'] or selection['per_budget'] != expected['per_budget']:
        raise ValueError('Selection differs from the declared validation rule')
    chosen = {c['name']: c for c in [selection['candidates'][0], selection['global'], *selection['per_budget'].values()]}
    if selection['candidates'][0]['name'] != 'parent':
        raise ValueError('Baseline is not the first eligible candidate')
    results = {}
    for name, c in chosen.items():
        result = read(out / f'final-{name}.json')
        validate_evaluation(result, plan, plan['final']['seed'], 'final', plan['final']['events'], c['checkpoint_sha256'])
        results[name] = result
    totals = read(out / 'training-totals.json')
    if totals['new_ppo_steps'] != sum(j['steps'] for j in totals['jobs']) or totals['new_ppo_steps'] != sum(j['steps'] for j in plan['variants']) + plan['extension']['steps']:
        raise ValueError('Training total differs from plan')
    for job in totals['jobs']:
        actual = read(out / job['name'] / 'training-summary.json')
        if actual['state']['steps'] != job['steps'] or actual['state']['completed_training_events'] != job['completed_training_events']:
            raise ValueError('Training event count differs from recorded state')
    global_name = selection['global']['name']
    summary = {'schema': 'sfld-history-training-results-v1', 'training': totals,
        'global_policy': global_name, 'per_budget': {b: c['name'] for b, c in selection['per_budget'].items()},
        'global_comparisons': [paired_comparison(results['parent'], results[global_name], b, global_name) for b in BUDGETS],
        'budget_comparisons': [paired_comparison(results['parent'], results[selection['per_budget'][str(b)]['name']], b,
                              selection['per_budget'][str(b)]['name']) for b in BUDGETS],
        'global_vs_budget_routing': [paired_comparison(results[selection['per_budget'][str(b)]['name']],
                                    results[global_name], b, global_name) for b in BUDGETS],
        'final_seed': plan['final']['seed'], 'final_events_per_cap_per_policy': plan['final']['events'],
        'unique_final_policies': list(results),
        'raw_final_evaluations': {name: {'path': str(out / f'final-{name}.json'),
                                 'sha256': train_rl.sha(out / f'final-{name}.json')}
                                  for name in results},
        'unique_final_event_evaluations': sum(len(r['outcomes']) for r in results.values()),
        'library_sha256': plan['library_sha256'], 'profile_sha256': plan['profile_sha256'],
        'invalid_actions': sum(r['invalid_actions'] for result in results.values() for r in result['outcomes']),
        'truncations': sum(r['truncated'] for result in results.values() for r in result['outcomes']),
        'interval_scope': 'Monte Carlo uncertainty within the frozen simulator; excludes errors in game mechanics.',
        'multiplicity': '99% marginal paired intervals provide an approximate Bonferroni 95% family bound across five caps for one declared comparison family.'}
    if (out / 'behavior-parent-summary.json').exists() and (out / 'behavior-selected-summary.json').exists():
        before, after = [read(out / f'behavior-{name}-summary.json') for name in ('parent', 'selected')]
        if (before['cohort'] != after['cohort'] or
            before['checkpoint']['sha256'] != plan['parent_checkpoint_sha256'] or
            after['checkpoint']['sha256'] != selection['global']['checkpoint_sha256']):
            raise ValueError('Behavior audit does not compare the declared baseline and overall policy')
        def behavior_totals(data):
            return {'zero_key_rerolls': sum(r['shop_rerolls_at_zero_keys'] for r in data['per_budget']),
                    'shop_rerolls': sum(r['shop_rerolls'] for r in data['per_budget']),
                    'traveler_barrels': {a: sum(r['traveler_barrels'][a] for r in data['per_budget'])
                                         for a in ('interact', 'skip')},
                    'events': data['accounting']['completed_events']}
        summary['behavior_audit'] = {'baseline': behavior_totals(before), 'overall_policy': behavior_totals(after),
            'cohort': after['cohort'], 'scope': 'Descriptive choices on each policy\'s own visited states; overall policy at every cap, including cap 50.'}
    return plan, selection, summary, results


def markdown(summary, artifacts):
    t = summary['training']
    rows = ['# Further PPO training with offered-stone history — 2026-10-11', '',
        f"Added **{t['new_ppo_steps']:,} PPO decisions** across three branches and a validation-selected continuation, comprising **{t['completed_training_events']:,} completed seven-day training events**. In-flight events at the end of a branch are not counted as completed events.", '',
        'The objective remains maximum completions in 168 hours, with separate gross mushroom caps. Both the baseline and new policies use the same corrected all-offered stone exclusion. Engine 0.6.7 appends 23 already-seen stone indicators to the 437 original observations; the action menu and game mechanics are unchanged from 0.6.6.', '',
        '## Independent final comparison', '',
        'The overall policy below was selected using validation data before the final cohort was opened. Each row uses 5,000 matched, previously unused events per policy. The baseline is the preceding neural policy transferred without changing its decisions, evaluated again on these seeds.', '',
        '| Weekly mushroom cap | Previous policy | New overall policy | Change | Paired 99% interval | Selected branch |',
        '|---:|---:|---:|---:|---:|---|']
    for r in summary['global_comparisons']:
        ci = r['paired_improvement']['normal_99_ci']
        rows.append(f"| {r['budget']} | {r['baseline']['completions']['mean']:.4f} | {r['candidate_result']['completions']['mean']:.4f} | {r['relative_gain_percent']:+.2f}% | [{ci[0]:+.4f}, {ci[1]:+.4f}] | {r['candidate']} |")
    rows += ['', f'![Final policy comparison]({artifacts}/final-comparison.png)', '',
             'Intervals describe additional completed runs per week. They cover simulation sampling error, not uncertainty in the real game. A positive interval supports an improvement in this model; an interval spanning zero is inconclusive.', '',
             f"The single policy selected across all budgets is **{summary['global_policy']}**. Its means at caps 0 / 20 / 50 / 100 / 500 are **" + ' / '.join(f"{r['candidate_result']['completions']['mean']:.4f}" for r in summary['global_comparisons']) + '**.', '',
             '## Spending and completion probability', '',
             '| Cap | Mean spent | On refills | On rerolls | Chance of at least 3 clears |',
             '|---:|---:|---:|---:|---:|']
    for r in summary['global_comparisons']:
        n = r['candidate_result']
        rows.append(f"| {r['budget']} | {n['mean_mushrooms']:.2f} | {n['mean_recovery_mushrooms']:.2f} | {n['mean_reroll_mushrooms']:.2f} | {100*n['probability_at_least']['3']:.2f}% |")
    rows += ['', '## Separate budget-selection check', '',
             'The predeclared budget-specific selection is preserved below. It was not reranked after the final test. The overall checkpoint was also selected before final testing and remains the default.', '',
             '| Cap | Budget-selected branch | Final mean | Overall-policy mean |',
             '|---:|---|---:|---:|']
    for r, g in zip(summary['budget_comparisons'], summary['global_comparisons']):
        rows.append(f"| {r['budget']} | {r['candidate']} | {r['candidate_result']['completions']['mean']:.4f} | {g['candidate_result']['completions']['mean']:.4f} |")
    for r, g, difference in zip(summary['budget_comparisons'], summary['global_comparisons'], summary['global_vs_budget_routing']):
        if r['candidate'] != g['candidate']:
            ci = difference['paired_improvement']['normal_99_ci']
            rows += ['', f"At cap {r['budget']}, the separate choice did not outperform the overall policy in the final cohort. Overall minus budget-selected completions: {difference['paired_improvement']['mean']:+.4f}, paired 99% interval [{ci[0]:+.4f}, {ci[1]:+.4f}]. This is retained as a failed validation choice, rather than relabeling a policy after viewing its test result."]
    if 'behavior_audit' in summary:
        behavior = summary['behavior_audit']; a, b = behavior['baseline'], behavior['overall_policy']
        opened = lambda d: 100 * d['traveler_barrels']['interact'] / sum(d['traveler_barrels'].values())
        rows += ['', '## Observed behavior of the overall policy', '',
            f"On a separate matched diagnostic cohort of {a['events']} complete events per policy (128 per cap), zero-key shop rerolls fell from **{a['zero_key_rerolls']:,} to {b['zero_key_rerolls']:,}**. Total shop rerolls fell from {a['shop_rerolls']:,} to {b['shop_rerolls']:,}.", '',
            f"With Time Traveler owned, the fraction of barrel decisions choosing interaction fell from **{opened(a):.1f}% to {opened(b):.1f}%**. These counts describe each policy's visited states and do not isolate the causal contribution of either behavior to completion gains.", '',
            'The audit uses the overall policy at all caps, matching the main performance table. Some zero-key rerolls remain; the learner is not a proven optimal decision table.', '',
            f'[Previous-policy audit]({artifacts}/behavior-parent-summary.json) and [overall-policy audit]({artifacts}/behavior-selected-summary.json).']
    rows += ['', '## Protocol and artifacts', '',
        '- Three 16,777,216-decision branches varied learning rate, exploration and how far rewards influence earlier decisions. A 33,554,432-decision continuation started from the winner on a separate common validation cohort.',
        '- Branch checkpoint selection used 256 events per cap. Common validation used 768; final selection used 1,536. The unchanged baseline remained eligible throughout. Exact ties keep the earlier candidate.',
        '- Final seed: 9703001, namespace 0xC3, event indices 0–4,999 per cap. Training, validation, identity and diagnostic roots have separate compressed seed fingerprints, also checked against previous studies.',
        '- Migration zero-filled the new input coefficients and Adam moments while retaining all old weights, moments and counters. A 320-event live comparison against the archived 0.6.6 engine matched decisions, original observations and outcomes exactly.',
        f"- Final accounting: {summary['unique_final_event_evaluations']:,} policy-event evaluations, {summary['invalid_actions']} invalid actions and {summary['truncations']} safety truncations. Different policies share event indices; these are not all independent random worlds.",
        '- No reward was added for keys, health, stone choice or saving mushrooms. No stones were removed from the available pools.',
        '- This study combines more training with access to offered history. It does not isolate how much improvement each contributes, and it does not prove a globally optimal strategy.', '',
        f'![Internal training validation]({artifacts}/training-curves.png)', '',
        f"[Results JSON]({artifacts}/summary.json), [indexed final outcomes]({artifacts}/outcomes.csv), [frozen selection]({artifacts}/selection.json), [training plan]({artifacts}/study-plan.json), [checkpoint routing]({artifacts}/policy-budgets.json), [global checkpoint]({artifacts}/selected-checkpoint.pt).", '',
        'Reproduce the declared run from the matching source tree:', '', '```powershell',
        'out/rl-env/Scripts/python.exe tools/train_rl_history_study.py --output out/rl-history-reproduction',
        'out/rl-env/Scripts/python.exe tools/publish_rl_history.py --study out/rl-history-reproduction --output docs/rl-history-reproduction',
        '```', '',
        'Figures use `tools/plot_rl_history.py --study docs/rl-history-reproduction` in a separate optional Matplotlib environment. PNG, SVG and PDF exports retain the same verified data.', '',
        'The archived DLL and profile are required for evaluating these checkpoints. Older 437-input checkpoints must use their archived engine or an explicit validated migration; the strict trainer rejects silent schema changes.', '']
    return '\n'.join(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study', type=Path, default=ROOT / 'out/rl-history-2026-10-11')
    p.add_argument('--output', type=Path, default=ROOT / 'docs/rl-history-2026-10-11')
    args = p.parse_args()
    out, dest = args.study.resolve(), args.output.resolve()
    plan, selection, summary, results = verify_and_summarize(out)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ('study-plan.json', 'selection.json', 'common-selection.json', 'migration-identity.json',
                 'parent-checkpoint.migration.json', 'source-manifest.json', 'training-totals.json',
                 'sfld_rl.dll', 'synthetic.profile', 'parent-checkpoint.pt'):
        copy_checked(out / name, dest / name)
    for c in selection['candidates']:
        copy_checked(out / c['evaluation_file'], dest / c['evaluation_file'])
        copy_checked(out / c['checkpoint'], dest / c['checkpoint'])
    for job in summary['training']['jobs']:
        for name in ('run.json', 'curve.jsonl', 'best.json', 'latest.json', 'training-summary.json'):
            copy_checked(out / job['name'] / name, dest / job['name'] / name)
        for suffix in ('invocation.json', 'status.json'):
            copy_checked(out / (job['name'] + '-' + suffix), dest / (job['name'] + '-' + suffix))
    for name in ('parallel-exploration.json', 'behavior-parent-summary.json', 'behavior-selected-summary.json', 'figure-requirements.txt'):
        if (out / name).exists():
            copy_checked(out / name, dest / name)
    registry = {'schema': 'sfld-policy-budget-routing-v1', 'global': 'selected-checkpoint.pt', 'budgets': {}}
    copy_checked(out / selection['global']['checkpoint'], dest / 'selected-checkpoint.pt')
    for b, c in selection['per_budget'].items():
        target = 'policies/' + c['name'] + '.pt'
        copy_checked(out / c['checkpoint'], dest / target)
        registry['budgets'][b] = {'checkpoint': target, 'sha256': c['checkpoint_sha256']}
    once(dest / 'policy-budgets.json', registry)
    once(dest / 'summary.json', summary)
    fields = CSV_FIELDS
    csv_path = dest / 'outcomes.csv'
    if not csv_path.exists():
        with csv_path.open('x', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
            for name, result in results.items():
                for row in result['outcomes']:
                    writer.writerow({'policy': name, **{k: row[k] for k in fields if k != 'policy'}})
    check_csv(csv_path, results)
    for relative in read(out / 'source-manifest.json'):
        copy_checked(out / 'source' / relative, dest / 'source' / relative)
    copy_checked(Path(__file__), dest / 'publish_rl_history.py')
    for name in ('plot_rl_history.py', 'summarize_rl_history_behavior.py'):
        copy_checked(ROOT / 'tools' / name, dest / name)
    report = dest.parent / 'RL_HISTORY_2026_10_11.md'
    content = markdown(summary, dest.name)
    if report.exists() and report.read_text(encoding='utf-8') != content:
        raise RuntimeError('Refusing to overwrite a different report')
    report.write_text(content, encoding='utf-8')
    once(dest / 'verification.json', {'passed': True, 'summary_sha256': train_rl.sha(dest / 'summary.json'),
         'outcomes_sha256': train_rl.sha(csv_path), 'selection_sha256': train_rl.sha(dest / 'selection.json'),
         'report_sha256': train_rl.sha(report), 'publisher_sha256': train_rl.sha(__file__)})
    print(json.dumps({'published': str(dest), 'global': summary['global_policy'], 'budgets': summary['per_budget']}))


if __name__ == '__main__':
    main()
