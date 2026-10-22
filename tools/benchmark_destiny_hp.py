"""At which HP does a Destiny door beat a mystery or monster door?

Engineered positions, not natural frequencies: every case forces either the
Destiny door or the alternative, then the deployed adviser plays the remaining
week. Both arms of a trial share a fresh matched seed. Imported positions start
the 168-hour horizon at the decision, with no earlier completions.
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

ROOMS=(12,24,37,49,62,74,87,99)
GEMS=('','greed','greed,hero','greed,hero,blood')
HPS=tuple(range(10,101,10))


def cases():
    return [dict(room=room,hp=hp,other=other,name=f'r{room}-hp{hp}-destiny-vs-{other}',
                 progress=position(room,hp,GEMS[(room-1)//25],doors=f'destiny,{other}'))
            for room in ROOMS for other in ('mystery','monster') for hp in HPS]


def run(args):
    out=args.output;out.mkdir(parents=True,exist_ok=True);panel=cases()
    plan=dict(budgets=args.budgets,trials=args.trials,seed=args.seed,namespace=args.namespace,rooms=ROOMS,gems=GEMS,hps=HPS,
              checkpoint_sha256=audit.CHECKPOINT_SHA,library_sha256=train_rl.sha(audit.LIBRARY),profile_sha256=train_rl.sha(audit.PROFILE))
    write_once(out/'plan.json',plan)
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    root=(args.namespace<<56)|(train_rl.seed_fingerprint(args.seed)<<24);rows=[];z=NormalDist().inv_cdf(.975);started=time.monotonic()
    for budget in args.budgets:
        with VectorEnv(audit.LIBRARY,[audit.PROFILE],512,budgets=[budget],seed=args.seed,seed_namespace=args.namespace,
                       auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
            transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
            env.reset();lab=EffectLab(env);adviser=audit.Adviser(model,env,'cpu')
            snapshots=[lab.fixture(c['progress'],budget) for c in panel]
            try:
                door=[env.action_names.index('door_0'),env.action_names.index('door_1')]
                jobs=[(i,a,t) for i in range(len(panel)) for t in range(args.trials) for a in door]
                records=audit.branch(env,lab,adviser,snapshots,jobs,mix64(root^budget),f'destiny-{budget}')
            finally:
                for s in snapshots:s.close()
        for i,c in enumerate(panel):
            d=np.array([records[i,door[0],t][0]-records[i,door[1],t][0] for t in range(args.trials)])
            m,se=float(d.mean()),float(d.std(ddof=1)/args.trials**.5)
            rows.append(dict(budget=budget,room=c['room'],hp=c['hp'],other=c['other'],destiny_minus_other=m,se=se,
                ci95=[m-z*se,m+z*se],destiny=float(np.mean([records[i,door[0],t][0] for t in range(args.trials)])),
                other_clears=float(np.mean([records[i,door[1],t][0] for t in range(args.trials)])),
                verdict='destiny' if m-z*se>0 else args.other_label if m+z*se<0 else 'tie'))
        print(json.dumps(dict(budget=budget,seconds=round(time.monotonic()-started))),flush=True)
    write_once(out/'results.json',dict(plan=plan,rows=rows,scope=__doc__.strip()))
    for budget in args.budgets:
        for other in ('mystery','monster'):
            print(f'\nbudget {budget}: Destiny vs {other} (+ = Destiny better, clears/week; * = 95% CI excludes 0)')
            print('room  '+' '.join(f'{h:>7}%' for h in HPS))
            for room in ROOMS:
                cells=[r for r in rows if r['budget']==budget and r['other']==other and r['room']==room]
                print(f'{room:>4}  '+' '.join(f"{r['destiny_minus_other']:+7.3f}{'*' if r['verdict']!='tie' else ' '}" for r in cells))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--budgets',type=int,nargs='+',default=[0,100]);p.add_argument('--trials',type=int,default=512)
    p.add_argument('--seed',type=int,default=2026102202);p.add_argument('--namespace',type=int,default=0x4D)
    a=p.parse_args();a.other_label='other';run(a)
