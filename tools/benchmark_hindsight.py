"""Clairvoyant one-step lookahead: how much could perfect foresight add?

CLAIRVOYANT RESEARCH ONLY. Clones copy each game's random streams, so every
lookahead sees the week's actual future. At a hashed 1-in-N sample of multi-action decisions, every
legal action is played out by the deployed adviser on the true future and the
best one is taken. Each week's baseline is the deployed adviser on the same
dice (a clone taken at the week's start). Because lookahead is exact and the
adviser's own action is always a candidate, the planner never scores below the
baseline on a week. Its result is a lower bound on the perfect-information
optimum, and the gap mixes the value of information with better decisions; no
real strategy can see the future.

With --workers N the weeks are split into N shards with disjoint episode ranges.
A finished shard is saved under parts/; rerunning the same command skips it. An
unfinished shard reruns whole, because keeping only its fast-finishing weeks
would bias the sample.
"""
import argparse
import ctypes as ct
import json
import os
from pathlib import Path
from statistics import NormalDist
import subprocess
import sys
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once, transfer_changes
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, mix64
import audit_local_optimality as audit

LIBRARY=Path(__file__).resolve().parents[1]/'build-hindsight/Release/sfld_rl.dll'
BASE=-2


class HoldEnv(VectorEnv):
    """VectorEnv whose step accepts -1 (hold this slot) after sfld_rl_enable_hold."""
    def enable(self):
        lib=self._lib
        lib.sfld_rl_clone_slot.argtypes=[ct.c_void_p,ct.c_int32,ct.c_int32];lib.sfld_rl_clone_slot.restype=ct.c_int32
        lib.sfld_rl_enable_hold.argtypes=[ct.c_void_p];lib.sfld_rl_enable_hold.restype=ct.c_int32
        self._check(lib.sfld_rl_enable_hold(self._handle))

    def clone(self, source, target):
        self._check(self._lib.sfld_rl_clone_slot(self._handle,int(source),int(target)))

    def step(self, actions, *, wait_targets=None):
        actions=np.ascontiguousarray(actions,dtype=np.int32)
        if np.any(actions<-1) or np.any(actions>=self.action_dim):raise ValueError('action index out of range')
        targets=np.ascontiguousarray(wait_targets,dtype=np.float64)
        obs,masks=self._outputs();terminal_obs,terminal_masks=self._outputs()
        rewards=np.empty(self.n_envs,np.float32);terminated=np.empty(self.n_envs,np.uint8);truncated=np.empty(self.n_envs,np.uint8)
        metrics=np.empty((self.n_envs,self.metric_dim),np.float64)
        self._check(self._lib.sfld_rl_step_wait_targets(self._handle,self._ptr(actions,ct.c_int32),self._ptr(targets,ct.c_double),
            self._ptr(obs,ct.c_float),self._ptr(masks,ct.c_uint8),self._ptr(rewards,ct.c_float),self._ptr(terminated,ct.c_uint8),
            self._ptr(truncated,ct.c_uint8),self._ptr(metrics,ct.c_double),self._ptr(terminal_obs,ct.c_float),self._ptr(terminal_masks,ct.c_uint8)))
        self._episode_info={name:metrics[:,i] for i,name in enumerate(self.metric_names)}
        return obs,masks.astype(bool),terminated.astype(bool),truncated.astype(bool),{k:v.copy() for k,v in self._episode_info.items()}


