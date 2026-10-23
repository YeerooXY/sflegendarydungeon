"""Trapped doors versus sacrifice/cursed doors, and the value of HP before a boss.

traps: each case forces a trapped door or a sacrifice/cursed door, then the
deployed adviser plays the remaining week. Both arms of a trial share a fresh
matched seed. The adviser's own pick in each position is recorded.

preboss: the value of a door screen at a given room and HP. Every case takes
the left door (mystery, beside a monster door) and the adviser plays on, so the
HP curve shows how much each HP point is worth at that distance from the boss.

Engineered positions, not natural frequencies. Imported positions start the
168-hour horizon at the decision, with no earlier completions.
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
from sfld_rl.effect_lab import EffectLab, mix64, position
import audit_local_optimality as audit

GEMS=('','greed','greed,hero','greed,hero,blood')
HPS=tuple(range(10,101,10))
TRAP_ROOMS=(12,24,37,49,62,74,87,99)
TRAPPED=('mystery','monster','destiny')
SAFE=('sacrifice','cursed')
PREBOSS_ROOMS=tuple(b-d for b in (25,50,75,100) for d in (9,7,5,3,1))


def gems(room):
    return GEMS[(room-1)//25]


def cases(study, rooms, trapped=TRAPPED):
    if study=='traps':
        return [dict(room=r,hp=h,trapped=t,other=o,name=f'r{r}-hp{h}-{t}:trap-vs-{o}',
                     progress=position(r,h,gems(r),doors=f'{t}:trap,{o}'))
                for r in rooms for t in trapped for o in SAFE for h in HPS]
    return [dict(room=r,hp=h,name=f'r{r}-hp{h}',progress=position(r,h,gems(r),doors='mystery,monster'))
            for r in rooms for h in HPS]


def run(args):
    rooms=tuple(args.rooms) if args.rooms else (TRAP_ROOMS if args.study=='traps' else PREBOSS_ROOMS)
    trapped=tuple(args.trapped) if args.trapped else TRAPPED
    out=args.output/f'{args.study}-budget-{args.budget}-rooms-{rooms[0]}-{rooms[-1]}';out.mkdir(parents=True,exist_ok=True)
    panel=cases(args.study,rooms,trapped)
    plan=dict(study=args.study,budget=args.budget,rooms=rooms,trapped=trapped,trials=args.trials,seed=args.seed,namespace=args.namespace,hps=HPS,gems=GEMS,
              checkpoint_sha256=audit.CHECKPOINT_SHA,library_sha256=train_rl.sha(audit.LIBRARY),profile_sha256=train_rl.sha(audit.PROFILE))
    write_once(out/'plan.json',plan)
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    root=(args.namespace<<56)|(train_rl.seed_fingerprint(args.seed)<<24);z=NormalDist().inv_cdf(.975);started=time.monotonic()
    with VectorEnv(audit.LIBRARY,[audit.PROFILE],512,budgets=[args.budget],seed=args.seed,seed_namespace=args.namespace,
                   auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        env.reset();lab=EffectLab(env);adviser=audit.Adviser(model,env,'cpu')
        snapshots=[lab.fixture(c['progress'],args.budget) for c in panel]
        try:
            door=[env.action_names.index('door_0'),env.action_names.index('door_1')]
            picks=[]
            for i in range(0,len(panel),env.n_envs):
                chunk=snapshots[i:i+env.n_envs]
                for slot,s in enumerate(chunk):lab.restore(slot,s,mix64(root^0xAD^i^slot))
                obs,masks=lab.observe();actions,_=adviser(obs,masks);picks+=[int(a) for a in actions[:len(chunk)]]
            arms=door if args.study=='traps' else door[:1]
            jobs=[(i,a,t) for i in range(len(panel)) for t in range(args.trials) for a in arms]
            records=audit.branch(env,lab,adviser,snapshots,jobs,mix64(root^args.budget^hash(rooms)%997),args.study)
        finally:
            for s in snapshots:s.close()
    rows=[]
    for i,c in enumerate(panel):
        y0=np.array([records[i,door[0],t][0] for t in range(args.trials)])
        row=dict({k:v for k,v in c.items() if k!='progress'},budget=args.budget,adviser_pick=env.action_names[picks[i]],
                 clears=float(y0.mean()),se=float(y0.std(ddof=1)/args.trials**.5))
        if args.study=='traps':
            d=y0-np.array([records[i,door[1],t][0] for t in range(args.trials)])
            m,se=float(d.mean()),float(d.std(ddof=1)/args.trials**.5)
            row.update(trapped_minus_other=m,diff_se=se,ci95=[m-z*se,m+z*se],
                       verdict='trapped' if m-z*se>0 else c['other'] if m+z*se<0 else 'tie',
                       adviser_agrees=(row['adviser_pick']=='door_0')==(m>0) or abs(m)<z*se)
        rows.append(row)
    write_once(out/'results.json',dict(plan=plan,rows=rows,seconds=time.monotonic()-started,scope=__doc__.strip()))
    print(json.dumps(dict(finished=str(out),cases=len(panel),seconds=round(time.monotonic()-started))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--study',choices=('traps','preboss'),required=True)
    p.add_argument('--budget',type=int,default=0);p.add_argument('--rooms',type=int,nargs='*')
    p.add_argument('--trials',type=int,default=384);p.add_argument('--trapped',nargs='*',choices=TRAPPED)
    p.add_argument('--seed',type=int,default=2026102308);p.add_argument('--namespace',type=int,default=0x58)
    run(p.parse_args())
