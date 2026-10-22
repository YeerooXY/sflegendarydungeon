"""Replay an audit's deterministic sampling and print visible states by id."""
import argparse
import json
from pathlib import Path
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import transfer_changes
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, mix64
import audit_local_optimality as audit


def main(folder, ids):
    plan=json.loads((folder/'plan.json').read_text());wanted=set(ids);found={}
    torch.set_num_threads(2);saved,model=train_rl.load_checkpoint(audit.CHECKPOINT,torch.device('cpu'));model.eval()
    root=(plan['namespace']<<56)|(train_rl.seed_fingerprint(plan['seed'])<<24)
    with VectorEnv(audit.LIBRARY,[audit.PROFILE],512,budgets=[plan['budget']],seed=plan['seed'],
                   seed_namespace=plan['namespace'],auto_reset=False,event_start_hour=17) as env, torch.inference_mode():
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        lab=EffectLab(env);adviser=audit.Adviser(model,env,'cpu');visible=lab.visible
        capture=lab.capture
        def record(slot):
            # Mirror sampling order: every capture gets the next id.
            index=len(found.setdefault('_order',[]));found['_order'].append(index)
            if index in wanted:found[index]=visible(slot)
            return capture(slot)
        lab.capture=record
        _,snapshots,_=audit.sample(env,lab,adviser,plan['weeks'],plan['rate'],mix64(root^0x5A))
        for s in snapshots:s.close()
    for i in ids:print(json.dumps(dict(id=i,visible=found[i])))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);p.add_argument('ids',type=int,nargs='+')
    a=p.parse_args();main(a.folder,a.ids)
