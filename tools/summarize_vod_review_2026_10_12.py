"""Reproduce the manually verified October 12 offer audit, not detector output."""
from __future__ import annotations
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import math

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vod-hunt-2026-10-12'
PHYSICAL = {'locked','cursed','sacrifice','hungry','wall','destiny','blessing','unlocked','double_locked','epic'}
GENERATION_GEMS = {'spying','greed','explorer','pendant','blood','hick','thirsty','misadventurer','sapphire','devil','lodestone','greasy'}
PAIR_ORDER = ['door/mystery','door/monster','mystery/mystery','door/golden','monster/mystery','golden/mystery','golden/monster','golden/golden']
PAIR_MODEL = dict(zip(PAIR_ORDER,[1287/2191,394/2191,278/2191,50/2191,99/2191,71/2191,11/2191,1/2191]))

def wilson(k,n,z=1.959963984540054):
    if not n: return None
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,c-h),min(1,c+h)]

def interval(rows,predicate,model=None):
    n=len(rows); k=sum(bool(predicate(r)) for r in rows)
    grouped=defaultdict(lambda:[0,0])
    for r in rows:
        grouped[r['source_id']][0]+=bool(predicate(r));grouped[r['source_id']][1]+=1
    clusters=list(grouped.values()); boot=[]; rng=random.Random(20261012)
    # Resampling an all-zero/all-one sample cannot estimate unseen outcomes.
    # Keep Wilson's nonzero uncertainty instead of displaying a false [0,0].
    if len(clusters)>=3 and 0 < k < n:
        for _ in range(10000):
            sample=rng.choices(clusters,k=len(clusters)); boot.append(sum(v[0] for v in sample)/sum(v[1] for v in sample))
        boot.sort()
    loo=[(k-a)/(n-b) for a,b in clusters if n>b]
    return {'k':k,'n':n,'estimate':k/n if n else None,'wilson_95':wilson(k,n),
            'source_clusters':len(clusters),'source_cluster_bootstrap_95':[boot[249],boot[9749]] if boot else None,
            'bootstrap_boundary_not_estimable':bool(n and k in {0,n}),
            'leave_one_source_out_range':[min(loo),max(loo)] if loo else None,
            'by_source':dict(grouped),'model_nominal_probability':model}

def pair(row):
    a,b=['door' if d['kind'] in PHYSICAL else d['kind'] for d in row['doors']]
    for key in PAIR_ORDER:
        if sorted(key.split('/'))==sorted([a,b]): return key
    return '/'.join(sorted([a,b]))

