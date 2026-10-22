"""Select recovery thresholds, then test frozen winners on independent weeks.

The deployed actor and all non-recovery decisions stay fixed. No real accounts
are accessed. Screening data never enter confirmation confidence intervals.
"""
import argparse
import json
from pathlib import Path
from statistics import NormalDist
import time
import numpy as np
import torch
import train_rl
from benchmark_daily_schedule import interval, METRICS, BUDGETS
from benchmark_player_ideas import write_once, transfer_changes
from train_continuation_study import audit_seeds, status
from train_current_study import copy
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.recovery_thresholds import IDEAS, RecoveryThresholds
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA

ROOT=Path(__file__).resolve().parents[1]
FAMILIES=dict(threshold=['entry_mean','entry_max','wait_mean','wait_max','wait_one'],
              hybrid=['hybrid_mean','hybrid_max','hybrid_nearby'])
COUNTERS=['reentries','reentry_hp_sum','rusty_reentries','rusty_reentry_hp_sum',
          'greasy_traveler_reentries','partial_refills','full_refills','paid10','paid15','paid20','paid_over20',
          'custom_waits','selected_wait_hours','boss25_count','boss25_keys_sum','changed_actions']
COUNTERS += [f'stage{s}_{field}' for s in range(1,5) for field in ('reentries','hp_sum')]
COUNTERS += ['plan_'+s for s in ('threshold_plans','extra_waits','partial_refills','expensive_refills_blocked','planned_reentries')]


