"""Verify and archive the preselected Pendant adaptation experiment."""
from collections import Counter
import csv
import json
from pathlib import Path
import shutil

import benchmark_rl_shop_arena as study
import train_rl

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'out/pendant-adaptation-2026-10-11'
DEST = ROOT / 'docs/pendant-adaptation-2026-10-11'
CANDIDATES = ('parent', 'parent_priority', 'trained_control', 'trained_priority')
LABELS = ('Original', 'Original + priority', 'Further-trained control', 'Further-trained priority')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def copy_checked(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and train_rl.sha(source) != train_rl.sha(target):
        raise RuntimeError('Existing archive differs: ' + str(target))
    if not target.exists():
        shutil.copyfile(source, target)


def stone_rates(cells):
    totals = {}
    for cell in cells:
        for gem, counts in cell['stones'].items():
            totals.setdefault(gem, Counter()).update(counts)
    return [{'gem': gem, **counts, 'chosen_when_offered': counts['picked'] / counts['offered']}
            for gem, counts in sorted(totals.items(),
                                     key=lambda x: x[1]['picked'] / x[1]['offered'], reverse=True)]


def main():
    plan, selection = load(SOURCE / 'plan.json'), load(SOURCE / 'selection.json')
    budgets = [0, 20, 50, 100, 500, 1000]
    if (plan['budgets'] != budgets or plan['steps_per_branch'] != 16777216
            or plan['final'] != {'seed': 10003001, 'namespace': 0xDC,
                                 'events_per_cap': 2048, 'candidates': list(CANDIDATES)}):
        raise RuntimeError('Unexpected study plan')
    for path, fingerprint in plan['sources'].items():
        if train_rl.sha(ROOT / path) != fingerprint:
            raise RuntimeError('Source changed during study: ' + path)
        copy_checked(ROOT / path, DEST / 'source' / path)
    copy_checked(Path(__file__), DEST / 'source/tools' / Path(__file__).name)
    for filename in ('plan.json', 'selection.json'):
        copy_checked(SOURCE / filename, DEST / filename)

    training = {}
    for branch in plan['branches']:
        name = branch['name']
        path = SOURCE / name
        best, summary, run = load(path / 'best.json'), load(path / 'training-summary.json'), load(path / 'run.json')
        metadata = run['metadata']
        if (metadata['seed'] != str(branch['seed']) or metadata['budgets'] != budgets
                or metadata['finetune']['parent_sha256'] != plan['parent_sha256']
                or any(metadata[key] != plan['training'][key]
                       for key in ('n_envs', 'rollout_steps'))
                or metadata['validation_events'] != plan['training']['validation_events_per_cap']
                or run['validation_every'] != plan['training']['validate_every_updates']
                or any(run['ppo_config'][key] != plan['training'][key]
                       for key in ('learning_rate', 'entropy_coefficient', 'gae_lambda', 'target_kl'))):
            raise RuntimeError('Executed training differs from plan')
        curve = [json.loads(line) for line in (path / 'curve.jsonl').read_text().splitlines()]
        validations = [row for row in curve if row.get('validation_score') is not None]
        winner = max(validations, key=lambda row: (row['validation_score'], -row['steps']))
        if (summary['state']['steps'] != plan['steps_per_branch']
                or sum(row.get('completed_events', 0) for row in curve) != summary['state']['completed_training_events']
                or best['steps'] != winner['steps'] or best['selection_score'] != winner['validation_score']
                or Path(selection[name]['path'].replace('\\', '/')) != Path(name) / best['checkpoint']
                or {key: value for key, value in selection[name].items() if key != 'path'} != best
                or run['metadata']['stone_priority'] != branch['stone_priority']):
            raise RuntimeError('Training audit failed: ' + name)
        for row in validations:
            values = row['validation_summaries']
            if (len(values) != len(budgets) or [v['budget'] for v in values] != budgets
                    or any(v['failed_events'] or v['invalid_actions'] or v['truncations'] for v in values)
                    or abs(sum(v['completions']['mean'] for v in values) / len(values) - row['validation_score']) > 1e-12):
                raise RuntimeError('Validation selection failed')
        checkpoint = path / best['checkpoint']
        if train_rl.sha(checkpoint) != best['sha256']:
            raise RuntimeError('Checkpoint changed')
        saved, actor = train_rl.load_checkpoint(checkpoint, 'cpu')
        if (saved['state']['steps'] != best['steps']
                or getattr(actor, 'priority_mode', 'learned') != branch['stone_priority']):
            raise RuntimeError('Checkpoint priority mode did not survive loading')
        for filename in ('run.json', 'best.json', 'training-summary.json', 'curve.jsonl'):
            copy_checked(path / filename, DEST / name / filename)
        copy_checked(checkpoint, DEST / name / 'selected-checkpoint.pt')
        training[name] = {'summary': summary, 'selected_steps': best['steps'],
                          'selected_sha256': best['sha256'], 'selected_validation_score': best['selection_score'],
                          'priority_mode': branch['stone_priority'],
                          'validation_events': sum(v['events'] for row in validations for v in row['validation_summaries'])}

    all_rows, groups, cells, behavior, cell_hashes = [], [], {}, [], {}
    for candidate in CANDIDATES:
        path = SOURCE / ('final-' + candidate)
        final_plan = load(path / 'plan.json')
        policy = 'pendant_priority' if candidate == 'parent_priority' else 'baseline'
        checkpoint_hash = (plan['parent_sha256'] if candidate.startswith('parent')
                           else selection[candidate.removeprefix('trained_')]['sha256'])
        if (final_plan['seed'] != str(plan['final']['seed']) or final_plan['namespace'] != plan['final']['namespace']
                or final_plan['events_per_cell'] != 2048 or final_plan['budgets'] != budgets
                or [p['name'] for p in final_plan['policies']] != [policy]
                or final_plan['arena_legendary_rate'] != .0625
                or final_plan['hashes']['checkpoint'] != checkpoint_hash
                or final_plan['hashes']['library'] != plan['library_sha256']
                or final_plan['hashes']['base_profile'] != plan['profile_sha256']):
            raise RuntimeError('Final evaluation differs from frozen selection')
        for filename, key in (('benchmark_rl_shop_arena.py', 'runner'), ('shop_experiments.py', 'rules'),
                              ('stone_priority.py', 'priority'), ('scenario.profile', 'profile')):
            if train_rl.sha(path / filename) != final_plan['hashes'][key]:
                raise RuntimeError('Final evaluation source mismatch')
            copy_checked(path / filename, DEST / ('final-' + candidate) / filename)
        for filename in ('plan.json', 'summary.json'):
            copy_checked(path / filename, DEST / ('final-' + candidate) / filename)
        summaries = []
        for budget in budgets:
            filename = f'cap-{budget}-{policy}.json'
            cell_path = path / filename
            cell = load(cell_path)
            if cell['plan'] != final_plan:
                raise RuntimeError('Cell plan mismatch')
            rows = cell['outcomes']
            study.validate_rows(rows, 2048, budget, policy, .0625)
            summary = study.summarize(rows)
            if cell['summary'] != summary or sum(cell['shop_buys'].values()) != sum(r['shop_purchases'] for r in rows):
                raise RuntimeError('Cell summary or purchase count mismatch')
            if 'pendant' in cell['stones_by_run']['first']:
                raise RuntimeError('Pendant offered in the first run')
            for gem, counts in cell['stones'].items():
                for field in ('offered', 'picked'):
                    if sum(part.get(gem, {}).get(field, 0) for part in cell['stones_by_run'].values()) != counts[field]:
                        raise RuntimeError('Stone run-class partition mismatch')
            summaries.append(summary)
            completed = [r['first_completion_hours'] for r in rows if r['first_completion_hours'] >= 0]
            groups.append(dict(candidate=candidate, **summary,
                               first_completion_hours=study.interval(completed) if completed else None,
                               events_without_first_completion=len(rows) - len(completed)))
            all_rows.extend(dict(candidate=candidate, **r) for r in rows)
            behavior.append({'candidate': candidate, 'budget': budget, 'stones': cell['stones'],
                             'stones_by_run': cell['stones_by_run'], 'shop_buys': cell['shop_buys']})
            cells[candidate, budget] = cell
            cell_hashes[f'final-{candidate}/{filename}'] = train_rl.sha(cell_path)
        if load(path / 'summary.json')['summaries'] != summaries:
            raise RuntimeError('Final cohort summary differs')

    if len(all_rows) != 49152 or len({(r['candidate'], r['budget'], r['event_index']) for r in all_rows}) != len(all_rows):
        raise RuntimeError('Duplicate or missing events')
    comparisons = []
    pairs = (('parent', 'parent_priority'), ('parent', 'trained_control'),
             ('parent_priority', 'trained_priority'), ('trained_control', 'trained_priority'))
    for baseline, candidate in pairs:
        for budget in budgets:
            comparisons.append({'baseline': baseline, 'candidate': candidate, 'budget': budget,
                                'deltas': study.paired(cells[baseline, budget]['outcomes'], cells[candidate, budget]['outcomes'])})
    for budget in budgets:
        a = {r['event_index']: r for r in cells['parent', budget]['outcomes']}
        if any(r['first_completion_hours'] != a[r['event_index']]['first_completion_hours']
               for r in cells['parent_priority', budget]['outcomes']):
            raise RuntimeError('First clear changed under later-run-only parent intervention')
    csv_path = DEST / 'outcomes.csv'
    with csv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)
    rates = {candidate: stone_rates([cells[candidate, b] for b in budgets]) for candidate in CANDIDATES}
    verification = {'events': len(all_rows), 'decisions': sum(r['decisions'] for r in all_rows),
                    'invalid_actions': sum(r['invalid_actions'] for r in all_rows),
                    'truncations': sum(r['truncated'] for r in all_rows),
                    'training_decisions': sum(t['summary']['state']['steps'] for t in training.values()),
                    'completed_training_events': sum(t['summary']['state']['completed_training_events'] for t in training.values()),
                    'outcomes_sha256': train_rl.sha(csv_path), 'parent_first_clear_unchanged': True}
    result = {'schema': 'sfld-pendant-adaptation-report-v1', 'plan': plan, 'selection': selection,
              'training': training, 'summaries': groups, 'paired_comparisons': comparisons,
              'verification': verification, 'raw_cell_sha256': cell_hashes}
    study.write_once(DEST / 'summary.json', result)
    study.write_once(DEST / 'behavior.json', {'by_candidate_budget': behavior, 'pooled_stone_rates': rates,
                                           'interpretation': 'Frequencies on each policy own visited states; not causal stone strengths.'})
    write_report(groups, comparisons, rates, training, verification)
    print(json.dumps(verification, indent=2))


