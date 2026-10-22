"""Summarize a local optimality audit with cross-fitted improvement estimates.

For every sampled decision, the best arm is chosen on one half of the matched
trials and measured on the other half (then the halves swap). This removes the
upward selection bias of "best of several noisy arms". The mean per-decision
gain times multi-action decisions per week is a first-order estimate of the
weekly clears available from one step of policy improvement; it ignores
interactions between corrected decisions and is model-conditional.
"""
import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import numpy as np


def cross_fit(arms, policy, z=2.):
    """arms: action -> per-trial completions. Returns cross-fitted gain and chosen arms.

    A deviation is chosen only when its paired lead on the selection half
    exceeds z standard errors, so noise rarely displaces the policy."""
    trials=len(arms[policy]);halves=(np.arange(trials)%2==0,np.arange(trials)%2==1);gains=[];chosen=[]
    for pick,score in (halves,halves[::-1]):
        best=policy;lead=0.
        for a in arms:
            if a==policy:continue
            d=arms[a][pick]-arms[policy][pick];se=d.std(ddof=1)/len(d)**.5
            if se>0 and d.mean()>z*se and d.mean()>lead:best,lead=a,d.mean()
        gains.append(arms[best][score].mean()-arms[policy][score].mean());chosen.append(best)
    return float(np.mean(gains)),chosen


def interval(values, scale=1.):
    values=np.asarray(values,float)*scale;se=values.std(ddof=1)/len(values)**.5 if len(values)>1 else 0.
    return dict(mean=float(values.mean()),se=float(se),ci95=[float(values.mean()-1.96*se),float(values.mean()+1.96*se)],n=len(values))


def budget_report(folder):
    result=json.loads((folder/'results.json').read_text());states={s['id']:s for s in result['states']}
    trials=defaultdict(dict)
    with gzip.open(folder/'screen-records.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            r=json.loads(line);trials[r['state']].setdefault(r['arm'],{})[r['trial']]=r['completions']
    gains={};switches=defaultdict(list);phases=defaultdict(list)
    for i,s in states.items():
        arms={a:np.array([v[t] for t in sorted(v)]) for a,v in trials[i].items()}
        g,chosen=cross_fit(arms,s['policy']);gains[i]=g;phases[s['phase']].append(g)
        for c in chosen:
            if c!=s['policy']:switches[f"{s['phase']}: {s['policy']} -> {c}"].append(g)
    per_week=result['summary']['decisions_per_week'];values=list(gains.values())
    confirmed=[dict(id=s['id'],phase=s['phase'],room=s['room'],hp=s['hp'],keys=s['keys'],hours_left=s['hours_left'],
                    policy=s['policy'],better=s['best_alternative'],**s['confirm'])
               for s in result['states'] if s.get('confirm',{}).get('confirmed')]
    return dict(budget=result['plan']['budget'],engine=result['summary']['engine'],summary=result['summary'],
        weekly_gain_estimate=interval(values,per_week),per_decision_gain=interval(values),
        decisions_where_policy_kept=float(np.mean([g==0 for g in values])),
        by_phase={p:dict(share=len(v)/len(values),weekly_contribution=interval(v,per_week*len(v)/len(values)))
                  for p,v in sorted(phases.items())},
        frequent_switches=sorted(({'switch':k,'count':len(v),'mean_gain':float(np.mean(v))} for k,v in switches.items()),
                                 key=lambda r:-r['count'])[:15],
        confirmed=confirmed)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);a=p.parse_args()
    reports=[budget_report(f) for f in sorted(a.folder.glob('budget-*'),key=lambda f:int(f.name.split('-')[1]))
             if (f/'results.json').exists()]
    (a.folder/'report.json').write_text(json.dumps(reports,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    for r in reports:
        w=r['weekly_gain_estimate'];s=r['summary']
        print(f"budget {r['budget']}: {s['sampled_states']} decisions, flagged {s['flagged']}, confirmed {s['confirmed']}; "
              f"one-step weekly gain {w['mean']:.3f} clears (95% CI {w['ci95'][0]:.3f}..{w['ci95'][1]:.3f})")
