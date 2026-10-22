"""Further PPO with an equal-compute control and explicit effect-slot curriculum."""
import argparse
import copy
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import torch
import train_rl
from benchmark_player_ideas import transfer_changes, write_once
from train_current_study import args_common, copy as copy_file, evaluate, read
from sfld_rl.effect_lab import challenge_cases, probe_cases

ROOT=Path(__file__).resolve().parents[1]
PARENT=ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt'
BUDGETS=[0,20,50,100,500,1000]


def migrate(out):
    """Explicit API-only transfer; simulator physics and model weights unchanged."""
    saved,model=train_rl.load_checkpoint(PARENT,torch.device('cpu'))
    args=train_rl.parse(['--mode','train','--output',str(out/'unused'),*args_common(out),'--shop-policy','shop5'])
    with train_rl.make_env(args,n_envs=1) as env:
        changes=transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        if set(changes)-{'library_sha256'}:raise RuntimeError('This study must keep the same profile and schema')
        metadata=train_rl.source_metadata(args,env)
    allowed={'src/rl_env.cpp','include/sfld/rl_env.hpp','tools/train_rl.py','tools/sfld_rl/ppo.py','tools/sfld_rl/shop_policy.py'}
    changed={p: {'old':h,'new':train_rl.sha(ROOT/p)} for p,h in saved['metadata']['sources'].items() if train_rl.sha(ROOT/p)!=h}
    if {p.replace('\\','/') for p in changed}-allowed:raise RuntimeError('Unexpected source changes: '+str(changed))
    if metadata['shop_policy']!=saved['metadata']['shop_policy']:raise RuntimeError('Shop constraints changed')
    metadata['seed']=saved['metadata']['seed']
    metadata['finetune']=copy.deepcopy(saved['metadata']['finetune'])
    metadata['engine_transfer']={'schema':'sfld-effect-replay-transfer-v1','parent_sha256':train_rl.sha(PARENT),
        'original_metadata':saved['metadata'],'environment_changes':changes,'source_changes':changed,
        'new_training_decisions':0,'reason':'Visible-state replay API and cached deterministic PPO masks; no game mechanics change.',
        'verification':'Native old/new action replay parity; cached/uncached logits, values and gradients agree exactly.'}
    result=copy.deepcopy(saved);result['metadata']=metadata;result['state']['migration_only']=True
    target=out/'parent-checkpoint.pt';torch.save(result,target)
    loaded,restored=train_rl.load_checkpoint(target,torch.device('cpu'))
    for key,value in model.state_dict().items():torch.testing.assert_close(value,restored.state_dict()[key],rtol=0,atol=0)
    for index,state in saved['optimizer']['state'].items():
        for key,value in state.items():
            actual=loaded['optimizer']['state'][index][key]
            if isinstance(value,torch.Tensor):torch.testing.assert_close(value,actual,rtol=0,atol=0)
            elif value!=actual:raise RuntimeError('Changed optimizer during transfer')
    write_once(out/'parent-checkpoint.transfer.json',metadata['engine_transfer'])


