"""Independent weekly comparison of stage-one fights versus paid key doors.

The already-paid hungry-chest correction applies to every arm. No training,
hidden-state reads, simulator changes or automatic deployment.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean,stdev,NormalDist
import time
import numpy as np
import torch
import train_rl
from train_continuation_study import audit_seeds
from train_current_study import copy
from benchmark_player_ideas import write_once,transfer_changes
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.opening_keys import OpeningKeyPolicy,VARIANTS
from sfld_rl.visual_policy import CHECKPOINT,CHECKPOINT_SHA

ROOT=Path(__file__).resolve().parents[1]
BUDGETS=(0,100,1000)


def evaluate(out,plan,variant,cohort,budget):
    setup=plan[cohort];n=setup['events'];rows={};counts=Counter();first_keys=[];later_keys=[]
    saved,model=train_rl.load_checkpoint(out/'checkpoint.pt',torch.device('cuda'));model.eval()
    started=time.monotonic()
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],min(512,n),budgets=[budget],
                   seed=setup['seed'],seed_namespace=setup['namespace'],auto_reset=False) as env:
        transfer_changes(saved['metadata']['environment_signature'],env.metadata,True)
        rule=OpeningKeyPolicy(env,profile_values(out/'synthetic.profile'),variant);rule.count_before_episode=n
        obs,masks=env.reset()
        with torch.inference_mode():
            while len(rows)<n:
                proposed=train_rl.policy_actions(model,obs,masks,'cuda',greedy=True)[0].cpu().numpy()
                actions,_,_=rule.apply(obs,masks,proposed)
                valid=env.episode_indices<n
                f=lambda name:rule.flag(obs,name)
                v=lambda name:rule.v(obs,name)
                room=np.rint(v('room_div100')*100)
                stage=valid&(room<25)
                for side in range(2):
                    use=stage&f('phase.doors')&(actions==rule.ai[f'door_{side}'])
                    costs=np.rint(v(f'doors.{side}.key_cost_div6')*6)
                    counts['s1_paid_doors']+=int((use&(costs>0)).sum())
                    counts['s1_door_keys_spent']+=int(costs[use].sum())
                    doubled=use&(costs>0)&(rule.effect(obs,'hard_lock')>0)&(rule.effect(obs,'clumsy')>0)
                    counts['s1_paid_doors_both_curses']+=int(doubled.sum())
                chest=valid&f('phase.encounter')&f('encounter.sated_chest')
                counts['hungry_chests_opened']+=int((chest&(actions==rule.ai['interact'])).sum())
                counts['hungry_chests_skipped']+=int((chest&(actions==rule.ai['skip'])).sum())
                counts['s1_fights']+=int((stage&(actions==rule.ai['fight'])).sum())
                boss=valid&(room==25);first=f('first_event_run').copy();deaths_before=np.rint(v('deaths_div100')*100)
                obs,masks,_,done,trunc,info=env.step(actions)
                counts['s1_deaths']+=int((info['deaths']-deaths_before)[stage].sum())
                won=boss&(info['room']==26)
                first_keys.extend((rule.v(obs,'keys_div20')[won&first]*20).tolist())
                later_keys.extend((rule.v(obs,'keys_div20')[won&~first]*20).tolist())
                for slot in np.flatnonzero(done|trunc):
                    index=int(info['episode_index'][slot])
                    if index>=n:continue
                    row={key:float(info[key][slot]) for key in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries','elapsed_hours','invalid_actions')}
                    if (index in rows or trunc[slot] or row['invalid_actions'] or row['mushrooms']>budget or
                        row['mushrooms']!=row['recovery_mushrooms']+row['reroll_mushrooms'] or not 167.99<=row['elapsed_hours']<=168.000001):
                        raise RuntimeError('Invalid or duplicate weekly event')
                    rows[index]=dict(event=index,**row)
                ended=np.flatnonzero(done|trunc)
                if len(ended):obs,masks=env.reset_done(ended)
    result=dict(variant=variant,cohort=cohort,budget=budget,rows=[rows[i] for i in range(n)],
                counters=dict(counts),corrections=rule.counts,
                first_run_boss_keys=mean(first_keys) if first_keys else None,
                later_run_boss_keys=mean(later_keys) if later_keys else None,seconds=time.monotonic()-started)
    result['mean_completions']=mean(row['completions'] for row in result['rows'])
    write_once(out/f'{cohort}-{variant}-{budget}.json',result)
    print(json.dumps({k:result[k] for k in ('variant','cohort','budget','mean_completions','first_run_boss_keys','seconds')}),flush=True)
    return result


def run(out):
    torch.set_num_threads(2)
    if out.exists():raise FileExistsError(out)
    seeds=audit_seeds([2026101581,2026101582])
    out.mkdir(parents=True)
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA:raise RuntimeError('Changed checkpoint')
    for source,name in ((CHECKPOINT,'checkpoint.pt'),(ROOT/'build-v0611/Release/sfld_rl.dll','sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile')):copy(source,out/name)
    sources=[Path(__file__),ROOT/'tools/train_rl.py',ROOT/'tools/benchmark_player_ideas.py']+sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    for source in sources:copy(source,out/'source'/source.relative_to(ROOT))
    plan=dict(schema='sfld-opening-keys-v1',budgets=BUDGETS,variants=VARIANTS,
        checkpoint_sha256=CHECKPOINT_SHA,library_sha256=train_rl.sha(out/'sfld_rl.dll'),profile_sha256=train_rl.sha(out/'synthetic.profile'),
        sources={source.relative_to(ROOT).as_posix():train_rl.sha(source) for source in sources},
        selection=dict(seed=2026101581,namespace=218,events=512),final=dict(seed=2026101582,namespace=219,events=2048),
        selection_rule='Highest equal-budget completion mean; control wins exact ties. Freeze winner before final.',
        final_rule='Selected candidate versus control, independent 2048 paired weeks per budget. Four simultaneous 99% intervals; positive equal-budget lower bound and no negative individual upper bound required. No automatic deployment.',
        scope='Healthy untrapped visible S1 monsters versus paid doors; both curses only, or keys below 8 / Hard Lock active. Worst configured hit plus healing/poison must be survivable. All arms collect already-paid hungry chests.')
    write_once(out/'seed-audit.json',seeds);write_once(out/'plan.json',plan)
    cells={(variant,budget):evaluate(out,plan,variant,'selection',budget) for variant in VARIANTS for budget in BUDGETS}
    scores={v:mean(cells[v,b]['mean_completions'] for b in BUDGETS) for v in VARIANTS}
    winner=max(scores,key=scores.get);write_once(out/'selection.json',dict(winner=winner,scores=scores))
    contrasts=[];final=[];eligible=False
    if winner!='control':
        cells={(v,b):evaluate(out,plan,v,'final',b) for v in ('control',winner) for b in BUDGETS}
        z=NormalDist().inv_cdf(1-.01/(2*4));all_delta=[]
        def interval(values):
            m=mean(values);se=stdev(values)/len(values)**.5
            return dict(delta=m,standard_error=se,simultaneous99=[m-z*se,m+z*se])
        for b in BUDGETS:
            delta=[a['completions']-c['completions'] for a,c in zip(cells[winner,b]['rows'],cells['control',b]['rows'])]
            all_delta.append(delta)
            contrasts.append(dict(budget=b,control=cells['control',b]['mean_completions'],candidate=cells[winner,b]['mean_completions'],**interval(delta)))
        contrasts.append(dict(budget='equal-budget mean',**interval([mean(v) for v in zip(*all_delta)])))
        eligible=contrasts[-1]['simultaneous99'][0]>0 and all(row['simultaneous99'][1]>=0 for row in contrasts[:-1])
        final=[{k:v for k,v in cell.items() if k!='rows'} for cell in cells.values()]
    for source,sha in plan['sources'].items():
        if train_rl.sha(ROOT/source)!=sha:raise RuntimeError('Source changed during comparison: '+source)
    write_once(out/'summary.json',dict(winner=winner,selection_scores=scores,contrasts=contrasts,
        promotion_eligible=eligible,final=final,limits='Monte Carlo uncertainty only; provisional game mechanics. No global optimality claim.'))
    print(json.dumps(dict(winner=winner,promotion_eligible=eligible,contrasts=contrasts)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    run(p.parse_args().output.resolve())