def simulate(args, weeks, mains, envs, first_episode):
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    started=time.monotonic();K=mains;n=envs;names=None
    with HoldEnv(LIBRARY,[audit.PROFILE],n,budgets=[args.budget],seed=args.seed,seed_namespace=args.namespace,
                 auto_reset=False,event_start_hour=17,initial_episode_index=first_episode) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        env.reset();env.enable();lab=EffectLab(env);adviser=audit.Adviser(model,env,'cpu');names=env.action_names
        state=['start']*K;week=[None]*K;pending=[{} for _ in range(K)];expected=[0]*K;chosen=[None]*K
        decisions=np.zeros(K,int);changed=np.zeros(K,int);turn=np.zeros(K,np.int64);started_weeks=0;rows=[]
        queue=[];slot_job={};free=list(range(K,n));forced={}
        while len(rows)<weeks:
            obs,masks=lab.observe()
            # Finished scratch games have no menu; they are held, so any placeholder is ignored.
            empty=~masks.any(axis=1);masks[empty,0]=True;obs[empty]=0
            acts,tgs=adviser(obs,masks)
            for m in range(K):
                if state[m]=='start':
                    if started_weeks>=weeks:state[m]='idle';continue
                    started_weeks+=1;week[m]=dict(episode=int(env.episode_indices[m]));queue.append((m,BASE,-1.));state[m]='decide'
                if state[m]!='decide':continue
                legal=np.flatnonzero(masks[m]);turn[m]+=1
                if len(legal)==1:chosen[m]=(int(legal[0]),-1.);state[m]='act';continue
                # Lookahead runs serially in game time, so only a hashed fraction of
                # decisions is clairvoyant; the adviser takes the others unchanged.
                if mix64((week[m]['episode']<<24)|turn[m])%args.every:chosen[m]=(int(acts[m]),float(tgs[m]));state[m]='act';continue
                decisions[m]+=1;pending[m]={};expected[m]=len(legal);state[m]='wait'
                base=int(acts[m]);week[m]['base_action']=base
                for a in legal:queue.append((m,int(a),float(tgs[m]) if a==base else -1.))
            while queue and free:
                m,a,t=queue.pop(0);s=free.pop();env.clone(m,s);slot_job[s]=(m,a);forced[s]=(a,t)
            step=np.full(n,-1,np.int64);targets=np.full(n,-1.)
            for m in range(K):
                if state[m]=='act':step[m],targets[m]=chosen[m]
            for s,(m,a) in slot_job.items():
                if s in forced:
                    a0,t0=forced.pop(s)
                    if a0!=BASE:step[s],targets[s]=a0,t0;continue
                step[s],targets[s]=acts[s],tgs[s]
            obs,masks,done,trunc,info=env.step(step,wait_targets=targets)
            if trunc.any():raise RuntimeError('Truncated week')
            for s in [s for s in list(slot_job) if done[s]]:
                m,a=slot_job.pop(s);free.append(s);value=(float(info['completions'][s]),float(info['legendaries'][s]))
                if a==BASE:week[m]['base']=value
                else:pending[m][a]=value
            for m in range(K):
                if state[m]=='wait' and len(pending[m])==expected[m]:
                    base=week[m]['base_action'];best=max(pending[m],key=lambda a:(pending[m][a],a==base))
                    changed[m]+=best!=base;chosen[m]=(best,float(tgs[m]) if best==base else -1.);state[m]='act'
                elif state[m]=='act' and step[m]>=0:
                    if done[m]:
                        state[m]='finish';week[m].update(hindsight=(float(info['completions'][m]),float(info['legendaries'][m])),
                                                          decisions=int(decisions[m]),changed=int(changed[m]))
                    else:state[m]='decide'
                if state[m]=='finish' and 'base' in week[m]:
                    rows.append(week[m]);decisions[m]=changed[m]=0
                    print(json.dumps(dict(weeks=len(rows),base=week[m]['base'][0],hindsight=week[m]['hindsight'][0],
                        seconds=round(time.monotonic()-started))),flush=True)
                    env.reset_done(np.array([m]));state[m]='start';turn[m]=0
    return rows


def shards(args):
    """Weeks, games and episode range of each shard; envs keep the single-process scratch ratio."""
    out=[]
    for k in range(args.workers):
        weeks=args.weeks//args.workers+(k<args.weeks%args.workers);mains=min(args.mains,weeks)
        out.append(dict(weeks=weeks,mains=mains,envs=max(mains+8,round(mains*args.envs/args.mains)),first_episode=k<<20))
    return out


