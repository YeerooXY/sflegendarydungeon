"""Archive an output-identical alternating performance comparison."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from statistics import median

ROOT = Path(__file__).resolve().parents[1]


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--target', type=Path, required=True)
    p.add_argument('--baseline-library', type=Path, required=True)
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args(); source = a.source.resolve(); target = a.target.resolve()
    data = json.loads((source/'alternating.json').read_text())
    if not data['comparison']['identical_outputs']: raise ValueError('Outputs differ')
    if sha(a.library) != data['library_sha256'] or sha(a.baseline_library) != data['baseline_library_sha256']:
        raise ValueError('A measured binary changed')
    if sha(source/'actions.npz') != data['trace_sha256']: raise ValueError('Trace changed')
    for name, digest in data['sources'].items():
        if sha(ROOT/name) != digest: raise ValueError('Measured source changed: '+name)
    before, after = [data['comparison']['variants'][v] for v in ('before', 'after')]
    for kind in ('native', 'policy', 'closed_loop'):
        for device in ([None] if kind == 'native' else before[kind]):
            old = before[kind] if device is None else before[kind][device]
            new = after[kind] if device is None else after[kind][device]
            key = 'action_and_mask_sha256' if kind == 'policy' else 'all_outputs_sha256'
            if len(old) != 5 or len(new) != 5 or len({r[key] for r in old+new}) != 1:
                raise ValueError('Expected five repetitions with identical outputs')
    target.mkdir(parents=True, exist_ok=False)
    for name in ('before.json', 'after-initial.json', 'alternating.json', 'actions.npz', 'actions.profile.txt'):
        shutil.copyfile(source/name, target/name)
    shutil.copyfile(a.baseline_library, target/'baseline-sfld_rl.dll')
    shutil.copyfile(a.library, target/'sfld_rl.dll')
    # Freeze both the reference function and the measured implementation.
    reference = ROOT/'docs/effect-slots-2026-10-11/source/tools/sfld_rl/shop_policy.py'
    if sha(reference) != data['reference_shop_sha256']: raise ValueError('Reference changed')
    shutil.copyfile(reference, target/'reference_shop_policy.py')
    for name in [*data['sources'], 'tools/train_rl.py', 'tools/publish_runtime.py',
                 'tests/test_rl_shop_policy.py', 'tests/test_effect_lab.py']:
        dest = target/'source'/name; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, dest)
    (target/'.gitattributes').write_text('** -text\n', encoding='utf-8')
    speed = data['comparison']['closed_loop_speedup']
    native_old = median(r['median_seconds'] for r in before['native'])
    native_new = median(r['median_seconds'] for r in after['native'])
    rows = [('Native vector simulator + Python adapter', native_old, native_new)]
    rows += [(f'Simulator + neural decisions ({d.upper()})', s['before_seconds'], s['after_seconds']) for d,s in speed.items()]
    lines = ['# Simulator performance, 2026-10-11', '',
        'The optimized implementation preserves the actions and all recorded simulator outputs on the frozen benchmark. No game probability, reward, damage rule or policy weight changed for this comparison.', '',
        '| Workload: 524,288 decisions | Before | After | Speedup |',
        '| --- | ---: | ---: | ---: |']
    for label, old, new in rows: lines.append(f'| {label} | {old:.3f} s | {new:.3f} s | {old/new:.2f}x |')
    lines += ['', 'Medians of five repetitions per variant. Execution order alternates before/after and after/before. Each replay uses 512 environments and 1,024 vector steps. The GPU loop reaches about '+f"{speed['cuda']['decisions_per_second']:,.0f}"+' decisions/second on this machine.', '',
        'These are computation timings: setup, checkpoint loading and checksum calculation are excluded. They are not total experiment wall times or full PPO training speedups. CPU environment transitions, tensor transfers, neural action selection and returned metrics are included in the combined loop.', '',
        '## Changes', '',
        '- Cache native legal-action masks until an action, reset or snapshot restore changes the state. Preflight still checks the entire batch before mutating any game.',
        '- Check only actions relevant to the current phase; deduplicate recovery waits once. Write categorical features as zero-filled spans plus the selected value.',
        '- Evaluate deterministic shop rules on the CPU for actual shop rows. GPU inference transfers only the 51 required feature columns, avoiding hundreds of small kernel launches.',
        '- Keep PPO mask caching: fixed visible-state constraints are calculated once for each update batch. That optimization predates the before/after measurements here.', '',
        '## Equivalence checks', '',
        'Every observation, legal-action mask, reward, termination/truncation flag and returned metric is hashed throughout each replay. Both versions and all repetitions have identical hashes. The combined CPU/GPU runs also hash chosen actions. The fixed trace contains 524,288 decisions; repetitions are not additional independent game samples.', '',
        'The shop regression compares the archived Torch rules against the optimized implementation over 8,192 mixed inputs on both CPU and GPU. Existing sampling/log-likelihood/gradient checks, native mechanics and replay tests pass. The benchmark uses the previous frozen shop-aware checkpoint to isolate runtime changes from the newly selected effect-curriculum policy.', '',
        'This measurement uses Windows 11, Torch 2.11.0+cu128, two Torch CPU threads and the local CUDA device. Timing depends on hardware and workloads. The native optimization applies to the vector RL interface; it does not claim the same gain for every command-line rule-policy experiment.', '',
        '## Reproduce', '', '```powershell',
        'out/rl-env/Scripts/python.exe tools/benchmark_runtime.py `',
        f'  --library docs/{target.name}/sfld_rl.dll `',
        f'  --compare-library docs/{target.name}/baseline-sfld_rl.dll `',
        f'  --reference-shop docs/{target.name}/reference_shop_policy.py `',
        f'  --trace docs/{target.name}/actions.npz `',
        '  --output out/runtime-repeated.json --repeats 5', '```', '',
        'Windows binaries and source hashes are archived with the measurement. Other platforms should build the matching archived source versions and pass those libraries instead. Reusing this trace is intentional for performance/equivalence checks and is not new strategy evidence.', '',
        f'[Full timing samples and hashes]({target.name}/alternating.json).', '']
    a.report.write_text('\n'.join(lines), encoding='utf-8')
    (target/'manifest.json').write_text(json.dumps({'files': {
        p.relative_to(target).as_posix(): sha(p) for p in sorted(target.rglob('*')) if p.is_file()}}, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'native_speedup': native_old/native_new, 'combined': speed, 'report': str(a.report)}))


if __name__ == '__main__': main()