def prepare(out,smoke,allow_seed_reuse=False,library=None):
    out.mkdir(parents=True,exist_ok=True)
    library=library or ROOT/'build-effects/Release/sfld_rl.dll'
    for source,name in [(PARENT,'original-checkpoint.pt'),(library,'sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile')]:copy_file(source,out/name)
    sources=[ROOT/'CMakeLists.txt',Path(__file__),ROOT/'tools/train_rl.py',ROOT/'tools/train_current_study.py',
             ROOT/'tools/benchmark_player_ideas.py']
    sources+=sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources+=sorted(p for folder in ('src','include') for p in (ROOT/folder).rglob('*') if p.suffix in ('.cpp','.hpp'))
    plan={'schema':'sfld-effect-curriculum-study-v1','smoke':smoke,'budgets':BUDGETS,
        'parent_sha256':train_rl.sha(PARENT),'library_sha256':train_rl.sha(out/'sfld_rl.dll'),
        'profile_sha256':train_rl.sha(out/'synthetic.profile'),
        'sources':{p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        'steps_per_branch':2048 if smoke else 16777216,'n_envs':8 if smoke else 512,
        'rollout_steps':16 if smoke else 128,'validation_events':2 if smoke else 128,
        'validate_every':8 if smoke else 64,'minibatch_size':64 if smoke else 2048,
        'branches':[{'name':'ordinary','seed':12200011 if smoke else 12211001,'curriculum':False},
                    {'name':'effect-curriculum','seed':12200012 if smoke else 12211002,'curriculum':True}],
        'selection':{'seed':12200013 if smoke else 12212001,'namespace':241,'events':2 if smoke else 512},
        'final':{'seed':12200014 if smoke else 12213001,'namespace':242,'events':2 if smoke else 2048},
        'probe':{'seed':12200015 if smoke else 12214001,'namespace':243,'events':4 if smoke else 2048,'budgets':[0,100]},
        'objective':'Completed runs only, gamma=1. Random eligible offered stones. Current five-credit shop constraints unchanged.',
        'curriculum':'25% of reset training episodes use the 1632 declared effect-slot positions; they start a remaining 168-hour horizon. All validation/selection/final events are ordinary fresh weeks.',
        'selection_rule':'Internal validation chooses each branch checkpoint including the parent. Independent selection chooses one overall equal-budget winner among parent and both branches; ties retain the parent.',
        'promotion_gate':'Selected winner must beat parent with positive lower family-adjusted 99% CI over equal-budget paired clusters. Two branch-parent and one branch-branch comparisons across six budgets plus pooled (21 contrasts).',
        'limitations':'One training seed per branch; challenge frequency is artificial; no proof of optimality or live-game accuracy.',
        'allow_seed_reuse':allow_seed_reuse}
    write_once(out/'plan.json',plan)
    write_once(out/'challenges.json',challenge_cases());write_once(out/'probe-cases.json',probe_cases())
    for path in sources:copy_file(path,out/'source'/path.relative_to(ROOT))
    roots=[b['seed'] for b in plan['branches']]+[plan[k]['seed'] for k in ('selection','final','probe')]
    if not (out/'seed-audit.json').exists():
        pattern=re.compile(r'"(?:root_seed|seed)"\s*:\s*(\d+)');old=set()
        for path in (ROOT/'docs').rglob('*.json'):
            old.update(map(int,pattern.findall(path.read_text(encoding='utf-8-sig'))))
        fps=[train_rl.seed_fingerprint(s) for s in roots]
        overlap=bool(set(fps)&{train_rl.seed_fingerprint(s) for s in old})
        if len(set(fps))!=len(roots) or (overlap and not allow_seed_reuse):
            raise RuntimeError('Root-seed fingerprint overlap')
        write_once(out/'seed-audit.json',{'roots':roots,'fingerprints':fps,'archived_roots_checked':len(old),
            'overlap':overlap,'reproduction_not_independent_evidence':allow_seed_reuse})
    if not (out/'parent-checkpoint.pt').exists():migrate(out)
    return plan


def train_branch(out,plan,branch):
    dest=out/branch['name'];status=out/(branch['name']+'-status.json')
    command=[sys.executable,str(ROOT/'tools/train_rl.py'),'--mode','train','--finetune-from',str(out/'parent-checkpoint.pt'),
        *args_common(out),'--output',str(dest),'--seed',str(branch['seed']),'--shop-policy','shop5',
        '--n-envs',str(plan['n_envs']),'--rollout-steps',str(plan['rollout_steps']),
        '--total-steps',str(plan['steps_per_branch']),'--validation-events',str(plan['validation_events']),
        '--validate-every',str(plan['validate_every']),'--checkpoint-every',str(plan['validate_every']),
        '--minibatch-size',str(plan['minibatch_size']),'--learning-rate','.0001',
        '--entropy-coefficient','.005','--gae-lambda','.99','--target-kl','.02']
    if branch['curriculum']:command.append('--effect-curriculum')
    write_once(out/(branch['name']+'-invocation.json'),{'command':command,'parent_sha256':train_rl.sha(out/'parent-checkpoint.pt')})
    if not status.exists():
        start=time.monotonic();print(json.dumps({'training':branch['name'],'decisions':plan['steps_per_branch']}),flush=True)
        with (out/(branch['name']+'.log')).open('x',encoding='utf-8') as log:
            result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        write_once(status,{'exit_code':result.returncode,'wall_seconds':time.monotonic()-start})
    if read(status)['exit_code']:raise RuntimeError('Training failed: '+branch['name'])
    summary=read(dest/'training-summary.json')
    if summary['state']['steps']!=plan['steps_per_branch']:raise RuntimeError('Incomplete training')
    best=read(dest/'best.json');path=dest/best['checkpoint']
    if train_rl.sha(path)!=best['sha256']:raise RuntimeError('Checkpoint changed')
    print(json.dumps({'trained':branch['name'],'best_steps':best['steps'],'validation_score':best['selection_score']}),flush=True)
    return {'name':branch['name'],'checkpoint':path.relative_to(out).as_posix(),'sha256':best['sha256'],'steps':best['steps'],'style':'practical'}


def run(out,smoke,allow_seed_reuse=False,library=None):
    torch.set_num_threads(2);plan=prepare(out,smoke,allow_seed_reuse,library)
    branches=[train_branch(out,plan,b) for b in plan['branches']]
    write_once(out/'training-selection.json',branches)
    candidates=[{'name':'parent','checkpoint':'parent-checkpoint.pt','sha256':train_rl.sha(out/'parent-checkpoint.pt'),
                 'steps':0,'style':'practical'},*branches]
    validation={(c['name'],b):evaluate(out,plan,c,'selection',b) for c in candidates for b in BUDGETS}
    means={c['name']:statistics.mean(validation[c['name'],b]['summary']['completions']['mean'] for b in BUDGETS) for c in candidates}
    selection={'winner':max(means,key=means.get),'scores':means}
    write_once(out/'selection.json',selection);print(json.dumps({'selection':selection}),flush=True)
    finals=[evaluate(out,plan,c,'final',b) for c in candidates for b in BUDGETS]
    for path,h in plan['sources'].items():
        if train_rl.sha(ROOT/path)!=h:raise RuntimeError('Frozen training source changed: '+path)
    write_once(out/'summary.json',{'plan':plan,'candidates':candidates,'selection':selection,
                                  'cells':[{k:v for k,v in cell.items() if k!='rows'} for cell in finals]})
    print('Effect training and independent weekly evaluation complete.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--smoke',action='store_true')
    p.add_argument('--allow-seed-reuse',action='store_true',help='Explicit reproduction; cannot count as a new independent final test')
    p.add_argument('--library',type=Path,help='Native replay library; defaults to build-effects/Release/sfld_rl.dll')
    a=p.parse_args();run(a.output.resolve(),a.smoke,a.allow_seed_reuse,a.library)
