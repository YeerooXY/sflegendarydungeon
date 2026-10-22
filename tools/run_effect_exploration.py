"""Preregistered exploration extension; fresh selection/final cohorts for four policies."""
import argparse
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import torch
import train_rl
from benchmark_player_ideas import write_once
from train_current_study import copy, read, evaluate, args_common

ROOT=Path(__file__).resolve().parents[1]
BUDGETS=[0,20,50,100,500,1000]


def run(main,out,smoke,allow_seed_reuse=False):
    torch.set_num_threads(2);out.mkdir(parents=True,exist_ok=True)
    for name in ('parent-checkpoint.pt','sfld_rl.dll','synthetic.profile'):copy(main/name,out/name)
    sources=['tools/train_effect_exploration.py','tools/run_effect_exploration.py','tools/train_rl.py',
             'tools/sfld_rl/ppo.py','tools/sfld_rl/shop_policy.py','tools/sfld_rl/effect_lab.py',
             'tools/train_current_study.py']
    plan={'schema':'sfld-targeted-exploration-extension-v1','smoke':smoke,'budgets':BUDGETS,
        'parent_sha256':train_rl.sha(out/'parent-checkpoint.pt'),'library_sha256':train_rl.sha(out/'sfld_rl.dll'),
        'profile_sha256':train_rl.sha(out/'synthetic.profile'),
        'steps':2048 if smoke else 8388608,'seed':12220011 if smoke else 12221001,
        'temperature':8,'curriculum_fraction':.75,
        'selection':{'seed':12220012 if smoke else 12222001,'namespace':244,'events':2 if smoke else 512},
        'final':{'seed':12220013 if smoke else 12223001,'namespace':245,'events':2 if smoke else 2048},
        'sources':{p:train_rl.sha(ROOT/p) for p in sources},
        'reason':'The inherited full-curse chest opening probability was about 2.7e-10. More ordinary rollouts almost never test that action.',
        'training':'PPO temperature 8 only at declared stacked-effect decisions; same temperature in collection and likelihoods. 75% partial reset challenges. No changed game odds or rewards.',
        'selection_rule':'Equal mean over budgets; choose among parent and internally selected ordinary, curriculum, exploration checkpoints. Parent eligible and first on ties. No final results used for selection.',
        'promotion_gate':'Selected candidate versus parent must have positive lower family-adjusted 99% interval. All six policy pairs, six budgets plus pooled, 42 contrasts.',
        'relationship_to_first_study':'An extension prompted by inspecting parent action probabilities and diagnostic rollouts. Uses no first-study final results and fresh selection/final roots.',
        'limitations':'A single exploratory training seed; finite search, synthetic probabilities, no guarantee of optimality.',
        'allow_seed_reuse':allow_seed_reuse}
    write_once(out/'plan.json',plan)
    for source in sources:copy(ROOT/source,out/'source'/source)
    if not (out/'seed-audit.json').exists():
        roots=[plan['seed'],plan['selection']['seed'],plan['final']['seed']]
        historical=set();pattern=re.compile(r'"(?:root_seed|seed)"\s*:\s*(\d+)')
        for p in (ROOT/'docs').rglob('*.json'):historical.update(map(int,pattern.findall(p.read_text(encoding='utf-8-sig'))))
        historical.update([12211001,12211002,12212001,12213001,12214001])
        fingerprints={train_rl.seed_fingerprint(s) for s in roots}
        overlap=bool(fingerprints&{train_rl.seed_fingerprint(s) for s in historical})
        if len(fingerprints)!=len(roots) or (overlap and not allow_seed_reuse):raise ValueError('Seed overlap')
        write_once(out/'seed-audit.json',{'roots':roots,'fingerprints':sorted(fingerprints),'overlap':overlap,
            'reproduction_not_independent_evidence':allow_seed_reuse})
    dest=out/'exploration';status=out/'training-status.json'
    command=[sys.executable,str(ROOT/'tools/train_effect_exploration.py'),'--mode','train',
        '--finetune-from',str(out/'parent-checkpoint.pt'),*args_common(out),'--output',str(dest),
        '--seed',str(plan['seed']),'--shop-policy','shop5','--effect-curriculum','--n-envs','8' if smoke else '512',
        '--rollout-steps','16' if smoke else '128','--total-steps',str(plan['steps']),
        '--validation-events','2' if smoke else '128','--validate-every','16' if smoke else '64',
        '--checkpoint-every','16' if smoke else '64','--minibatch-size','64' if smoke else '2048',
        '--learning-rate','.0001','--entropy-coefficient','.005','--gae-lambda','.99','--target-kl','.02']
    write_once(out/'invocation.json',{'command':command})
    if not status.exists():
        print(json.dumps({'exploration_training':plan['steps']}),flush=True);started=time.monotonic()
        with (out/'training.log').open('x',encoding='utf-8') as log:
            trained=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        write_once(status,{'exit_code':trained.returncode,'wall_seconds':time.monotonic()-started})
    if read(status)['exit_code']:raise RuntimeError('Exploration training failed')
    best=read(dest/'best.json');candidate={'name':'exploration','checkpoint':'exploration/'+best['checkpoint'],
        'sha256':best['sha256'],'steps':best['steps'],'style':'practical'}
    candidates=[{'name':'parent','checkpoint':'parent-checkpoint.pt','sha256':plan['parent_sha256'],'steps':0,'style':'practical'}]
    if not smoke:
        while not (main/'training-selection.json').exists():
            print('Waiting for the two original branches to finish training.',flush=True);time.sleep(30)
        for c in read(main/'training-selection.json'):
            copy(main/c['checkpoint'],out/c['checkpoint']);candidates.append(c)
    candidates.append(candidate);write_once(out/'training-selection.json',candidates)
    validation={(c['name'],b):evaluate(out,plan,c,'selection',b) for c in candidates for b in BUDGETS}
    scores={c['name']:statistics.mean(validation[c['name'],b]['summary']['completions']['mean'] for b in BUDGETS) for c in candidates}
    selection={'winner':max(scores,key=scores.get),'scores':scores};write_once(out/'selection.json',selection)
    print(json.dumps({'extension_selection':selection}),flush=True)
    finals=[evaluate(out,plan,c,'final',b) for c in candidates for b in BUDGETS]
    for p,h in plan['sources'].items():
        if train_rl.sha(ROOT/p)!=h:raise RuntimeError('Exploration source changed: '+p)
    write_once(out/'summary.json',{'plan':plan,'candidates':candidates,'selection':selection,
        'cells':[{k:v for k,v in x.items() if k!='rows'} for x in finals]})
    print('Exploration extension and fresh four-policy comparison complete.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--smoke',action='store_true')
    p.add_argument('--allow-seed-reuse',action='store_true',help='Explicit reproduction; cannot count as a new independent final test')
    a=p.parse_args();run(a.study.resolve(),a.output.resolve(),a.smoke,a.allow_seed_reuse)
