"""Verify and publish the fixed Pendant-priority comparison and run-class audit."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

import benchmark_rl_shop_arena as study

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ('baseline', 'pendant_priority', 'pendant_escape')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish(source, output):
    output.mkdir(parents=True, exist_ok=True)
    rows, groups, comparisons, behavior, plans = [], [], [], [], {}
    first_equal = True
    for cohort, budgets in (('low-budgets', (0, 20, 50)), ('high-budgets', (100, 500, 1000))):
        plan = json.loads((source / cohort / 'plan.json').read_text())
        if (plan['events_per_cell'] != 2048 or plan['budgets'] != list(budgets)
                or [p['name'] for p in plan['policies']] != list(POLICIES)
                or plan['arena_legendary_rate'] != .0625 or plan['seed'] != '9901001'
                or plan['namespace'] != 0xDB):
            raise RuntimeError('Unexpected frozen comparison plan')
        plans[cohort] = plan
        target = output / cohort
        target.mkdir(exist_ok=True)
        for filename, hash_key in (('benchmark_rl_shop_arena.py', 'runner'),
                                   ('shop_experiments.py', 'rules'), ('scenario.profile', 'profile')):
            path = source / cohort / filename
            if digest(path) != plan['hashes'][hash_key]:
                raise RuntimeError('Archived experiment source does not match plan')
            shutil.copyfile(path, target / filename)
        study.write_once(target / 'plan.json', plan)
        for budget in budgets:
            cells = {}
            for name in POLICIES:
                cell = json.loads((source / cohort / f'cap-{budget}-{name}.json').read_text())
                if cell['plan'] != plan:
                    raise RuntimeError('Cell plan differs')
                outcomes = cell['outcomes']
                study.validate_rows(outcomes, plan['events_per_cell'], budget, name, .0625)
                summary = study.summarize(outcomes)
                if summary != cell['summary']:
                    raise RuntimeError('Stored summary differs from raw outcomes')
                if 'pendant' in cell['stones_by_run']['first']:
                    raise RuntimeError('Pendant appeared in the first run')
                for gem, counts in cell['stones'].items():
                    for field in ('offered', 'picked'):
                        total = sum(by_run.get(gem, {}).get(field, 0) for by_run in cell['stones_by_run'].values())
                        if total != counts[field]:
                            raise RuntimeError('Run-class stone totals differ')
                first = [r['first_completion_hours'] for r in outcomes if r['first_completion_hours'] >= 0]
                summary['first_completion_hours_if_completed'] = study.interval(first) if first else None
                summary['events_without_first_completion'] = len(outcomes) - len(first)
                groups.append(summary)
                rows.extend(dict(cohort=cohort, **r) for r in outcomes)
                behavior.append({'cohort': cohort, 'budget': budget, 'policy': name,
                                 'stones': cell['stones'], 'stones_by_run': cell['stones_by_run']})
                cells[name] = cell
            baseline = {r['event_index']: r for r in cells['baseline']['outcomes']}
            for name in POLICIES[1:]:
                for row in cells[name]['outcomes']:
                    first_equal &= row['first_completion_hours'] == baseline[row['event_index']]['first_completion_hours']
                if cells[name]['stones_by_run']['first'] != cells['baseline']['stones_by_run']['first']:
                    raise RuntimeError('First-run stone choices changed')
                comparisons.append({'budget': budget, 'policy': name,
                                    'deltas': study.paired(cells['baseline']['outcomes'], cells[name]['outcomes'])})
    if not first_equal:
        raise RuntimeError('First completion changed despite later-run-only intervention')
    if len(rows) != 36864 or len({(r['budget'], r['policy'], r['event_index']) for r in rows}) != len(rows):
        raise RuntimeError('Incomplete or duplicated cohort')
    csv_path = output / 'outcomes.csv'
    with csv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    result = {'schema': 'sfld-pendant-priority-study-v1', 'plans': plans,
              'summaries': groups, 'paired_comparisons': comparisons,
              'verification': {'events': len(rows), 'decisions': sum(r['decisions'] for r in rows),
                               'invalid_actions': sum(r['invalid_actions'] for r in rows),
                               'truncations': sum(r['truncated'] for r in rows),
                               'first_completion_identical_for_every_matched_event': first_equal,
                               'first_run_pendant_offers': 0, 'outcomes_sha256': digest(csv_path)}}
    study.write_once(output / 'summary.json', result)
    study.write_once(output / 'behavior.json', behavior)
    table = ['| Weekly mushroom cap | Existing learner | Pendant priority | Priority with escape stone |',
             '|---|---:|---:|---:|']
    for budget in (0, 20, 50, 100, 500, 1000):
        by_policy = {s['policy']: s for s in groups if s['budget'] == budget}
        table.append('| ' + str(budget) + ' | ' + ' | '.join(f"{by_policy[p]['completions']['mean']:.4f}" for p in POLICIES) + ' |')
    deltas = ['| Cap | Rule | Change in clears/week | Paired 99% interval |', '|---|---|---:|---|']
    for c in comparisons:
        d = c['deltas']['completions']
        deltas.append(f"| {c['budget']} | {c['policy']} | {d['mean']:+.4f} | {d['normal_99_ci'][0]:+.4f} to {d['normal_99_ci'][1]:+.4f} |")
    picks = ['| Rule | Pendant picked / offered | Picked when offered |', '|---|---:|---:|']
    for policy in POLICIES:
        selected = [b['stones'].get('pendant', {}) for b in behavior if b['policy'] == policy]
        offered = sum(x.get('offered', 0) for x in selected)
        picked = sum(x.get('picked', 0) for x in selected)
        picks.append(f'| {policy} | {picked:,} / {offered:,} | {100 * picked / offered:.2f}% |')
    first_times = ['| Mushroom cap | Mean first-clear hours | Events without first clear |', '|---|---:|---:|']
    for s in groups:
        if s['policy'] == 'baseline':
            first_times.append(f"| {s['budget']} | {s['first_completion_hours_if_completed']['mean']:.2f} | {s['events_without_first_completion']} / {s['events']} |")
    report = f'''# Pendant priority and run classes — 2026-10-11

Two selectable policies raise Pendant of the Key Master's priority without
altering its mechanics or offer frequency. The comparison holds the trained
agent fixed and changes only stone choices. Both policies were specified before
the main evaluation; neither was selected from this cohort as a new default.

Broadly promoting Pendant reduced mean weekly completions by 7.3-9.1% across
the six tested caps. Promoting it only with an already-owned escape stone had
a smaller 0.6-0.9% loss. All twelve paired 99% difference intervals were below
zero within this model. Both requested variants remain available for further
experiments; the existing learned policy remains the completion benchmark.

* `pendant_priority`: when a legal Pendant offer appears, choose Rabbit if
  offered, otherwise Moonstone if offered, otherwise Pendant. Other menus keep
  the learned choice. Preparatory healing is preserved.
* `pendant_escape`: the same rule, but only when Rabbit or Moonstone is already
  owned. Otherwise keep the learned choice.

## Weekly completions

Each cell has 2,048 fresh seven-day events. Caps 0/20/50 use the established
[lower-budget checkpoint](rl-history-2026-10-11/selected-checkpoint.pt); caps
100/500/1000 use the [high-budget specialist](shop-arena-2026-10-11/high-budget-checkpoint.pt).
Within each budget, all three policies share the same checkpoint and initial
event seeds. The working profile keeps 6.25% legendary chance for tier-five
arena chests. Shop, combat and recovery actions remain learned.

{chr(10).join(table)}

Changes relative to the matched existing learner:

{chr(10).join(deltas)}

Intervals describe simulation sampling error for each comparison, not model
uncertainty. Initial event seeds match; paths and later random-number use can
diverge. These policies were not retrained to exploit their new stone choices.
The experiment therefore does not establish Pendant's optimal value under a
separately adapted strategy.

{chr(10).join(picks)}

These selection rates describe each policy's own visited menus, not causal
stone strengths. Pendant never appeared on the first run in this cohort.

## First versus later runs

[RUN_CLASSES.md](RUN_CLASSES.md) gives the complete implementation comparison,
both stone pools, resets, learner-visible run information and remaining gaps.
First means the first run of each event, not the first life or first-ever run.
The special free room-five shop and 11-stone pool apply only to run 1. Later
runs use a normal stage-one shop and a 20-stone pool containing Pendant.

Every matched event had exactly the same first-completion time and the same
aggregate first-run stone choices under all three policies. The interventions
can only affect later runs because Pendant is absent from the first-run pool.
Mean first-clear times below are conditional on completion within the week:

{chr(10).join(first_times)}

The reported first-run protection from empty/exploding containers is not yet
implemented; run classes currently share loot tables. Opening mystery rooms
also share the ordinary monster-adjacent content pool. Pendant's guaranteed
locked route is modeled, but the distribution of its accompanying route remains
an assumption. These are reasons to treat the strategy results as conditional
on the simulator rather than final live-game rankings.

## Reproduction and verification

The new rules are in [shop_experiments.py](../tools/sfld_rl/shop_experiments.py).
Use [benchmark_rl_shop_arena.py](../tools/benchmark_rl_shop_arena.py) with
`--policies baseline,pendant_priority,pendant_escape --arena-rate 6.25`,
`--events 2048 --n-envs 512 --seed 9901001 --namespace 0xDB`, and the checkpoint
and budget group recorded above. Rules do not expose hidden rooms or future RNG.

Verified {len(rows):,} completed evaluation weeks and
{result['verification']['decisions']:,} actions; zero illegal actions and zero
truncated episodes. Every event's budget, reward attribution and completion
summary is checked. First-run timing equality and stone-count partitioning are
also checked. Unit tests cover prioritization, the ownership gate, absent or
illegal Pendant offers, other menus and preparatory healing.

Data: [summary and intervals](pendant-2026-10-11/summary.json),
[event outcomes](pendant-2026-10-11/outcomes.csv), and
[stone choices split by run class](pendant-2026-10-11/behavior.json).
Each budget group archives its profile, plan and runner/rule sources with hashes.
The native 0.6.7 library and both trained checkpoints are unchanged.
'''
    (output.parent / 'PENDANT_PRIORITY_2026_10_11.md').write_text(report, encoding='utf-8')
    shutil.copyfile(__file__, output / Path(__file__).name)
    print(json.dumps({'verification': result['verification'], 'report': 'PENDANT_PRIORITY_2026_10_11.md'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'out/pendant-2026-10-11')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/pendant-2026-10-11')
    args = parser.parse_args()
    publish(args.source, args.output)
