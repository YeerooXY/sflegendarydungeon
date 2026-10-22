"""Replay-independent comparison of the reported poisonous room and weekly guard."""
import argparse
import importlib.util
import json
from pathlib import Path
from statistics import NormalDist
import numpy as np
import torch
import train_rl
from train_continuation_study import audit_seeds
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, mix64
from sfld_rl.player_ideas import profile_values
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.visual_policy import CHECKPOINT

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'out/lockpick-poison-2026-10-18'
PROFILE=ROOT/'docs/key-calibration-2026-10-18/updated.profile'
LIB=ROOT/'build-calibration-20261018/Release/sfld_rl.dll'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module);return module


old_advice=load('old_lockpick_advice',OUT/'previous_visual_advice.py')
old_runtime=load('sfld_rl.old_lockpick_runtime',OUT/'previous_interaction_policy.py')
old_runtime.correct_action=old_advice.correct_action;old_runtime.annotate=old_advice.annotate


def stats(values,z=1.959963984540054):
    v=np.asarray(values,dtype=float);m=float(v.mean());se=float(v.std(ddof=1)/len(v)**.5)
    return dict(mean=m,n=len(v),se=se,ci=[m-z*se,m+z*se])


def save(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


def room(model,case=None):
    snapshot=(case['snapshot'] if case else json.loads((OUT/'reported-planning-snapshot.json').read_text()))['planning_state']
    room_number=case['observation']['room'] if case else 55
    prefix='trap' if case else 'room'
    data=[[],[]];encounters=[{},{}];choices=[{},{}];samples=8192
    with VectorEnv(LIB,[PROFILE],512,budgets=[100],auto_reset=False,event_start_hour=17) as env:
        env.reset();lab=EffectLab(env);rule=old_runtime.InteractionPolicy(env,profile_values(PROFILE),'base')
        with lab.planning_fixture(snapshot) as snap,torch.inference_mode():
            for first in range(0,samples,256):
                for slot in range(512):lab.restore(slot,snap,mix64(2026101861 ^ mix64(first+slot//2)))
                obs,masks=lab.observe();actions=np.array([env.action_names.index('door_'+str(i%2)) for i in range(512)])
                obs,masks,_,_,_,info=env.step(actions)
                for slot in range(512):
                    names=[n.split('.',1)[1] for n,v in zip(env.observation_names,obs[slot]) if n.startswith('encounter.') and v>.5]
                    name=names[0] if names else 'none';c=encounters[slot%2];c[name]=c.get(name,0)+1
                pending=np.ones(512,bool)
                for step in range(25):
                    proposed=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                    actions,targets,_=rule.apply(obs,masks,proposed)
                    for slot in np.flatnonzero(pending):
                        key=env.action_names[int(actions[slot])];c=choices[slot%2];c[key]=c.get(key,0)+1
                    obs,masks,_,done,trunc,info=env.step(actions)
                    if np.any(trunc[pending]) or np.any(info['invalid_actions'][pending]):raise RuntimeError('Invalid room continuation')
                    ended=np.flatnonzero(pending&((info['room']!=room_number)|done))
                    for slot in ended:
                        data[slot%2].append(dict(replicate=int(first+slot//2),survived=bool(info['hp'][slot]>1e-12),hp=float(info['hp'][slot])))
                    pending[ended]=False
                    if not pending.any():break
                if pending.any():raise RuntimeError('Room continuation failed to settle')
    data=[sorted(rows,key=lambda r:r['replicate']) for rows in data]
    assert all(len(rows)==samples for rows in data)
    delta=[int(a['survived'])-int(b['survived']) for a,b in zip(*data)]
    result=dict(samples_per_choice=samples,choices=['monster','trapped mystery'] if case else ['locked','mystery'],
        survival=[stats([r['survived'] for r in rows]) for rows in data],paired_survival_difference=stats(delta),
        encounters=encounters,followup_actions=choices,
        scope='Reported public state; fresh independent future seeds; existing policy after forcing each door. Immediate room-exit survival, not week completions.')
    save(f'{prefix}-outcomes.json',data);save(f'{prefix}-comparison.json',result);print(json.dumps(result),flush=True)


def feedback(model):
    cases=json.loads((OUT/'new-feedback-cases.json').read_text())
    room(model,next(c for c in cases if c['step']==507))
    for step in (677,685):
        case=next(c for c in cases if c['step']==step);rows=[[],[]];samples=4096
        with VectorEnv(LIB,[PROFILE],512,budgets=[100],auto_reset=False,event_start_hour=17) as env:
            env.reset();lab=EffectLab(env)
            before=old_runtime.InteractionPolicy(env,profile_values(PROFILE),'base')
            after=InteractionPolicy(env,profile_values(PROFILE),'base');use_after=np.arange(512)%2==1
            with lab.planning_fixture(case['snapshot']['planning_state']) as snap,torch.inference_mode():
                for first in range(0,samples,256):
                    for slot in range(512):lab.restore(slot,snap,mix64(2026101863 ^ mix64(step*100000+first+slot//2)))
                    obs,masks=lab.observe();pending=np.ones(512,bool)
                    for decision in range(500):
                        proposed=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                        a,at,_=before.apply(obs,masks,proposed);b,bt,_=after.apply(obs,masks,proposed)
                        actions=np.where(use_after,b,a);targets=np.where(use_after,bt,at)
                        obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets if np.any(targets>=0) else None)
                        if np.any(trunc[pending]) or np.any(info['invalid_actions'][pending]):raise RuntimeError('Invalid feedback continuation')
                        for slot in np.flatnonzero(pending&done):
                            rows[slot%2].append(dict(replicate=int(first+slot//2),clears=float(info['completions'][slot])-case['observation']['completed_runs']))
                        pending[done]=False
                        if not pending.any():break
                        if done.any():obs,masks=env.reset_done(np.flatnonzero(done))
                    if pending.any():raise RuntimeError('Feedback continuation did not finish')
        rows=[sorted(r,key=lambda x:x['replicate']) for r in rows]
        assert all(len(r)==samples for r in rows)
        result=dict(step=step,room=case['observation']['room'],samples_per_choice=samples,
                    before=stats([r['clears'] for r in rows[0]]),after=stats([r['clears'] for r in rows[1]]),
                    paired_difference=stats([b['clears']-a['clears'] for a,b in zip(*rows)]))
        save(f'recovery-{step}-outcomes.json',rows);save(f'recovery-{step}-comparison.json',result)
        print(json.dumps(result),flush=True)


def weeks(model,reuse_before=False):
    cells={};count=2048
    for budget in (0,100,1000):
        for name,Policy in [('before',old_runtime.InteractionPolicy),('after',InteractionPolicy)]:
            baseline=OUT/f'weeks-{name}-{budget}.json'
            if name=='before' and reuse_before and baseline.exists():
                rows=json.loads(baseline.read_text())
                assert len(rows)==count and all(r['event']==i for i,r in enumerate(rows))
                cells[name,budget]=rows
                print(json.dumps(dict(policy=name,budget=budget,reused_frozen_baseline=True)),flush=True)
                continue
            rows={}
            with VectorEnv(LIB,[PROFILE],512,budgets=[budget],seed=2026101862,seed_namespace=236,
                           auto_reset=False,event_start_hour=17) as env:
                rule=Policy(env,profile_values(PROFILE),'base');rule.count_before_episode=count
                obs,masks=env.reset()
                with torch.inference_mode():
                    while len(rows)<count:
                        proposed=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                        actions,targets,_=rule.apply(obs,masks,proposed)
                        if not masks[np.arange(512),actions].all():raise RuntimeError('Illegal action')
                        obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets if np.any(targets>=0) else None);ended=np.flatnonzero(done|trunc)
                        for slot in ended:
                            index=int(info['episode_index'][slot])
                            if index>=count:continue
                            row={k:float(info[k][slot]) for k in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','elapsed_hours','invalid_actions')}
                            if index in rows or trunc[slot] or row['invalid_actions'] or row['mushrooms']>budget or not 167.99<=row['elapsed_hours']<=168.000001:raise RuntimeError('Invalid week')
                            if row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms']:raise RuntimeError('Spending mismatch')
                            rows[index]=dict(event=index,**row)
                        if len(ended):obs,masks=env.reset_done(ended)
            rows=[rows[i] for i in range(count)];cells[name,budget]=rows
            save(f'weeks-{name}-{budget}.json',rows)
            print(json.dumps(dict(policy=name,budget=budget,clears=stats([r['completions'] for r in rows]))),flush=True)
    contrasts=[];z=NormalDist().inv_cdf(1-.05/6)
    for budget in (0,100,1000):
        a,b=cells['before',budget],cells['after',budget]
        contrasts.append(dict(budget=budget,before=stats([r['completions'] for r in a]),after=stats([r['completions'] for r in b]),
                             paired_simultaneous_95=stats([y['completions']-x['completions'] for x,y in zip(a,b)],z)))
    result=dict(weeks=6*count,contrasts=contrasts,reused_frozen_baseline=reuse_before,
                source_sha256={str(p.relative_to(ROOT)):train_rl.sha(p) for p in
                    [ROOT/'tools/sfld_rl/interaction_policy.py',ROOT/'tools/sfld_rl/late_recovery.py',ROOT/'tools/sfld_rl/visual_advice.py']},
                limits='Frozen policy; revised calibration; continuous play. Confidence intervals cover Monte Carlo noise only.')
    save('weekly-comparison.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['room','weeks','feedback']);p.add_argument('--reuse-before',action='store_true');args=p.parse_args()
    if not (OUT/'plan.json').exists():
        audit=audit_seeds([2026101861,2026101862]);save('plan.json',dict(room_seed=2026101861,root_seed=2026101862,namespace=236,
            room_samples=8192,weekly_events_per_cell=2048,budgets=[0,100,1000],seed_audit=audit,
            checkpoint_sha256=train_rl.sha(CHECKPOINT),profile_sha256=train_rl.sha(PROFILE),library_sha256=train_rl.sha(LIB)))
    torch.set_num_threads(2);_,model=train_rl.load_checkpoint(CHECKPOINT,torch.device('cuda'));model.eval()
    if args.mode=='weeks':weeks(model,args.reuse_before)
    else:dict(room=room,feedback=feedback)[args.mode](model)
