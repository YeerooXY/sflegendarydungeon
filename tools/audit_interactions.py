"""Inspect player-interaction states and run independent first-action trials.

Probe outcomes concern engineered positions, not their natural frequency. Each
arm receives a fresh shared future seed; hidden native RNG state is never read.
All continuations follow the same frozen core policy, including existing guards.
"""
import argparse
import gzip
import json
from pathlib import Path
from statistics import mean, stdev, NormalDist
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once, transfer_changes
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, mix64
from sfld_rl.interaction_cases import cases
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values
from sfld_rl.visual_policy import encode, CHECKPOINT
from sfld_rl.visual_advice import annotate, correct_action

ROOT=Path(__file__).resolve().parents[1]


def choices(checkpoint, output):
    torch.set_num_threads(2)
    saved,model=train_rl.load_checkpoint(checkpoint,torch.device('cpu'));model.eval()
    panel=cases();rows=[]
    library=Path(plan.get('library',ROOT/'build-v0611/Release/sfld_rl.dll'));profile=ROOT/'profiles/synthetic.profile'
    for budget in (0,100,1000):
        with VectorEnv(library,[profile],len(panel),budgets=[budget],auto_reset=False) as env:
            transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
            env.reset();lab=EffectLab(env)
            for slot,case in enumerate(panel):
                with lab.fixture(case['progress'],budget) as snap:lab.restore(slot,snap,0)
            obs,masks=lab.observe();rule=Practical(env.observation_names,env.action_names,'learned',profile_values(profile))
            with torch.inference_mode():
                distribution,_=model.distribution(torch.from_numpy(obs),torch.from_numpy(masks))
                proposed=distribution.probs.argmax(dim=1).numpy()
            actions,_,_=rule.apply(obs,masks,proposed)
            for slot,case in enumerate(panel):
                visible=lab.visible(slot);_,_,mapping=encode(visible,env.observation_names,env.action_names)
                annotate(visible);chosen,reason,adjustment=correct_action(visible,mapping[int(actions[slot])])
                runtime=next(env.action_names[a] for a,v in mapping.items() if v==chosen)
                rows.append(dict(case=case['name'],family=case['family'],budget=budget,
                    raw=env.action_names[proposed[slot]],core=env.action_names[actions[slot]],runtime=runtime,
                    adjustment=adjustment,reason=reason,visible=visible,
                    probabilities={env.action_names[a]:float(distribution.probs[slot,a]) for a in np.flatnonzero(masks[slot])}))
    write_once(output,dict(checkpoint=str(checkpoint),checkpoint_sha256=train_rl.sha(checkpoint),
        library_sha256=train_rl.sha(library),profile_sha256=train_rl.sha(profile),
        scope='Engineered visible states. Choices alone do not establish which action is better.',cases=panel,rows=rows))
    print(json.dumps(dict(positions=len(rows),output=str(output))),flush=True)


