"""Two independent continuations for each current policy, with untouched tests."""
import argparse
import copy
import json
from pathlib import Path
import re
from statistics import mean, NormalDist
import subprocess
import sys
import time
import torch
import train_rl
import train_current_study as previous
from benchmark_player_ideas import write_once, transfer_changes
from publish_effect_study import stats

ROOT=Path(__file__).resolve().parents[1]
BUDGETS=[0,20,50,100,500,1000]
PARENTS={'raw':ROOT/'docs/rl-current-2026-10-11/unrestricted/checkpoint-000025165824.pt',
         'shop':ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt'}


def transfer(out,family):
    original=out/f'original-{family}.pt'
    saved,model=train_rl.load_checkpoint(original,torch.device('cpu'))
    args=train_rl.parse(['--mode','train','--output',str(out/'unused'),'--library',str(out/'sfld_rl.dll'),
                         '--profile',str(out/'synthetic.profile'),'--shop-policy','learned' if family=='raw' else 'shop5'])
    with train_rl.make_env(args,n_envs=1) as env:
        changes=transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        if set(changes)-{'library_sha256'}:raise RuntimeError('Unexpected physics/schema transfer')
        metadata=train_rl.source_metadata(args,env)
    allowed={'src/rl_env.cpp','include/sfld/rl_env.hpp','tools/train_rl.py','tools/sfld_rl/ppo.py',
             'tools/sfld_rl/shop_policy.py','tools/sfld_rl/env.py'}
    changed={p:{'old':h,'new':train_rl.sha(ROOT/p)} for p,h in saved['metadata']['sources'].items() if train_rl.sha(ROOT/p)!=h}
    if {p.replace('\\','/') for p in changed}-allowed:raise RuntimeError('Unreviewed source change: '+str(changed))
    if metadata['shop_policy']!=saved['metadata']['shop_policy']:raise RuntimeError('Changed shop constraints')
    metadata['seed']=saved['metadata']['seed'];metadata['finetune']=copy.deepcopy(saved['metadata']['finetune'])
    record={'original_checkpoint_sha256':train_rl.sha(original),'original_metadata':saved['metadata'],
            'source_changes':changed,'environment_changes':changes,'new_training_decisions':0,
            'reason':'Replay API and runtime optimizations only; exact simulator, policy and PPO parity benchmark. No game mechanics change.'}
    metadata['runtime_transfer']=record
    result=copy.deepcopy(saved);result['metadata']=metadata;result['state']['migration_only']=True
    target=out/f'parent-{family}.pt';torch.save(result,target)
    loaded,restored=train_rl.load_checkpoint(target,torch.device('cpu'))
    for k,v in model.state_dict().items():torch.testing.assert_close(v,restored.state_dict()[k],rtol=0,atol=0)
    for i,state in saved['optimizer']['state'].items():
        for k,v in state.items():
            actual=loaded['optimizer']['state'][i][k]
            if isinstance(v,torch.Tensor):torch.testing.assert_close(v,actual,rtol=0,atol=0)
            elif v!=actual:raise RuntimeError('Optimizer changed during transfer')
    write_once(out/f'parent-{family}.transfer.json',record)


def prepare(out,smoke):
    out.mkdir(parents=True,exist_ok=False)
    for p,n in [(ROOT/'build-perf/Release/sfld_rl.dll','sfld_rl.dll'),(ROOT/'profiles/synthetic.profile','synthetic.profile')]:previous.copy(p,out/n)
    for family,path in PARENTS.items():previous.copy(path,out/f'original-{family}.pt')
    sources=[Path(__file__),ROOT/'tools/train_rl.py',ROOT/'tools/train_current_study.py',ROOT/'tools/benchmark_player_ideas.py',ROOT/'CMakeLists.txt']
    sources+=sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources+=sorted(p for folder in ('src','include') for p in (ROOT/folder).rglob('*') if p.suffix in ('.cpp','.hpp'))
    branches=[{'name':f'{family}-{i+1}','family':family,'seed':(12309001 if smoke else 12311001)+2*j+i,
               'steps':2048 if smoke else (33554432 if family=='raw' else 16777216)} for j,family in enumerate(PARENTS) for i in range(2)]
    plan={'schema':'sfld-multiseed-study-v1','smoke':smoke,'budgets':BUDGETS,'branches':branches,
          'parents':{f:train_rl.sha(p) for f,p in PARENTS.items()},'profile_sha256':train_rl.sha(out/'synthetic.profile'),
          'library_sha256':train_rl.sha(out/'sfld_rl.dll'),'sources':{p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
          'settings':{'n_envs':8 if smoke else 512,'rollout_steps':16 if smoke else 128,
                      'minibatch_size':64 if smoke else 2048,'validation_events':2 if smoke else 256,
                      'validate_every':8 if smoke else 64,'learning_rate':.00007,'entropy_coefficient':.0075,
                      'gae_lambda':.99,'target_kl':.02},
          'selection':{'seed':12309011 if smoke else 12312001,'namespace':247,'events':2 if smoke else 1024},
          'final':{'seed':12309012 if smoke else 12313001,'namespace':248,'events':4 if smoke else 8192},
          'sensitivity':{'seed':12309013 if smoke else 12314001,'namespace':249,'events':2 if smoke else 1024},
          'sensitivity_profiles':{'rare_blessings':{'strong_effect':.15,'barrel_blessing':.35,'combat_curse_chance':.15},
                                  'common_blessings':{'strong_effect':.35,'barrel_blessing':.65,'combat_curse_chance':.05}},
          'objective':'Completions only, gamma one, 168 hours, random eligible stones. Raw learns all legal choices; shop retains the current five-credit/four-key mask and evaluation recovery rules.',
          'selection_rule':'Within each family, parent and both internally selected checkpoints compete on fresh equal-budget means. Exact ties keep parent. No final-result checkpoint selection.',
          'promotion_gate':'Selected challenger must have positive lower family-adjusted 99% bound on equal-budget gain and no negative upper bound at any budget. Fourteen contrasts cover two families and six budgets plus pooled. Shop also needs browser acceptance.',
          'sensitivity_rule':'Frozen selected candidates only. Stress settings are hypothetical, not confidence intervals or alternative training data. Report ranking changes separately; no tuning on these results.',
          'limits':'Two continuation seeds per family, from one selected parent each. No proof of global optimality or validated live-game performance.'}
    historical=set();pattern=re.compile(r'"(?:root_seed|seed)"\s*:\s*"?(\d+)')
    for p in (ROOT/'docs').rglob('*.json'):historical.update(map(int,pattern.findall(p.read_text(encoding='utf-8-sig'))))
    roots=[b['seed'] for b in branches]+[plan[k]['seed'] for k in ('selection','final','sensitivity')]
    fp=[train_rl.seed_fingerprint(r) for r in roots]
    if len(set(fp))!=len(fp) or set(fp)&{train_rl.seed_fingerprint(r) for r in historical}:raise RuntimeError('Seed fingerprint reuse')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',{'roots':roots,'fingerprints':fp,'historical_overlap':False})
    for p in sources:previous.copy(p,out/'source'/p.relative_to(ROOT))
    for family in PARENTS:transfer(out,family)
    return plan


def train(out,plan,branch):
    s=plan['settings'];dest=out/branch['name']
    command=[sys.executable,str(ROOT/'tools/train_rl.py'),'--mode','train','--finetune-from',str(out/f'parent-{branch["family"]}.pt'),
             '--library',str(out/'sfld_rl.dll'),'--profile',str(out/'synthetic.profile'),'--output',str(dest),
             '--budgets',','.join(map(str,BUDGETS)),'--device','cuda','--torch-threads','2',
             '--seed',str(branch['seed']),'--shop-policy','learned' if branch['family']=='raw' else 'shop5',
             '--total-steps',str(branch['steps']),'--checkpoint-every',str(s['validate_every'])]
    for key,value in s.items():command+=['--'+key.replace('_','-'),str(value)]
    write_once(out/(branch['name']+'-invocation.json'),{'command':command})
    print(json.dumps({'training':branch['name'],'decisions':branch['steps']}),flush=True);start=time.monotonic()
    with (out/(branch['name']+'.log')).open('x',encoding='utf8') as log:result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    write_once(out/(branch['name']+'-status.json'),{'exit_code':result.returncode,'seconds':time.monotonic()-start})
    if result.returncode:raise RuntimeError('Training failed: '+branch['name'])
    summary=previous.read(dest/'training-summary.json');best=previous.read(dest/'best.json');checkpoint=dest/best['checkpoint']
    if summary['state']['steps']!=branch['steps'] or train_rl.sha(checkpoint)!=best['sha256']:raise RuntimeError('Incomplete training or changed checkpoint')
    candidate={'name':branch['name'],'family':branch['family'],'checkpoint':checkpoint.relative_to(out).as_posix(),
               'sha256':best['sha256'],'steps':best['steps'],'style':'raw' if branch['family']=='raw' else 'practical'}
    print(json.dumps({'trained':candidate,'completed_training_events':summary['state']['completed_training_events']}),flush=True)
    return candidate


def run(out,smoke):
    torch.set_num_threads(2);plan=prepare(out,smoke)
    parents=[{'name':'parent-'+f,'family':f,'checkpoint':f'parent-{f}.pt','sha256':train_rl.sha(out/f'parent-{f}.pt'),
              'steps':0,'style':'raw' if f=='raw' else 'practical'} for f in PARENTS]
    trained=[train(out,plan,b) for b in plan['branches']];candidates=parents+trained
    write_once(out/'training-selection.json',candidates)
    cells={(c['name'],b):previous.evaluate(out,plan,c,'selection',b) for c in candidates for b in BUDGETS}
    selection={}
    for family in PARENTS:
        scores={c['name']:mean(cells[c['name'],b]['summary']['completions']['mean'] for b in BUDGETS) for c in candidates if c['family']==family}
        selection[family]={'winner':max(scores,key=scores.get),'scores':scores}
    write_once(out/'selection.json',selection);print(json.dumps({'selection':selection}),flush=True)
    final={};contrasts={};promotion={};z=NormalDist().inv_cdf(1-.01/(2*14));selected=[]
    for family in PARENTS:
        parent=next(c for c in parents if c['family']==family);winner=next(c for c in candidates if c['name']==selection[family]['winner'])
        selected.extend([parent] if parent==winner else [parent,winner])
        for c in ([parent] if parent==winner else [parent,winner]):
            for b in BUDGETS:final[c['name'],b]=previous.evaluate(out,plan,c,'final',b)
        if parent==winner:promotion[family]=False;continue
        deltas=[];rows=[]
        for b in BUDGETS:
            delta=[n['completions']-o['completions'] for o,n in zip(final[parent['name'],b]['rows'],final[winner['name'],b]['rows'])]
            deltas.append(delta);rows.append({'budget':b,**stats(delta,z)})
        rows.append({'budget':'equal-budget mean',**stats([mean(x) for x in zip(*deltas)],z)})
        contrasts[family]=rows;promotion[family]=not smoke and rows[-1]['family_99_ci'][0]>0 and all(r['family_99_ci'][1]>=0 for r in rows[:-1])
    write_once(out/'primary-result.json',{'selection':selection,'contrasts':contrasts,'promotion':promotion})
    # Sensitivity leaves checkpoints fixed and permits only profile identity to differ.
    sensitivities={}
    for scenario,changes in plan['sensitivity_profiles'].items():
        sub=out/scenario;sub.mkdir();text=(out/'synthetic.profile').read_text()
        for k,v in changes.items():
            text,count=re.subn(r'^'+re.escape(k)+r'=.*$',k+'='+str(v),text,flags=re.M)
            if count!=1:raise RuntimeError('Missing sensitivity parameter '+k)
        (sub/'synthetic.profile').write_text(text,encoding='utf8');previous.copy(out/'sfld_rl.dll',sub/'sfld_rl.dll')
        setup={**plan,'profile_sha256':train_rl.sha(sub/'synthetic.profile')}
        for c in selected:
            dest=sub/c['checkpoint'];previous.copy(out/c['checkpoint'],dest)
        # Evaluation's transfer check permits profile differences only when explicit.
        original=previous.transfer_changes
        def sensitivity_transfer(saved,current,allow):
            changed=original(saved,current,True)
            if set(changed)-{'profile_sha256'}:raise RuntimeError('Unexpected sensitivity change')
            return changed
        previous.transfer_changes=sensitivity_transfer
        try:result=[previous.evaluate(sub,setup,c,'sensitivity',b) for c in selected for b in BUDGETS]
        finally:previous.transfer_changes=original
        sensitivities[scenario]=[{k:v for k,v in cell.items() if k!='rows'} for cell in result]
    for p,h in plan['sources'].items():
        if train_rl.sha(ROOT/p)!=h:raise RuntimeError('Frozen source changed: '+p)
    write_once(out/'summary.json',{'selection':selection,'contrasts':contrasts,'promotion':promotion,'sensitivity':sensitivities,
        'final':[{k:v for k,v in c.items() if k!='rows'} for c in final.values()]})
    print(json.dumps({'complete':True,'promotion':promotion}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--smoke',action='store_true')
    a=p.parse_args();run(a.output.resolve(),a.smoke)