def evaluate(out,plan,model,saved,cohort,variant,budget,wake):
    dest=out/cohort/f'{variant}-{budget}-{wake}.json'
    if dest.exists():return json.loads(dest.read_text(encoding='utf-8'))
    setup=plan[cohort];events=setup['events'];n=min(plan['n_envs'],events)
    rows={};start=time.monotonic();profile=profile_values(out/'synthetic.profile')
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],n,budgets=[budget],
                   seed=setup['seed'],seed_namespace=setup['namespace'],auto_reset=False,
                   event_start_hour=17,sleep_until_hour=wake) as env:
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        rule=RecoveryThresholds(env,profile,variant);rule.base.count_before_episode=events
        obs,masks=env.reset();elapsed=np.zeros(n);counters={k:np.zeros(n) for k in COUNTERS}
        decisions=0
        while len(rows)<events:
            if wake and np.any((17+elapsed)%24<wake-1e-9):raise RuntimeError('Decision during sleep')
            original=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
            fallback=original.copy()
            if variant=='cap20':
                paid=np.isin(original,[rule.ai['heal_step'],rule.ai['heal_full']])
                if paid.any():
                    restricted=masks[paid].copy();restricted[:,[rule.ai['heal_step'],rule.ai['heal_full']]]=False
                    fallback[paid]=train_rl.policy_actions(model,obs[paid],restricted,'cuda',greedy=True)[0].cpu().numpy()
            actions,targets,_=rule.apply(obs,masks,original.copy(),fallback)
            v=lambda name:rule.v(obs,name);f=lambda name:rule.f(obs,name)
            reenter=actions==rule.ai['reenter'];partial=actions==rule.ai['heal_step'];full=actions==rule.ai['heal_full']
            price=np.rint(v('heal_step_price_div100')*100);hp=v('hp')
            room=np.rint(v('room_div100')*100).astype(int);stage=np.clip((room-1)//25+1,1,4)
            rusty=reenter&f('owned_gem.rusty');both=reenter&f('owned_gem.greasy')&f('owned_gem.time_traveler')
            counters['reentries']+=reenter;counters['reentry_hp_sum']+=reenter*hp
            counters['rusty_reentries']+=rusty;counters['rusty_reentry_hp_sum']+=rusty*hp
            counters['greasy_traveler_reentries']+=both
            counters['partial_refills']+=partial;counters['full_refills']+=full
            for amount in (10,15,20):counters[f'paid{amount}']+=partial&(price==amount)
            counters['paid_over20']+=partial&(price>20)
            if rule.idea.hybrid or variant=='cap20':
                if full.any() or np.any(partial&(price>20)):raise RuntimeError('Cheap refill restriction violated')
            counters['custom_waits']+=targets>=0;counters['changed_actions']+=actions!=original
            for s in range(1,5):
                entered=reenter&(stage==s);counters[f'stage{s}_reentries']+=entered;counters[f'stage{s}_hp_sum']+=entered*hp
            for key,value in rule.last_flags.items():counters['plan_'+key]+=value
            decisions+=int((env.episode_indices<events).sum())
            obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets if (targets>=0).any() else None)
            counters['selected_wait_hours']+=(info['elapsed_hours']-elapsed)*(actions>=rule.ai['wait_hp_20'])
            elapsed=info['elapsed_hours'].copy()
            boss25=(room==25)&f('phase.gems')
            counters['boss25_count']+=boss25;counters['boss25_keys_sum']+=boss25*np.rint(v('keys_div20')*20)
            ended=np.flatnonzero(done|trunc)
            if len(ended):
                totals=env.reward_totals()
                for slot in ended:
                    index=int(info['episode_index'][slot])
                    if index>=events:continue
                    row=dict(event=index,**{k:float(info[k][slot]) for k in METRICS})
                    row.update({k:float(x[slot]) for k,x in totals.items()})
                    row.update({k:float(x[slot]) for k,x in counters.items()})
                    if index in rows or trunc[slot] or row['invalid_actions'] or row['mushrooms']>budget or \
                       row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or \
                       not 167.99<=row['elapsed_hours']<=168.000001:raise RuntimeError('Invalid completed event')
                    rows[index]=row
                obs,masks=env.reset_done(ended);elapsed[ended]=0
                for values in counters.values():values[ended]=0
    ordered=[rows[i] for i in range(events)]
    first=[r['first_completion_hours'] for r in ordered if r['first_completion_hours']>=0]
    result=dict(variant=variant,budget=budget,sleep_until_hour=wake,cohort=setup,rows=ordered,
                environment=env.metadata,decisions=decisions,seconds=time.monotonic()-start,
                summary={k:interval([r[k] for r in ordered]) for k in ordered[0] if k not in ('event','first_completion_hours')},
                first_completion_fraction=len(first)/events,conditional_first_completion_hours=interval(first) if first else None)
    write_once(dest,result)
    print(json.dumps(dict(cohort=cohort,variant=variant,budget=budget,wake=wake,
                          completions=result['summary']['completions']['mean'],seconds=result['seconds'])),flush=True)
    return result