def probe(checkpoint, plan_path, output, device):
    torch.set_num_threads(2);plan=json.loads(plan_path.read_text());panel=plan['cases'];events=plan['events']
    saved,model=train_rl.load_checkpoint(checkpoint,torch.device(device));model.eval()
    library=ROOT/'build-v0611/Release/sfld_rl.dll';profile=ROOT/'profiles/synthetic.profile'
    tasks=[(c,t,a) for c,case in enumerate(panel) for t in range(events) for a in range(len(case['actions']))]
    n=min(512,len(tasks));records=[];cursor=0;started=time.monotonic();suggestions={}
    budgets=sorted({c['budget'] for c in panel})
    if len(budgets)!=1: raise ValueError('Use one budget per probe plan so restored slots retain their budget identity')
    with VectorEnv(library,[profile],n,budgets=budgets,seed=plan['seed'],seed_namespace=plan['namespace'],auto_reset=False) as env:
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        env.reset();lab=EffectLab(env);snapshots=[lab.fixture(c['progress'],c['budget']) for c in panel]
        if plan.get('runtime_policy',False):
            from sfld_rl.interaction_policy import InteractionPolicy
            rule=InteractionPolicy(env,profile_values(profile),'base')
        else:
            rule=Practical(env.observation_names,env.action_names,'learned',profile_values(profile))
        jobs=[None]*n;active=np.ones(n,bool);first=np.zeros(n,bool)
        first_room=np.ones(n,bool);first_hp=np.full(n,np.nan);first_deaths=np.zeros(n)
        initial_room=np.array([int(dict(line.split('=',1) for line in c['progress'].splitlines())['room']) for c in panel])
        master=(plan['namespace']<<56)|(train_rl.seed_fingerprint(plan['seed'])<<24)
        def assign(slot):
            nonlocal cursor
            if cursor>=len(tasks):
                active[slot]=False;lab.restore(slot,snapshots[0],mix64(master^0xfffffe));return
            c,t,a=tasks[cursor];cursor+=1;jobs[slot]=(c,t,a)
            lab.restore(slot,snapshots[c],mix64(master^(c*events+t)))
            first[slot]=True;first_room[slot]=True;first_hp[slot]=np.nan;first_deaths[slot]=0
        for slot in range(n):assign(slot)
        obs,masks=lab.observe();reported=0
        try:
            with torch.inference_mode():
                while active.any():
                    proposed=train_rl.policy_actions(model,obs,masks,device,greedy=True)[0].cpu().numpy()
                    actions,targets,_=rule.apply(obs,masks,proposed)
                    for slot in np.flatnonzero(first&active):
                        c,t,a=jobs[slot];suggestions.setdefault(c,env.action_names[int(actions[slot])])
                        chosen=env.action_names.index(panel[c]['actions'][a])
                        if not masks[slot,chosen]:raise RuntimeError('Illegal first action: '+panel[c]['name']+' '+panel[c]['actions'][a])
                        actions[slot]=chosen;targets[slot]=-1
                    first[:]=False
                    obs,masks,_,done,trunc,info=env.step(actions)
                    if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid trial')
                    for slot in np.flatnonzero(active&first_room):
                        c,_,_=jobs[slot]
                        if info['room'][slot]!=initial_room[c] or info['deaths'][slot]>0:
                            first_hp[slot]=info['hp'][slot];first_deaths[slot]=info['deaths'][slot];first_room[slot]=False
                    ended=np.flatnonzero(active&done)
                    for slot in ended:
                        c,t,a=jobs[slot]
                        if info['mushrooms'][slot]>panel[c]['budget'] or info['mushrooms'][slot]!=info['recovery_mushrooms'][slot]+info['reroll_mushrooms'][slot]:
                            raise RuntimeError('Spending error')
                        record=dict(case=c,trial=t,arm=a)
                        for key in ('completions','mushrooms','deaths','first_completion_hours','legendaries'):
                            record[key]=float(info[key][slot])
                        record.update(first_room_hp=None if np.isnan(first_hp[slot]) else float(first_hp[slot]),first_room_deaths=float(first_deaths[slot]))
                        records.append(record);assign(int(slot))
                    idle=np.flatnonzero(~active&done)
                    for slot in idle:lab.restore(int(slot),snapshots[0],mix64(master^0xfffffe))
                    if len(ended) or len(idle):obs,masks=lab.observe()
                    if len(records)//5000>reported:
                        reported=len(records)//5000;print(json.dumps(dict(completed=len(records),total=len(tasks))),flush=True)
        finally:
            for snapshot in snapshots:snapshot.close()
    by={(r['case'],r['trial'],r['arm']):r for r in records}
    if len(by)!=len(tasks):raise RuntimeError('Missing or duplicate trials')
    summaries=[];contrasts=sum(len(c['actions'])-1 for c in panel);z=NormalDist().inv_cdf(1-.05/(2*contrasts))
    for c,case in enumerate(panel):
        arms=[[by[c,t,a] for t in range(events)] for a in range(len(case['actions']))]
        comparisons=[]
        for a in range(1,len(arms)):
            d=[y['completions']-x['completions'] for x,y in zip(arms[0],arms[a])];m=mean(d);se=stdev(d)/events**.5
            comparisons.append(dict(action=case['actions'][a],versus=case['actions'][0],delta=m,se=se,
                                    ci95=[m-1.96*se,m+1.96*se],simultaneous95=[m-z*se,m+z*se]))
        summaries.append(dict(case=case,recommendation=suggestions[c],
            arms=[dict(action=case['actions'][a],**{k:mean(r[k] for r in rows) for k in ('completions','mushrooms','deaths','legendaries','first_room_deaths')}) for a,rows in enumerate(arms)],
            comparisons=comparisons))
    data=output.with_suffix('.jsonl.gz')
    with gzip.open(data,'xt',encoding='utf-8') as stream:
        for r in sorted(records,key=lambda r:(r['case'],r['trial'],r['arm'])):stream.write(json.dumps(r,allow_nan=False)+'\n')
    write_once(output,dict(plan=plan,checkpoint_sha256=train_rl.sha(checkpoint),library_sha256=train_rl.sha(library),
        profile_sha256=train_rl.sha(profile),records_sha256=train_rl.sha(data),summaries=summaries,
        wall_seconds=time.monotonic()-started,scope='Forced first action, frozen core continuation for remaining 168h. '
        'Synthetic positions, no natural frequency inference. Matched fresh seeds, not identical draws after branches diverge.'))
    print(json.dumps(dict(finished=str(output),trajectories=len(records))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--checkpoint',type=Path,default=CHECKPOINT)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--probe-plan',type=Path)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu');a=p.parse_args()
    if a.probe_plan:probe(a.checkpoint,a.probe_plan,a.output,a.device)
    else:choices(a.checkpoint,a.output)
