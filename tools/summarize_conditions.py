"""Break a local-optimality audit down by held blessings, curses, stones and encounters.

For decisions with exactly two legal actions the screen contrast (other action
minus the adviser's) involves no selection, so its group mean estimates how much
the adviser's choice costs in that context, positive meaning the other action is
better. Confirmed mistakes use the audit's fresh-seed confirmation. The weekly
estimate scales confirmed gains by the sampling rate: each sampled state stands
for 1/rate decisions over the audit's weeks.
"""
import argparse
from collections import Counter
from itertools import combinations
import json
from pathlib import Path
import numpy as np


def stats(states, rate, weeks):
    pairs=[s for s in states if len(s['legal'])==2]
    d=np.array([s['screen'][next(a for a in s['legal'] if a!=s['policy'])]['delta'] for s in pairs])
    se=np.array([s['screen'][next(a for a in s['legal'] if a!=s['policy'])]['se'] for s in pairs])
    confirmed=[s for s in states if s.get('flagged') and s['confirm']['confirmed']]
    return dict(n=len(states),pairs=len(pairs),
        mean_other_minus_policy=float(d.mean()) if len(d) else None,
        ci95=[float(d.mean()-1.96*np.sqrt((se**2).sum())/len(d)),float(d.mean()+1.96*np.sqrt((se**2).sum())/len(d))] if len(d) else None,
        flagged=sum(bool(s.get('flagged')) for s in states),confirmed=len(confirmed),
        weekly_gain_if_fixed=sum(s['confirm']['delta'] for s in confirmed)/(rate*weeks))


def groups(states):
    out={}
    def add(name, test):
        g=[s for s in states if test(s)]
        if g:out[name]=g
    for phase in sorted({s['phase'] for s in states}):add(f'phase {phase}',lambda s,p=phase:s['phase']==p)
    for enc in sorted({s['encounter'] for s in states if s['encounter']}):add(f'encounter {enc}',lambda s,e=enc:s['encounter']==e)
    for b in sorted({b for s in states for b in s['blessings']}):add(f'blessing {b}',lambda s,b=b:b in s['blessings'])
    for n in range(4):add(f'blessing slots {n}',lambda s,n=n:s['blessing_slots']==n)
    for c in sorted({c for s in states for c in s['curses']}):add(f'curse {c}',lambda s,c=c:c in s['curses'])
    for g in sorted({g for s in states for g in s['gems']}):add(f'stone {g}',lambda s,g=g:g in s['gems'])
    pair_counts=Counter(p for s in states for p in combinations(sorted(s['gems']),2))
    for (a,b),n in pair_counts.most_common():
        if n>=150:add(f'stones {a}+{b}',lambda s,a=a,b=b:a in s['gems'] and b in s['gems'])
    for n in range(4):
        add(f'barrel, {n} blessing slots used',lambda s,n=n:s['encounter']=='barrel' and s['blessing_slots']==n)
    for b in sorted({b for s in states for b in s['blessings']}):
        add(f'barrel with {b} active',lambda s,b=b:s['encounter']=='barrel' and b in s['blessings'])
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('results',type=Path);p.add_argument('--json',type=Path)
    a=p.parse_args();data=json.loads(a.results.read_text(encoding='utf-8'))
    states=data['states'];plan=data['plan'];rate,weeks=plan['rate'],plan['weeks']
    if 'blessings' not in states[0]:raise SystemExit('This audit predates condition context; rerun it.')
    rows={name:stats(g,rate,weeks) for name,g in groups(states).items()}
    print(f"{'condition':44}{'states':>7}{'2-way':>7}{'other-policy (95% CI)':>30}{'confirmed':>11}{'week gain':>11}")
    for name,r in sorted(rows.items(),key=lambda kv:-(kv[1]['weekly_gain_if_fixed'] or 0)):
        m=r['mean_other_minus_policy'];ci=r['ci95']
        cell=f'{m:+.4f} ({ci[0]:+.4f},{ci[1]:+.4f})' if m is not None else '-'
        flag=' <' if ci and ci[0]>0 else ''
        print(f"{name:44}{r['n']:7}{r['pairs']:7}{cell:>30}{r['confirmed']:>11}{r['weekly_gain_if_fixed']:+11.4f}{flag}")
    if a.json:a.json.write_text(json.dumps(rows,indent=1)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
