"""Matched completion and loot evaluation; ten million gold per relative unit."""
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
from sfld_rl.escape_loot import CASES_LOOT, REASONS, EscapeLoot
from sfld_rl.player_ideas import profile_values

ROOT = Path(__file__).resolve().parents[1]
METRICS = ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries',
           'gold_units','gold','gold_rewards','epics','soldier_searches','soldiers_awakened',
           'soldier_gold_units','keys_spent_on_doors','changed_actions')


def evaluate(args,saved,model,device,budget,case):
    start = time.monotonic(); collected = {}
    with VectorEnv(args.library,[args.profile],min(args.n_envs,args.events),budgets=[budget],
                   seed=args.seed,seed_namespace=args.namespace,deadline_hours=168,max_actions=100000,auto_reset=False) as env:
        transfer = transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        rule = EscapeLoot(env.observation_names,env.action_names,case,profile_values(args.profile))
        obs,masks = env.reset(); loot = env.reward_totals()
        counters = {key:np.zeros(env.n_envs) for key in ('decisions','soldier_searches','soldiers_awakened',
                    'soldier_gold_units','keys_spent_on_doors','changed_actions',*('rule_'+r for r in REASONS))}
        ai,v,flag = rule.ai,rule.v,rule.flag
        with torch.inference_mode():
            while len(collected)<args.events:
                proposed=model.actions(train_rl.tensor(obs,device),train_rl.tensor(masks,device,torch.bool),greedy=True)[0].cpu().numpy()
                actual,reasons=rule.apply(obs,masks,proposed)
                counters['decisions']+=1; counters['changed_actions']+=actual!=proposed
                for i,reason in enumerate(REASONS): counters['rule_'+reason]+=reasons[:,i]
                search=flag(obs,'phase.encounter') & flag(obs,'encounter.skeleton') & (actual==ai['interact'])
                entering=flag(obs,'phase.doors') & np.isin(actual,[ai['door_0'],ai['door_1']])
                keys_before=np.rint(v(obs,'keys_div20')*20)
                counters['soldier_searches']+=search
                old_loot=loot
                obs,masks,reward,terminated,truncated,info=env.step(actual)
                loot=env.reward_totals()
                counters['soldiers_awakened']+=search & flag(obs,'encounter.monster')
                counters['soldier_gold_units']+=search*(loot['gold_units']-old_loot['gold_units'])
                counters['keys_spent_on_doors']+=entering*np.maximum(0,keys_before-np.rint(v(obs,'keys_div20')*20))
                ended=np.flatnonzero(terminated|truncated)
                for slot in ended:
                    index=int(info['episode_index'][slot])
                    if index>=args.events: continue
                    if index in collected: raise RuntimeError('Duplicate event')
                    row={'event_index':index,'case':case,'budget':budget}
                    for k in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries',
                              'elapsed_hours','first_completion_hours','actions','invalid_actions'):
                        row[k]=float(info[k][slot])
                    row.update({k:float(a[slot]) for k,a in loot.items()})
                    row.update({k:float(a[slot]) for k,a in counters.items()})
                    row['gold']=row['gold_units']*args.gold_per_unit
                    if (not terminated[slot] or truncated[slot] or row['invalid_actions'] or
                        row['actions']!=row['decisions'] or row['mushrooms']>budget or
                        row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or
                        not 168-2/3600-1e-8<=row['elapsed_hours']<=168+1e-8 or row['gold_units']<0):
                        raise RuntimeError('Invalid event accounting')
                    collected[index]=row
                if len(ended):
                    obs,masks=env.reset_done(ended); loot=env.reward_totals()
                    for value in counters.values(): value[ended]=0
    rows=[collected[i] for i in range(args.events)]
    summary={'case':case,'budget':budget,'events':len(rows),**{k:interval([r[k] for r in rows]) for k in METRICS}}
    summary['gold_per_completion']=sum(r['gold'] for r in rows)/sum(r['completions'] for r in rows)
    summary['mushrooms_per_completion']=sum(r['mushrooms'] for r in rows)/sum(r['completions'] for r in rows)
    return {'summary':summary,'outcomes':rows,'transfer':transfer,'wall_seconds':time.monotonic()-start}


