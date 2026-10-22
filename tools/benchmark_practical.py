"""Paired seven-day tests of explicit tactics around the frozen neural policy."""
import argparse
import json
from pathlib import Path
import shutil
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import interval, transfer_changes, write_once
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.practical import Practical, CASES, REASONS

ROOT=Path(__file__).resolve().parents[1]

def evaluate(args,saved,model,budget,case):
    start=time.monotonic();rows={}
    with VectorEnv(args.library,[args.profile],min(args.n_envs,args.events),budgets=[budget],
                   seed=args.seed,seed_namespace=args.namespace,deadline_hours=168,max_actions=100000,auto_reset=False) as env:
        transfer=transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        rule=Practical(env.observation_names,env.action_names,case,profile_values(args.profile))
        obs,masks=env.reset();count={k:np.zeros(env.n_envs) for k in ('decisions','changed',*REASONS,'trial_entries','tier5_prizes','boss_paid','boss_waits','paid_steps')}
        with torch.inference_mode():
            while len(rows)<args.events:
                proposed=model.actions(train_rl.tensor(obs,args.device),train_rl.tensor(masks,args.device,torch.bool),greedy=True)[0].cpu().numpy()
                actual,targets,reasons=rule.apply(obs,masks,proposed)
                count['decisions']+=1;count['changed']+=actual!=proposed
                for i,k in enumerate(REASONS):count[k]+=reasons[:,i]
                f=lambda n:rule.flag(obs,n)
                v=lambda n:rule.v(obs,n)
                for side in range(2):count['trial_entries']+=f('phase.doors')&f(f'doors.{side}.kind.trial')&(actual==rule.ai[f'door_{side}'])&(v('trial_depth_div5')<.01)
                count['tier5_prizes']+=f('phase.encounter')&f('encounter.prize')&(v('pending_trial_reward_div5')>.99)&(actual==rule.ai['interact'])
                boss=np.rint(v('room_div100')*100)==100
                count['boss_paid']+=boss*np.where(actual==rule.ai['heal_full'],v('heal_full_price_div100')*100,
                                       np.where(actual==rule.ai['heal_step'],v('heal_step_price_div100')*100,0))
                count['paid_steps']+=actual==rule.ai['heal_step']
                count['boss_waits']+=boss&np.isin(actual,[rule.ai[k] for k in rule.ai if k.startswith('wait_')])
                obs,masks,_,terminated,truncated,info=env.step(actual,wait_targets=targets) if np.any(targets>=0) else env.step(actual)
                totals=env.reward_totals();ended=np.flatnonzero(terminated|truncated)
                for slot in ended:
                    idx=int(info['episode_index'][slot])
                    if idx>=args.events:continue
                    if idx in rows:raise RuntimeError('Duplicate event')
                    row={'event':idx,'case':case,'budget':budget}
                    for k in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries','elapsed_hours','actions','invalid_actions'):
                        row[k]=float(info[k][slot])
                    row.update({k:float(a[slot]) for k,a in totals.items()});row.update({k:float(a[slot]) for k,a in count.items()})
                    row['gold']=row['gold_units']*10000000
                    if (truncated[slot] or not terminated[slot] or row['invalid_actions'] or row['actions']!=row['decisions'] or row['mushrooms']>budget or
                        row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or not 168-2/3600-1e-8<=row['elapsed_hours']<=168+1e-8):
                        raise RuntimeError('Invalid event accounting')
                    rows[idx]=row
                if len(ended):
                    obs,masks=env.reset_done(ended)
                    for values in count.values():values[ended]=0
    rows=[rows[i] for i in range(args.events)]
    metrics=[k for k in rows[0] if k not in ('event','case','budget')]
    return {'case':case,'budget':budget,'events':len(rows),'wall_seconds':time.monotonic()-start,'transfer':transfer,
            'summary':{k:interval([r[k] for r in rows]) for k in metrics},'rows':rows}

