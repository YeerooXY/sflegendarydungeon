"""Independently audit indexed stone-rule results and publish a compact archive.

Uses the standard library only; does not import the experiment's aggregators.
Run after benchmark_stone_exclusion.py has written summary.json.
"""
from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'out/stone-exclusion-2026-10-10'
OUTPUT = ROOT / 'docs/stone-exclusion-2026-10-10'
METRICS = ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms', 'deaths', 'actions')
COLUMNS = ('arm', 'budget', 'event_index', *METRICS, 'elapsed_hours', 'first_completion_hours')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def check_interval(values, reported):
    mean = statistics.mean(values)
    se = statistics.stdev(values) / math.sqrt(len(values))
    expected = {'mean': mean, 'standard_error': se,
                'normal_95_ci': [mean - 1.96 * se, mean + 1.96 * se],
                'normal_99_ci': [mean - 2.5758293035489004 * se, mean + 2.5758293035489004 * se]}
    for key, value in expected.items():
        if isinstance(value, list):
            assert all(math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-12)
                       for x, y in zip(value, reported[key]))
        else:
            assert math.isclose(value, reported[key], rel_tol=1e-12, abs_tol=1e-12)


def save_once(path, content):
    if isinstance(content, dict):
        content = (json.dumps(content, indent=2, allow_nan=False) + '\n').encode()
    if path.exists():
        assert path.read_bytes() == content, f'Refusing to replace changed archive: {path}'
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def main():
    plan, summary = read(INPUT / 'plan.json'), read(INPUT / 'summary.json')
    unsigned = {k: v for k, v in plan.items() if k != 'plan_content_sha256'}
    assert hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == plan['plan_content_sha256']
    assert summary['plan_sha256'] == plan['plan_content_sha256']
    for key, path in plan['input_paths'].items():
        assert sha(Path(path)) == plan['hashes'][key], f'Changed input: {key}'
    for arm, info in plan['profiles'].items():
        assert sha(Path(info['path'])) == info['sha256'], arm
    indexed_hashes = {x['path']: x['sha256'] for x in summary['indexed_artifacts']}
    all_rows, comparisons, validation = [], [], []
    total_actions = 0
    for budget in plan['budgets']:
        identity = [read(INPUT / f'identity-{arm}-cap-{budget}.json') for arm in ('legacy', 'held_only')]
        assert identity[0]['outcomes'] == identity[1]['outcomes']
        assert len(identity[0]['outcomes']) == plan['identity_events']
        cells = {}
        for arm in ('held_only', 'all_offered'):
            path = INPUT / f'main-{arm}-cap-{budget}.json'
            assert sha(path) == indexed_hashes[path.name]
            cell = read(path)
            rows = cell['outcomes']
            assert len(rows) == plan['events_per_arm_budget']
            assert [row['event_index'] for row in rows] == list(range(len(rows)))
            assert cell['configuration']['plan_sha256'] == plan['plan_content_sha256']
            assert cell['environment']['library_sha256'] == plan['hashes']['library']
            menus, repeated_runs, three_menu_runs = 0, 0, 0
            for row in rows:
                assert row['budget'] == budget and row['terminated'] and not row['truncated']
                assert row['invalid_actions'] == 0 and row['decisions'] == row['actions'] < 100000
                assert 167.999 <= row['elapsed_hours'] <= 168.00000001
                assert row['mushrooms'] == row['recovery_mushrooms'] + row['reroll_mushrooms'] <= budget
                total_actions += row['actions']
                for number, run in enumerate(row['stone_runs'], 1):
                    assert run['run_number'] == number
                    assert run['run_class'] == ('first' if number == 1 else 'later')
                    seen, chosen, repeats = set(), set(), False
                    assert len(run['menus']) <= 3
                    three_menu_runs += len(run['menus']) == 3
                    for i, menu in enumerate(run['menus'], 1):
                        offered = set(menu['offered'])
                        assert len(offered) == len(menu['offered']) == 3
                        assert menu['selection'] == i and not (offered & chosen)
                        assert menu['chosen'] is None or menu['chosen'] in offered
                        repeats |= bool(offered & seen)
                        seen.update(offered)
                        if menu['chosen'] is not None:
                            chosen.add(menu['chosen'])
                        menus += 1
                    if arm == 'all_offered':
                        assert not repeats
                    repeated_runs += repeats
                all_rows.append({'arm': arm, **{key: row[key] for key in COLUMNS if key != 'arm'}})
            for metric in METRICS:
                check_interval([r[metric] for r in rows], cell['summary'][metric])
            assert {str(k): v for k, v in Counter(r['completions'] for r in rows).items()} == cell['summary']['completion_distribution']
            published = next(s for s in summary['summaries'] if s['arm'] == arm and s['budget'] == budget)
            assert all(published[k] == v for k, v in cell['summary'].items())
            validation.append({'budget': budget, 'arm': arm, 'events': len(rows), 'observed_menus': menus,
                               'observed_three_menu_runs': three_menu_runs, 'observed_runs_with_repeats': repeated_runs})
            cells[arm] = rows
        paired = next(c for c in summary['comparisons'] if c['budget'] == budget)
        old, new = cells['held_only'], cells['all_offered']
        for metric in METRICS[:-1]:
            check_interval([b[metric] - a[metric] for a, b in zip(old, new)], paired[metric])
        diffs = [b['completions'] - a['completions'] for a, b in zip(old, new)]
        assert paired['wins'] == sum(x > 0 for x in diffs)
        assert paired['ties'] == diffs.count(0)
        assert paired['losses'] == sum(x < 0 for x in diffs)
        comparisons.append({'budget': budget, 'old': statistics.mean(x['completions'] for x in old),
                            'new': statistics.mean(x['completions'] for x in new),
                            'delta': statistics.mean(diffs), 'ci95': paired['completions']['normal_95_ci'],
                            'old_at_least_three': sum(x['completions'] >= 3 for x in old) / len(old),
                            'new_at_least_three': sum(x['completions'] >= 3 for x in new) / len(new)})
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(all_rows)
    save_once(OUTPUT / 'outcomes.csv', buffer.getvalue().encode())
    for filename in ('plan.json', 'summary.json', 'identity.json', 'held_only.profile',
                     'all_offered.profile', 'baseline-original.profile', 'sfld_rl.dll', 'benchmark_stone_exclusion.py'):
        save_once(OUTPUT / filename, (INPUT / filename).read_bytes())
    audit = {'schema': 'sfld-stone-exclusion-independent-audit-v1', 'published_on': '2026-10-11',
             'passed': True, 'events': len(all_rows), 'actions': total_actions,
             'identity_events_per_engine': plan['identity_events'] * len(plan['budgets']),
             'identity_events_are_reused_main_prefix': True, 'cells': validation,
             'comparisons': comparisons, 'outcomes_csv_sha256': sha(OUTPUT / 'outcomes.csv'),
             'publisher_sha256': sha(Path(__file__)),
             'telemetry_limit': 'Menus generated by the final event-ending action may not reach pre-action telemetry. Counts describe observed decision screens.',
             'method': 'Independent standard-library recomputation from indexed outcomes; all budgets, terminations, action totals, menus, source hashes, aggregate means and paired intervals checked.'}
    save_once(OUTPUT / 'verification.json', audit)
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
