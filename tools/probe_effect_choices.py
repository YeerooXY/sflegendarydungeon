"""Paired action interventions from visible effect-slot challenge positions.

Force only the first move, then follow a frozen policy until room 100 clears
or the remaining 168-hour deadline. A timeout is retained as 168 hours. This
diagnoses decisions; it is not an estimate of how often these states occur.
"""
import argparse
import gzip
import json
from pathlib import Path
import statistics
from statistics import NormalDist
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import transfer_changes, write_once
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, mix64
from sfld_rl.player_ideas import profile_values
from sfld_rl.practical import Practical


def decisions(model, rule, obs, masks, profile, device):
    actions=model.actions(train_rl.tensor(obs,device),train_rl.tensor(masks,device,torch.bool),greedy=True)[0].cpu().numpy()
    actions,targets,_=rule.apply(obs,masks,actions)
    v=lambda n:rule.v(obs,n)
    safe=min(1,float(profile['boss_damage'].split(',')[-1].split(':')[1])+.001)
    final=rule.flag(obs,'phase.recovery')&(np.rint(v('room_div100')*100)==100)
    wait=np.maximum(0,safe-v('hp'))*float(profile['recovery_hours'])
    enough=v('hours_left_div168')*168>wait+2*float(profile['action_seconds'])/3600
    patient=final&enough&(np.rint(v('paid_heals_div20')*20)>=2)
    enter=patient&(v('hp')>=safe-1e-10)&masks[:,rule.ai['reenter']]
    actions[enter]=rule.ai['reenter']
    cap=np.zeros(len(obs),bool)
    for name,index in rule.ai.items():
        if name.startswith('wait_hp_'):cap|=(actions==index)&(int(name.rsplit('_',1)[1])/100>safe)
        elif name=='wait_deadline':cap|=(actions==index)&(v('hours_left_div168')*168>wait)
    use=final&(v('hp')<safe-1e-10)&enough&(patient|cap)
    actions[use]=rule.ai['wait_deadline'];targets[use]=safe
    return actions,targets


