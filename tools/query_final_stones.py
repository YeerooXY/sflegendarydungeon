"""Rank a held pair's possible third stones, optionally restricted to real offers."""
import argparse
import json
from pathlib import Path
import numpy as np
import train_rl
from publish_final_stones import rank_arrays, read


def lookup(out,run,held,keys,offers=None,excluded=None):
    verification=read(out/'verification.json');plan=read(out/'plan.json')
    if not verification['passed'] or train_rl.sha(out/'plan.json')!=verification['plan_sha256']:
        raise ValueError('Study verification is missing or no longer matches')
    if len(held)!=2 or len(set(held))!=2:raise ValueError('Supply two distinct held stones')
    if keys not in plan['keys']:raise ValueError('This study measured only keys 0,1,3,5,10')
    candidates={}
    for entry in plan['entries']:
        if entry['run']==run and set(held).issubset(entry['stones']):
            candidate=next(g for g in entry['stones'] if g not in held);candidates[candidate]=entry
    if not candidates:raise ValueError('Held pair is not in this run pool')
    if offers is not None and (len(offers)!=3 or len(set(offers))!=3 or not set(offers).issubset(candidates)):
        raise ValueError('Offers must be three distinct eligible unowned stones')
    excluded=excluded or []
    if len(set(excluded))!=len(excluded) or len(excluded)>4 or not set(excluded).issubset(candidates):
        raise ValueError('Exclusions must be up to four distinct previously rejected, unowned stones')
    if offers is not None and set(offers)&set(excluded):raise ValueError('A previously rejected stone cannot also be offered')
    selected=[g for g in candidates if (offers is None or g in offers) and g not in excluded]
    arrays=[]
    for stone in selected:
        entry=candidates[stone];path=out/'results'/f'set-{entry["id"]}.npz';meta=read(path.with_suffix('.json'))
        if train_rl.sha(path)!=meta['data_sha256']:raise ValueError('Raw data hash mismatch')
        with np.load(path) as archive:arrays.append(archive['data'][plan['keys'].index(keys)])
    result,_=rank_arrays(selected,arrays,verification['z'])
    return dict(run=run,held=held,hp=1,keys=keys,mushroom_cap=5,offers=offers,excluded=excluded,
        simultaneous_contrasts=verification['simultaneous_contrasts'],**result,
        limits='Ranks use the frozen downstream policy and the study history panels, no initial temporary effects, '
               'continuous play and 168 hours remaining. Exclusions filter availability only; they do not recompute '
               'the exact observed rejection history. A null supported_best means more than one eligible choice remains unresolved.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--run',type=int,choices=[1,2],required=True);p.add_argument('--held',required=True)
    p.add_argument('--keys',type=int,required=True);p.add_argument('--offers');p.add_argument('--exclude');a=p.parse_args()
    try:result=lookup(a.study.resolve(),a.run,a.held.split(','),a.keys,
                      a.offers.split(',') if a.offers else None,a.exclude.split(',') if a.exclude else None)
    except ValueError as error:p.error(str(error))
    print(json.dumps(result,indent=2))
