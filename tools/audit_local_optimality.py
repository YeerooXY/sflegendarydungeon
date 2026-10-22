"""Local optimality audit: does any single deviation beat the deployed adviser?

Decision points are sampled from the deployed policy's own fresh weeks, so they
follow its natural state frequency. At each sampled point every legal action is
forced once, then the same frozen policy (neural checkpoint plus desktop
corrections) plays the remaining week. Arms of one trial share a matched fresh
seed; snapshots never carry the source RNG, so no arm sees a revealed future.

Screening estimates every contrast. Only the best screened alternative per
flagged state proceeds to confirmation, which uses new seeds and a Holm
correction, so selection does not bias the confirmed estimates. A confirmed
gain shows the deployed policy is not a fixed point of one-step improvement on
this model. Absence of confirmed gains bounds only effects above the reported
detection threshold; it is not a proof of optimality or of live performance.
"""
import argparse
import gzip
import json
from pathlib import Path
from statistics import NormalDist
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once, transfer_changes
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, mix64
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.player_ideas import profile_values

ROOT=Path(__file__).resolve().parents[1]
CHECKPOINT=ROOT/'out/interactions-2026-10-15/ordinary/checkpoint-000016777216.pt'
CHECKPOINT_SHA='9689128e5df57f75724b6c0384a3df040d866f283629b4bb9d4c61e630fba59e'
LIBRARY=ROOT/'build-ui-decisions/Release/sfld_rl.dll'
PROFILE=ROOT/'profiles/synthetic.profile'
OUTCOMES=('completions','legendaries','mushrooms','deaths')
PHASES=('doors','encounter','shop','curse_shop','gems','recovery','boss','dead')


class Adviser:
    def __init__(self, model, env, device):
        self.model,self.device=model,device
        self.rule=InteractionPolicy(env,profile_values(PROFILE),'base')

    def __call__(self, obs, masks):
        proposed=train_rl.policy_actions(self.model,obs,masks,self.device,greedy=True)[0].cpu().numpy()
        actions,targets,_=self.rule.apply(obs,masks,proposed)
        if not masks[np.arange(len(obs)),actions].all():raise RuntimeError('Illegal adviser action')
        return actions,targets


def describe(env, rule, obs, masks, slot, action):
    names=env.observation_names
    phase=next((p for p in PHASES if f'phase.{p}' in names and rule.flag(obs,f'phase.{p}')[slot]),'other')
    return dict(phase=phase,room=int(round(float(rule.v(obs,'room_div100')[slot])*100)),
        hp=round(float(rule.v(obs,'hp')[slot]),4),keys=int(round(float(rule.v(obs,'keys_div20')[slot])*20)),
        hours_left=round(float(rule.v(obs,'hours_left_div168')[slot])*168,3),
        legal=[env.action_names[a] for a in np.flatnonzero(masks[slot])],policy=env.action_names[int(action)])


def sample(env, lab, adviser, weeks, rate, root):
    """Capture pre-decision snapshots at a hashed fraction of multi-action decisions."""
    states=[];snapshots=[];steps=np.zeros(env.n_envs,np.int64);finished=0;decisions=0
    obs,masks=env.reset()
    while finished<weeks:
        valid=env.episode_indices<weeks
        actions,targets=adviser(obs,masks)
        choice=valid&(masks.sum(1)>=2)
        for slot in np.flatnonzero(choice):
            episode=int(env.episode_indices[slot])
            if mix64(root^mix64((episode<<24)|int(steps[slot])))%1000000>=rate*1000000:continue
            snapshots.append(lab.capture(int(slot)))
            states.append(dict(id=len(states),episode=episode,step=int(steps[slot]),
                **describe(env,adviser.rule,obs,masks,slot,actions[slot])))
        decisions+=int(choice.sum());steps+=1
        obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets)
        if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid sampling week')
        ended=np.flatnonzero(done)
        if len(ended):
            finished+=int((env.episode_indices[ended]<weeks).sum());steps[ended]=0
            obs,masks=env.reset_done(ended)
    return states,snapshots,decisions


