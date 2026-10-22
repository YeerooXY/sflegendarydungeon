"""Validate and publish the high-budget shop/arena study without reselection."""
from collections import Counter
import csv
import json
from pathlib import Path
import re
import shutil

import benchmark_rl_shop_arena as study
import train_rl

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'out/shop-arena-2026-10-11'
DEST = ROOT / 'docs/shop-arena-2026-10-11'
LABELS = {
    'baseline': 'Flexible learned shopping', 'first_useful': 'First One Hit / Recovery / high-HP strong Lockpick',
    'healthy_one_hit8': 'Hunt 8-room One Hit when healthy', 'strong_pair': 'Strong Recovery or strong One Hit',
    'strong_pair_greed': 'Strong pair; also strong Lockpick with Greed',
    'arena_flee': 'Always push arena; always evade', 'arena_protected': 'Always push arena; fight with One Hit, otherwise evade',
    'arena_prepared': 'Push arena with room46-49 preparation',
    'greasy15': 'Force cheap Greasy revives through15 mushrooms',
    'greasy20': 'Force cheap Greasy revives through20 mushrooms',
    'strong_arena15': 'Strong/Greed shop + prepared arena + Greasy15',
    'strong_arena20': 'Strong/Greed shop + prepared arena + Greasy20',
    'first_arena15': 'First useful shop + prepared arena + Greasy15',
    'onehit_arena15': 'Healthy OneHit8 shop + prepared arena + Greasy15',
}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def table(summary, metric):
    lookup = {(s['policy'], s['budget']): s for s in summary['summaries']}
    policies = [p['name'] for p in summary['plan']['policies']]
    lines = ['| Policy | 100 mushrooms | 500 mushrooms | 1,000 mushrooms |', '|---|---:|---:|---:|']
    for p in policies:
        values = [lookup[p, b][metric]['mean'] for b in (100, 500, 1000)]
        lines.append('| ' + LABELS[p] + ' | ' + ' | '.join(f'{x:.3f}' for x in values) + ' |')
    return '\n'.join(lines)