def run(study, checkpoint, name, device='cpu', only_budget=None):
    torch.set_num_threads(2)
    plan=json.loads((study/'plan.json').read_text());cases=json.loads((study/'probe-cases.json').read_text())
    setup=plan['probe'];events=setup['events'];profile=profile_values(study/'synthetic.profile')
    saved,model=train_rl.load_checkpoint(checkpoint,torch.device(device));model.eval()
    for budget in ([only_budget] if only_budget is not None else setup['budgets']):
        target=study/f'probes-{name}-{budget}.json'
        identity={'checkpoint_sha256':train_rl.sha(checkpoint),'cases_sha256':train_rl.sha(study/'probe-cases.json'),
                  'library_sha256':plan['library_sha256'],'profile_sha256':plan['profile_sha256'],
                  'setup':setup,'budget':budget,'device':device}
        if target.exists():
            if json.loads(target.read_text())['identity']!=identity:raise RuntimeError('Probe inputs changed')
            continue
        start=time.monotonic();n=min(512,len(cases)*2*events)
        tasks=[(c,t,a) for c in range(len(cases)) for t in range(events) for a in (0,1)]
        records=[];recommendations={};cursor=0
        with VectorEnv(study/'sfld_rl.dll',[study/'synthetic.profile'],n,budgets=[budget],seed=setup['seed'],
                       seed_namespace=setup['namespace'],auto_reset=False) as env:
            transfer_changes(saved['metadata']['environment_signature'],env.metadata,False)
            obs,masks=env.reset();lab=EffectLab(env)
            snapshots=[lab.fixture(c['progress'],budget) for c in cases]
            rule=Practical(env.observation_names,env.action_names,'learned',profile)
            active=np.ones(n,bool);first=np.zeros(n,bool);first_room=np.zeros(n,bool)
            room_hp=np.full(n,np.nan);room_death=np.zeros(n,bool)
            jobs=[None]*n
            master=(setup['namespace']<<56)|(train_rl.seed_fingerprint(setup['seed'])<<24)
            def assign(slot):
                nonlocal cursor
                if cursor>=len(tasks):
                    active[slot]=False
                    # A live placeholder keeps the native batch legal after a timeout.
                    lab.restore(slot,snapshots[0],mix64(master ^ 0xfffffe))
                    return
                c,t,a=tasks[cursor];cursor+=1;jobs[slot]=(c,t,a)
                lab.restore(slot,snapshots[c],mix64(master ^ (c*events+t)))
                first[slot]=True;first_room[slot]=True;room_hp[slot]=np.nan;room_death[slot]=False
            for slot in range(n):assign(slot)
            obs,masks=lab.observe();last_report=0
            try:
                with torch.inference_mode():
                    while active.any():
                        actions,targets=decisions(model,rule,obs,masks,profile,device)
                        for slot in np.flatnonzero(first&active):
                            c,t,a=jobs[slot]
                            recommendations.setdefault(c,env.action_names[int(actions[slot])])
                            chosen=env.action_names.index(cases[c]['actions'][a])
                            if not masks[slot,chosen]:raise RuntimeError('Illegal intervention')
                            actions[slot]=chosen;targets[slot]=-1
                        first[:]=False
                        obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets) if np.any(targets>=0) else env.step(actions)
                        if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid probe rollout')
                        moved=active&first_room&((info['room']!=60)|(info['deaths']>0))
                        room_hp[moved]=info['hp'][moved];room_death[moved]=info['deaths'][moved]>0;first_room[moved]=False
                        ended=np.flatnonzero(active&((info['completions']>=1)|done))
                        for slot in ended:
                            c,t,a=jobs[slot];cleared=info['completions'][slot]>=1
                            records.append({'case':c,'trial':t,'arm':a,'cleared':int(cleared),
                                'hours':float(info['first_completion_hours'][slot]) if cleared else 168.,
                                'deaths':float(info['deaths'][slot]),'mushrooms':float(info['mushrooms'][slot]),
                                'first_room_hp':float(room_hp[slot]),'first_room_death':int(room_death[slot])})
                            assign(int(slot))
                        for slot in np.flatnonzero(~active&done):
                            lab.restore(int(slot),snapshots[0],mix64(master ^ 0xfffffe))
                        if len(ended) or np.any(~active&done):obs,masks=lab.observe()
                        if len(records)//10000>last_report:
                            last_report=len(records)//10000
                            print(json.dumps({'probe':name,'budget':budget,'completed':len(records),'total':len(tasks)}),flush=True)
            finally:
                for snapshot in snapshots:snapshot.close()
        if len(records)!=len(tasks):raise RuntimeError('Missing probe trajectories')
        summaries=[];z=NormalDist().inv_cdf(1-.01/(2*len(cases)*len(setup['budgets'])))
        by_key={(r['case'],r['trial'],r['arm']):r for r in records}
        if len(by_key)!=len(records):raise RuntimeError('Duplicate probe trajectory')
        for c,case in enumerate(cases):
            arm_rows=[[by_key[c,t,a] for t in range(events)] for a in (0,1)]
            differences=[x['hours']-y['hours'] for x,y in zip(*arm_rows)]
            mean=statistics.mean(differences);se=statistics.stdev(differences)/events**.5
            summaries.append({'case':case,'recommendation':recommendations[c],
                'arms':[{k:statistics.mean(r[k] for r in rows) for k in ('hours','cleared','deaths','mushrooms','first_room_hp','first_room_death')} for rows in arm_rows],
                'first_minus_second_hours':{'mean':mean,'standard_error':se,'paired_99_ci':[mean-2.57583*se,mean+2.57583*se],
                                            'family_99_ci':[mean-z*se,mean+z*se]}})
        data=target.with_suffix('.jsonl.gz')
        with gzip.open(data,'xt',encoding='utf-8') as stream:
            for r in sorted(records,key=lambda r:(r['case'],r['trial'],r['arm'])):stream.write(json.dumps(r,allow_nan=False)+'\n')
        write_once(target,{'identity':identity,'trajectories':len(records),'wall_seconds':time.monotonic()-start,
            'interpretation':'First action forced, frozen policy thereafter. Negative time difference favors first action. Censored at 168 hours, retained. Engineered positions do not estimate natural frequencies.',
            'paired_rng':'Identical fresh root per visible case/trial for both first actions; branches consume different draws after diverging.',
            'records_sha256':train_rl.sha(data),'summaries':summaries})
        print(json.dumps({'finished_probe':name,'budget':budget,'trajectories':len(records),'seconds':time.monotonic()-start}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--name',required=True)
    p.add_argument('--device',choices=['cpu','cuda'],default='cpu');p.add_argument('--budget',type=int)
    a=p.parse_args();run(a.study.resolve(),a.checkpoint.resolve(),a.name,a.device,a.budget)
