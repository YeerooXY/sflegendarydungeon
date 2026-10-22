"""Evaluate frozen rule candidates on common, previously unused event seeds.

The case manifest is a JSON list of {id, controller, rules, budgets}. This tool
never searches or changes candidates. It records input hashes and commands.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess

MASK = (1 << 64) - 1


def mix(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK
    return value ^ (value >> 31)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--profiles', type=Path, nargs='+', required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--exe', type=Path, default=Path('build/Release/sfld_rule_search.exe'))
    p.add_argument('--runs', type=int, default=20000)
    p.add_argument('--seed', type=int, required=True)
    p.add_argument('--jobs', type=int, default=2)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--baseline', choices=['none', 'legacy', 'survival', 'both'], default='both')
    p.add_argument('--independent-of', type=Path, action='append', default=[])
    args = p.parse_args()
    if not 2 <= args.runs <= 10000000 or not 1 <= args.jobs <= 4 or not 1 <= args.threads <= 4:
        p.error('Invalid event or worker count')
    if not 0 <= args.seed <= MASK:
        p.error('Seed must be an unsigned 64-bit integer')
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    if not cases or len({c['id'] for c in cases}) != len(cases):
        p.error('Cases require distinct ids')
    for c in cases:
        if not c['id'].replace('-', '').replace('_', '').isalnum():
            p.error('Use letters, digits, hyphens or underscores in case ids')
        if c['controller'] not in ('rules', 'legacy', 'survival'):
            p.error('Unknown controller')
        c['rules_text'] = Path(c['rules']).read_text(encoding='utf-8')
        c['rules_sha256'] = sha(Path(c['rules']))
    if len({p.stem for p in args.profiles}) != len(args.profiles):
        p.error('Profile filenames must be distinct')
    master = (0x73 << 56) | ((mix(args.seed) & 0xffffffff) << 24)
    seeds = {mix(master ^ i) for i in range(args.runs)}
    audit = []
    for previous in args.independent_of:
        old = json.loads(previous.read_text(encoding='utf-8'))
        for cohort in old['seed_plan']['cohorts']:
            overlaps = sum(mix(int(cohort['master_seed']) ^ i) in seeds for i in range(cohort['events']))
            audit.append({'report': str(previous), 'cohort': cohort['name'], 'overlapping_events': overlaps})
            if overlaps:
                p.error(f'Final seeds overlap {previous}: {cohort["name"]}')
    manifest = {'schema': 1, 'purpose': 'Frozen-candidate evaluation, no tuning',
                'root_seed': str(args.seed), 'event_master_seed': str(master), 'runs': args.runs,
                'shared_event_seeds_across_cases_and_profiles': True,
                'executable_sha256': sha(args.exe), 'cases': cases, 'seed_audit': audit,
                'profiles': [{'path': str(x), 'sha256': sha(x), 'text': x.read_text(encoding='utf-8')}
                             for x in args.profiles], 'results': []}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    target = args.output_dir / 'manifest.json'
    target.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')

    def run(pair):
        case, profile = pair
        expected_profile_hash = next(x['sha256'] for x in manifest['profiles'] if x['path'] == str(profile))
        def verify_inputs():
            if sha(Path(case['rules'])) != case['rules_sha256'] or sha(profile) != expected_profile_hash or sha(args.exe) != manifest['executable_sha256']:
                raise RuntimeError('A frozen input changed during evaluation')
        verify_inputs()
        name = f'{profile.stem}--{case["id"]}'
        output = args.output_dir / (name + '.json')
        command = [str(args.exe.resolve()), '--mode', 'evaluate', '--controller', case['controller'],
                   '--profile', str(profile), '--rules', case['rules'], '--output', str(output),
                   '--budgets', ','.join(map(str, case['budgets'])), '--seed', str(args.seed),
                   '--runs', str(args.runs), '--threads', str(args.threads), '--baseline', args.baseline]
        result = subprocess.run(command, capture_output=True, text=True)
        (args.output_dir / (name + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'{name} failed: {result.stderr}')
        verify_inputs()
        report = json.loads(output.read_text(encoding='utf-8'))
        print(f'Finished {name}', flush=True)
        return {'id': name, 'case': case['id'], 'profile': profile.stem,
                'command': command, 'report': output.name, 'sha256': sha(output),
                'report_settings': report['settings']}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for row in pool.map(run, [(c, p) for p in args.profiles for c in cases]):
            manifest['results'].append(row)
            target.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Saved {target}', flush=True)


if __name__ == '__main__':
    main()