def run(args):
    out=args.output/f'budget-{args.budget}';out.mkdir(parents=True,exist_ok=True)
    plan=dict(budget=args.budget,weeks=args.weeks,every=args.every,mains=args.mains,envs=args.envs,seed=args.seed,namespace=args.namespace,
              checkpoint_sha256=audit.CHECKPOINT_SHA,library_sha256=train_rl.sha(LIBRARY),profile_sha256=train_rl.sha(audit.PROFILE))
    if args.workers>1:plan['workers']=args.workers
    if args.shard is not None:
        audit.exit_with_parent(args.parent);spec=shards(args)[args.shard]
        rows=simulate(args,spec['weeks'],spec['mains'],spec['envs'],spec['first_episode'])
        write_once(out/'parts'/f'shard-{args.shard}.json',dict(plan=plan,shard=spec,rows=rows));return
    write_once(out/'plan.json',plan);started=time.monotonic()
    if args.workers<=1:rows=simulate(args,args.weeks,args.mains,args.envs,0)
    else:
        parts=out/'parts';parts.mkdir(exist_ok=True);flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0;procs={}
        for k in range(args.workers):
            if (parts/f'shard-{k}.json').exists():continue
            procs[k]=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--shard',str(k),'--parent',str(os.getpid())],
                stdout=(parts/f'shard-{k}.log').open('a'),stderr=(parts/f'shard-{k}.err').open('a'),creationflags=flags)
        print(json.dumps(dict(stage='launch',resumed=args.workers-len(procs),running=len(procs))),flush=True)
        try:
            while any(p.poll() is None for p in procs.values()):
                time.sleep(60)
                done=sum((parts/f'shard-{k}.json').exists() for k in range(args.workers))
                print(json.dumps(dict(shards_done=done,shards=args.workers,seconds=round(time.monotonic()-started))),flush=True)
        finally:
            for p in procs.values():
                if p.poll() is None:p.kill()
        failed=[k for k,p in procs.items() if p.returncode]
        if failed:raise RuntimeError(f'Shards failed: {failed}; see {parts}')
        rows=[]
        for k in range(args.workers):
            part=json.loads((parts/f'shard-{k}.json').read_text(encoding='utf8'))
            if part['plan']!=plan:raise RuntimeError(f'Shard {k} was made with a different plan')
            rows+=part['rows']
    base=np.array([r['base'][0] for r in rows]);best=np.array([r['hindsight'][0] for r in rows]);d=best-base
    z=NormalDist().inv_cdf(.975);se=float(d.std(ddof=1)/len(d)**.5)
    summary=dict(budget=args.budget,weeks=len(rows),base_mean=float(base.mean()),hindsight_mean=float(best.mean()),
                 gain=float(d.mean()),gain_ci95=[float(d.mean()-z*se),float(d.mean()+z*se)],relative_gain=float(d.mean()/base.mean()),
                 weeks_improved=float(np.mean(d>0)),changed_per_week=float(np.mean([r['changed'] for r in rows])),
                 decisions_per_week=float(np.mean([r['decisions'] for r in rows])),
                 base_legendaries=float(np.mean([r['base'][1] for r in rows])),hindsight_legendaries=float(np.mean([r['hindsight'][1] for r in rows])),
                 seconds=time.monotonic()-started)
    write_once(out/'results.json',dict(plan=plan,summary=summary,rows=rows,scope=__doc__.strip()))
    print(json.dumps(summary,indent=1),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budget',type=int,required=True);p.add_argument('--weeks',type=int,default=96)
    p.add_argument('--mains',type=int,default=192);p.add_argument('--every',type=int,default=8);p.add_argument('--envs',type=int,default=512)
    p.add_argument('--seed',type=int,default=2026102204);p.add_argument('--namespace',type=int,default=0x53)
    p.add_argument('--workers',type=int,default=1)
    p.add_argument('--shard',type=int,help=argparse.SUPPRESS);p.add_argument('--parent',type=int,help=argparse.SUPPRESS)
    run(p.parse_args())
