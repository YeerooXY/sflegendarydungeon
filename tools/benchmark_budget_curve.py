"""Frozen-policy budget curves and prospective Ultimate entry reservations.

Ultimate plans reserve 650 mushrooms for each of N runs before the week starts.
The remaining fixed operating cap drives the ordinary policy. Pure Ultimate
stops after N completions; mixed plans then continue normally. Prefix replay is
exact for this declared strategy when loot multipliers do not affect gameplay.
Selection and final events are independent. No live account actions or training.
"""
import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import shutil
from statistics import mean, stdev
import time

import numpy as np
import torch
import train_rl
from benchmark_player_ideas import transfer_changes, write_once
from sfld_rl.env import VectorEnv
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA
from train_continuation_study import audit_seeds

ROOT = Path(__file__).resolve().parents[1]
SUPPLIES = [0,20,50,100,200,350,500,650,750,1000,1250,1500,2000,2500,
            3000,4000,5000,7500,10000,15000,20000]
BUDGETS = [0,20,50,100,200,350,500,650,750,1000,1500,2000,3000,5000,7500,10000,15000,20000]
FIELDS = ('completions','legendaries','mushrooms','recovery_mushrooms',
          'reroll_mushrooms','deaths','elapsed_hours','actions','invalid_actions',
          'gold_units','epics','arena_legendaries','arena_chests_5')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def interval(values):
    values = list(values)
    center = mean(values)
    se = stdev(values) / len(values)**.5 if len(values)>1 else 0
    return {'mean':center, 'standard_error':se, 'ci95':[center-1.959964*se,center+1.959964*se]}


def status(out, phase, **extra):
    value = {'phase':phase,'utc':datetime.now(timezone.utc).isoformat(),**extra}
    train_rl.write_json(out/'status.json',value)
    print(json.dumps(value),flush=True)


def prepare(out, smoke):
    if out.exists():
        plan=read(out/'plan.json')
        if plan['smoke'] != smoke: raise ValueError('Cannot mix smoke and research results')
        for name,digest in plan['inputs'].items():
            if train_rl.sha(out/name)!=digest: raise ValueError('Frozen input changed: '+name)
        for name,digest in plan['sources'].items():
            if train_rl.sha(ROOT/name)!=digest: raise ValueError('Study source changed: '+name)
        return plan
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA: raise ValueError('Checkpoint identity changed')
    roots=[2026101451,2026101452] if not smoke else [2026101458,2026101459]
    audit=audit_seeds(roots)
    out.mkdir(parents=True)
    for source,name in [(CHECKPOINT,'policy.pt'),(ROOT/'build-v0610/Release/sfld_rl.dll','sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile')]:
        shutil.copyfile(source,out/name)
    paths=[Path(__file__), ROOT/'tools/train_rl.py', ROOT/'tools/benchmark_player_ideas.py',
           ROOT/'tools/train_continuation_study.py']+sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    plan={'schema':'sfld-budget-ultimate-v1','smoke':smoke,'engine':'0.6.10',
          'supplies':[0,350,1000] if smoke else SUPPLIES,
          'budgets':[0,650,1000,2000] if smoke else BUDGETS,
          'selection':{'events':4 if smoke else 512,'seed':roots[0],'namespace':211},
          'final':{'events':8 if smoke else 4096,'seed':roots[1],'namespace':212},
          'entry_fee':650,'multiplier':3,'deadline_hours':168,'max_actions':100000,
          'checkpoint_sha256':CHECKPOINT_SHA,'trained_budget_range':[0,1000],
          'inputs':{n:train_rl.sha(out/n) for n in ['policy.pt','sfld_rl.dll','synthetic.profile']},
          'sources':{p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in paths},
          'ultimate_strategy':'Choose operating cap S and reserve 650*N before the event; '
             'pay once at the start of each of the first N runs, never on death. '
             'Pure Ultimate stops after N clears; mixed continues normally. '
             'Unused reservations are not released to operating spending.',
          'selection_rule':'For each total cap and mode maximize mean legendary item count on '
             'selection events across the declared supply grid, using N=floor((B-S)/650). '
             'Mixed also permits N=0 and any affordable operating cap. '
             'Ties prefer lower mean actual spend then fewer reserved entries. Freeze before final.',
          'loot_assumptions':'Final-boss legendaries and gold are tripled. Triple other legendary '
             'and epic items is a user scenario, with a final-boss-only sensitivity. '
             'Keys, blessings, curses and state dynamics are unchanged; policy receives base-equivalent '
             'loot counters. Ordinary item counts are not exposed by the native batch API.',
          'limits':'Policy frozen, trained at caps <=1000. No claim of optimality at high caps or '
             'under Ultimate fees. Pointwise 95% intervals cover Monte Carlo error only.',
          'official_source':'https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/201-legendary-dungeon-ultimate-1670924829/'}
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for p in paths:
        target=out/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
    return plan