def run(args):
    args.output.mkdir(parents=True,exist_ok=True); torch.set_num_threads(2)
    device=torch.device(args.device); saved,model=train_rl.load_checkpoint(args.checkpoint,device); model.eval()
    sources={'checkpoint':args.checkpoint,'library':args.library,'profile':args.profile,'runner':Path(__file__),
             'rules':ROOT/'tools/sfld_rl/escape_loot.py','base_rules':ROOT/'tools/sfld_rl/player_ideas.py',
             'adapter':ROOT/'tools/sfld_rl/env.py','helpers':ROOT/'tools/benchmark_player_ideas.py',
             'network':ROOT/'tools/sfld_rl/ppo.py','loader':ROOT/'tools/train_rl.py'}
    plan={'schema':'sfld-escape-loot-v1','events_per_cell':args.events,'budgets':args.budgets,'cases':list(CASES_LOOT),
          'seed':str(args.seed),'namespace':args.namespace,'gold_per_unit':args.gold_per_unit,
          'n_envs':min(args.events,args.n_envs),'device':str(device),'torch':str(torch.__version__),
          'hashes':{k:train_rl.sha(p) for k,p in sources.items()},
          'notes':['Gold conversion is a user-defined reporting scenario, not a measured account payout.',
                   'Frozen completion-trained policy; no gold reward shaping or retraining.',
                   'Same corrected mechanics and random offers in every arm. No recovery/key-farming/boss-buffer override.',
                   'Soldier-only changes only search versus leave at guaranteed effective escape and survivable current poison.',
                   'Safe escape avoids selected traps if a legal untrapped alternative exists; saves keys versus an untrapped monster; flees ordinary monsters unless One Hit is active.',
                   'Pendant fallback only replaces a learned Bull/Hick choice. Other stone choices remain learned.',
                   'Live gold and simulator gold are separate measurements. No account gold balance is rescaled.']}
    write_once(args.output/'plan.json',plan)
    for k in ('library','profile','runner','rules','base_rules','adapter','helpers'):
        dst=args.output/sources[k].name
        if dst.exists() and train_rl.sha(dst)!=plan['hashes'][k]: raise RuntimeError('Snapshot differs')
        if not dst.exists(): shutil.copyfile(sources[k],dst)
    args.library=args.output/sources['library'].name; args.profile=args.output/sources['profile'].name
    cells={}
    for budget in args.budgets:
        for case in CASES_LOOT:
            path=args.output/f'cap-{budget}-{case}.json'
            if path.exists():
                cell=json.loads(path.read_text());
                if cell['plan']!=plan: raise RuntimeError('Cached experiment differs')
            else:
                cell=evaluate(args,saved,model,device,budget,case); cell['plan']=plan; write_once(path,cell)
            cells[budget,case]=cell
            s=cell['summary']; print(json.dumps({'budget':budget,'case':case,'clears':s['completions']['mean'],
                                               'gold_millions':s['gold']['mean']/1e6,'seconds':cell['wall_seconds']}),flush=True)
    comparisons=[]
    for (budget,case),cell in cells.items():
        if case=='control': continue
        a=cells[budget,'control']['outcomes']; b=cell['outcomes']
        if [r['event_index'] for r in a]!=[r['event_index'] for r in b]: raise RuntimeError('Unpaired events')
        comparisons.append({'budget':budget,'case':case,'deltas':{k:interval([y[k]-x[k] for x,y in zip(a,b)]) for k in METRICS}})
    write_once(args.output/'summary.json',{'plan':plan,'summaries':[x['summary'] for x in cells.values()],'comparisons':comparisons})
    for k,p in sources.items():
        if train_rl.sha(p)!=plan['hashes'][k]: raise RuntimeError('Source changed: '+k)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--events',type=int,default=4096); p.add_argument('--n-envs',type=int,default=512)
    p.add_argument('--budgets',nargs='+',type=int,default=[0,20,50,100,500,1000])
    p.add_argument('--seed',type=int,default=11069002); p.add_argument('--namespace',type=int,default=232)
    p.add_argument('--gold-per-unit',type=float,default=10_000_000); p.add_argument('--device',default='cuda')
    p.add_argument('--library',type=Path,default=ROOT/'build/Release/sfld_rl.dll')
    p.add_argument('--profile',type=Path,default=ROOT/'profiles/synthetic.profile')
    p.add_argument('--checkpoint',type=Path,default=ROOT/'docs/pendant-adaptation-2026-10-11/control/selected-checkpoint.pt')
    args=p.parse_args()
    if args.events<2 or args.n_envs<1 or not np.isfinite(args.gold_per_unit) or args.gold_per_unit<=0: p.error('Invalid experiment size or gold conversion')
    run(args)