def main():
    rows=[]; inputs=[]
    from sfld_vod.media import sha256, write_json
    for path in sorted(WORK.glob('*/review-preparation/manual-offers.json')):
        rows.extend(json.loads(path.read_text()));inputs.append({'path':str(path.relative_to(ROOT)),'sha256':sha256(path)})
    brief=WORK/'brief-recovery/manual-offers.json'
    rows.extend(json.loads(brief.read_text()));inputs.append({'path':str(brief.relative_to(ROOT)),'sha256':sha256(brief)})
    ids=[r['id'] for r in rows]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate candidate IDs')
    decisions=[r for r in rows if r['status']=='decision' and not r.get('sequence_dedup_pending')]
    ordinary=[r for r in decisions if r['phase']=='ordinary' and r.get('room') is not None and 5<r['room']<100 and r['room']%25!=0 and len(r['doors'])==2 and all(d['kind'] in PHYSICAL | {'mystery','monster','golden'} for d in r['doors']) and r['stone_set_complete']]
    cohorts={'no_stones':[r for r in ordinary if not r['gems']],
             'no_known_generation_modifier':[r for r in ordinary if not GENERATION_GEMS.intersection(r['gems'])]}
    offers={}
    for name, group in cohorts.items():
        physical=[dict(r,physical_kind=d['kind']) for r in group for d in r['doors'] if d['kind'] in PHYSICAL]
        offers[name]={'n':len(group),'pair_counts':dict(Counter(pair(r) for r in group)),
            'pairs':{key:interval(group,lambda r,k=key:pair(r)==k,PAIR_MODEL[key]) for key in PAIR_ORDER},
            'physical_styles':{key:interval(physical,lambda r,k=key:r['physical_kind']==k) for key in sorted(PHYSICAL)},
            'stage_counts':dict(Counter(r['stage'] for r in group))}
    exact=[]
    for gems in sorted({tuple(sorted(r['gems'])) for r in ordinary}):
        group=[r for r in ordinary if tuple(sorted(r['gems']))==gems]
        physical=[dict(r,physical_kind=d['kind']) for r in group for d in r['doors'] if d['kind'] in PHYSICAL]
        exact.append({'gems':list(gems),'n':len(group),'pair_counts':dict(Counter(pair(r) for r in group)),
                      'physical_counts':dict(Counter(r['physical_kind'] for r in physical))})
    isolated_modifiers={}
    for modifier in ['spying','lodestone','devil','pendant']:
        group=[r for r in ordinary if GENERATION_GEMS.intersection(r['gems'])=={modifier}]
        physical=[dict(r,physical_kind=d['kind']) for r in group for d in r['doors'] if d['kind'] in PHYSICAL]
        wood=[r for r in physical if r['physical_kind'] in {'locked','unlocked'}]
        isolated_modifiers[modifier]={'ordinary_offers':len(group),
            'pair_counts':dict(Counter(pair(r) for r in group)),
            'physical_counts':dict(Counter(r['physical_kind'] for r in physical)),
            'unlocked_among_locked_or_unlocked':interval(wood,lambda r:r['physical_kind']=='unlocked',.23) if modifier=='spying' else None,
            'note':'No other known pair/style generation modifier; other combat/effect stones may still be active.'}
    lookup={(r['source_id'],r['index']):r for r in rows}
    trap_path=WORK/'trap-validation/reviewed.json'
    dense_path=WORK/'trap-validation/dense-reviewed.json'
    if dense_path.exists() and json.loads(dense_path.read_text()).get('complete'):
        trap_path=dense_path
    trap_data=json.loads(trap_path.read_text())
    traps=[r for r in trap_data['rows'] if r['eligible']]
    resolved=[r for r in traps if r['trap_left'] is True or r['trap_right'] is True or (r['trap_left'] is False and r['trap_right'] is False)]
    traprows=[dict(lookup[(r['source_id'],r['index'])],trap_present=bool(r['trap_left'] or r['trap_right'])) for r in resolved]
    expected=[1-.71**sum(d['kind'] in {'monster','mystery'} for d in r['doors']) for r in traprows]
    trapstats=interval(traprows,lambda r:r['trap_present'],sum(expected)/len(expected))
    trapstats['eligible_sides_histogram']=dict(Counter(sum(d['kind'] in {'monster','mystery'} for d in r['doors']) for r in traprows))
    trapstats['unresolved_pairs']=len(traps)-len(resolved)
    trapstats['sampled_eligible_pairs']=len(traps)
    trapstats['review_file']=str(trap_path.relative_to(ROOT))
    trapstats['review_sha256']=sha256(trap_path)
    trapstats['review_method']='dense_native_8fps' if trap_path==dense_path else 'two_native_stills'
    trapstats['caution']='Sampling balanced by source, not proportional to all event offers. Dense temporal review reduces animation misses but does not establish detector recall outside the sampled offers.'
    shops=[r for r in decisions if r['phase']=='shop' or any(d['kind']=='shop' for d in r['doors'])]
    trials=[r for r in decisions if r['phase']=='trial_entry']
    result={'schema':1,'date':'2026-10-12','inputs':inputs,'candidate_views':len(rows),
        'decision_views':len(decisions),'phase_counts':dict(Counter(r['phase'] for r in decisions)),
        'eligible_ordinary_known_room_complete_stones':len(ordinary),'cohorts':offers,'exact_gem_strata':exact,'isolated_generation_modifiers':isolated_modifiers,
        'trap_audit':trapstats,'shops':{'n':len(shops),'room_histogram':dict(Counter(str(r['room']) for r in shops)),
             'rows':[{'source_id':r['source_id'],'index':r['index'],'room':r['room'],'start':r['start']} for r in shops]},
        'trial_entries':{'n':len(trials),'room_histogram':dict(Counter(str(r['room']) for r in trials)),
             'rows':[{'source_id':r['source_id'],'index':r['index'],'room':r['room'],'start':r['start']} for r in trials]},
        'limitations':['Pointwise intervals, not simultaneous guarantees across all outcomes.',
          'Wilson intervals assume independent observations; source bootstrap is a sensitivity check with few clusters.',
          'Neither interval addresses detection errors, selected VOD windows, player decisions, hidden run/mode differences or unidentified account changes.',
          'No-stone observations concentrate in stage one; first-event-run status is not established for every character.',
          'Video source is the cluster, not a known independent character or run.',
          'Reviewed counts are new-source only. No prior corpus is pooled automatically.']}
    write_json(WORK/'offer-statistics.json',result)
    out=ROOT/'data/vods/reviewed-offers-2026-10-12.jsonl';out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r,ensure_ascii=False)+'\n')
    print(json.dumps({k:result[k] for k in ['candidate_views','decision_views','phase_counts','eligible_ordinary_known_room_complete_stones']},indent=2))
    print('trap',json.dumps(trapstats));print('no stones',json.dumps(offers['no_stones']))

if __name__=='__main__': main()
