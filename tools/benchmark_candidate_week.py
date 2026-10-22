"""Paired full-week comparison: deployed adviser versus adviser plus candidate rules.

Both arms play the same fresh episode seeds from the start of the week, so the
comparison includes every downstream consequence of each rule. This is the
promotion gate; per-position audits only nominate candidates.
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
from sfld_rl.candidate_rules import CandidateRules, CandidateAdviser, RULES
from sfld_rl.player_ideas import profile_values
import audit_local_optimality as audit

METRICS=('completions','legendaries','mushrooms','deaths')


def weeks(model, saved, profile, budget, count, seed, namespace, rules, teacher=None):
    rows={};counts={}
    with VectorEnv(audit.LIBRARY,[profile],min(512,count),budgets=[budget],seed=seed,seed_namespace=namespace,
                   auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        adviser=audit.Adviser(model,env,'cpu')
        if rules:
            # Rules, like the deployed adviser, assume the shipped profile even when the world differs.
            extra=CandidateRules(env,profile_values(audit.PROFILE),rules);adviser=CandidateAdviser(adviser,extra)
        obs,masks=env.reset()
        while len(rows)<count:
            if teacher:actions,targets=env.teacher_actions(teacher),np.full(env.n_envs,-1.)
            else:actions,targets=adviser(obs,masks)
            obs,masks,_,done,trunc,info=env.step(actions,wait_targets=targets)
            if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid week')
            ended=np.flatnonzero(done)
            for slot in ended:
                e=int(info['episode_index'][slot])
                if e<count:rows[e]=[float(info[k][slot]) for k in METRICS]
            if len(ended):obs,masks=env.reset_done(ended)
        if rules:counts=dict(extra.counts)
    return np.array([rows[i] for i in range(count)]),counts


def run(args):
    out=args.output;out.mkdir(parents=True,exist_ok=True);label=f'{args.label}-budget-{args.budget}'
    rules=[r for r in args.rules.split(',') if r] if args.rules else []
    if set(rules)-set(RULES):raise ValueError('Unknown rule')
    plan=dict(budget=args.budget,weeks=args.weeks,rules=rules,versus=args.versus,profile=str(args.profile),profile_sha256=train_rl.sha(args.profile),
              seed=args.seed,namespace=args.namespace,checkpoint_sha256=audit.CHECKPOINT_SHA,library_sha256=train_rl.sha(audit.LIBRARY))
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    started=time.monotonic()
    base,_=weeks(model,saved,args.profile,args.budget,args.weeks,args.seed,args.namespace,[])
    cand,counts=weeks(model,saved,args.profile,args.budget,args.weeks,args.seed,args.namespace,rules,args.versus)
    z=NormalDist().inv_cdf(1-args.alpha/2);result={}
    for i,k in enumerate(METRICS):
        d=cand[:,i]-base[:,i];se=float(d.std(ddof=1)/len(d)**.5)
        result[k]=dict(base=float(base[:,i].mean()),candidate=float(cand[:,i].mean()),difference=float(d.mean()),se=se,
                       ci=[float(d.mean()-z*se),float(d.mean()+z*se)],weeks_better=float(np.mean(d>0)),weeks_worse=float(np.mean(d<0)))
    summary=dict(label=label,rules=rules,budget=args.budget,weeks=args.weeks,confidence=1-args.alpha,
                 rule_firings_per_week={r:c/args.weeks for r,c in counts.items()},metrics=result,seconds=time.monotonic()-started)
    write_once(out/f'{label}.json',dict(plan=plan,summary=summary,base=base.tolist(),candidate=cand.tolist(),scope=__doc__.strip()))
    c=result['completions'];print(json.dumps(dict(label=label,base=round(c['base'],4),candidate=round(c['candidate'],4),
        difference=round(c['difference'],4),ci=[round(x,4) for x in c['ci']],seconds=round(summary['seconds']))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budget',type=int,required=True);p.add_argument('--rules',default='')
    p.add_argument('--label',default='candidate');p.add_argument('--versus',choices=('survival','legacy'),help='compare the adviser with a handwritten strategy instead');p.add_argument('--weeks',type=int,default=4096)
    p.add_argument('--profile',type=Path,default=audit.PROFILE);p.add_argument('--alpha',type=float,default=.05)
    p.add_argument('--seed',type=int,default=2026102205);p.add_argument('--namespace',type=int,default=0x54)
    run(p.parse_args())