def branch(env, lab, adviser, snapshots, jobs, master, label):
    """Run (state, arm action, trial) jobs; arms of one trial share a seed."""
    n=env.n_envs;records={};cursor=0;slots=[None]*n;active=np.ones(n,bool);forced=np.full(n,-1)
    spare=snapshots[0]
    def assign(slot):
        nonlocal cursor
        if cursor>=len(jobs):
            active[slot]=False;slots[slot]=None;forced[slot]=-1
            lab.restore(slot,spare,mix64(master^0xfffffffe));return
        s,a,t=jobs[cursor];cursor+=1;slots[slot]=(s,a,t);forced[slot]=a
        lab.restore(slot,snapshots[s],mix64(master^mix64((s<<24)|t)))
    for slot in range(n):assign(slot)
    obs,masks=lab.observe();reported=0;started=time.monotonic()
    while active.any():
        actions,targets=adviser(obs,masks)
        first=np.flatnonzero(forced>=0)
        if not masks[first,forced[first]].all():raise RuntimeError('Forced arm is illegal after restore')
        # The adviser's own arm keeps its exact wait target; deviations use the discrete action.
        targets[first]=np.where(actions[first]==forced[first],targets[first],-1)
        actions[first]=forced[first];forced[first]=-1
        obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets)
        if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid continuation')
        ended=np.flatnonzero(done)
        for slot in ended:
            if slots[slot] is not None:
                if info['mushrooms'][slot]>env.episode_budgets[slot]:raise RuntimeError('Spending error')
                records[slots[slot]]=tuple(float(info[k][slot]) for k in OUTCOMES)
            assign(int(slot))
        if len(ended):obs,masks=lab.observe()
        if len(records)//20000>reported:
            reported=len(records)//20000
            print(json.dumps(dict(stage=label,completed=len(records),total=len(jobs),
                seconds=round(time.monotonic()-started))),flush=True)
    if len(records)!=len(jobs):raise RuntimeError('Missing or duplicate trials')
    return records


def contrast(records, s, a, p, trials):
    d=np.array([records[s,a,t][0]-records[s,p,t][0] for t in range(trials)])
    return float(d.mean()),float(d.std(ddof=1)/trials**.5)


