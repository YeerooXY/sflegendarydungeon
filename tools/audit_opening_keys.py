"""Record the current adviser on explicit stage-one curse/health positions."""
import argparse
import json
from pathlib import Path
from itertools import product
import torch
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from sfld_rl.interaction_cases import state
from sfld_rl.visual_policy import VisualPolicy,CHECKPOINT_SHA,encode
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.player_ideas import profile_values

ROOT=Path(__file__).resolve().parents[1]


def run(output):
    torch.set_num_threads(2);policy=VisualPolicy();profile=ROOT/'profiles/synthetic.profile';rows=[]
    with VectorEnv(ROOT/'build-v0611/Release/sfld_rl.dll',[profile],1,budgets=[1000],auto_reset=False) as env:
        env.reset();lab=EffectLab(env);rule=InteractionPolicy(env,profile_values(profile),'base')
        for run,hp,free,blessing in product((1,2),(10,21,40,80),('monster','mystery'),('none','one_hit:strong:6','recovery:strong:6')):
            progress=state(room=12,hp=hp,keys=6,gems='none',run_number=run,doors=free+',locked',
                blessings=blessing,curses='hard_lock:strong:8,clumsy:strong:10')
            with lab.fixture(progress,1000) as snap:lab.restore(0,snap,113)
            obs,masks=lab.observe();o=lab.visible()
            with torch.inference_mode():raw=policy.model.actions(torch.as_tensor(obs),torch.as_tensor(masks),greedy=True)[0].numpy()
            chosen=rule.apply(obs,masks,raw)[0][0]
            _,_,mapping=encode(o,policy.names,policy.actions)
            rows.append(dict(run=run,hp=hp,free=free,blessing=blessing,
                selected=free if env.action_names[chosen]=='door_0' else 'locked',
                cost=o['doors'][1]['key_cost'],action=mapping[int(chosen)],progress=progress))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(dict(checkpoint_sha256=CHECKPOINT_SHA,scope='Synthetic positions, not natural encounter frequencies.',rows=rows),indent=2)+'\n')
    print(json.dumps([r for r in rows if r['run']==1 and (r['blessing']=='none' or r['hp'] in (21,80))],indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output)
