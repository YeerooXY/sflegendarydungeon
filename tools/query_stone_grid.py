"""Look up an exact studied final-stone state; never interpolate a winner."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import NormalDist
import numpy as np


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def load(path):
    meta=read(path.with_suffix('.json'))
    if hashlib.sha256(path.read_bytes()).hexdigest()!=meta['data_sha256']:raise RuntimeError('Data hash mismatch')
    with np.load(path) as archive:return meta,archive['data'],archive['baseline']


def lookup(studies,run,held,offered,hp,keys,budget,rejected=None):
    if len(held)!=2 or len(set(held))!=2 or len(offered)!=3 or len(set(offered))!=3 or set(held)&set(offered):
        raise ValueError('Supply two distinct held stones and three distinct unowned offers')
    if not 1<=hp<=100 or keys<0 or budget<0:raise ValueError('Invalid HP, keys or budget')
    if rejected is not None and (len(rejected)!=4 or len(set(rejected))!=4 or set(rejected)&set(held+offered)):
        raise ValueError('Supply four distinct prior rejections, separate from held stones and offers')
    candidates=[]
    for study in studies:
        plan=read(study/'plan.json')
        if not (study/'verification.json').exists() or not read(study/'verification.json')['passed']:
            raise ValueError('Study has not passed publication verification: '+str(study))
        if budget not in plan['budgets']:continue
        matches=[m for m in plan['menus'] if m['run']==run and set(m['held'])==set(held) and set(m['offered'])==set(offered)
                 and (rejected is None or set(m['rejected'])==set(rejected))]
        for menu in matches:
            selections=read(study/'selection.json')
            for choice in selections:
                if choice['menu_id']!=menu['id'] or choice['budget']!=budget:continue
                for index,state in enumerate(choice['states']):
                    if state==dict(hp=hp,keys=keys):
                        path=study/'confirmation'/f'b{budget}-case-{choice["id"]}.npz'
                        _,data,base=load(path)
                        family=read(study/'comparisons.json')['simultaneous_contrasts']
                        candidates.append((len(data[index]),'confirmation',path,menu,data[index],int(base[index]),family))
            for dense in study.glob('dense-*'):
                if not (dense/'verification.json').exists():continue
                for path in (dense/'dense').glob(f'b{budget}-menu-{menu["id"]}-part-*.npz'):
                    meta=read(path.with_suffix('.json'))
                    for index,state in enumerate(meta['states']):
                        if state==dict(hp=hp,keys=keys):
                            _,data,base=load(path);family=read(dense/'verification.json')['simultaneous_contrasts']
                            candidates.append((len(data[index]),'dense',path,menu,data[index],int(base[index]),family))
            if hp in plan['health'] and keys in plan['keys']:
                path=study/'screening'/f'b{budget}-menu-{menu["id"]}.npz';meta,data,base=load(path)
                index=meta['states'].index(dict(hp=hp,keys=keys))
                candidates.append((len(data[index]),'screening',path,menu,data[index],int(base[index]),None))
    if not candidates:raise ValueError('No exact studied state/menu/history. No nearest-state winner substituted.')
    # Prefer untouched confirmation to dense follow-up, and either to screening.
    rank={'screening':0,'dense':1,'confirmation':2}
    n,phase,path,menu,rows,base,family=max(candidates,key=lambda c:(rank[c[1]],c[0]))
    means=rows.mean(axis=0);winner=int(means[:,0].argmin());margins={};supported=False
    if phase!='screening':
        z=NormalDist().inv_cdf(1-.05/(2*family))
        for other in range(3):
            if other==winner:continue
            differences=rows[:,other,0]-rows[:,winner,0];mean=float(differences.mean());se=float(differences.std(ddof=1)/n**.5)
            margins[menu['offered'][other]]=dict(hours_saved=mean,simultaneous_95=[mean-z*se,mean+z*se])
        supported=all(m['simultaneous_95'][0]>0 for m in margins.values())
    return dict(run=run,held=held,offered=menu['offered'],rejected=menu['rejected'],hp=hp,keys=keys,
        remaining_mushrooms=budget,source=str(path),phase=phase,samples_per_stone=n,
        confidence_method='Paired normal approximation, Bonferroni simultaneous 95%' if family else None,
        simultaneous_contrasts=family,
        learned_choice=menu['offered'][base],estimated_best=menu['offered'][winner],
        supported_best=menu['offered'][winner] if supported else None,advantages=margins,
        estimates=[dict(stone=g,hours=float(means[i,0]),completion_probability=float(means[i,1]),
            deaths=float(means[i,2]),mushrooms_spent=float(means[i,3]),no_death_clear_probability=float(means[i,5]))
            for i,g in enumerate(menu['offered'])],
        limits='Exact recorded synthetic context: no initial temporary effects, prior refill count zero, continuous play, '
               '168 hours remaining. Rejected history is shown above. Screening cannot establish a supported winner. '
               'This measures the current downstream policy, not globally optimal play.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--studies',type=Path,nargs='+',required=True)
    p.add_argument('--run',type=int,choices=[1,2],required=True);p.add_argument('--held',required=True);p.add_argument('--offers',required=True)
    p.add_argument('--rejected');p.add_argument('--hp',type=int,required=True);p.add_argument('--keys',type=int,required=True)
    p.add_argument('--budget',type=int,required=True);a=p.parse_args()
    try:
        result=lookup([x.resolve() for x in a.studies],a.run,a.held.split(','),a.offers.split(','),a.hp,a.keys,a.budget,
                      a.rejected.split(',') if a.rejected else None)
    except ValueError as error:p.error(str(error))
    print(json.dumps(result,indent=2))
