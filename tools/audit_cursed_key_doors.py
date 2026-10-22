"""Replay a counted control cohort and inspect expensive early-door decisions."""
import argparse
import json
from pathlib import Path
from collections import Counter
import numpy as np
import torch
import train_rl
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from sfld_rl.opening_keys import OpeningKeyPolicy
from sfld_rl.player_ideas import profile_values
from benchmark_player_ideas import write_once


def run(study):
    torch.set_num_threads(2);plan=json.loads((study/'plan.json').read_text());cohort=plan['selection'];n=cohort['events']
    _,model=train_rl.load_checkpoint(study/'checkpoint.pt',torch.device('cuda'));model.eval();decisions=[]
    for budget in plan['budgets']:
        expected=json.loads((study/f'selection-control-{budget}.json').read_text())['rows'];completed={}
        with VectorEnv(study/'sfld_rl.dll',[study/'synthetic.profile'],min(512,n),budgets=[budget],
                       seed=cohort['seed'],seed_namespace=cohort['namespace'],auto_reset=False) as env:
            lab=EffectLab(env);rule=OpeningKeyPolicy(env,profile_values(study/'synthetic.profile'),'control')
            obs,masks=env.reset()
            with torch.inference_mode():
                while len(completed)<n:
                    proposed=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                    actions,_,_=rule.apply(obs,masks,proposed)
                    phase=rule.flag(obs,'phase.doors');room=np.rint(rule.v(obs,'room_div100')*100)
                    scoped=(env.episode_indices<n)&phase&(room<25)&(rule.effect(obs,'hard_lock')>0)&(rule.effect(obs,'clumsy')>0)
                    for side in range(2):
                        use=scoped&(actions==rule.ai[f'door_{side}'])&(rule.v(obs,f'doors.{side}.key_cost_div6')>0)
                        for slot in np.flatnonzero(use):
                            o=lab.visible(int(slot));other=o['doors'][1-side]
                            decisions.append(dict(budget=budget,event=int(env.episode_indices[slot]),run=o['run_number'],room=o['room'],
                                hp=o['hp'],keys=o['keys'],chosen=o['doors'][side],other=other,blessings=o['blessings'],curses=o['curses'],
                                circumstance='trapped alternative' if other['trap'] or other['cursed_trap'] else 'unavailable alternative' if not other['available'] else 'untrapped alternative',
                                visible=o))
                    obs,masks,_,done,trunc,info=env.step(actions)
                    if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid replay')
                    for slot in np.flatnonzero(done):
                        index=int(info['episode_index'][slot])
                        if index>=n:continue
                        row={key:float(info[key][slot]) for key in expected[index] if key!='event'}
                        if row!={k:v for k,v in expected[index].items() if k!='event'}:raise RuntimeError('Replay diverged from counted event')
                        completed[index]=row
                    ended=np.flatnonzero(done)
                    if len(ended):obs,masks=env.reset_done(ended)
        print(json.dumps(dict(budget=budget,replayed=len(completed))),flush=True)
    result=dict(scope='Exact replay of already-counted selection control events, not additional independent samples.',
                cases=len(decisions),circumstances=dict(Counter(r['circumstance'] for r in decisions)),decisions=decisions)
    write_once(study/'cursed-door-audit.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='decisions'}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    run(p.parse_args().study)