def stones(cells):
    result = {}
    for cell in cells:
        for name, c in cell['stones'].items():
            result.setdefault(name, Counter()).update(c)
    return [{'gem': g, **c, 'chosen_when_offered': c['picked'] / c['offered']}
            for g, c in sorted(result.items(), key=lambda pair: pair[1]['picked'] / pair[1]['offered'], reverse=True)]


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    selection = load(SOURCE / 'selection.json')
    training = load(SOURCE / 'high-budget-training/training-summary.json')
    best = load(SOURCE / 'high-budget-training/best.json')
    if best['sha256'] != selection['checkpoint_sha256']:
        raise RuntimeError('Selected checkpoint changed')
    cohorts, cells, manifest = {}, {}, {}
    all_rows = []
    for cohort in ('screen', 'adapted-screen', 'confirmation', 'sensitivity', 'parent-confirmation'):
        path = SOURCE / cohort
        summary = load(path / 'summary.json')
        plan = summary['plan']
        if cohort in ('confirmation', 'sensitivity'):
            expected = selection['confirmation_policies' if cohort == 'confirmation' else 'sensitivity_policies']
            if [p['name'] for p in plan['policies']] != expected or plan['hashes']['checkpoint'] != best['sha256']:
                raise RuntimeError('Final evaluation differs from pre-final selection')
        seen = []
        local = {}
        for budget in plan['budgets']:
            for p in plan['policies']:
                filename = f'cap-{budget}-{p["name"]}.json'
                cell_path = path / filename
                cell = load(cell_path)
                study.write_once(DEST / 'schema.json', cell['environment']['schema'])
                if cell['plan'] != plan:
                    raise RuntimeError('Cell plan mismatch')
                study.validate_rows(cell['outcomes'], plan['events_per_cell'], budget, p['name'], plan['arena_legendary_rate'])
                if cell['summary'] != study.summarize(cell['outcomes']):
                    raise RuntimeError('Cell summary mismatch')
                for r in cell['outcomes']:
                    if abs(r['expected_legendaries'] - r['completions'] - r['expected_arena_legendaries']) > 1e-9:
                        raise RuntimeError('Total expected reward is inconsistent')
                if sum(cell['shop_buys'].values()) != sum(r['shop_purchases'] for r in cell['outcomes']):
                    raise RuntimeError('Shop audit does not balance')
                seen.append(cell['summary'])
                local[budget, p['name']] = cell
                all_rows.extend(dict(cohort=cohort, **r) for r in cell['outcomes'])
                manifest[cohort + '/' + filename] = train_rl.sha(cell_path)
        if seen != summary['summaries']:
            raise RuntimeError('Cohort summary mismatch')
        for c in summary['comparisons']:
            if c['deltas'] != study.paired(local[c['budget'], 'baseline']['outcomes'], local[c['budget'], c['policy']]['outcomes']):
                raise RuntimeError('Paired interval mismatch')
        cohorts[cohort], cells[cohort] = summary, local
        (DEST / cohort).mkdir(exist_ok=True)
        for f in ('plan.json', 'summary.json', 'scenario.profile'):
            shutil.copy2(path / f, DEST / cohort / f)
    a, b = cohorts['parent-confirmation']['plan'], cohorts['confirmation']['plan']
    for key in ('seed', 'namespace', 'events_per_cell', 'budgets', 'arena_legendary_rate'):
        if a[key] != b[key]:
            raise RuntimeError('Parent comparison is not matched: ' + key)
    fields = list(all_rows[0])
    with (DEST / 'outcomes.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader(); writer.writerows(all_rows)
    baselines = [c for (b, p), c in cells['confirmation'].items() if p == 'baseline']
    gem_rows = stones(baselines)
    by_budget = {str(b): stones([cells['confirmation'][b, 'baseline']]) for b in (100, 500, 1000)}
    reference = load(ROOT / 'docs/rl-history-2026-10-11/behavior-selected-summary.json')
    old_stones = stones([{'stones': row['stones']} for row in reference['per_budget']])
    behavior = {'schema': 'sfld-high-budget-behavior-v1', 'pooled_high_budget_stones': gem_rows,
                'high_budget_stones_by_budget': by_budget, 'prior_0_to_500_stones': old_stones,
                'shop_buys': {str(b): {p: c['shop_buys'] for (bb, p), c in cells['confirmation'].items() if bb == b} for b in (100, 500, 1000)},
                'interpretation': 'Choices on each policy own visited states. Offered denominators differ; this is not a causal stone ranking.'}
    study.write_once(DEST / 'behavior.json', behavior)
    parent_changes = {str(b): study.paired(cells['parent-confirmation'][b, 'baseline']['outcomes'], cells['confirmation'][b, 'baseline']['outcomes']) for b in (100, 500, 1000)}
    accounting = {'events': len(all_rows), 'decisions': sum(r['decisions'] for r in all_rows),
                  'invalid_actions': sum(r['invalid_actions'] for r in all_rows),
                  'truncations': sum(r['truncated'] for r in all_rows),
                  'csv_sha256': train_rl.sha(DEST / 'outcomes.csv')}
    summary = {'selection': selection, 'training': training, 'accounting': accounting,
               'parent_to_adapted_paired': parent_changes, 'raw_cell_sha256': manifest,
               'cohorts': {k: {'summaries': v['summaries'], 'comparisons': v['comparisons'], 'plan': v['plan']} for k, v in cohorts.items()}}
    study.write_once(DEST / 'summary.json', summary)
    for filename in ('experiment-plan.json', 'selection.json', 'greasy-followup.json'):
        shutil.copy2(SOURCE / filename, DEST / filename)
    for filename in ('run.json', 'best.json', 'training-summary.json', 'curve.jsonl'):
        shutil.copy2(SOURCE / 'high-budget-training' / filename, DEST / ('training-' + filename))
    shutil.copy2(SOURCE / 'high-budget-training' / best['checkpoint'], DEST / 'high-budget-checkpoint.pt')
    for path in ('tools/benchmark_rl_shop_arena.py', 'tools/sfld_rl/shop_experiments.py', 'tools/publish_shop_arena_study.py', 'tests/test_rl_shop_arena.py'):
        dest = DEST / 'source' / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, dest)
    names = dict(re.findall(r'^\s*(\w+):\["([^"]+)"', (ROOT / 'web/app.js').read_text(encoding='utf-8'), re.M))
    stone_table = ['| Stone | Picked / offered, pooled100/500/1000 | Chosen when offered | At1,000 mushrooms |', '|---|---:|---:|---:|']
    large = {r['gem']: r for r in by_budget['1000']}
    for r in gem_rows:
        stone_table.append(f'| {names.get(r["gem"], r["gem"])} | {r["picked"]:,} / {r["offered"]:,} | {100*r["chosen_when_offered"]:.1f}% | {100*large[r["gem"]]["chosen_when_offered"]:.1f}% |')
    arena = ['| Policy | Budget | Entries/week | Final chests/week | Final chest per entry | Arena deaths/week | Extra arena legendaries/week, expectation |', '|---|---:|---:|---:|---:|---:|---:|']
    for s in cohorts['confirmation']['summaries']:
        if s['policy'] == 'baseline' or 'arena' in s['policy']:
            arena.append(f'| {LABELS[s["policy"]]} | {s["budget"]} | {s["mean_arena_entries"]:.3f} | {s["mean_arena_chests_5"]:.3f} | {100*(s["arena_five_chest_fraction_per_entry"] or 0):.1f}% | {s["mean_arena_deaths"]:.3f} | {s["expected_arena_legendaries"]["mean"]:.3f} |')
    spending = ['| Policy | Budget | Actual spending/week | Rerolls/week | Refills/week |', '|---|---:|---:|---:|---:|']
    for s in cohorts['confirmation']['summaries']:
        spending.append(f'| {LABELS[s["policy"]]} | {s["budget"]} | {s["mean_mushrooms"]:.2f} | {s["mean_reroll_mushrooms"]:.2f} | {s["mean_recovery_mushrooms"]:.2f} |')
    ci = ['| Policy vs flexible | Budget | Clear difference,99% CI | Expected legendary difference,99% CI |', '|---|---:|---:|---:|']
    for c in cohorts['confirmation']['comparisons']:
        def fmt(metric):
            d = c['deltas'][metric]; lo, hi = d['normal_99_ci']
            return f'{d["mean"]:+.3f} [{lo:+.3f}, {hi:+.3f}]'
        ci.append(f'| {LABELS[c["policy"]]} | {c["budget"]} | {fmt("completions")} | {fmt("expected_legendaries")} |')
    text = f'''# High-budget shops, arena and Greasy revives — 2026-10-11

This study tests the player's proposed shop targets, arena pushing and cheap
Greasy revives. The new specialist received {training['state']['steps']:,}
additional PPO decisions across100/500/1000-mushroom budgets. Its checkpoint
was selected on training validation before fresh rule screening. The established
0–500-mushroom policy remains separately archived and unchanged.

The primary requested scenario sets the final arena chest to10% legendary.
The current footage-informed profile is6.25% (3/48 reviewed chests), so a
separate sensitivity cohort retains that value. Neither is an exact proven rate.
Conditional probabilities among nonlegendary final rewards are preserved.

Flexible shopping beat all four shop-target interventions on completion count
at every tested budget. Cheap Greasy revives through15 mushrooms improved the
1,000-mushroom result by0.06885 clears/week (paired99% interval+0.03558 to+0.10212),
were indistinguishable at500 and slightly reduced clears at100. The additional
20-mushroom purchase did not improve the smaller screening comparison.

Under10% arena rarity, pushing the arena while fighting only with One Hit
increased expected total legendary yield at100 and500, but was indistinguishable
at1,000. At6.25%, it had no clear advantage at100 and reduced total yield at500
and1,000. These conclusions concern the tested policies and configured model.

## Independent confirmation

Each cell below contains2,048 fresh seven-day events, random stones and full
health after room100. Policies were frozen before these event seeds were used.
All non-overridden decisions come from the same high-budget checkpoint.

Full dungeon completions per week:

{table(cohorts['confirmation'], 'completions')}

Expected total legendary items per week (completions plus arena rewards):

{table(cohorts['confirmation'], 'expected_legendaries')}

The total uses each trajectory's actual chest counts and configured rarity
probabilities to reduce noise from rare drops. Actual sampled legendary counts
and their intervals are retained in[summary.json](shop-arena-2026-10-11/summary.json)
and[outcomes.csv](shop-arena-2026-10-11/outcomes.csv). This expectation is not
a guaranteed item count or a counterfactual replay of changed rarity.

## Arena yield

{chr(10).join(arena)}

One completion itself guarantees one legendary in this simulator. An arena
strategy must replace any lost completion rewards before its bonus chests
increase total legendary yield. Always evade and fight-with-One-Hit are separate
screened policies. The latter otherwise evades and never cashes out early;
preparatory healing chosen by the learner remains available.

At the6.25% current-profile arena rate, expected total legendary items:

{table(cohorts['sensitivity'], 'expected_legendaries')}

## Tested rules

* First useful: accept either One Hit strength, either Recovery strength, or
  strong Lockpick at70%+HP. No search for an instant potion or extra gold.
* Healthy OneHit8: when HP exceeds the upcoming boss's unmodified maximum hit
  plus20% for two traps and a0.1% margin, search for eight-room One Hit.
  Otherwise use the first-useful targets. This health threshold does not promise
  survival against all intermediate fights, poison or future events.
* Strong pair: target strong Recovery (20% per room for6rooms) or strong
  One Hit (8rooms). The Greed variant also accepts strong Lockpick (4doors)
  when Boulder of Greed is owned.
* A qualifying affordable offer is accepted immediately. If both qualify,
  retain the learned choice if eligible; otherwise favor Recovery below70%HP
  and One Hit above it. No per-shop reroll ceiling, but at least4keys are
  required to search, and the weekly gross mushroom cap always applies.
  If search is impossible, an affordable learned fallback is allowed, including
  an instant potion; extra gold is never bought by a shop intervention.
  The free opening shop always takes the fixed four-room One Hit.
* Prepared arena: in rooms46–49, prefer ordinary locked/unlocked loot over
  ordinary alternatives, keeping golden rooms available. A visible monster
  with One Hit takes priority when its trap is absent/negated or HP is70%+.
  Enter and continue all five arena rounds, then collect the final chest.
* Greasy15/20: in ordinary death recovery, buy a20% step while its next price
  is at most15/20mushrooms, then reenter at20%. Boss recovery is unchanged.
  Prices still persist through deaths and reset after room100. These are
  additional forced purchases, not caps on the learner's other recovery spending.

An independent256-event diagnostic traced410 cheap Greasy reentries. None was
immediately followed by another paid refill. This checks only the next action;
later paid healing remains allowed. See[greasy-followup.json](shop-arena-2026-10-11/greasy-followup.json).

These are action interventions on a trained policy. Training a separate policy
to anticipate each constraint could change early key collection and its result.
They do not establish that every possible strong-blessing strategy is inferior
or superior. All mechanics, masks and observations remain native and unchanged.

## Mushroom allocation

{chr(10).join(spending)}

## Stone choices

These are descriptive choices of the flexible high-budget specialist in the
confirmation cohort. Denominator: occasions that stone was offered, not all
stone selections. Different offers, stages and held stones mean these rates
are not causal comparisons. The pooled column weights actual offer occurrences;
the1,000-mushroom column isolates that budget.

{chr(10).join(stone_table)}

The prior policy's0–500-mushroom choice counts and all high-budget counts are
in[behavior.json](shop-arena-2026-10-11/behavior.json). Three legacy stones
(Masochist, Sacrifice, Kidney) are absent from the current pool and unranked.

## Full screening, including the rejected variants

These are exploratory means from 512 events per cell. The rows above were
selected before the independent confirmation cohort. The following table keeps
the always-flee, room46–49 preparation and third-Greasy-purchase tests visible;
their apparent order on this smaller cohort is not a guaranteed ordering.

{table(cohorts['adapted-screen'], 'expected_legendaries')}

## Paired uncertainty

{chr(10).join(ci)}

Intervals pair event identities, irrespective of completion order. They are
99% normal intervals for individual comparisons, not simultaneous guarantees.
Simulation uncertainty, particularly blessing offer odds, future mystery
contents and arena rarity, is outside these intervals.

## Reproduction and accounting

The frozen plan and pre-final selection are in[experiment-plan.json](shop-arena-2026-10-11/experiment-plan.json)
and[selection.json](shop-arena-2026-10-11/selection.json). Screening includes all14
rules at512events per budget; confirmation uses a fresh root seed and2,048events
per selected cell. Sensitivity uses another fresh root. Initial parent-policy
screening is retained but is not mixed into adapted-policy means.

{accounting['events']:,} indexed event outcomes; {accounting['decisions']:,} decisions;
{accounting['invalid_actions']} invalid actions; {accounting['truncations']} truncated events.
Every event balances gross spending against refills+rerolls. Every legendary
balances completion rewards+arena rewards. All raw cells have recorded SHA256
hashes, and the published CSV retains every event's counters.

The selected specialist is[high-budget-checkpoint.pt](shop-arena-2026-10-11/high-budget-checkpoint.pt),
SHA256 `{best['sha256']}`. It uses the unchanged archived
[0.6.7 library](rl-history-2026-10-11/sfld_rl.dll) and460 public inputs.
The parent-to-specialist comparison on matched fresh events is in the summary.
No final data were used to reselect the checkpoint or interventions.

Run `out/rl-env/Scripts/python.exe tools/benchmark_rl_shop_arena.py --help`
for evaluation options. Rule tests:
`out/rl-env/Scripts/python.exe -m unittest discover -s tests -p test_rl_shop_arena.py -v`.

For the new specialist, pass
`--checkpoint docs/shop-arena-2026-10-11/high-budget-checkpoint.pt`.
For example, compare `--policies baseline,strong_pair_greed,arena_protected`
with `--budgets 1000 --arena-rate 10 --events 2048` and a new `--output` folder.
Keep new experiments on fresh seeds; use the archived plans' seeds only for
deliberate reproduction.
'''
    # Keep prose readable without modifying machine-readable identifiers/paths.
    for before, after in {
        'across100/500/1000': 'across 100/500/1000', 'to10%': 'to 10%',
        'is6.25%': 'is 6.25%', 'contains2,048': 'contains 2,048', 'room100': 'room 100',
        'the6.25%': 'the 6.25%', 'at70%': 'at 70%', 'plus20%': 'plus 20%',
        'a0.1%': 'a 0.1%', 'for6rooms': 'for 6 rooms', '(8rooms)': '(8 rooms)',
        '(4doors)': '(4 doors)', 'below70%HP': 'below 70% HP', 'least4keys': 'least 4 keys',
        'rooms46': 'rooms 46', 'is70%': 'is 70%', 'buy a20%': 'buy a 20%',
        'most15/20mushrooms': 'most 15/20 mushrooms', 'at20%': 'at 20%',
        'pooled100/500/1000': 'pooled 100/500/1000', 'At1,000': 'At 1,000',
        'the1,000': 'the 1,000', "policy's0": "policy's 0", '99% CI': '99% CI',
        'all14': 'all 14', 'at512events': 'at 512 events', 'and2,048events': 'and 2,048 events',
        'and460': 'and 460', 'through15': 'through 15', 'through20': 'through 20',
        'in[': 'in [', 'and[': 'and [', 'is[': 'is [', 'room46': 'room 46',
        'by0.06885': 'by 0.06885', 'paired99%': 'paired 99%', 'interval+': 'interval +',
        'to+0.10212': 'to +0.10212', 'at500': 'at 500', 'at100': 'at 100',
        'Under10%': 'Under 10%', 'and500': 'and 500', 'at1,000': 'at 1,000',
        'At6.25%': 'At 6.25%', 'and1,000': 'and 1,000', 'independent256': 'independent 256',
        'traced410': 'traced 410', 'See[': 'See [',
    }.items():
        text = text.replace(before, after)
    (ROOT / 'docs/SHOP_ARENA_2026_10_11.md').write_text(text, encoding='utf-8')
    print(json.dumps({'accounting': accounting, 'checkpoint': best['sha256'], 'report': 'docs/SHOP_ARENA_2026_10_11.md'}, indent=2))


if __name__ == '__main__':
    main()
