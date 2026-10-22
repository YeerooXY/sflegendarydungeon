"""Conditional remaining-run experiments for Rusty's repeated poison cost.

Synthetic recovery states are deliberately enriched for rare stone combinations.
These are not complete-event forecasts or naturally sampled player states.
"""
import argparse
import json
from pathlib import Path
from statistics import NormalDist
import time
import numpy as np
import torch
import train_rl
from benchmark_daily_schedule import interval
from benchmark_player_ideas import transfer_changes,write_once
from train_current_study import copy
from train_continuation_study import audit_seeds,status
from sfld_rl.effect_lab import EffectLab,mix64
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.rusty_recovery import CASES,TARGETS,RustyRecovery,progress
from sfld_rl.visual_policy import CHECKPOINT,CHECKPOINT_SHA

ROOT=Path(__file__).resolve().parents[1]


def evaluate(out,plan,model,saved,cohort,case_index,variant,wake):
    case=CASES[case_index];dest=out/cohort/f'{case["name"]}-{variant}-{wake}.json'
    if dest.exists():return json.loads(dest.read_text(encoding='utf-8'))
    setup=plan[cohort];count=setup['events'];n=min(256,count);start=time.monotonic()
    rows={};next_id=n;episode=np.arange(n);counters={k:np.zeros(n) for k in
        ('reentries','hp_sum','custom_waits','changed_actions','ordinary_reentries','ordinary_hp_sum')}
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],n,budgets=[0],seed=setup['seed'],
                   seed_namespace=228,auto_reset=False,event_start_hour=17,sleep_until_hour=wake) as env:
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        env.reset();lab=EffectLab(env);rule=RustyRecovery(env,profile_values(out/'synthetic.profile'),variant,'greasy' in case['gems'])
        def restore(slot,index):
            key=mix64(setup['seed']^mix64(case_index+7919)^mix64(index+104729))
            text=progress(case,6*(index%2))
            with lab.fixture(text,0,seed=mix64(key^0x5A17)) as snap:lab.restore(slot,snap,mix64(key^0xF010))
            episode[slot]=index
            for values in counters.values():values[slot]=0
        for slot in range(n):restore(slot,slot)
        obs,masks=lab.observe();elapsed=np.zeros(n);decisions=0
        while len(rows)<count:
            if wake and np.any((17+elapsed)%24<wake-1e-9):raise RuntimeError('Action during sleep')
            proposed=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
            actions,targets,_=rule.apply(obs,masks,proposed.copy())
            reenter=actions==rule.ai['reenter'];hp=rule.base.v(obs,'hp')
            ordinary=reenter&(np.rint(rule.base.v(obs,'room_div100')*100).astype(int)%25!=0)
            counters['reentries']+=reenter;counters['hp_sum']+=hp*reenter
            counters['ordinary_reentries']+=ordinary;counters['ordinary_hp_sum']+=hp*ordinary
            counters['custom_waits']+=targets>=0;counters['changed_actions']+=actions!=proposed
            decisions+=int((episode<count).sum())
            obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets if np.any(targets>=0) else None)
            elapsed=info['elapsed_hours'].copy()
            complete=info['first_completion_hours']>=0;ended=np.flatnonzero(complete|done|trunc)
            for slot in ended:
                index=int(episode[slot])
                if index<count:
                    if index in rows or trunc[slot] or info['invalid_actions'][slot] or info['mushrooms'][slot]:
                        raise RuntimeError('Invalid conditional trajectory')
                    rows[index]=dict(event=index,initial_keys=6*(index%2),completed=int(complete[slot]),
                        hours=float(info['first_completion_hours'][slot] if complete[slot] else 168),
                        deaths=float(info['deaths'][slot]),actions=float(info['actions'][slot]),
                        **{k:float(v[slot]) for k,v in counters.items()})
            if len(rows)==count:break
            if len(ended):
                for slot in ended:restore(int(slot),next_id);next_id+=1;elapsed[slot]=0
                obs,masks=lab.observe()
        metadata=env.metadata
    ordered=[rows[i] for i in range(count)]
    result=dict(case=case,variant=variant,wake=wake,cohort=setup,rows=ordered,environment=metadata,
        decisions=decisions,seconds=time.monotonic()-start,
        summary={k:interval([r[k] for r in ordered]) for k in ordered[0] if k not in ('event','initial_keys')})
    write_once(dest,result)
    print(json.dumps(dict(cohort=cohort,case=case['name'],variant=variant,wake=wake,
                          hours=result['summary']['hours']['mean'],completed=result['summary']['completed']['mean'],
                          seconds=result['seconds'])),flush=True)
    return result