def run(args):
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA:raise ValueError('Adviser checkpoint identity changed')
    out=args.output/f'budget-{args.budget}';out.mkdir(parents=True,exist_ok=True)
    plan=dict(budget=args.budget,rules=args.rules,weeks=args.weeks,rate=args.rate,screen_trials=args.screen_trials,
        confirm_trials=args.confirm_trials,screen_z=args.screen_z,alpha=args.alpha,seed=args.seed,
        namespace=args.namespace,event_start_hour=17,sleep_until_hour=0,device=args.device,
        checkpoint=str(CHECKPOINT),checkpoint_sha256=CHECKPOINT_SHA,
        library=str(LIBRARY),library_sha256=train_rl.sha(LIBRARY),profile=str(PROFILE),profile_sha256=train_rl.sha(PROFILE))
    write_once(out/'plan.json',plan)
    torch.set_num_threads(args.threads)
    saved,model=train_rl.load_checkpoint(CHECKPOINT,torch.device(args.device));model.eval()
    root=(args.namespace<<56)|(train_rl.seed_fingerprint(args.seed)<<24);started=time.monotonic()
    with VectorEnv(LIBRARY,[PROFILE],args.envs,budgets=[args.budget],seed=args.seed,seed_namespace=args.namespace,
                   auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        lab=EffectLab(env);adviser=Adviser(model,env,args.device)
        if args.rules:
            from sfld_rl.candidate_rules import CandidateRules, CandidateAdviser
            adviser=CandidateAdviser(adviser,CandidateRules(env,profile_values(PROFILE),args.rules.split(',')))
        states,snapshots,decisions=sample(env,lab,adviser,args.weeks,args.rate,mix64(root^0x5A))
        print(json.dumps(dict(stage='sample',states=len(states),decisions=decisions,
            seconds=round(time.monotonic()-started))),flush=True)
        try:
            index={n:i for i,n in enumerate(env.action_names)}
            jobs=[(s['id'],index[a],t) for s in states for t in range(args.screen_trials) for a in s['legal']]
            screen=branch(env,lab,adviser,snapshots,jobs,mix64(root^0x5C),'screen')
            for s in states:
                p=index[s['policy']];arms={}
                for a in s['legal']:
                    ys=[screen[s['id'],index[a],t] for t in range(args.screen_trials)]
                    arms[a]={k:float(np.mean([y[i] for y in ys])) for i,k in enumerate(OUTCOMES)}
                    if a!=s['policy']:
                        arms[a]['delta'],arms[a]['se']=contrast(screen,s['id'],index[a],p,args.screen_trials)
                s['screen']=arms
                alts=[a for a in s['legal'] if a!=s['policy']]
                best=max(alts,key=lambda a:arms[a]['delta'])
                s['best_alternative']=best
                s['flagged']=arms[best]['delta']>args.screen_z*arms[best]['se']>0
            flagged=[s for s in states if s['flagged']]
            confirm={}
            if flagged:
                jobs=[(s['id'],index[a],t) for s in flagged for t in range(args.confirm_trials)
                      for a in (s['policy'],s['best_alternative'])]
                confirm=branch(env,lab,adviser,snapshots,jobs,mix64(root^0xC0),'confirm')
            tests=[]
            for s in flagged:
                m,se=contrast(confirm,s['id'],index[s['best_alternative']],index[s['policy']],args.confirm_trials)
                p_value=1-NormalDist().cdf(m/se) if se>0 else (0. if m>0 else 1.)
                s['confirm']=dict(delta=m,se=se,p_one_sided=p_value);tests.append(s)
            tests.sort(key=lambda s:s['confirm']['p_one_sided'])
            for rank,s in enumerate(tests):
                threshold=args.alpha/(len(tests)-rank)
                s['confirm']['confirmed']=s['confirm']['p_one_sided']<=threshold and all(
                    t['confirm']['confirmed'] for t in tests[:rank])
        finally:
            for snapshot in snapshots:snapshot.close()
    data=out/'screen-records.jsonl.gz'
    with gzip.open(data,'xt',encoding='utf-8') as stream:
        for (s,a,t),y in sorted(screen.items()):
            stream.write(json.dumps(dict(state=s,arm=env.action_names[a],trial=t,**dict(zip(OUTCOMES,y))))+'\n')
    ses=[s['screen'][a]['se'] for s in states for a in s['legal'] if a!=s['policy']]
    summary=dict(budget=args.budget,engine=env.schema['engine'],sampled_states=len(states),
        multi_action_decisions=decisions,decisions_per_week=decisions/args.weeks,
        policy_best_in_screen=float(np.mean([all(s['screen'][a]['delta']<=0 for a in s['legal'] if a!=s['policy']) for s in states])),
        median_screen_se=float(np.median(ses)),screen_detectable_gain=float(2.8*np.median(ses)),
        flagged=len(flagged),confirmed=sum(s['confirm']['confirmed'] for s in flagged),
        confirm_detectable_gain=float(2.8*np.median([s['confirm']['se'] for s in flagged])) if flagged else None,
        wall_seconds=time.monotonic()-started)
    write_once(out/'results.json',dict(plan=plan,summary=summary,records_sha256=train_rl.sha(data),states=states,
        scope='Synthetic profile, deployed adviser, one forced deviation then frozen continuation for the remaining '
              '168-hour week. Natural decision frequency under this adviser. Model-conditional; not a live-game result.'))
    print(json.dumps(dict(finished=str(out),**summary)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--budget',type=int,required=True)
    p.add_argument('--weeks',type=int,default=64);p.add_argument('--rate',type=float,default=.01)
    p.add_argument('--screen-trials',type=int,default=64);p.add_argument('--confirm-trials',type=int,default=1024)
    p.add_argument('--screen-z',type=float,default=2.);p.add_argument('--alpha',type=float,default=.05)
    p.add_argument('--seed',type=int,default=2026102201);p.add_argument('--namespace',type=int,default=0x4C)
    p.add_argument('--envs',type=int,default=512);p.add_argument('--threads',type=int,default=2)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu');p.add_argument('--rules',default='')
    run(p.parse_args())
