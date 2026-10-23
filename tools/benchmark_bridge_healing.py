"""What handling healing in the MFBot bridge is worth.

Hybrid: the deployed adviser decides everything except healing screens, where
MFBot 6.35's native tasker decides (re-enter at its HP target) and only notices
the target 15-30 minutes late (its pause between checks), as before the bridge
handled healing. Compared with the adviser deciding healing itself, on the
same weeks. Synthetic profile, 0 mushrooms.
"""
import argparse
import json
from pathlib import Path
from statistics import NormalDist
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import transfer_changes, write_once
from sfld_rl.env import VectorEnv
from sfld_rl.mfbot_policy import MFBotPolicy
import audit_local_optimality as audit


class Hybrid:
    def __init__(self, adviser, env, low_hp_entry, rng):
        self.adviser=adviser;self.mfbot=MFBotPolicy(env,low_hp_entry=low_hp_entry);self.rng=rng
        self.i=env.observation_names.index('phase.recovery');self.rule=adviser.rule

    def __call__(self, obs, masks):
        actions,targets=self.adviser(obs,masks)
        healing=obs[:,self.i]>.5
        if healing.any():
            a,t=self.mfbot(obs,masks)
            # MFBot rechecks every 15-30 minutes, so it reaches its HP target late.
            late=np.where(t>0,np.minimum(1,t+self.rng.uniform(15,30,len(t))/60/24),t)
            actions[healing],targets[healing]=a[healing],late[healing]
        return actions,targets


def weeks(model, saved, count, seed, namespace, variant):
    rows={};rng=np.random.default_rng(seed)
    with VectorEnv(audit.LIBRARY,[audit.PROFILE],min(512,count),budgets=[0],seed=seed,seed_namespace=namespace,
                   auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        adviser=audit.Adviser(model,env,'cpu')
        if variant!='adviser':adviser=Hybrid(adviser,env,variant=='mfbot_lowhp',rng)
        obs,masks=env.reset()
        while len(rows)<count:
            actions,targets=adviser(obs,masks)
            obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets)
            if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid week')
            ended=np.flatnonzero(done)
            for s in ended:
                e=int(info['episode_index'][s])
                if e<count:rows[e]=(float(info['completions'][s]),float(info['deaths'][s]))
            if len(ended):obs,masks=env.reset_done(ended)
    return np.array([rows[i] for i in range(count)])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--weeks',type=int,default=2048);p.add_argument('--seed',type=int,default=2026102350)
    p.add_argument('--namespace',type=int,default=0x60);a=p.parse_args()
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    base=weeks(model,saved,a.weeks,a.seed,a.namespace,'adviser');z=NormalDist().inv_cdf(.975);result={}
    for variant in ('mfbot_default','mfbot_lowhp'):
        hybrid=weeks(model,saved,a.weeks,a.seed,a.namespace,variant);d=hybrid[:,0]-base[:,0]
        se=float(d.std(ddof=1)/len(d)**.5)
        result[variant]=dict(adviser=float(base[:,0].mean()),hybrid=float(hybrid[:,0].mean()),difference=float(d.mean()),
                             ci95=[float(d.mean()-z*se),float(d.mean()+z*se)],deaths_adviser=float(base[:,1].mean()),deaths_hybrid=float(hybrid[:,1].mean()))
        print(json.dumps({variant:result[variant]}),flush=True)
    write_once(a.output/'results.json',dict(weeks=a.weeks,seed=a.seed,namespace=a.namespace,result=result,scope=__doc__.strip()))


if __name__=='__main__':
    main()
