"""Alternating before/after runtime tests with exact output and PPO parity."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
import numpy as np
import torch
import train_rl
from sfld_rl.env import VectorEnv
from sfld_rl.ppo import PPOConfig, ppo_update

ROOT=Path(__file__).resolve().parents[1]
CHECKPOINT=ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt'


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value;spec.loader.exec_module(value);return value


def legacy_actions(model,obs,masks,device,*,greedy=False):
    return model.actions(train_rl.tensor(obs,device),train_rl.tensor(masks,device,torch.bool),greedy=greedy)


def digest_tensor(value):
    h=hashlib.sha256()
    def visit(v):
        if isinstance(v,torch.Tensor):h.update(v.detach().cpu().numpy().tobytes())
        elif isinstance(v,dict):
            for key in sorted(v,key=str):h.update(str(key).encode());visit(v[key])
        elif isinstance(v,(list,tuple)):
            for item in v:visit(item)
        else:h.update(repr(v).encode())
    visit(value);return h.hexdigest()


def loop(env_type,actions_fn,device,library,steps,checkpoint=CHECKPOINT):
    _,model=train_rl.load_checkpoint(checkpoint,torch.device(device));model.eval()
    digest=hashlib.sha256();seconds=0.
    with env_type(library,[ROOT/'profiles/synthetic.profile'],512,budgets=[0,20,50,100,500,1000],
                  auto_reset=False,seed=12301001,seed_namespace=245) as env,torch.inference_mode():
        obs,masks=env.reset()
        for _ in range(16):actions_fn(model,obs,masks,device,greedy=True)
        for _ in range(steps):
            start=time.perf_counter()
            chosen,logs,values=actions_fn(model,obs,masks,device,greedy=True)
            actions=chosen.cpu().numpy();result=env.step(actions)
            ended=np.flatnonzero(result[3]|result[4]);obs,masks=result[:2]
            if len(ended):obs,masks=env.reset_done(ended)
            seconds+=time.perf_counter()-start
            digest.update(actions.tobytes())
            for array in result[:5]:digest.update(array.tobytes())
            for key in sorted(result[5]):digest.update(result[5][key].tobytes())
            digest.update(env.episode_indices.tobytes())
    return {'seconds':seconds,'outputs_sha256':digest.hexdigest(),'decisions':512*steps}


def update(fn,device,library,checkpoint=CHECKPOINT):
    _,model=train_rl.load_checkpoint(checkpoint,torch.device(device));model.eval()
    # A fixed real observation bank, then identical on-policy labels and targets.
    with VectorEnv(library,[ROOT/'profiles/synthetic.profile'],512,budgets=[0,100,1000],seed=12301002) as env,torch.no_grad():
        obs,mask=env.reset();bank=[];masks=[]
        for _ in range(64):
            bank.append(obs);masks.append(mask)
            actions=legacy_actions(model,obs,mask,device,greedy=True)[0].cpu().numpy()
            obs,mask,*_=env.step(actions)
        obs=np.concatenate(bank);mask=np.concatenate(masks)
        torch.manual_seed(421)
        acts,logs,values=legacy_actions(model,obs,mask,device)
        rng=np.random.default_rng(421);adv=rng.normal(size=len(obs)).astype(np.float32)
        batch={'observations':obs,'masks':mask,'actions':acts.cpu().numpy(),'log_probs':logs.cpu().numpy(),
               'advantages':adv,'returns':values.cpu().numpy()+adv*.1}
    optimizer=torch.optim.Adam(model.parameters(),lr=.0001,eps=1e-5)
    config=PPOConfig(learning_rate=.0001,epochs=2,minibatch_size=2048,target_kl=0)
    torch.manual_seed(441);model.train()
    if device=='cuda':torch.cuda.synchronize()
    start=time.perf_counter();metrics=fn(model,optimizer,batch,config,device)
    if device=='cuda':torch.cuda.synchronize()
    seconds=time.perf_counter()-start
    return {'seconds':seconds,'metrics':metrics,'weights_and_optimizer_sha256':digest_tensor([model.state_dict(),optimizer.state_dict()])}


def run(output,reference,library,repeats,steps,checkpoint=CHECKPOINT):
    torch.set_num_threads(2)
    old_env=module('sfld_runtime_reference_env',reference/'tools/sfld_rl/env.py').VectorEnv
    old_update=module('sfld_runtime_reference_ppo',reference/'tools/sfld_rl/ppo.py').ppo_update
    variants={'before':(old_env,legacy_actions,old_update),'after':(VectorEnv,train_rl.policy_actions,ppo_update)}
    devices=['cpu']+(['cuda'] if torch.cuda.is_available() else [])
    rows=[]
    for repeat in range(repeats):
        for name in (['before','after'] if repeat%2==0 else ['after','before']):
            env,actions,fn=variants[name]
            for device in devices:
                row={'repeat':repeat,'variant':name,'device':device,'loop':loop(env,actions,device,library,steps,checkpoint),
                     'ppo_update':update(fn,device,library,checkpoint)}
                rows.append(row);print(json.dumps({k:v for k,v in row.items() if k not in ('loop','ppo_update')}|{'loop_seconds':row['loop']['seconds'],'update_seconds':row['ppo_update']['seconds']}),flush=True)
    summary={}
    for device in devices:
        data=[r for r in rows if r['device']==device]
        for kind,key in [('loop','outputs_sha256'),('ppo_update','weights_and_optimizer_sha256')]:
            if len({r[kind][key] for r in data})!=1:raise RuntimeError(f'{device} {kind} parity failed')
            med={n:statistics.median(r[kind]['seconds'] for r in data if r['variant']==n) for n in variants}
            summary[device+'.'+kind]={**med,'speedup':med['before']/med['after']}
        if len({json.dumps(r['ppo_update']['metrics'],sort_keys=True) for r in data})!=1:raise RuntimeError('PPO metrics differ')
    result={'schema':'sfld-runtime-v2','library_sha256':train_rl.sha(library),'checkpoint_sha256':train_rl.sha(checkpoint),
            'checkpoint':str(checkpoint.resolve().relative_to(ROOT)),
            'repeats':repeats,'steps':steps,'summary':summary,'rows':rows,
            'scope':'Loop excludes setup and hashing. PPO timing covers one fixed update, not total training. Each device has exact before/after simulator outputs and PPO weights, optimizer and metrics.'}
    output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--library',type=Path,default=ROOT/'build-perf/Release/sfld_rl.dll')
    p.add_argument('--repeats',type=int,default=5);p.add_argument('--steps',type=int,default=1024)
    p.add_argument('--checkpoint',type=Path,default=CHECKPOINT)
    a=p.parse_args();run(a.output,a.reference,a.library,a.repeats,a.steps,a.checkpoint)
