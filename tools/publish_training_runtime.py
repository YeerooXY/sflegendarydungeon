"""Archive the second runtime comparison without multiplying unlike benchmarks."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import torch

ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(source,target,report):
    result=json.loads((source/'benchmark.json').read_text())
    if target.exists() or report.exists():raise FileExistsError('Archive already exists')
    for device in ('cpu','cuda'):
        rows=[r for r in result['rows'] if r['device']==device]
        for kind,key in [('loop','outputs_sha256'),('ppo_update','weights_and_optimizer_sha256')]:
            if len({r[kind][key] for r in rows})!=1:raise ValueError('Runtime parity failed')
        if len({json.dumps(r['ppo_update']['metrics'],sort_keys=True) for r in rows})!=1:raise ValueError('PPO metrics differ')
    target.mkdir(parents=True);shutil.copyfile(source/'benchmark.json',target/'benchmark.json')
    shutil.copytree(source/'source-before',target/'source-before')
    files=['tools/train_rl.py','tools/train_current_study.py','tools/sfld_rl/env.py','tools/sfld_rl/ppo.py',
           'tools/sfld_rl/shop_policy.py','tools/benchmark_training_runtime.py','tools/publish_training_runtime.py','tests/test_runtime_v2.py']
    for name in files:
        dest=target/'source-after'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    checkpoint=ROOT/result.get('checkpoint','docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt')
    library=ROOT/'build-perf/Release/sfld_rl.dll'
    if sha(checkpoint)!=result['checkpoint_sha256'] or sha(library)!=result['library_sha256']:raise ValueError('Runtime input changed')
    shutil.copyfile(checkpoint,target/'checkpoint.pt');shutil.copyfile(library,target/'sfld_rl.dll')
    shutil.copyfile(ROOT/'profiles/synthetic.profile',target/'synthetic.profile')
    environment={'python':platform.python_version(),'torch':str(torch.__version__),'platform':platform.platform(),
                 'gpu':torch.cuda.get_device_name(0),'torch_threads':2}
    (target/'environment.json').write_text(json.dumps(environment,indent=2)+'\n')
    (target/'.gitattributes').write_text('** -text\n')
    lines=['# Further training-loop performance, 2026-10-12','',
        'Before/after timings for the checkpoint identified in the archived benchmark. No probability, reward, neural weight, or game rule changed. The ratios below describe this workload; they do not establish a total training speedup.','',
        '| Workload | Before | After | Ratio |','| --- | ---: | ---: | ---: |']
    for name,label in [('cpu.loop','524,288 decisions, CPU'),('cuda.loop','524,288 decisions, GPU'),
                       ('cpu.ppo_update','One fixed PPO update, CPU'),('cuda.ppo_update','One fixed PPO update, GPU')]:
        row=result['summary'][name];lines.append(f'| {label} | {row["before"]:.4f} s | {row["after"]:.4f} s | {row["speedup"]:.3f}x |')
    lines+=['','Medians of five repetitions, reversing before/after order on alternating repetitions. The loop includes action selection, native simulation, Python results and explicit resets. Setup and hashing are excluded. PPO timing uses a fixed 32,768-row batch, two epochs and 2,048-row minibatches with KL stopping disabled to keep the workload fixed. The first CUDA optimizer call includes warm-up; the median limits its influence. These figures do not establish a total training speedup.','',
        '## Changes','',
        '- Compute shop masks from CPU observations before uploading them, avoiding an immediate GPU-to-CPU round trip. Specialized policy subclasses retain their own action implementation.','- Reuse current metrics returned by the native step, while refreshing identities after automatic episode resets. Returned arrays cannot mutate the environment\'s private metric cache.','- Transfer PPO reporting statistics together, preserving Python accumulation order and all numerical and KL checks.','',
        '## Verification','',
        'Before/after hashes match every recorded simulator result and action within each device. PPO comparisons also match final weights, optimizer moments, step counters and reporting metrics exactly. CPU/GPU arithmetic is not assumed identical to each other. Tests cover sampled and greedy policy outputs, specialized wrappers, explicit and automatic resets, and metric ownership.','',
        'The earlier 3.16x GPU benchmark used a different loop. Do not multiply its ratio by this one to claim a measured combined gain.','',
        '```powershell',
        'out/rl-env/Scripts/python.exe tools/benchmark_training_runtime.py --reference docs/runtime-v2-2026-10-12/source-before --checkpoint '+checkpoint.relative_to(ROOT).as_posix()+' --output out/runtime-v2-repeat.json',
        '```','',f'[Full samples]({target.name}/benchmark.json), [environment]({target.name}/environment.json), [checksums]({target.name}/manifest.json).','']
    report.write_text('\n'.join(lines),encoding='utf8')
    (target/'manifest.json').write_text(json.dumps({'files':{p.relative_to(target).as_posix():sha(p) for p in sorted(target.rglob('*')) if p.is_file()}},indent=2)+'\n')
    print(json.dumps({'report':str(report),'parity':'passed','summary':result['summary']}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--target',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();publish(a.source,a.target,a.report)
