"""Balanced, reproducible gem-priority search with untouched offer pools.

Stages: prepare -> search -> select -> final -> report. Selection and final
use different event cohorts; final results never choose the recommendation.
Run from the repository root. Existing completed jobs are verified and reused.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import html
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import time

from benchmark_rule_final import mix, sha


GEMS = ['rabbit', 'moonstone', 'spying', 'pendant', 'greasy', 'gambler',
        'greed', 'hero', 'time_traveler', 'thirsty', 'pearl', 'blood',
        'explorer', 'misadventurer', 'devil', 'deceit', 'lodestone', 'bull',
        'hick', 'rusty']
BUDGETS = [0, 20, 50, 100, 500]
OLD = Path('docs/rule-strategy-2026-10-09/rules')
NAMES = ['Soul of the Rabbit', 'Cursed Moonstone', 'Spying Gem',
         'Pendant of the Key Master', 'Greasy Healing Stone',
         'Boulder of the Gambler', 'Boulder of Greed', 'Treasure of the Hero',
         'Diamond of the Time Traveler', 'Hope of the Thirsty One',
         'Cursed Pearl', 'Blood Drop of Sacrifice', 'Emerald of the Explorer',
         'Sapphire of the Misadventurer', 'Crown Jewel of the Devil',
         'Pebble of Deceit', 'Lodestone', 'Eye of the Bull',
         'Erratic Boulder of the Hick', 'Rusty Healing Stone']


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rules_read(path):
    return dict(line.split('=', 1) for line in path.read_text(encoding='utf-8').splitlines()
                if line and not line.startswith('#'))


def rules_write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(f'{k}={v}\n' for k, v in values.items()), encoding='utf-8')


def master(root, domain):
    return (domain << 56) | ((mix(root) & 0xffffffff) << 24)


def check_cohorts(plan):
    # The event mixer is bijective; disjoint low-24-bit input blocks imply
    # disjoint event seeds. Verify block identities, including archived roots.
    stages = [('training', master(root, 0x31), plan['training_events'])
              for root in plan['search_roots']]
    stages += [('internal_selection', master(root, 0x52), plan['validation_events'])
               for root in plan['search_roots']]
    stages += [('common_selection', master(plan['selection_root'], 0x73), plan['selection_events']),
               ('final', master(plan['final_root'], 0x73), plan['final_events'])]
    previous = []
    for path in Path('docs/rule-strategy-2026-10-09').glob('**/manifest.json'):
        obj = read_json(path)
        if 'event_master_seed' in obj:
            previous.append((str(path), int(obj['event_master_seed']), obj['runs']))
    for path in Path('out/human-rules-2026-10-09').glob('**/search.json'):
        for c in read_json(path).get('seed_plan', {}).get('cohorts', []):
            previous.append((str(path) + ':' + c['name'], int(c['master_seed']), c['events']))
    blocks = {}
    for name, seed, count in stages:
        if seed & ((1 << 24) - 1) or not 2 <= count < (1 << 24):
            raise ValueError('Cohort is not a valid nonoverlapping seed block')
        if seed in blocks:
            raise ValueError(f'Cohort overlaps {name} / {blocks[seed]}')
        blocks[seed] = name
        for old_name, old_seed, old_count in previous:
            if old_count and seed >> 24 == old_seed >> 24:
                raise ValueError(f'Cohort overlaps historical {old_name}')
    return {'method': 'disjoint aligned 24-bit mixer-input blocks; mix is bijective',
            'new_unique_cohorts': len(stages), 'historical_cohorts_checked': len(previous),
            'overlaps': 0,
            'cohorts': [{'purpose': n, 'master_seed': str(s), 'events': c} for n, s, c in stages],
            'intentional_reuse': 'Within each restart/budget, all arms share events; selection and final each share events across all candidates.'}


def prepare(args):
    out = args.output_dir
    if (out / 'plan.json').exists():
        raise ValueError('Study already prepared; resume a later phase')
    out.mkdir(parents=True, exist_ok=True)
    files = [Path('CMakeLists.txt'), Path('profiles/synthetic.profile')]
    files += sorted(Path('src').glob('*.cpp')) + sorted(Path('include/sfld').glob('*.hpp'))
    files += [Path('tools/rule_search.cpp'), Path('tools/benchmark_balanced_gems.py'), Path('tools/benchmark_rule_final.py')]
    frozen = []
    for path in files:
        target = out / 'source' / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        frozen.append({'path': str(path), 'sha256': sha(path)})
    plan = {'schema': 1, 'purpose': 'Equal-effort gem-priority policy comparison, not a causal stone tier list',
            'gems': GEMS, 'budgets': BUDGETS, 'offer_pool_changes': False,
            'restarts': 3, 'unique_candidates': 128, 'training_events': 128,
            'validation_events': 1024, 'finalists': 4, 'population': 16,
            'selection_events': 5000, 'final_events': 20000,
            'selection_root': 713000, 'final_root': 713100,
            'search_roots': [712900 + i * 10 + r for i in range(5) for r in range(3)],
            'fixed_revive_hp': .2, 'login_hours': 0, 'deadline_hours': 168,
            'selection_rule': 'Highest common-selection mean across all 22 arms and previous controls; exact ties retain lexical case ID. Final data never select candidates.',
            'start_families': ['Previous human actions and fallback ranking, zero context bonuses',
                               'Fight-heavy actions and a shared random fallback ranking',
                               'Escape/conserve actions and a different shared random fallback ranking'],
            'limitations': ['Finite search within the readable-rule family; not exhaustive rankings or a global optimum.',
                           'Equal candidate counts do not mean equal stone exposure; first/later eligibility remains unchanged.',
                           'An anchor means take it whenever offered, not force it into a run.',
                           'Model assumptions remain uncertain; sampling intervals do not cover that uncertainty.'],
            'executable': str(args.exe), 'executable_sha256': sha(args.exe),
            'profile': 'profiles/synthetic.profile', 'profile_sha256': sha(Path('profiles/synthetic.profile')),
            'source_files': frozen, 'jobs': []}
    for bi, budget in enumerate(BUDGETS):
        base = rules_read(OLD / ('human-free.rules' if budget == 0 else 'human-paid.rules'))
        for restart in range(3):
            ranks = GEMS.copy()
            random.Random(714000 + restart).shuffle(ranks)
            values = dict(base)
            for key in values:
                if key.startswith('gem.') and key[4:] not in GEMS:
                    values[key] = '0'
            if restart:
                for i, gem in enumerate(ranks):
                    values['gem.' + gem] = str(20 - i)
            values.update(revive_hp='0.2', login_interval_hours='0', fight_when_cheaper='1')
            if restart == 1:
                values.update(fight_keys='16,12,8,4', door_reserve='4,4,2,0',
                              max_fight_death_risk='0.2', min_fight_hp='0', fight_escape_cutoff='1',
                              door_mode='0', barrel_mode='1', shop_purchase_floor='60',
                              paid_refill_mode='3' if budget else '0', shop_allowance='5' if budget else '0')
            elif restart == 2:
                values.update(fight_keys='4,1,0,0', door_reserve='6,6,4,1',
                              max_fight_death_risk='0.05', min_fight_hp='0', fight_escape_cutoff='0.4',
                              door_mode='1', guaranteed_escape_door_preference='1', barrel_mode='2',
                              shop_purchase_floor='80', paid_refill_mode='3' if budget else '0',
                              shop_allowance='12' if budget else '0', fight_when_cheaper='0')
            start = out / 'starts' / f'b{budget}-r{restart}.rules'
            values['id'] = start.stem
            rules_write(start, values)
            for arm in GEMS + ['unrestricted', 'avoid_bull_hick']:
                job_id = f'b{budget}-{arm}-r{restart}'
                plan['jobs'].append({'id': job_id, 'arm': arm, 'budget': budget, 'restart': restart,
                                     'seed': 712900 + bi * 10 + restart,
                                     'initial': str(start), 'initial_sha256': sha(start)})
    plan['seed_audit'] = check_cohorts(plan)
    write_json(out / 'plan.json', plan)
    print(f'Prepared {len(plan["jobs"])} searches; plan frozen before simulation', flush=True)


def verify(plan, exe):
    if sha(exe) != plan['executable_sha256'] or sha(Path(plan['profile'])) != plan['profile_sha256']:
        raise RuntimeError('Frozen executable or profile changed')


def verify_receipt(output):
    saved = read_json(output.with_suffix('.receipt.json'))
    if saved['sha256'] != sha(output):
        raise RuntimeError(f'Report changed: {output}')
    for artifact in saved['artifacts']:
        if sha(Path(artifact['path'])) != artifact['sha256']:
            raise RuntimeError(f'Artifact changed: {artifact["path"]}')


def run_command(command, output, plan, exe):
    verify(plan, exe)
    input_path = Path(command[command.index('--rules') + 1])
    input_hash = sha(input_path)
    receipt = output.with_suffix('.receipt.json')
    if receipt.exists():
        saved = read_json(receipt)
        if saved['command'] != command or saved['sha256'] != sha(output):
            raise RuntimeError(f'Cannot reuse changed job {output}')
        for artifact in saved['artifacts']:
            if sha(Path(artifact['path'])) != artifact['sha256']:
                raise RuntimeError(f'Cannot reuse changed artifact {artifact["path"]}')
        return read_json(output)
    if output.exists():
        raise RuntimeError(f'Unverified partial job exists: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with output.with_suffix('.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, stdout=log, stderr=log, text=True)
    if result.returncode:
        raise RuntimeError(f'Job failed ({result.returncode}); see {output.with_suffix(".log")}')
    verify(plan, exe)
    if sha(input_path) != input_hash:
        raise RuntimeError('Input rules changed during execution')
    artifacts = []
    for option in ['--save-rules', '--event-completions']:
        if option in command:
            path = Path(command[command.index(option) + 1])
            artifacts.append({'path': str(path), 'sha256': sha(path)})
    write_json(receipt, {'command': command, 'sha256': sha(output), 'artifacts': artifacts,
                         'seconds': time.monotonic() - started})
    return read_json(output)


def parallel(items, function, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(function, item): item for item in items}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            print(f'{index}/{len(items)} {result}', flush=True)


def search(args, plan):
    def task(job):
        if sha(Path(job['initial'])) != job['initial_sha256']:
            raise RuntimeError('Starting rules changed')
        output = args.output_dir / 'search' / job['id'] / 'search.json'
        command = [str(args.exe.resolve()), '--mode', 'search', '--rules', job['initial'],
                   '--profile', plan['profile'], '--controller', 'rules', '--scope', 'all',
                   '--selection', 'mean', '--unique-candidates', str(plan['unique_candidates']),
                   '--fixed-revive-hp', '0.2', '--budget', str(job['budget']),
                   '--population', str(plan['population']), '--training-events', str(plan['training_events']),
                   '--validation-events', str(plan['validation_events']), '--finalists', str(plan['finalists']),
                   '--seed', str(job['seed']), '--threads', str(args.threads), '--baseline', 'none',
                   '--login-hours', '0', '--output', str(output), '--save-rules', str(output.parent / 'winner.rules')]
        if job['arm'] in GEMS:
            command += ['--anchor-gem', job['arm']]
        elif job['arm'] == 'avoid_bull_hick':
            command += ['--avoid-gems', 'bull,hick']
        result = run_command(command, output, plan, args.exe)
        if len(result['candidates']) != plan['unique_candidates'] or len(result['validation_finalists']) != plan['finalists']:
            raise RuntimeError('Search allocation was not equal')
        if (output.parent / 'winner.rules').read_text(encoding='utf-8') != result['winner_rules_text']:
            raise RuntimeError('Saved winner does not match search report')
        return job['id']
    parallel(plan['jobs'], task, args.jobs)


def evaluation(args, plan, cases, phase, events, root, samples=False):
    def task(case):
        if sha(Path(case['rules'])) != case['rules_sha256']:
            raise RuntimeError('Frozen candidate rules changed')
        output = args.output_dir / phase / (case['id'] + '.json')
        command = [str(args.exe.resolve()), '--mode', 'evaluate', '--rules', case['rules'],
                   '--profile', plan['profile'], '--controller', case['controller'],
                   '--budget', str(case['budget']), '--runs', str(events), '--seed', str(root),
                   '--threads', str(args.threads), '--baseline', 'none', '--output', str(output)]
        if samples:
            command += ['--event-completions', str(output.with_suffix('.csv'))]
        result = run_command(command, output, plan, args.exe)
        row = result['evaluation']['budgets'][0]
        if case['arm'] in GEMS and row['gem_picks'][case['arm']] != row['gem_offers'][case['arm']]:
            raise RuntimeError('An anchor was not always chosen when offered')
        if case['arm'] in ('avoid_bull_hick', 'practical_with_ban') and any(row['gem_picks'][g] for g in ('bull', 'hick')):
            raise RuntimeError('An avoided stone was selected')
        return case['id']
    parallel(cases, task, args.jobs)


def case_from(case_id, arm, budget, rules, controller='rules', **extra):
    return {'id': case_id, 'arm': arm, 'budget': budget, 'controller': controller,
            'rules': str(rules), 'rules_sha256': sha(rules), **extra}


def summary(out, phase, case):
    output = out / phase / (case['id'] + '.json')
    verify_receipt(output)
    return read_json(output)['evaluation']['budgets'][0]


def select(args, plan):
    frozen = args.output_dir / 'selection-cases.json'
    if frozen.exists():
        cases = read_json(frozen)
    else:
        cases = []
        for job in plan['jobs']:
            directory = args.output_dir / 'search' / job['id']
            if not (directory / 'search.receipt.json').exists():
                raise RuntimeError(f'Search unfinished: {job["id"]}')
            verify_receipt(directory / 'search.json')
            if (directory / 'winner.rules').read_text(encoding='utf-8') != read_json(directory / 'search.json')['winner_rules_text']:
                raise RuntimeError('Winner differs from the recorded search selection')
            cases.append(case_from(job['id'], job['arm'], job['budget'], directory / 'winner.rules', restart=job['restart']))
        for budget in BUDGETS:
            regime = 'free' if budget == 0 else 'paid'
            cases += [case_from(f'b{budget}-previous-human', 'previous_human', budget, OLD / f'human-{regime}.rules'),
                      case_from(f'b{budget}-previous-score', 'previous_score', budget, OLD / f'score-{regime}.rules',
                                'legacy' if budget == 0 else 'survival')]
        write_json(frozen, cases)
    evaluation(args, plan, cases, 'selection', plan['selection_events'], plan['selection_root'])
    # This is the only place recommendations are selected; final/ is not read.
    chosen, practical = [], {}
    for budget in BUDGETS:
        for arm in GEMS + ['unrestricted', 'avoid_bull_hick', 'previous_human', 'previous_score']:
            eligible = [c for c in cases if c['budget'] == budget and c['arm'] == arm]
            best = min(eligible, key=lambda c: (-summary(args.output_dir, 'selection', c)['completions']['mean'], c['id']))
            chosen.append(best)
        allowed = [c for c in chosen if c['budget'] == budget]
        practical[str(budget)] = min(allowed, key=lambda c: (-summary(args.output_dir, 'selection', c)['completions']['mean'], c['id']))['id']
    # Same-policy restriction isolates the ban from a separately tuned rule set.
    for budget in BUDGETS:
        original = next(c for c in chosen if c['id'] == practical[str(budget)])
        values = rules_read(Path(original['rules']))
        values['gem.bull'] = '-1000000'
        values['gem.hick'] = '-1000000'
        values['id'] = f'b{budget}-practical-with-ban'
        target = args.output_dir / 'frozen-rules' / (values['id'] + '.rules')
        rules_write(target, values)
        chosen.append(case_from(values['id'], 'practical_with_ban', budget, target, original['controller'], original=original['id']))
    freeze = {'selected_before_final': True, 'selection_root': plan['selection_root'],
              'final_root': plan['final_root'], 'practical_ids': practical, 'cases': chosen}
    path = args.output_dir / 'final-cases.json'
    if path.exists() and read_json(path) != freeze:
        raise RuntimeError('Cannot change the frozen final candidates')
    write_json(path, freeze)
    print('Frozen practical choices: ' + json.dumps(practical), flush=True)


def final(args, plan):
    frozen = read_json(args.output_dir / 'final-cases.json')
    evaluation(args, plan, frozen['cases'], 'final', plan['final_events'], plan['final_root'], True)


def paired(out, a, b):
    plan = read_json(out / 'plan.json')
    expected_count = plan['final_events']
    def values(case):
        report_path = out / 'final' / (case['id'] + '.json')
        verify_receipt(report_path)
        metadata = read_json(report_path)
        if int(metadata['settings']['root_seed']) != plan['final_root'] or metadata['settings']['runs'] != expected_count:
            raise RuntimeError('Wrong final cohort metadata')
        with (out / 'final' / (case['id'] + '.csv')).open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        result = {(int(r['budget']), int(r['event_index'])): int(r['completions']) for r in rows}
        if len(result) != len(rows) or set(result) != {(case['budget'], i) for i in range(expected_count)}:
            raise RuntimeError('Duplicate, missing or wrong-budget event rows')
        if abs(statistics.mean(result.values()) - metadata['evaluation']['budgets'][0]['completions']['mean']) > 1e-10:
            raise RuntimeError('Event samples disagree with aggregate results')
        return result
    x, y = values(a), values(b)
    if x.keys() != y.keys():
        raise RuntimeError('Paired samples differ')
    diff = [x[k] - y[k] for k in x]
    mean = statistics.mean(diff)
    se = statistics.stdev(diff) / math.sqrt(len(diff))
    return {'mean': mean, 'standard_error': se, 'normal_95_ci': [mean - 1.96 * se, mean + 1.96 * se], 'events': len(diff)}


def report(args, plan):
    out = args.output_dir
    frozen = read_json(out / 'final-cases.json')
    rows = [{**c, 'selection': summary(out, 'selection', c) if c['arm'] != 'practical_with_ban' else None,
             'final': summary(out, 'final', c)} for c in frozen['cases']]
    comparisons = []
    for budget in BUDGETS:
        byarm = {c['arm']: c for c in rows if c['budget'] == budget}
        practical = next(c for c in rows if c['id'] == frozen['practical_ids'][str(budget)])
        comparisons.append({'budget': budget, 'practical_id': practical['id'],
                            'practical_vs_previous_score': paired(out, practical, byarm['previous_score']),
                            'tuned_ban_vs_unrestricted_arm': paired(out, byarm['avoid_bull_hick'], byarm['unrestricted']),
                            'tuned_ban_vs_practical': paired(out, byarm['avoid_bull_hick'], practical),
                            'same_policy_ban_vs_practical': paired(out, byarm['practical_with_ban'], practical)})
    result = {'schema': 1, 'plan': 'plan.json', 'practical_ids': frozen['practical_ids'],
              'paired_differences': comparisons, 'cases': rows, 'limitations': plan['limitations']}
    write_json(out / 'results.json', result)
    e = html.escape
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<title>Balanced gem strategy comparison</title><style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:0 20px;background:#111824;color:#e8edf5}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{text-align:left;padding:9px;border-bottom:1px solid #394353}a{color:#91c9ff}.note{color:#b7c1d2}h1,h2{line-height:1.2}small{font-size:12px}</style>',
             '<h1>Balanced gem strategy comparison</h1>',
             '<p>Seven days, random legal stone offers, full health after completion, ordinary reentry at 20% health; bosses wait for sufficient health. Each budget has its own search.</p>',
             '<p>Each of 20 anchored strategies, an unrestricted strategy and a Bull/Hick avoidance strategy received three starts × 128 distinct candidates. Combat, doors and shops could adapt. An anchor is chosen whenever offered; it is never inserted into offers.</p>',
             '<p>128 training events per candidate; 1,024 internal selection events per finalist; 5,000 new events to select each restart and the practical recommendation. The frozen candidates then received 20,000 untouched final events each. Means below are completed runs per week.</p>',
             '<p class="note">These are policy results within a synthetic model, not causal stone values or proof of an optimal strategy. Final results did not choose the recommendations. Nine stones are unavailable on the first run of an event.</p>',
             '<h2>Practical recommendations selected before the final test</h2><table><tr><th>Mushroom cap</th><th>Selected policy</th><th>Completions (95% interval)</th><th>Mushrooms spent</th><th>Rules</th></tr>']
    for budget in BUDGETS:
        c = next(c for c in rows if c['id'] == frozen['practical_ids'][str(budget)])
        s = c['final']; ci = s['completions']['normal_95_ci']
        target = out / 'recommended' / f'budget-{budget}.rules'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(c['rules']).read_bytes())
        parts.append(f'<tr><td>{budget}</td><td>{e(c["arm"])}</td><td>{s["completions"]["mean"]:.4f} ({ci[0]:.4f}–{ci[1]:.4f})</td><td>{s["mean_mushrooms"]:.2f}</td><td><a href="recommended/budget-{budget}.rules">Download</a> · {e(c["controller"])}</td></tr>')
    parts.append('</table><h2>Every anchored strategy</h2><p>Each row uses its independently selected restart at each budget. Lower results may reflect the cost of prioritizing that stone over a better offer; they do not mean it is always a bad fallback.</p><table><tr><th>Always prioritize when offered</th>' + ''.join(f'<th>{b} mushrooms</th>' for b in BUDGETS) + '</tr>')
    for gem, name in zip(GEMS, NAMES):
        parts.append('<tr><td>' + e(name) + '</td>' + ''.join(f'<td>{next(c for c in rows if c["arm"] == gem and c["budget"] == b)["final"]["completions"]["mean"]:.4f}</td>' for b in BUDGETS) + '</tr>')
    parts.append('</table><h2>Effect of avoiding Bull and Hick</h2><p>Differences are paired on identical initial event seeds. Negative means fewer completions. Intervals describe sampling uncertainty only.</p><table><tr><th>Budget</th><th>Separately tuned ban − unrestricted arm</th><th>Same policy with ban − practical policy</th></tr>')
    for c in comparisons:
        def fmt(key):
            d = c[key]; low, high = d['normal_95_ci']
            return f'{d["mean"]:+.4f} ({low:+.4f} to {high:+.4f})'
        parts.append(f'<tr><td>{c["budget"]}</td><td>{fmt("tuned_ban_vs_unrestricted_arm")}</td><td>{fmt("same_policy_ban_vs_practical")}</td></tr>')
    parts.append('</table><p><a href="results.json">All results, intervals and stone counts</a> · <a href="plan.json">Frozen experiment plan and seed audit</a></p></html>')
    (out / 'report.html').write_text(''.join(parts), encoding='utf-8')
    print(json.dumps({'practical': [{c['budget']: c['final']['completions']['mean']} for c in rows if c['id'] in frozen['practical_ids'].values()], 'comparisons': comparisons}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['prepare', 'search', 'select', 'final', 'report'], required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('out/balanced-gems-2026-10-09/study'))
    parser.add_argument('--exe', type=Path, default=Path('build/Release/sfld_rule_search.exe'))
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 4 or not 1 <= args.threads <= 4:
        parser.error('Jobs and threads must be 1..4')
    if args.phase == 'prepare':
        prepare(args)
        return
    plan = read_json(args.output_dir / 'plan.json')
    verify(plan, args.exe)
    {'search': search, 'select': select, 'final': final, 'report': report}[args.phase](args, plan)


if __name__ == '__main__':
    main()
