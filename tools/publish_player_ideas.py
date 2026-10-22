"""Verify and archive the corrected-engine player-idea comparison."""
from __future__ import annotations
import argparse
import csv
import gzip
import io
import json
from pathlib import Path
import shutil
import statistics

import train_rl
from benchmark_player_ideas import paired, summarize, validate, write_once

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = (0,20,50,100,500,1000)
NAMES = {
    'control': 'Frozen learned control',
    'reentry21': '21% ordinary entry floor',
    'boss_buffer': 'Extra health before final boss',
    'recovery_fights': 'More fights under useful Recovery',
    'free_escape': 'Guaranteed escape and soldier searches',
    'keys10_shop4': 'Early key farming / four-key shop reserve',
    'stone_priority': 'Rabbit → Moonstone → Greed → Pendant',
    'combined_no_stones': 'All action ideas; learned stones',
    'combined': 'All action ideas and stone priority',
}


def read(path): return json.loads(path.read_text(encoding='utf8'))


def archive_csv(path, rows):
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as zipped:
            with io.TextIOWrapper(zipped,encoding='utf8',newline='') as stream:
                writer = csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
                writer.writeheader(); writer.writerows(rows)


def publish(args):
    cells, plans, comparisons, rows = {}, {}, [], []
    for budget in BUDGETS:
        directory = args.confirm/f'cap-{budget}'
        summary = read(directory/'summary.json')
        plan = summary['plan']; plans[str(budget)] = plan
        if plan['budgets'] != [budget] or plan['namespace'] != 231:
            raise RuntimeError('Unexpected confirmation cohort')
        for policy in plan['cases']:
            name = policy['name']
            cell = read(directory/f'cap-{budget}-{name}.json')
            validate(cell['outcomes'],plan['events_per_cell'],budget,name)
            if cell['plan'] != plan or cell['summary'] != summarize(cell['outcomes']):
                raise RuntimeError('Source summary or plan mismatch')
            cells[budget,name] = cell
            rows.extend(cell['outcomes'])
        for name in NAMES:
            if name != 'control':
                comparisons.append({'budget':budget,'case':name,
                    'deltas':paired(cells[budget,'control']['outcomes'],cells[budget,name]['outcomes'])})
    if len({json.dumps(plan['hashes'],sort_keys=True) for plan in plans.values()}) != 1:
        raise RuntimeError('Inputs differ across budgets')
    # One family of48 completion comparisons, specified before final evaluation.
    critical = statistics.NormalDist().inv_cdf(1-.05/(2*len(comparisons)))
    for comparison in comparisons:
        estimate = comparison['deltas']['completions']; mean,se = estimate['mean'],estimate['standard_error']
        estimate['bonferroni_family_95_ci'] = [mean-critical*se,mean+critical*se]
    result = {'schema':'sfld-player-ideas-results-v1','plans_by_budget':plans,
              'events':len(rows),'events_per_cell':plans['0']['events_per_cell'],
              'summaries':[cell['summary'] for cell in cells.values()],
              'comparisons':comparisons,'family_comparison_count':len(comparisons),
              'family_normal_critical_value':critical}
    args.output.mkdir(parents=True,exist_ok=True)
    write_once(args.output/'summary.json',result)
    archive_csv(args.output/'events.csv.gz',rows)
    pilot = read(args.pilot/'summary.json')
    if pilot['plan']['namespace'] == plans['0']['namespace']: raise RuntimeError('Pilot overlaps confirmation')
    write_once(args.output/'pilot-summary.json',pilot)
    pilot_rows = []
    for budget in BUDGETS:
        for name in NAMES:
            cell = read(args.pilot/f'cap-{budget}-{name}.json')
            validate(cell['outcomes'],pilot['plan']['events_per_cell'],budget,name)
            pilot_rows.extend(cell['outcomes'])
    archive_csv(args.output/'pilot-events.csv.gz',pilot_rows)
    first = cells[0,'control']
    write_once(args.output/'schema.json',first['environment']['schema'])
    write_once(args.output/'engine-transfer.json',first['transfer'])
    for key,filename in (('library','sfld_rl.dll'),('profile','synthetic.profile'),
                         ('runner','benchmark_player_ideas.py'),('rules','player_ideas.py'),('adapter','env.py')):
        source = args.confirm/'cap-0'/filename
        if train_rl.sha(source) != plans['0']['hashes'][key]: raise RuntimeError('Snapshot hash mismatch')
        shutil.copyfile(source,args.output/filename)
    write_once(args.output/'artifact-hashes.json',{p.name:train_rl.sha(p) for p in sorted(args.output.iterdir())
                                                 if p.is_file() and p.name!='artifact-hashes.json'})
    s = lambda budget,name: cells[budget,name]['summary']
    delta = {(c['budget'],c['case']):c['deltas']['completions'] for c in comparisons}
    archive = args.output.name
    lines = [
        '# Player ideas on the corrected simulator, 2026-10-11', '',
        f'Engine **0.6.9**. The fresh confirmation contains **{len(rows):,} simulated seven-day events**,',
        f'{result["events_per_cell"]:,} per policy/budget cell, after a separate **{len(pilot_rows):,}-event** pilot.',
        'All nine policies and all six budgets were retained for confirmation; no winner was selected on this final cohort.', '',
        'The full intervention bundle reduces weekly clears at every tested budget, including after',
        'adjustment for the 48 comparisons. Keeping learned stone choices reduces the loss but does not',
        'remove it. No isolated intervention demonstrates a positive gain in this experiment.',
        'The 21% floor and guaranteed-escape rules remain inconclusive; forced Recovery fights lose',
        'a small amount at the 1,000 cap. The tested key rule improves shop affordability but loses clears.',
        'This supports retaining the learned control as the comparison baseline, while keeping all',
        'mechanic corrections. It does not establish that every conditional version of these ideas is worse.', '',
        'The control is the frozen unconstrained checkpoint from the Pendant adaptation study:',
        '`docs/pendant-adaptation-2026-10-11/control/selected-checkpoint.pt`.',
        'It was chosen before this experiment. It is a further-trained descendant of the high-budget',
        'learner, evaluated here at every cap; it is not a newly trained policy or a proof of optimality.', '',
        'Both control and interventions use the corrected damage modifiers, independent blessing/curse slots,',
        'healing-before-poison order, lethal zero-health handling, trap-free first five rooms,',
        'and nonempty first-event-run containers. Mechanic fixes are held constant.', '',
        '## Weekly completions', '',
        '| Mushroom cap | Learned control | All action ideas, learned stones | All ideas including stone order |',
        '|---:|---:|---:|---:|']
    for b in BUDGETS:
        lines.append(f'| {b} | '+ ' | '.join(f'{s(b,n)["completions"]["mean"]:.4f}' for n in ('control','combined_no_stones','combined'))+' |')
    lines += ['', '## Each change in isolation', '',
              'Values below are **change in weekly completions relative to the matched control**.',
              'A dagger marks an interval excluding zero after a Bonferroni correction across all 48 completion comparisons (95% family confidence).', '',
              '| Intervention | 0 | 20 | 50 | 100 | 500 | 1000 |', '|---|---:|---:|---:|---:|---:|---:|']
    for name in list(NAMES)[1:]:
        values = []
        for b in BUDGETS:
            d = delta[b,name]; low,high = d['bonferroni_family_95_ci']
            values.append(f'{d["mean"]:+.4f}'+('†' if low>0 or high<0 else ''))
        lines.append('| '+NAMES[name]+' | '+' | '.join(values)+' |')
    lines += ['', 'These intervals cover simulation sampling error. They do not cover uncertainty in',
              'room/drop probabilities, stone formulas, or how a retrained policy could adapt.',
              'An interval containing zero is inconclusive; it does not establish equivalence.', '',
              '## First clear and mushroom efficiency', '',
              'First-clear time is the mean simulated elapsed time among events completing at least once.',
              'Completion fractions are in the JSON. Times include recovery; they assume immediate return when',
              'the chosen health target is reached. Spending is gross use, not the configured cap.', '',
              '| Cap | Control first clear, hours | All ideas first clear, hours | Control spent | All ideas spent | Control mushrooms / clear | All ideas mushrooms / clear |',
              '|---:|---:|---:|---:|---:|---:|---:|']
    for b in BUDGETS:
        a,z = s(b,'control'),s(b,'combined')
        vals = [a['first_clear_hours_among_cleared']['mean'],z['first_clear_hours_among_cleared']['mean'],
                a['mushrooms']['mean'],z['mushrooms']['mean'],a['mushrooms_per_completion'],z['mushrooms_per_completion']]
        lines.append(f'| {b} | '+' | '.join(f'{v:.2f}' for v in vals)+' |')
    lines += ['', 'Mushrooms per clear includes clears supplied by starting health and natural regeneration.',
              'It is not the marginal price of purchasing one extra completion. First-clear time need not',
              'fall at every higher cap: this policy optimizes total weekly completions and changes how',
              'it spends through the week; it is not separately trained to minimize first-clear time.', '',
              '## Keys versus completion speed', '',
              'This records the first room-25 boss and first paid shop reached in each event.', '',
              '| Cap | Control keys after boss 25 | Key rule keys after boss 25 | Control ≥4 keys at shop | Key rule ≥4 keys at shop |',
              '|---:|---:|---:|---:|---:|']
    for b in BUDGETS:
        a,z = s(b,'control'),s(b,'keys10_shop4')
        lines.append(f'| {b} | {a["first_boss25_mean_keys"]:.2f} | {z["first_boss25_mean_keys"]:.2f} | '
                     f'{a["first_paid_shop_four_key_fraction"]:.1%} | {z["first_paid_shop_four_key_fraction"]:.1%} |')
    lines += ['', '## Exact tested rules', '',
              '- **21% entry:** raise lower ordinary-room recovery waits/reentries to 21%, preserving larger learned waits and paid heals. Boss-room recovery remains learned. Each extra 1% costs 0.24 hours at the configured 24-hour full recovery.',
              '- **Boss buffer:** during recovery at rooms 97–99, target the boss maximum plus up to two ordinary monster maximum hits plus 0.1 percentage points, capped at 100%. This can reach full health and cannot guarantee survival when the uncapped sum exceeds 100%. It does not deliberately die or stop a live run to regenerate.',
              '- **Recovery fights:** prefer an untrapped ordinary monster over a paid door or mystery when healing covers its mean hit plus current poison and the maximum hit is survivable; fight that monster. Future curse rolls remain random.',
              '- **Guaranteed escape:** prefer a safe ordinary monster over a paid door or trapped mystery at 100% effective escape; search sleeping soldiers. Preserve One Hit and useful Recovery key farming. Current evasion curses are included.',
              '- **Key rule:** when already facing an ordinary monster, fight safely below 10 keys in stage 1 or 5 in stage 2. Before an unvisited stage shop, prefer a safe untrapped monster if a paid door would leave fewer than 4 keys. In stage 1, prefer a safe monster over trapped mystery. This is not a ban on every early paid door and does not force 7–10 keys after boss 25.',
              '- **Stone priority:** only menus containing Pendant are overridden: Rabbit, then Moonstone, then Greed, then Pendant. Other menus retain learned choices. All stone offers are still random and use first/later-run pools and rejected-offer exclusion.',
              '- **Combined:** apply these together. The version without stone priority isolates the action changes from the stone restriction. Shop purchase/reroll logic and arena choices remain learned.', '',
              'These are interventions on one frozen trained policy. They are not the separate rule-based adviser',
              'displayed by the interactive viewer, and this experiment does not replace the viewer policy.', '',
              '## Reproduction and verification', '',
              'The native custom-wait API makes 21% and the boss buffer exact. It retains the 23-action',
              'network menu and 460 features, validates the complete batch before mutation, and delegates all',
              'healing, time and legality to the C++ engine. Ordinary learning APIs remain unchanged.', '',
              'Transfer from 0.6.7 to 0.6.9 requires `--allow-engine-transfer`. The runner rejects changed',
              'feature/action schemas; only the engine version and explicitly recorded binary/profile hashes may differ.',
              'The checkpoint is not rewritten. Every cell checks exact event indices, zero illegal actions,',
              'zero safety truncations, deadline termination and the mushroom spending identity/cap.',
              'An event may finish less than two seconds before hour 168 when its next two-second',
              'action no longer fits. Recovery waits can finish at the exact deadline.', '',
              'Example for one confirmed cap (use a new output directory):', '',
              '```powershell',
              f'python tools/benchmark_player_ideas.py --library docs/{archive}/sfld_rl.dll --profile docs/{archive}/synthetic.profile --output out/player-ideas-reproduction --events 2048 --n-envs 512 --budgets 0 --namespace 231 --allow-engine-transfer',
              '```', '',
              'Use the recorded checkpoint, source hashes, Torch version and profile for a strict reproduction.',
              'The pilot uses namespace 230; confirmation uses 231; root seed is 11069001.',
              'Matched starting seeds do not force identical subsequent rooms after actions change RNG consumption.', '',
              f'- [Final results and paired intervals]({archive}/summary.json)',
              f'- [Every confirmed event, compressed CSV]({archive}/events.csv.gz)',
              f'- [Pilot results]({archive}/pilot-summary.json)',
              f'- [Pilot events, compressed CSV]({archive}/pilot-events.csv.gz)',
              f'- [Engine transfer record]({archive}/engine-transfer.json)',
              f'- [Artifact hashes]({archive}/artifact-hashes.json)', '']
    args.report.write_text('\n'.join(lines),encoding='utf8')
    print(json.dumps({'events':len(rows),'pilot_events':len(pilot_rows),'report':str(args.report)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm',type=Path,default=ROOT/'out/player-ideas-069-confirm')
    parser.add_argument('--pilot',type=Path,default=ROOT/'out/player-ideas-069-pilot')
    parser.add_argument('--output',type=Path,default=ROOT/'docs/player-ideas-2026-10-11')
    parser.add_argument('--report',type=Path,default=ROOT/'docs/PLAYER_IDEAS_2026_10_11.md')
    publish(parser.parse_args())
