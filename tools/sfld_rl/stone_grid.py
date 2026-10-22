"""Legal final-stone menus and reproducible, paired state-grid experiments."""
from itertools import combinations
import random
import numpy as np

HEALTH = [1, 10, 20, 30, 40, 50, 60, 75, 100]
KEYS = list(range(11))
METRICS = ['hours', 'completed', 'deaths', 'mushrooms', 'actions', 'deathless']


def menus(profile):
    """Cover every held-pair/candidate, with three real offers and four rejections.

    Balanced disjoint menu groups avoid enumerating all permutations. The last
    first-run group reuses one candidate to complete its three-slot menu.
    Histories are synthetic but legal; no previously rejected stone is offered.
    """
    rows=[]
    for run,key in ((1,'gems_first_run'),(2,'gems_later_runs')):
        pool=profile[key].split(',')
        for pair in combinations(pool,2):
            available=[g for g in pool if g not in pair]
            random.Random('final-stone-v1:'+str(run)+':'+','.join(pair)).shuffle(available)
            for start in range(0,len(available),3):
                offered=available[start:start+3]
                offered+=available[:3-len(offered)]
                rejected=[g for g in available if g not in offered][:4]
                rows.append(dict(id=len(rows),run=run,held=list(pair),offered=offered,rejected=rejected))
    return rows


def position(menu,keys,hp,blessings='',curses=''):
    if not 0<hp<=100 or keys<0:raise ValueError('Invalid grid state')
    if len(set(menu['held']+menu['offered']+menu['rejected']))!=9:
        raise ValueError('History must contain nine distinct stones')
    fields=dict(schema=1,room=76,run_number=menu['run'],phase='gems',hp_percent=hp,keys=keys,
        gems=','.join(menu['held']),offered_gems=','.join(menu['held']+menu['rejected']),
        gem_offers=','.join(menu['offered']),shops_seen='1,2,3' if menu['run']==1 else '2,3',
        trial_seen='true',blessings=blessings,curses=curses)
    return ''.join(f'{k}={v}\n' for k,v in fields.items() if v!='')


def crossover_selection(plan,screen_arrays,limit=12):
    """Freeze strong-looking reversals before fresh data; no significance claim.

    Keys: compare 0 versus 10 at the same HP. Health: compare 1 versus 75 at
    the same key count. Require opposite screening winners, and rank the
    smaller of the two apparent advantages. Keep one example per menu/family.
    """
    candidates={'keys':[],'health':[]}
    hs=plan['health'];ks=plan['keys'];low=hs.index(1);high=hs.index(75)
    for (budget,menu_id),data in screen_arrays:
        score=data[...,METRICS.index('hours')].mean(axis=2)  # state, trial, arm -> H,K,arm
        for family in candidates:
            found=[]
            pairs=([(h,ks.index(0),h,ks.index(10)) for h in range(len(hs)) if hs[h]<=77.05]
                   if family=='keys' else [(low,k,high,k) for k in range(len(ks))])
            for h0,k0,h1,k1 in pairs:
                a=int(score[h0,k0].argmin());b=int(score[h1,k1].argmin())
                if a==b:continue
                gaps=[float(score[h0,k0,b]-score[h0,k0,a]),float(score[h1,k1,a]-score[h1,k1,b])]
                found.append(dict(family=family,budget=budget,menu_id=menu_id,arms=[a,b],
                    states=[dict(hp=hs[h0],keys=ks[k0]),dict(hp=hs[h1],keys=ks[k1])],
                    screening_advantages=gaps,selection_score=min(gaps)))
            if found:candidates[family].append(max(found,key=lambda r:r['selection_score']))
    selected=[]
    for budget in sorted({r['budget'] for rows in candidates.values() for r in rows}):
        for family in candidates:
            chosen=sorted([r for r in candidates[family] if r['budget']==budget],
                          key=lambda r:(-r['selection_score'],r['menu_id']))[:limit]
            for row in chosen:row['id']=len(selected);selected.append(row)
    return selected