def collect(out, plan, cohort, supply, saved, model):
    path=out/f'{cohort}-supply-{supply}.json.gz'
    if path.exists():
        with gzip.open(path,'rt',encoding='utf-8') as f: return json.load(f)['rows']
    setup=plan[cohort];start=time.monotonic();rows={}
    profile=profile_values(out/'synthetic.profile')
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],min(512,setup['events']),
                   budgets=[supply],seed=setup['seed'],seed_namespace=setup['namespace'],
                   deadline_hours=168,max_actions=100000,auto_reset=False) as env:
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,False)
        rule=Practical(env.observation_names,env.action_names,'learned',profile)
        obs,masks=env.reset();prefixes=[[] for _ in range(env.n_envs)]
        arena=np.zeros(env.n_envs);five=np.zeros(env.n_envs)
        last_clears=np.zeros(env.n_envs);last_legends=np.zeros(env.n_envs)
        with torch.inference_mode():
            while len(rows)<setup['events']:
                actions=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                actions,targets,_=rule.apply(obs,masks,actions)
                prize=rule.flag(obs,'phase.encounter') & rule.flag(obs,'encounter.prize') & (actions==rule.ai['interact'])
                tier5=prize & (np.rint(rule.v(obs,'pending_trial_reward_div5')*5)==5)
                obs,masks,_,done,truncated,info=env.step(actions,wait_targets=targets) if np.any(targets>=0) else env.step(actions)
                arena+=prize*(info['legendaries']-last_legends);five+=tier5
                completed=np.flatnonzero(info['completions']>last_clears)
                ended=np.flatnonzero(done|truncated)
                if len(completed) or len(ended):
                    totals=env.reward_totals()
                    def snapshot(slot):
                        row={key:float(info[key][slot]) for key in FIELDS if key in info}
                        row.update(gold_units=float(totals['gold_units'][slot]),epics=float(totals['epics'][slot]),
                                   arena_legendaries=float(arena[slot]),arena_chests_5=float(five[slot]),
                                   runs_started=int(round(rule.v(obs,'run_number_div10')[slot]*10)))
                        return row
                    for slot in completed:
                        snap=snapshot(slot)
                        if snap['completions']!=len(prefixes[slot])+1: raise RuntimeError('Missing run prefix')
                        prefixes[slot].append(snap)
                    for slot in ended:
                        event=int(info['episode_index'][slot])
                        if event>=setup['events']:continue
                        if event in rows:raise RuntimeError('Duplicate event')
                        row=snapshot(slot)
                        if (truncated[slot] or not done[slot] or row['invalid_actions'] or
                            row['mushrooms']>supply or row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or
                            not 167.998<=row['elapsed_hours']<=168.000001):
                            raise RuntimeError('Invalid event accounting: '+str(row))
                        row.update(event=event,supply=supply,prefixes=prefixes[slot]);rows[event]=row
                last_clears=info['completions'].copy();last_legends=info['legendaries'].copy()
                if len(ended):
                    obs,masks=env.reset_done(ended)
                    for slot in ended:prefixes[slot]=[]
                    for a in [arena,five,last_clears,last_legends]:a[ended]=0
    ordered=[rows[i] for i in range(setup['events'])]
    record={'cohort':setup,'supply':supply,'checkpoint_sha256':plan['checkpoint_sha256'],
            'wall_seconds':time.monotonic()-start,'rows':ordered}
    with gzip.open(path,'xt',encoding='utf-8') as f:json.dump(record,f,separators=(',',':'),allow_nan=False)
    status(out,cohort,supply=supply,events=len(ordered),clears=mean(r['completions'] for r in ordered),
           mean_spent=mean(r['mushrooms'] for r in ordered),seconds=record['wall_seconds'])
    return ordered


