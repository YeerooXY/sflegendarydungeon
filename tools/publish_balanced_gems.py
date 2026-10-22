"""Publish a verified completed balanced study without changing frozen results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import zipfile

from benchmark_balanced_gems import read_json, verify_receipt, write_json
from benchmark_rule_final import sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study', type=Path, default=Path('out/balanced-gems-2026-10-09/study'))
    p.add_argument('--output', type=Path, default=Path('docs/balanced-gems-2026-10-09'))
    args = p.parse_args()
    if args.output.exists():
        p.error('Publication directory already exists')
    plan = read_json(args.study / 'plan.json')
    results = read_json(args.study / 'results.json')
    for source in plan['source_files']:
        relative = Path(source['path'])
        if relative.is_absolute():
            relative = relative.relative_to(Path.cwd())
        if sha(args.study / 'source' / relative) != source['sha256']:
            raise RuntimeError(f'Source snapshot changed: {relative}')
    for path in args.study.glob('search/*/search.json'):
        verify_receipt(path)
    for phase in ('selection', 'final'):
        for path in (args.study / phase).glob('*.json'):
            if not path.name.endswith('.receipt.json'):
                verify_receipt(path)
    if len(list(args.study.glob('search/*/search.json'))) != len(plan['jobs']):
        raise RuntimeError('Incomplete search archive')
    for case in results['cases']:
        if sha(Path(case['rules'])) != case['rules_sha256']:
            raise RuntimeError('Frozen rules changed')
    args.output.mkdir(parents=True)
    for name in ('plan.json', 'results.json', 'report.html', 'selection-cases.json', 'final-cases.json'):
        shutil.copyfile(args.study / name, args.output / name)
    report_path = args.output / 'report.html'
    report_path.write_text(report_path.read_text(encoding='utf-8').replace(
        'ordinary reentry at 20% health', 'ordinary recovery target of 20% health (paid refills may restore more)'), encoding='utf-8')
    shutil.copytree(args.study / 'recommended', args.output / 'recommended')
    for case in results['cases']:
        target = args.output / 'rules' / (case['id'] + '.rules')
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(case['rules'], target)
    # Keep recorded repository-relative paths so extraction into a checkout
    # recreates the inputs and outputs named in the experiment receipts.
    archive_path = args.output / 'reproduction.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(args.study.rglob('*')):
            if path.is_file() and path.suffix not in ('.tmp', '.log', '.exe'):
                archive.write(path, path.as_posix())
        for case in results['cases']:
            path = Path(case['rules'])
            if not path.is_relative_to(args.study):
                if path.as_posix() not in archive.namelist():
                    archive.write(path, path.as_posix())
        for name in ('tools/publish_balanced_gems.py', 'tools/benchmark_balanced_gems.py', 'tests/test_rule_search_constraints.py',
                     'tests/test_balanced_gems.py', 'tests/rule_policy.cpp'):
            path = Path(name)
            archive.write(path, path.as_posix())
    index = {'schema': 1, 'purpose': 'Frozen balanced gem-priority study publication',
             'plan_sha256': sha(args.output / 'plan.json'),
             'distinct_candidate_evaluations': len(plan['jobs']) * plan['unique_candidates'],
             'training_event_simulations': len(plan['jobs']) * plan['unique_candidates'] * plan['training_events'],
             'internal_selection_event_simulations': len(plan['jobs']) * plan['finalists'] * plan['validation_events'],
             'common_selection_event_simulations': len(read_json(args.study / 'selection-cases.json')) * plan['selection_events'],
             'final_event_simulations': len(results['cases']) * plan['final_events'],
             'archive_layout': 'Repository-relative paths; study source/ contains the pre-run source snapshot. No executable or game assets included.',
             'files': [{'path': str(path.relative_to(args.output)), 'sha256': sha(path)}
                       for path in sorted(args.output.rglob('*')) if path.is_file()]}
    index['total_event_simulations'] = sum(index[k] for k in ('training_event_simulations',
        'internal_selection_event_simulations', 'common_selection_event_simulations', 'final_event_simulations'))
    write_json(args.output / 'publication-manifest.json', index)
    print(json.dumps({'output': str(args.output), 'archive_mb': archive_path.stat().st_size / 1e6,
                      'event_simulations': index['total_event_simulations']}, indent=2))


if __name__ == '__main__':
    main()