def run(out,smoke=False):
    if out.exists():raise FileExistsError(out)
    roots=[2026101571,2026101572] if not smoke else [2026101573,2026101574]
    audit=audit_seeds(roots)
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA:raise RuntimeError('Deployed policy changed')
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(CHECKPOINT,torch.device('cuda'));model.eval()
    out.mkdir(parents=True)
    for source,name in ((ROOT/'build-v0612/Release/sfld_rl.dll','sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile'),(CHECKPOINT,'checkpoint.pt')):copy(source,out/name)
    sources=[Path(__file__),ROOT/'tests/test_recovery_thresholds.py',ROOT/'CMakeLists.txt']
    sources += [ROOT/'tools'/n for n in ('train_rl.py','benchmark_daily_schedule.py','benchmark_player_ideas.py',
                                       'train_continuation_study.py','train_current_study.py')]
    sources += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources += sorted((ROOT/'include/sfld').glob('*.hpp'))+sorted((ROOT/'src').glob('*.cpp'))
    plan=dict(schema='sfld-recovery-thresholds-v1',smoke=smoke,n_envs=8 if smoke else 512,
        screening=dict(seed=roots[0],namespace=222,events=4 if smoke else 512,budgets=[0,100,1000],wakes=[0,8]),
        confirmation=dict(seed=roots[1],namespace=223,events=4 if smoke else 2048,budgets=BUDGETS,wakes=[0,8]),
        variants={name:vars(idea) for name,idea in IDEAS.items()},families=FAMILIES,
        policy='Unchanged deployed actor plus InteractionPolicy(base); recovery overrides only.',
        selection='Highest equal-cell mean completions across six screening cells, separately within each declared family. '
                  'Ties choose the first listed variant. Freeze two winners before confirmation.',
        intervals='Pointwise normal 95% CIs for means/paired differences. Simultaneous Bonferroni 95% over '
                  '24 cell contrasts and two equal-cell global contrasts. Global SE uses per-seed mean differences '
                  'to preserve correlation across budgets and schedules.',
        promotion='No automatic deployment. A supported general improvement requires positive simultaneous global lower '
                  'bound and no cell with a negative simultaneous upper bound.',
        horizon_hours=168,event_start_hour=17,checkpoint_sha256=CHECKPOINT_SHA,
        library_sha256=train_rl.sha(out/'sfld_rl.dll'),profile_sha256=train_rl.sha(out/'synthetic.profile'),
        sources={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        limits='Simulation sampling uncertainty only. Hypothetical consecutive normal hits, known stones and revival '
               'effects; no future room, trap, combat curse or damage draw is inspected. Maximum-hit targets are not '
               'guarantees for unknown rooms. Unchanged policy was not retrained for these recovery rules. '
               'Cheap hybrid and cap20 allow at most one 20%-HP purchase per death, costing at most 20 mushrooms; '
               'hybrid substitutes a safe single-hit boss target. Other families preserve paid and boss choices.')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for p in sources:copy(p,out/'source'/p.relative_to(ROOT))
    with torch.inference_mode():
        status(out,'screening recovery ideas')
        scores={}
        for variant in IDEAS:
            cells=[evaluate(out,plan,model,saved,'screening',variant,b,w) for b in plan['screening']['budgets'] for w in (0,8)]
            scores[variant]=float(np.mean([c['summary']['completions']['mean'] for c in cells]))
        selected={family:max(names,key=lambda n:scores[n]) for family,names in FAMILIES.items()}
        write_once(out/'selection.json',dict(scores=scores,winners=selected))
        status(out,'confirming frozen winners',selected=selected)
        cells={}
        for variant in ['control',*selected.values()]:
            for b in BUDGETS:
                for w in (0,8):cells[variant,b,w]=evaluate(out,plan,model,saved,'confirmation',variant,b,w)
    z=NormalDist().inv_cdf(1-.05/(2*26));contrasts=[];overall=[]
    for variant in selected.values():
        differences=[]
        for b in BUDGETS:
            for w in (0,8):
                old=cells['control',b,w]['rows'];new=cells[variant,b,w]['rows']
                if [r['event'] for r in old]!=[r['event'] for r in new]:raise RuntimeError('Unpaired events')
                delta=[y['completions']-x['completions'] for x,y in zip(old,new)];differences.append(delta)
                contrasts.append(dict(variant=variant,budget=b,sleep_until_hour=w,
                                      difference=interval(delta),simultaneous_95=interval(delta,z)))
        delta=np.mean(differences,axis=0).tolist();ci=interval(delta,z)
        harmed=[c for c in contrasts if c['variant']==variant and c['simultaneous_95']['ci'][1]<0]
        overall.append(dict(variant=variant,difference=interval(delta),simultaneous_95=ci,
                            supported_general_improvement=ci['ci'][0]>0 and not harmed))
    for p,sha in plan['sources'].items():
        if train_rl.sha(ROOT/p)!=sha:raise RuntimeError('Frozen source changed: '+p)
    write_once(out/'summary.json',dict(selection=selected,overall=overall,contrasts=contrasts,
                                      cells=[{k:v for k,v in c.items() if k!='rows'} for c in cells.values()]))
    status(out,'complete',total_events=plan['screening']['events']*len(IDEAS)*6+plan['confirmation']['events']*len(cells))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--smoke',action='store_true');a=p.parse_args();run(a.output.resolve(),a.smoke)
