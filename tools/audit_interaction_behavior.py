"""Replay a subset of the core final cohort to explain behavior, not add evidence."""
from collections import Counter
import json
from pathlib import Path
import sys
import numpy as np
import torch
import train_rl
from sfld_rl.env import VectorEnv
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values
from benchmark_player_ideas import write_once

ROOT=Path(__file__).resolve().parents[1]


def run():
    torch.set_num_threads(2);study=ROOT/'out/interactions-2026-10-15'
    plan=json.loads((study/'plan.json').read_text());candidates=json.loads((study/'training-selection.json').read_text())
    n=256;budget=1000;report=[]
    for c in [r for r in candidates if r['name'] in ('parent','ordinary')]:
        _,model=train_rl.load_checkpoint(study/c['checkpoint'],torch.device('cpu'));model.eval()
        counts=Counter();refill_prices=Counter();first_keys=[];rows={}
        with VectorEnv(study/'sfld_rl.dll',[study/'synthetic.profile'],n,budgets=[budget],seed=plan['final']['seed'],
                       seed_namespace=plan['final']['namespace'],auto_reset=False) as env:
            rule=Practical(env.observation_names,env.action_names,'learned',profile_values(study/'synthetic.profile'))
            obs,masks=env.reset()
            with torch.inference_mode():
                while len(rows)<n:
                    actions=model.actions(torch.from_numpy(obs),torch.from_numpy(masks),greedy=True)[0].numpy()
                    actions,_,_=rule.apply(obs,masks,actions)
                    valid=env.episode_indices<n
                    f=lambda name:rule.flag(obs,name)
                    v=lambda name:rule.v(obs,name)
                    for action in ('heal_full','heal_step','reroll','reenter'):
                        use=valid&(actions==rule.ai[action]);counts[action]+=int(use.sum())
                        if action.startswith('heal_'):
                            feature='heal_full_price_div100' if action=='heal_full' else 'heal_step_price_div100'
                            for price in np.rint(v(feature)[use]*100).astype(int):refill_prices[f'{action}:{price}']+=1
                    escape=np.clip(rule.base_escape+.4*f('owned_gem.rabbit')+.2*f('owned_gem.moonstone')-.3*f('owned_gem.bull')+
                                   rule.effect(obs,'escape')-rule.effect(obs,'clumsy'),0,1)
                    guaranteed=escape>=1-1e-6
                    for i in range(2):
                        chosen=valid&(actions==rule.ai[f'buy_{i}'])&f('phase.shop')
                        for kind in ('one_hit','recovery','escape','elixir','lockpick','raider','disarm'):
                            use=chosen&f(f'offers.{i}.effect.kind.{kind}');counts['buy_'+kind]+=int(use.sum())
                        trial=valid&f('phase.doors')&f(f'doors.{i}.kind.trial')&guaranteed&(rule.effect(obs,'poison')==0)&~f('owned_gem.pearl')
                        counts['guaranteed_trial_offers']+=int(trial.sum())
                        counts['guaranteed_trial_taken']+=int((trial&(actions==rule.ai[f'door_{i}'])).sum())
                        strong=valid&f('phase.shop')&f(f'offers.{i}.effect.kind.one_hit')&(v(f'offers.{i}.effect.remaining_div10')>=.8-1e-6)&masks[:,rule.ai[f'buy_{i}']]
                        counts['strong_ohw_offered']+=int(strong.sum())
                        counts['strong_ohw_rerolled']+=int((strong&(actions==rule.ai['reroll'])).sum())
                    before=(valid&(np.rint(v('room_div100')*100)==25)&f('first_event_run'))
                    obs,masks,_,done,trunc,info=env.step(actions)
                    if trunc.any() or np.any(info['invalid_actions']):raise RuntimeError('Invalid replay')
                    won=before&(info['room']==26)
                    first_keys.extend((rule.v(obs,'keys_div20')[won]*20).tolist())
                    ended=np.flatnonzero(done)
                    for slot in ended:
                        index=int(info['episode_index'][slot])
                        if index<n:rows[index]={k:float(info[k][slot]) for k in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms')}
                    if len(ended):obs,masks=env.reset_done(ended)
        published=json.loads((study/f'final-{c["name"]}-{budget}.json').read_text())['rows']
        for i in range(n):
            if any(rows[i][k]!=published[i][k] for k in rows[i]):raise RuntimeError('Replay does not match original cohort')
        report.append(dict(candidate=c,events=n,budget=budget,counts=dict(counts),refill_prices=dict(refill_prices),
                           first_run_room25_keys=float(np.mean(first_keys)),matched_published_events=n))
    output=ROOT/'out/interaction-behavior-2026-10-15.json'
    write_once(output,dict(scope='Exact replay of 256 already-counted final weeks per policy; not new independent evidence.',
                           seed=plan['final'],rows=report))
    print(json.dumps(report),flush=True)


if __name__=='__main__':run()
