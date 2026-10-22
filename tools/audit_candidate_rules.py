"""Test candidate corrections on the positions where they actually fire.

The deployed adviser plays fresh weeks. Wherever a candidate rule would change
its action, a thinned, capped sample of positions is captured. Each position is
branched: the adviser's own action versus the rule's action, then the deployed
adviser continues both arms with a shared matched seed. Mean gain per firing
times firings per week is a first-order weekly estimate; the promotion gate is
the separate full-week comparison.
"""
import argparse
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
from sfld_rl.candidate_rules import CandidateRules, RULES
from sfld_rl.player_ideas import profile_values
import audit_local_optimality as audit


def run(args):
    out=args.output/f'budget-{args.budget}';out.mkdir(parents=True,exist_ok=True)
    plan=dict(vars(args),output=str(args.output),checkpoint_sha256=audit.CHECKPOINT_SHA,
              library_sha256=train_rl.sha(audit.LIBRARY),profile_sha256=train_rl.sha(audit.PROFILE))
    write_once(out/'plan.json',plan)
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    root=(args.namespace<<56)|(train_rl.seed_fingerprint(args.seed)<<24);started=time.monotonic()
    with VectorEnv(audit.LIBRARY,[audit.PROFILE],args.envs,budgets=[args.budget],seed=args.seed,seed_namespace=args.namespace,
                   auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        lab=EffectLab(env);adviser=audit.Adviser(model,env,'cpu');rules=CandidateRules(env,profile_values(audit.PROFILE))
        fired={r:0 for r in RULES};states=[];snapshots=[];finished=0;steps=np.zeros(env.n_envs,np.int64)
        obs,masks=env.reset()
        while finished<args.weeks:
            valid=env.episode_indices<args.weeks
            actions,targets=adviser(obs,masks)
            for r,(where,target) in rules.proposals(obs,masks,actions).items():
                for slot in np.flatnonzero(where&valid&(target!=actions)):
                    fired[r]+=1;episode=int(env.episode_indices[slot])
                    key=mix64(root^mix64((RULES.index(r)<<48)|(episode<<24)|int(steps[slot])))
                    if key%1000000>=args.rate*1000000 or sum(s['rule']==r for s in states)>=args.cap:continue
                    snapshots.append(lab.capture(int(slot)))
                    states.append(dict(id=len(states),rule=r,episode=episode,step=int(steps[slot]),
                        **audit.describe(env,adviser.rule,obs,masks,slot,actions[slot]),replacement=env.action_names[int(target[slot])]))
            steps+=1
            obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets)
            if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid week')
            ended=np.flatnonzero(done)
            if len(ended):
                finished+=int((env.episode_indices[ended]<args.weeks).sum());steps[ended]=0;obs,masks=env.reset_done(ended)
        print(json.dumps(dict(stage='sample',fired=fired,captured=len(states),seconds=round(time.monotonic()-started))),flush=True)
        index={a:i for i,a in enumerate(env.action_names)}
        try:
            jobs=[(s['id'],index[a],t) for s in states for t in range(args.trials) for a in (s['policy'],s['replacement'])]
            records=audit.branch(env,lab,adviser,snapshots,jobs,mix64(root^0xCA),'rules') if jobs else {}
        finally:
            for s in snapshots:s.close()
    z=NormalDist().inv_cdf(.975);summary={}
    for s in states:
        d=np.array([records[s['id'],index[s['replacement']],t][0]-records[s['id'],index[s['policy']],t][0] for t in range(args.trials)])
        s['gain']=float(d.mean());s['se']=float(d.std(ddof=1)/args.trials**.5)
    for r in RULES:
        g=np.array([s['gain'] for s in states if s['rule']==r])
        if not len(g):summary[r]=dict(fired_per_week=fired[r]/args.weeks,positions=0);continue
        m,se=float(g.mean()),float(g.std(ddof=1)/len(g)**.5) if len(g)>1 else float('nan')
        rate=fired[r]/args.weeks
        summary[r]=dict(fired_per_week=rate,positions=len(g),gain_per_firing=m,se=se,ci95=[m-z*se,m+z*se],
                        weekly_estimate=m*rate,weekly_ci95=[(m-z*se)*rate,(m+z*se)*rate],
                        share_positive=float(np.mean(g>0)),share_negative=float(np.mean(g<0)))
    write_once(out/'results.json',dict(plan=plan,summary=summary,states=states,seconds=time.monotonic()-started,
        scope='Natural firing positions under the deployed adviser; one forced deviation then frozen continuation. Model-conditional.'))
    print(json.dumps(dict(budget=args.budget,summary=summary),indent=1),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budget',type=int,required=True);p.add_argument('--weeks',type=int,default=256)
    p.add_argument('--rate',type=float,default=.25);p.add_argument('--cap',type=int,default=300)
    p.add_argument('--trials',type=int,default=128);p.add_argument('--seed',type=int,default=2026102203)
    p.add_argument('--namespace',type=int,default=0x50);p.add_argument('--envs',type=int,default=512)
    run(p.parse_args())