def write_report(groups, comparisons, rates, training, verification):
    budgets = [0, 20, 50, 100, 500, 1000]
    lookup = {(s['candidate'], s['budget']): s for s in groups}
    table = ['| Weekly cap | ' + ' | '.join(LABELS) + ' |', '|---|---:|---:|---:|---:|']
    for budget in budgets:
        table.append('| ' + str(budget) + ' | ' + ' | '.join(f"{lookup[c, budget]['completions']['mean']:.4f}" for c in CANDIDATES) + ' |')
    delta_table = ['| Weekly cap | Priority minus control | Paired 99% interval |', '|---|---:|---|']
    for comp in comparisons:
        if comp['baseline'] == 'trained_control':
            d = comp['deltas']['completions']
            delta_table.append(f"| {comp['budget']} | {d['mean']:+.4f} | {d['normal_99_ci'][0]:+.4f} to {d['normal_99_ci'][1]:+.4f} |")
    pick_table = ['| Policy | Pendant picked / offered | Picked when offered |', '|---|---:|---:|']
    for candidate, label in zip(CANDIDATES, LABELS):
        pendant = next(row for row in rates[candidate] if row['gem'] == 'pendant')
        pick_table.append(f"| {label} | {pendant['picked']:,} / {pendant['offered']:,} | {100 * pendant['chosen_when_offered']:.2f}% |")
    training_table = ['| Branch | Training decisions | Completed training weeks | Selected extra decisions |', '|---|---:|---:|---:|']
    for branch, t in training.items():
        state = t['summary']['state']
        training_table.append(f"| {branch} | {state['steps']:,} | {state['completed_training_events']:,} | {t['selected_steps']:,} |")
    differences = [c['deltas']['completions'] for c in comparisons if c['baseline'] == 'trained_control']
    below = sum(d['normal_99_ci'][1] < 0 for d in differences)
    above = sum(d['normal_99_ci'][0] > 0 for d in differences)
    conclusion = (f'After equal training effort, the priority branch has fewer completions '
                  f'with a 99% interval below zero at {below} of six caps, and more '
                  f'with an interval above zero at {above}.')
    report = f'''# Pendant priority after additional learning, 2026-10-11

This study gives a Pendant-priority agent time to adapt its combat, routes,
shopping and recovery, and gives an unconstrained control the same number of
additional training decisions. Both start from the same high-budget checkpoint.
The completion reward, engine, stone effects, loot tables and offer rates are
unchanged. Pendant priority means Rabbit first if offered, otherwise Moonstone
if offered, otherwise Pendant, whenever a legal Pendant menu appears. Other
stone menus remain learned, and preparatory healing stays available.

The priority applies to the actor's action distribution during both sampling
and PPO updates. Its metadata is saved with the checkpoint, so ordinary loading
preserves it. There is no artificial reward for choosing Pendant.

{conclusion}

## Fresh seven-day evaluation

Each cell contains 2,048 events: **{verification['events']:,} evaluation weeks**
in total. All four candidates use matching initial seeds within each cap.
Checkpoint choices were frozen from validation before the final cohort ran.
The original here is the high-budget parent at every cap, including 0/20/50;
it is not the separate established lower-budget specialist.

{chr(10).join(table)}

The equally trained branches compare as follows:

{chr(10).join(delta_table)}

All four pairwise comparison families, including comparisons with the original
policies, are in [summary.json](pendant-adaptation-2026-10-11/summary.json).
The original-plus-priority reference uses the previous post-choice override;
the trained version masks competing stone actions before choosing. Both retain
legal preparatory healing, but masking can change when that healing wins over a
stone action even before learning. That reference comparison is therefore not
an exact isolation of training alone; the equally trained branches are the
main comparison of the two complete policies.
These are per-comparison normal intervals for simulation sampling error.
They do not include uncertainty in dungeon mechanics or variation between
training seeds. Training used one seed per branch, so it is not a replicated
estimate of the average benefit of a training method. Identical initial seeds
also do not prevent later paths and random-number consumption from diverging.

{chr(10).join(pick_table)}

Selection rates describe the menus visited by each policy. Pendant is absent
from the first-run pool. The original with and without the immediate priority
intervention therefore has exactly the same first-clear times; retraining can
change first-run actions as well. The trained priority policy remains an
explicit experimental alternative. Existing default checkpoint paths are
preserved; this study does not establish a globally optimal stone order.

## Training and reproducibility

{chr(10).join(training_table)}

Training uses caps 0/20/50/100/500/1000, 256 environments, 128-step rollouts,
learning rate 0.0001, entropy coefficient 0.003, GAE lambda 0.95 and target KL
0.015. Each branch evaluates 256 events per cap at step zero and every
4,194,304 decisions. Selection uses the highest equally weighted mean
completion count across caps; exact ties retain the earlier checkpoint.
Weights and Adam state are inherited, with fresh training seeds 10001001 and
10001002. Final evaluation uses seed 10003001 and namespace 0xDC.

The native library is the archived engine 0.6.7 and the unchanged working
profile retains the 6.25% tier-five arena legendary assumption. Gold decline
and empty-animation evidence is recorded separately in
[the chest audit](CHEST_EMPTY_RESULTS_2026_10_11.md); no unmeasured loot changes
were introduced during this comparison. Pendant's guaranteed locked route is
modeled, but the accompanying-route distribution remains uncalibrated.

Verified **{verification['decisions']:,} final evaluation actions**, zero illegal
actions and zero truncated events. Budget spending, reward accounting,
first/later stone partitions, checkpoint selection and source hashes are
checked before publication. Unit tests cover the constraint, unchanged legal
healing actions, non-Pendant menus, parameter reuse and checkpoint reloading.

Run [train_pendant_study.py](../tools/train_pendant_study.py), then
[publish_pendant_adaptation.py](../tools/publish_pendant_adaptation.py) using
the RL Python environment. The [frozen plan](pendant-adaptation-2026-10-11/plan.json)
and [selection](pendant-adaptation-2026-10-11/selection.json) record all hashes.
Archives include curves, training metadata, selected checkpoints, source files,
[per-event outcomes](pendant-adaptation-2026-10-11/outcomes.csv), and
[stone/shop behavior](pendant-adaptation-2026-10-11/behavior.json).

Checkpoints:
[control](pendant-adaptation-2026-10-11/control/selected-checkpoint.pt) and
[Pendant priority](pendant-adaptation-2026-10-11/priority/selected-checkpoint.pt).
Load them with the updated [trainer](../tools/train_rl.py), which imports
[stone_priority.py](../tools/sfld_rl/stone_priority.py), and the matching
[native library](rl-history-2026-10-11/sfld_rl.dll) and
[profile](rl-history-2026-10-11/synthetic.profile).
'''
    (DEST.parent / 'PENDANT_ADAPTATION_2026_10_11.md').write_text(report, encoding='utf-8')


if __name__ == '__main__':
    main()
