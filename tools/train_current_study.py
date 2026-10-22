"""Fresh PPO continuation on engine 0.6.9/profile v4, with a shop-aware branch.

Training, selection and final event cohorts are independent. No live requests.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import interval, transfer_changes, write_once
from migrate_current_engine import migrate
from sfld_rl.env import VectorEnv
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values
from sfld_rl.shop_policy import with_shop, profile_config

ROOT=Path(__file__).resolve().parents[1]
PARENT=ROOT/'docs/pendant-adaptation-2026-10-11/control/selected-checkpoint.pt'
BUDGETS=[0,20,50,100,500,1000]

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():shutil.copyfile(source,target)
    if train_rl.sha(source)!=train_rl.sha(target):raise RuntimeError('Snapshot changed: '+str(target))

def args_common(out):
    return ['--library',str(out/'sfld_rl.dll'),'--profile',str(out/'synthetic.profile'),
            '--budgets',','.join(map(str,BUDGETS)),'--device','cuda','--torch-threads','2']

def prepare(out,smoke):
    out.mkdir(parents=True,exist_ok=True)
    for source,target in [(PARENT,'original-checkpoint.pt'),(ROOT/'build/Release/sfld_rl.dll','sfld_rl.dll'),
                          (ROOT/'profiles/synthetic.profile','synthetic.profile')]:copy(source,out/target)
    sources=[Path(__file__),ROOT/'tools/migrate_current_engine.py',ROOT/'tools/train_rl.py',
             ROOT/'tools/benchmark_player_ideas.py',ROOT/'CMakeLists.txt']
    sources+=sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources+=sorted(p for folder in ('src','include') for p in (ROOT/folder).rglob('*') if p.suffix in ('.cpp','.hpp'))
    hashes={str(p.relative_to(ROOT).as_posix()):train_rl.sha(p) for p in sources}
    plan={'schema':'sfld-corrected-ppo-study-v1','budgets':BUDGETS,'smoke':smoke,
          'parent_sha256':train_rl.sha(PARENT),'library_sha256':train_rl.sha(out/'sfld_rl.dll'),
          'profile_sha256':train_rl.sha(out/'synthetic.profile'),'sources':hashes,
          'steps_per_branch':1024 if smoke else 33554432,'n_envs':8 if smoke else 512,
          'rollout_steps':16 if smoke else 128,'validation_events':2 if smoke else 256,
          'validate_every':8 if smoke else 64,'minibatch_size':64 if smoke else 2048,
          'learning_rate':.0001,'entropy_coefficient':.005,'gae_lambda':.99,'target_kl':.02,
          'branches':[{'name':'unrestricted','shop_policy':'learned','seed':12100001 if smoke else 12101001},
                      {'name':'shop-aware','shop_policy':'shop5','seed':12100002 if smoke else 12101002}],
          'selection':{'seed':12100003 if smoke else 12102001,'namespace':239,'events':2 if smoke else 1024},
          'final':{'seed':12100004 if smoke else 12103001,'namespace':240,'events':2 if smoke else 2048},
          'objective':'Completion increments only, gamma one, 168-hour event; budgets separate, random eligible offered stones.',
          'selection_rule':'Internal best checkpoint by equal mean over budgets, then fresh selection chooses parent or continuation separately for unrestricted and practical play, overall and per budget. Exact ties keep the earlier candidate.',
          'shop_constraint':'Five rolling credits, four keys before reroll, stop for useful offer. Same mask during collection and PPO. Other legal decisions learned; stone preference unconstrained.',
          'evaluation':'All arms search a soldier with guaranteed effective escape. Practical arms retain the safe final-boss wait. Original browser shop wrapper and untrained mask-only parent are separate controls.',
          'limits':'Single training seed per branch. No proof of optimality or live-game accuracy.'}
    roots=[b['seed'] for b in plan['branches']]+[plan[k]['seed'] for k in ('selection','final')]
    if len({train_rl.seed_fingerprint(s) for s in roots})!=len(roots):raise RuntimeError('Seed fingerprints overlap')
    write_once(out/'plan.json',plan)
    write_once(out/'deployment-rule.json',{
        'schema':'sfld-training-promotion-v1',
        'selection':'Overall practical winner frozen on the independent selection cohort; no choice by final scores.',
        'gate':'Promote only if its equal-budget final completion difference versus the previous browser has a positive lower family-adjusted 99% confidence bound. Otherwise retain the previous browser.',
        'note':'This is a deployment gate, not a change to training or checkpoint selection.'})
    if not (out/'seed-audit.json').exists():
        historical=set()
        pattern=re.compile(r'"(?:root_seed|seed)"\s*:\s*(\d+)')
        for path in (ROOT/'docs').rglob('*.json'):
            with path.open(encoding='utf-8-sig') as stream:
                for line in stream:historical.update(map(int,pattern.findall(line)))
        fingerprints=[train_rl.seed_fingerprint(s) for s in roots]
        overlap=bool(set(fingerprints)&{train_rl.seed_fingerprint(s) for s in historical})
        write_once(out/'seed-audit.json',{
            'archived_root_count':len(historical),'new_roots':roots,'new_fingerprints':fingerprints,
            'archived_root_fingerprint_overlap':overlap,
            'source':'Historical published JSON seed and root_seed fields; training, validation, selection and final namespaces remain separate.',
            'note':'An overlap can occur when reproducing this fixed study. Reusing known final events does not constitute a new independent test.'})
    for source in sources:copy(source,out/'source'/source.relative_to(ROOT))
    if not (out/'parent-checkpoint.pt').exists():
        args=train_rl.parse(['--mode','train','--output',str(out/'unused'),*args_common(out)])
        migrate(PARENT,out/'parent-checkpoint.pt',args)
    return plan

def train_branch(out,plan,branch):
    dest=out/branch['name'];status=out/(branch['name']+'-status.json')
    command=[sys.executable,str(ROOT/'tools/train_rl.py'),'--mode','train','--finetune-from',str(out/'parent-checkpoint.pt'),
             *args_common(out),'--output',str(dest),'--seed',str(branch['seed']),'--shop-policy',branch['shop_policy'],
             '--n-envs',str(plan['n_envs']),'--rollout-steps',str(plan['rollout_steps']),
             '--total-steps',str(plan['steps_per_branch']),'--validation-events',str(plan['validation_events']),
             '--validate-every',str(plan['validate_every']),'--checkpoint-every',str(plan['validate_every']),
             '--minibatch-size',str(plan['minibatch_size']),'--learning-rate',str(plan['learning_rate']),
             '--entropy-coefficient',str(plan['entropy_coefficient']),'--gae-lambda',str(plan['gae_lambda']),
             '--target-kl',str(plan['target_kl'])]
    write_once(out/(branch['name']+'-invocation.json'),{'command':command,'parent_sha256':train_rl.sha(out/'parent-checkpoint.pt')})
    if not status.exists():
        print(json.dumps({'training':branch['name'],'decisions':plan['steps_per_branch']}),flush=True)
        start=time.monotonic()
        with (out/(branch['name']+'.log')).open('x',encoding='utf-8') as log:
            result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        write_once(status,{'exit_code':result.returncode,'wall_seconds':time.monotonic()-start})
    if read(status)['exit_code']:raise RuntimeError('Training failed: '+branch['name'])
    summary=read(dest/'training-summary.json')
    if summary['state']['steps']!=plan['steps_per_branch']:raise RuntimeError('Wrong completed training length')
    best=read(dest/'best.json');checkpoint=dest/best['checkpoint']
    if checkpoint.parent.resolve()!=dest.resolve() or train_rl.sha(checkpoint)!=best['sha256']:raise RuntimeError('Checkpoint identity changed')
    print(json.dumps({'trained':branch['name'],'best_steps':best['steps'],'validation_score':best['selection_score']}),flush=True)
    return {'name':branch['name'],'checkpoint':str(checkpoint.relative_to(out).as_posix()),'sha256':best['sha256'],'steps':best['steps']}

def evaluate(out,plan,candidate,cohort,budget):
    destination=out/(cohort+'-'+candidate['name']+'-'+str(budget)+'.json')
    setup=plan[cohort];checkpoint=out/candidate['checkpoint']
    identity={'checkpoint_sha256':train_rl.sha(checkpoint),'candidate':candidate,'cohort':setup,
              'budget':budget,'profile_sha256':plan['profile_sha256'],'library_sha256':plan['library_sha256']}
    if destination.exists():
        result=read(destination)
        if result['identity']!=identity:raise RuntimeError('Evaluation inputs changed')
        return result
    saved,model=train_rl.load_checkpoint(checkpoint,torch.device('cuda'));model.eval()
    if candidate['style']=='mask-parent':
        model=with_shop(model,saved['metadata']['environment_signature']['schema'],profile_config([out/'synthetic.profile'],'shop5'))
    profile=profile_values(out/'synthetic.profile');rows={};start=time.monotonic()
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],min(512,setup['events']),budgets=[budget],
                   seed=setup['seed'],seed_namespace=setup['namespace'],deadline_hours=168,max_actions=100000,auto_reset=False) as env:
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,False)
        rule=Practical(env.observation_names,env.action_names,'shop5' if candidate['style']=='browser' else 'learned',profile)
        practical=candidate['style']!='raw'
        safe=min(1,float(profile['boss_damage'].split(',')[-1].split(':')[1])+.001)
        recovery=float(profile['recovery_hours']);action_hours=float(profile['action_seconds'])/3600
        obs,masks=env.reset()
        with torch.inference_mode():
            while len(rows)<setup['events']:
                actions=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                actions,targets,_=rule.apply(obs,masks,actions)
                if practical:
                    v=lambda n:rule.v(obs,n)
                    final=rule.flag(obs,'phase.recovery')&(np.rint(v('room_div100')*100)==100)
                    wait=np.maximum(0,safe-v('hp'))*recovery
                    enough=v('hours_left_div168')*168>wait+2*action_hours
                    patient=final&enough&(np.rint(v('paid_heals_div20')*20)>=2)
                    enter=patient&(v('hp')>=safe-1e-10)&masks[:,rule.ai['reenter']]
                    actions[enter]=rule.ai['reenter']
                    cap=np.zeros(len(obs),bool)
                    for name,index in rule.ai.items():
                        if name.startswith('wait_hp_'):cap|=(actions==index)&(int(name.rsplit('_',1)[1])/100>safe)
                        elif name=='wait_deadline':cap|=(actions==index)&(v('hours_left_div168')*168>wait)
                    use=final&(v('hp')<safe-1e-10)&enough&(patient|cap)
                    actions[use]=rule.ai['wait_deadline'];targets[use]=safe
                obs,masks,_,done,truncated,info=env.step(actions,wait_targets=targets) if np.any(targets>=0) else env.step(actions)
                totals=env.reward_totals();ended=np.flatnonzero(done|truncated)
                for slot in ended:
                    index=int(info['episode_index'][slot])
                    if index>=setup['events']:continue
                    if index in rows:raise RuntimeError('Duplicate final event')
                    row={'event':index,'budget':budget,'candidate':candidate['name']}
                    for key in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries','elapsed_hours','actions','invalid_actions'):
                        row[key]=float(info[key][slot])
                    row.update({key:float(value[slot]) for key,value in totals.items()})
                    if (truncated[slot] or not done[slot] or row['invalid_actions'] or row['mushrooms']>budget or
                        row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or not 168-2*action_hours-1e-8<=row['elapsed_hours']<=168+1e-8):
                        raise RuntimeError('Invalid event accounting')
                    rows[index]=row
                if len(ended):obs,masks=env.reset_done(ended)
    rows=[rows[i] for i in range(setup['events'])]
    result={'identity':identity,'events':len(rows),'wall_seconds':time.monotonic()-start,'rows':rows,
            'summary':{key:interval([r[key] for r in rows]) for key in rows[0] if key not in ('event','budget','candidate')}}
    write_once(destination,result)
    print(json.dumps({'evaluated':destination.name,'clears':result['summary']['completions']['mean'],'seconds':result['wall_seconds']}),flush=True)
    return result

def run(out,smoke):
    torch.set_num_threads(2)
    plan=prepare(out,smoke)
    branches=[train_branch(out,plan,b) for b in plan['branches']]
    write_once(out/'training-selection.json',branches)
    parent={'checkpoint':'parent-checkpoint.pt','sha256':train_rl.sha(out/'parent-checkpoint.pt'),'steps':0}
    candidates=[{**parent,'name':'parent-raw','style':'raw'},
                {**branches[0],'name':'trained-raw','style':'raw'},
                {**parent,'name':'parent-browser','style':'browser'},
                {**parent,'name':'parent-mask','style':'mask-parent'},
                {**branches[1],'name':'trained-shop','style':'trained-shop'}]
    validation={(c['name'],b):evaluate(out,plan,c,'selection',b) for c in candidates for b in BUDGETS}
    groups={'unrestricted':['parent-raw','trained-raw'],'practical':['parent-browser','parent-mask','trained-shop']}
    selection={}
    for family,names in groups.items():
        means={n:statistics.mean(validation[n,b]['summary']['completions']['mean'] for b in BUDGETS) for n in names}
        selection[family]={'overall':max(names,key=means.get),'scores':means,
            'per_budget':{str(b):max(names,key=lambda n:validation[n,b]['summary']['completions']['mean']) for b in BUDGETS}}
    write_once(out/'selection.json',selection)
    print(json.dumps({'frozen_selection':selection}),flush=True)
    finals=[evaluate(out,plan,c,'final',b) for c in candidates for b in BUDGETS]
    for source,fingerprint in plan['sources'].items():
        if train_rl.sha(ROOT/source)!=fingerprint:raise RuntimeError('Training source changed: '+source)
    write_once(out/'summary.json',{'plan':plan,'branches':branches,'candidates':candidates,'selection':selection,
                                 'cells':[{k:v for k,v in c.items() if k!='rows'} for c in finals]})
    print('Training and untouched final comparison complete.',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--smoke',action='store_true')
    options=parser.parse_args();run(options.output.resolve(),options.smoke)
