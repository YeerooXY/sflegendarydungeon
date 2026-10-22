"""Compare survival, key reserves, adaptive stones and rolling shop refreshes.

Python freezes inputs and launches the C++ engine; it does not simulate actions.
Pilot and confirmation use separate seeds. All cases remain model-conditional.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cohort_overlap(seed, count, previous_seed, previous_count):
    """Count reused event inputs under Random::mix(master_seed ^ event_index)."""
    difference = seed ^ previous_seed
    return sum((difference ^ index) < previous_count for index in range(count))


def policies():
    survival = {'survival-aware': 'true'}
    reserve = {**survival, 'early-key-target': '10'}
    adaptive = {**reserve, 'adaptive-gems': 'true'}
    result = {'legacy': {}, 'survival': survival,
              'reserve7': {**survival, 'early-key-target': '7'}, 'reserve10': reserve,
              'adaptive': adaptive, 'adaptive-no-farm': {**survival, 'adaptive-gems': 'true'}}
    for allowance in (2, 5, 8):
        result[f'reroll{allowance}'] = {**adaptive, 'shop-allowance': str(allowance), 'shop-key-reserve': '4'}
    for reserved in (40, 100):
        result[f'reroll5-reserve{reserved}'] = {**result['reroll5'], 'shop-mushroom-reserve': str(reserved)}
    result['fixed-gems-reroll5'] = {**reserve, 'shop-allowance': '5', 'shop-key-reserve': '4'}
    for allowance in (2, 5, 8):
        result[f'no-farm-reroll{allowance}'] = {**survival, 'shop-allowance': str(allowance), 'shop-key-reserve': '4'}
    result['legacy-reroll5'] = {'shop-allowance': '5', 'shop-key-reserve': '4'}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exe', type=Path, default=Path('build/Release/sfld.exe'))
    p.add_argument('--profile', type=Path, default=Path('profiles/synthetic.profile'))
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--runs', type=int, default=3000)
    p.add_argument('--seed', type=int, default=2026100927)
    p.add_argument('--threads', type=int, default=3)
    p.add_argument('--cases', nargs='+', choices=tuple(policies()), default=list(policies()))
    p.add_argument('--budgets', nargs='+', type=int, default=[0, 500])
    p.add_argument('--first-run-audit', action='store_true')
    p.add_argument('--independent-of', type=Path, action='append', default=[],
                   help='Reject event-seed overlap with an earlier report from this tool; repeat for multiple reports')
    p.add_argument('--windows', action='store_true', help='Force the observed shop windows (already used by shipped profiles); label the override')
    a = p.parse_args()
    if a.runs < 1 or not 1 <= a.threads <= 256 or not 0 <= a.seed < 2**64 or any(not 0 <= b <= 1000000 for b in a.budgets):
        p.error('Invalid runs, threads, seed or budget')
    exe = a.exe.resolve(strict=True)
    independence_checks = []
    for reference in a.independent_of:
        previous = json.loads(reference.read_text(encoding='utf-8'))
        previous_seed, previous_count = previous['seed'], previous['events_per_case']
        if type(previous_seed) is not int or type(previous_count) is not int or not 0 <= previous_seed < 2**64 or previous_count < 1:
            p.error(f'Invalid seed cohort in {reference}')
        overlap = cohort_overlap(a.seed, a.runs, previous_seed, previous_count)
        if overlap:
            p.error(f'{overlap} event seeds overlap {reference}; use a disjoint master-seed/index cohort')
        independence_checks.append({'report': str(reference), 'sha256': sha(reference),
                                    'seed': previous_seed, 'events': previous_count, 'overlap': overlap})
    target = a.output_dir.resolve(); target.mkdir(parents=True, exist_ok=False)
    (target/'results').mkdir()
    profile = a.profile.read_text(encoding='utf-8')
    if a.windows:
        from benchmark_strategies import setting
        identity = next(x[3:] for x in profile.splitlines() if x.startswith('id='))
        profile = setting(profile, 'id', identity+'-shop-windows-sensitivity')
        if any(line.startswith('shop_windows=') for line in profile.splitlines()):
            profile = setting(profile, 'shop_windows', '6:24,30:40,61:70,80:85')
        else:
            profile += '\n# Observed bounds; uniformity and later-run stage-one quota remain assumptions.\nshop_windows=6:24,30:40,61:70,80:85\n'
    (target/'scenario.profile').write_text(profile, encoding='utf-8', newline='\n')
    root = Path(__file__).resolve().parents[1]
    sources = ['src/policy.cpp', 'src/strategy.cpp', 'src/adaptive_gems.cpp', 'src/engine.cpp',
               'include/sfld/damage_risk.hpp', 'include/sfld/policy.hpp', 'tools/benchmark_policy_refresh.py']
    report = {'schema': 1, 'engine': subprocess.check_output([str(exe), '--version'], text=True).strip(),
              'executable_sha256': sha(exe), 'source_sha256': {s: sha(root/s) for s in sources},
              'profile_text': profile, 'profile_sha256': sha(target/'scenario.profile'),
              'seed': a.seed, 'events_per_case': a.runs, 'shop_windows_sensitivity': a.windows,
              'event_seed_derivation': 'Random::mix(master_seed ^ event_index)',
              'independence_checks': independence_checks,
              'interpretation': ['Handwritten policies; no optimality claim.',
                'Continuous availability, 168 hours, full initial/restart health, hard gross mushroom cap.',
                'Shop allowance is credit per entered rerollable shop, not forced spending.',
                'Unused credit survives deaths and completed runs; nonrerollable opening earns none.',
                'Common event seeds; differing actions consume differing draws.',
                'Nearby master seeds may reuse trials; use --independent-of when claiming a separate holdout.',
                'First-run audits reuse event seeds, not independent additional samples.',
                'Monte Carlo errors omit unresolved mechanics and probability uncertainty.'],
              'cases': [], 'first_run_audits': []}
    def save():
        (target/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    for name in a.cases:
        settings = policies()[name]
        for budget in a.budgets:
            if budget == 0 and 'shop-allowance' in settings:
                continue  # Same free policy; no duplicative zero-budget reroll cases.
            args = ['event', '--allow-assumptions', '--profile', 'scenario.profile', '--strategy', 'key-stockpile',
                    '--budget', str(budget), '--runs', str(a.runs), '--seed', str(a.seed), '--threads', str(a.threads),
                    '--deadline-hours', '168', '--barrels', 'adaptive', '--shop-policy', 'adaptive', '--rerolls', '0']
            for key, value in settings.items(): args += ['--'+key, value]
            result = subprocess.run([str(exe), *args], cwd=target, check=True, capture_output=True, text=True)
            r = json.loads(result.stdout)
            if not r['valid'] or r['action_limit_events'] or r['events'] != a.runs or not 0 <= r['mean_mushrooms'] <= budget:
                raise RuntimeError('Invalid case '+name)
            if abs(r['mean_recovery_mushrooms']+r['mean_reroll_mushrooms']-r['mean_mushrooms']) > 1e-5:
                raise RuntimeError('Spending components disagree')
            (target/'results'/f'{name}-{budget}.json').write_text(json.dumps(r, indent=2)+'\n')
            report['cases'].append({'name': name, 'budget': budget, 'settings': settings, 'arguments': args, 'report': r})
            save()
            print(f"{name} budget={budget}: {r['mean_completions']:.4f} completions; {r['mean_reroll_mushrooms']:.2f} rerolls; {r['mean_mushrooms']:.2f} total", flush=True)
        if a.first_run_audit and 'shop-allowance' not in settings:
            args = ['simulate', '--allow-assumptions', '--profile', 'scenario.profile', '--strategy', 'key-stockpile',
                    '--budget', '0', '--runs', str(a.runs), '--seed', str(a.seed), '--threads', str(a.threads), '--deadline-hours', '168']
            for key, value in settings.items(): args += ['--'+key, value]
            r = json.loads(subprocess.run([str(exe), *args], cwd=target, check=True, capture_output=True, text=True).stdout)
            if not r['valid_deadline_experiment']: raise RuntimeError('Invalid first-run audit')
            report['first_run_audits'].append({'name': name, 'arguments': args, 'report': r}); save()
    if sha(exe) != report['executable_sha256'] or any(sha(root/s) != h for s,h in report['source_sha256'].items()):
        raise RuntimeError('Source or executable changed during the experiment')


if __name__ == '__main__':
    main()