def run(args):
    args.output.mkdir(parents=True,exist_ok=True);torch.set_num_threads(2)
    saved,model=train_rl.load_checkpoint(args.checkpoint,torch.device(args.device));model.eval()
    paths={k:ROOT/v for k,v in {'rules':'tools/sfld_rl/practical.py','escape_rules':'tools/sfld_rl/escape_loot.py',
        'base_rules':'tools/sfld_rl/player_ideas.py','adapter':'tools/sfld_rl/env.py','network':'tools/sfld_rl/ppo.py',
        'loader':'tools/train_rl.py','helpers':'tools/benchmark_player_ideas.py'}.items()}
    paths.update(runner=Path(__file__),library=args.library,profile=args.profile,checkpoint=args.checkpoint)
    plan={'schema':'practical-candidates-v1','events':args.events,'budgets':args.budgets,'cases':list(CASES),
          'seed':args.seed,'namespace':args.namespace,'hashes':{k:train_rl.sha(v) for k,v in paths.items()},
          'notes':['Frozen neural policy with random stone offers, profile v4: Rabbit later-run only.',
                   'Common random event seeds; 168h continuous availability; hard total mushroom budgets.',
                   'Five rolling shop credits, stop early for useful offers; four-key reroll gate.',
                   'Patient variants apply at room 100 only, after two partial refills, with enough natural recovery time.',
                   'Model candidates; no new training or live calibration. Gold reporting: 10M per unit.']}
    write_once(args.output/'plan.json',plan)
    for k,p in paths.items():
        if k=='checkpoint':continue
        dst=args.output/p.name
        if dst.exists() and train_rl.sha(dst)!=plan['hashes'][k]:raise RuntimeError('Snapshot differs')
        if not dst.exists():shutil.copyfile(p,dst)
    args.library=args.output/paths['library'].name;args.profile=args.output/paths['profile'].name
    cells={}
    for budget in args.budgets:
        for case in CASES:
            path=args.output/f'cap-{budget}-{case}.json'
            if path.exists():cell=json.loads(path.read_text());assert cell['plan']==plan
            else:cell=evaluate(args,saved,model,budget,case);cell['plan']=plan;write_once(path,cell)
            cells[budget,case]=cell;s=cell['summary']
            print(json.dumps({'budget':budget,'case':case,'clears':s['completions']['mean'],'legendaries':s['legendaries']['mean'],
                              'boss_wait_overrides':s['boss_wait']['mean'],'seconds':cell['wall_seconds']}),flush=True)
    comparisons=[]
    for (budget,case),cell in cells.items():
        if case=='learned':continue
        control='learned' if case=='shop5' else 'tactics' if case.startswith('patient') else 'shop5'
        a=cells[budget,control]['rows'];b=cell['rows']
        comparisons.append({'budget':budget,'case':case,'control':control,'deltas':{
            k:interval([y[k]-x[k] for x,y in zip(a,b)]) for k in ('completions','legendaries','mushrooms','gold')}})
    write_once(args.output/'summary.json',{'plan':plan,'cells':[{k:v for k,v in c.items() if k!='rows'} for c in cells.values()],'comparisons':comparisons})
    for k,p in paths.items():
        if train_rl.sha(p)!=plan['hashes'][k]:raise RuntimeError('Source changed during experiment: '+k)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--events',type=int,default=2048);p.add_argument('--n-envs',type=int,default=512)
    p.add_argument('--budgets',nargs='+',type=int,default=[100,500,1000]);p.add_argument('--seed',type=int,default=11069003)
    p.add_argument('--namespace',type=int,default=234);p.add_argument('--device',default='cuda')
    p.add_argument('--library',type=Path,default=ROOT/'build/Release/sfld_rl.dll');p.add_argument('--profile',type=Path,default=ROOT/'profiles/synthetic.profile')
    p.add_argument('--checkpoint',type=Path,default=ROOT/'docs/pendant-adaptation-2026-10-11/control/selected-checkpoint.pt')
    args=p.parse_args()
    if args.events<2 or args.n_envs<1 or any(b<=0 for b in args.budgets):p.error('Use positive budgets and at least two events.')
    run(args)