def project(row,budget,supply,reserved,mode):
    """A predetermined plan, never a choice based on this event's future."""
    if mode not in ('normal','ultimate','mixed') or reserved<0 or supply+650*reserved>budget:
        raise ValueError('Unaffordable plan')
    if mode=='ultimate' and reserved<1:raise ValueError('Ultimate needs an entry')
    prefixes=row['prefixes'];stopped=mode=='ultimate' and len(prefixes)>=reserved
    end=prefixes[reserved-1] if stopped else row
    boosted=prefixes[reserved-1] if reserved and len(prefixes)>=reserved else row if reserved else None
    fees=650*min(reserved,end['runs_started'])
    result={k:end[k] for k in FIELDS};result.update(event=row['event'],budget=budget,supply=supply,
          reserved_entries=reserved,entry_mushrooms=fees,operating_mushrooms=end['mushrooms'],
          mushrooms=end['mushrooms']+fees,ultimate_clears=min(reserved,end['completions']),
          mode=mode,unused_mushrooms=budget-end['mushrooms']-fees)
    result['legendary_final_boss_only']=end['legendaries']+2*(boosted['completions'] if boosted else 0)
    if boosted:
        for key in ('legendaries','gold_units','epics','arena_legendaries'):result[key]+=2*boosted[key]
    result['normal_clears']=result['completions']-result['ultimate_clears']
    if result['mushrooms']>budget or fees%650 or result['ultimate_clears']>fees/650:
        raise RuntimeError('Entry/reward accounting failure')
    return result


def summarize(rows):
    names=[k for k in rows[0] if k not in ('event','mode','budget','supply','reserved_entries')]
    return {'events':len(rows),**{k:interval(r[k] for r in rows) for k in names}}


def select(plan,cells):
    chosen=[]
    for budget in plan['budgets']:
        for mode in ('normal','ultimate','mixed'):
            candidates=[]
            for supply in plan['supplies']:
                if supply>budget:continue
                counts=[0] if mode=='normal' else [max(0,(budget-supply)//650)]
                if mode=='mixed' and counts[0]:counts.append(0)
                for count in counts:
                    if mode=='normal' and supply!=budget:continue
                    if mode=='ultimate' and count<1:continue
                    rows=[project(r,budget,supply,count,mode) for r in cells[supply]]
                    score=mean(r['legendaries'] for r in rows)
                    candidates.append((score,-mean(r['mushrooms'] for r in rows),-count,supply,count))
            if not candidates:continue
            best=max(candidates)
            chosen.append({'budget':budget,'mode':mode,'supply':best[3],
                           'reserved_entries':best[4],'selection_legendaries':best[0],
                           'candidate_count':len(candidates)})
    return chosen


def run(out,smoke,phase):
    torch.set_num_threads(2)
    plan=prepare(out,smoke)
    saved,model=train_rl.load_checkpoint(out/'policy.pt',torch.device('cuda'));model.eval()
    if phase in ('all','selection'):
        cells={s:collect(out,plan,'selection',s,saved,model) for s in plan['supplies']}
        write_once(out/'selection.json',select(plan,cells))
    if phase=='selection':return
    chosen=read(out/'selection.json')
    supplies=sorted({p['supply'] for p in chosen})
    cells={s:collect(out,plan,'final',s,saved,model) for s in supplies}
    final=[];all_rows=[]
    for p in chosen:
        rows=[project(r,p['budget'],p['supply'],p['reserved_entries'],p['mode']) for r in cells[p['supply']]]
        final.append({**p,'summary':summarize(rows)});all_rows.extend(rows)
    with gzip.open(out/'final-plans.json.gz','wt',encoding='utf-8') as f:json.dump(all_rows,f,separators=(',',':'))
    normal=[p for p in chosen if p['mode']=='normal']
    marginal=[]
    for left,right in zip(normal,normal[1:]):
        a=cells[left['supply']];b=cells[right['supply']];delta=right['budget']-left['budget']
        gain=interval((rb['completions']-ra['completions'])*100/delta for ra,rb in zip(a,b))
        marginal.append({'from':left['budget'],'to':right['budget'],'extra_clears_per_100_mushrooms':gain})
    write_once(out/'summary.json',{'plan':plan,'results':final,'marginal':marginal,
        'selection_policy_weeks':len(plan['supplies'])*plan['selection']['events'],
        'final_policy_weeks':len(supplies)*plan['final']['events']})
    status(out,'complete',selection_policy_weeks=len(plan['supplies'])*plan['selection']['events'],
           final_policy_weeks=len(supplies)*plan['final']['events'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--phase',choices=['all','selection','final'],default='all')
    args=parser.parse_args();run(args.output.resolve(),args.smoke,args.phase)