def run(out,smoke=False):
    if out.exists():raise FileExistsError(out)
    roots=[2026101583,2026101584] if smoke else [20261015301,20261015302]
    audit=audit_seeds(roots)
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA:raise RuntimeError('Deployed actor changed')
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(CHECKPOINT,torch.device('cuda'));model.eval()
    out.mkdir(parents=True)
    for source,name in ((CHECKPOINT,'checkpoint.pt'),(ROOT/'build-v0612/Release/sfld_rl.dll','sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile')):copy(source,out/name)
    sources=[Path(__file__),ROOT/'tests/test_rusty_recovery.py']
    sources += [ROOT/'tools'/n for n in ('train_rl.py','benchmark_daily_schedule.py','benchmark_player_ideas.py',
                                       'train_current_study.py','train_continuation_study.py')]
    sources+=sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources+=sorted((ROOT/'src').glob('*.cpp'))+sorted((ROOT/'include/sfld').glob('*.hpp'))
    plan=dict(schema='sfld-rusty-conditional-recovery-v1',smoke=smoke,cases=CASES,targets=TARGETS,
        screening=dict(seed=roots[0],events=4 if smoke else 128),
        confirmation=dict(seed=roots[1],events=4 if smoke else 1024),
        budget=0,deadline_hours=168,start_hour=17,wakes=[0,8],initial_keys=[0,6],
        fixtures='Synthetic later-run death states at rooms 35, 60 and 85; current-stage shop pending, '
                 'earlier stage-2-and-later shops consumed, no stage-1 shop. Room 85 is the last stage-4 shop slot. '
                 'Prior stone menus use a complete synthetic offered history. These are conditional states, not sampled weeks.',
        objective='Minimize restricted mean hours to complete the current run, capped at 168. '
                  'Always report noncompletion; do not discard censored runs.',
        selection='Within each starting-stone case, choose the non-control target with lowest equal-schedule screening mean. '
                  'Ties keep the first listed target. Freeze before any confirmation events.',
        behavior='Only override natural ordinary-room recovery while Rusty and the initial Greasy-presence flag still match. '
                 'Keep boss, combat, door, shop and stone choices. If Greasy is gained in a no-Greasy case, return to the base policy.',
        seed_scheme='For event i and case c: k=mix64(root xor mix64(c+7919) xor mix64(i+104729)); '
                    'initial visible-room seed=mix64(k xor 0x5A17); independent continuation seed=mix64(k xor 0xF010). '
                    'Same event seeds across candidate targets and sleep schedules. Keys alternate 0,6.',
        intervals='Paired normal 95% intervals, with Bonferroni simultaneous 95% across 14 confirmation hour contrasts. '
                  'Death and completion proportions are diagnostics, not extra significance claims.',
        checkpoint_sha256=CHECKPOINT_SHA,library_sha256=train_rl.sha(out/'sfld_rl.dll'),
        profile_sha256=train_rl.sha(out/'synthetic.profile'),sources={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        limits='Artificial later-run death states, not naturally weighted weekly results. Rejected offers are explicit synthetic history. '
               'Only zero-mushroom recovery is tested. The existing actor is not retrained. Bounds omit uncertainty in game mechanics.')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for p in sources:copy(p,out/'source'/p.relative_to(ROOT))
    selected={};scores={}
    with torch.inference_mode():
        status(out,'screening Rusty targets')
        for i,case in enumerate(CASES):
            scores[case['name']]={}
            for variant in TARGETS:
                cells=[evaluate(out,plan,model,saved,'screening',i,variant,wake) for wake in (0,8)]
                scores[case['name']][variant]=float(np.mean([c['summary']['hours']['mean'] for c in cells]))
            selected[case['name']]=min([x for x in TARGETS if x!='control'],key=lambda x:scores[case['name']][x])
        write_once(out/'selection.json',dict(selected=selected,scores=scores))
        status(out,'confirming Rusty targets',selected=selected)
        cells=[];contrasts=[];z=NormalDist().inv_cdf(1-.05/(2*14))
        for i,case in enumerate(CASES):
            for wake in (0,8):
                a=evaluate(out,plan,model,saved,'confirmation',i,'control',wake)
                b=evaluate(out,plan,model,saved,'confirmation',i,selected[case['name']],wake)
                cells.extend([a,b]);d=[y['hours']-x['hours'] for x,y in zip(a['rows'],b['rows'])]
                contrasts.append(dict(case=case['name'],variant=selected[case['name']],wake=wake,
                                      hours_difference=interval(d),simultaneous_95=interval(d,z)))
    for name,sha in plan['sources'].items():
        if train_rl.sha(ROOT/name)!=sha:raise RuntimeError('Frozen source changed: '+name)
    write_once(out/'summary.json',dict(selected=selected,contrasts=contrasts,
                                      cells=[{k:v for k,v in c.items() if k!='rows'} for c in cells]))
    status(out,'complete',conditional_runs=plan['screening']['events']*len(CASES)*len(TARGETS)*2+
           plan['confirmation']['events']*len(CASES)*4)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--smoke',action='store_true');a=p.parse_args();run(a.output.resolve(),a.smoke)
