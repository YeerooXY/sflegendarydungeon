"""Freeze expanded 200/200 cohorts, reusing the earlier outcome-blind reviews."""
import hashlib
import json
from collections import Counter
from pathlib import Path

from prepare_trap_order_audit import DEST, ROOT, SALT, pack, read
from summarize_vod_cumulative_2026_10_12 import ordinary_new
from analyze_trap_order_2026_10_12 import exclusion, make_row


def main():
    offers = [r for r in read(ROOT/'data/vods/reviewed-offers-2026-10-12.jsonl') if ordinary_new(r)]
    lookup = {(r['source_id'],r['index']):r for r in offers}
    old_plan = read(DEST/'frozen-plan.json')
    prev_ids = {r['id'] for r in old_plan['rows']}
    singles = [r for r in offers if sum(d['kind'] in {'monster','mystery'} for d in r['doors'])==1
               and all(d['kind']!='golden' for d in r['doors'])]
    first = []
    dense = read(ROOT/'data/vods/latest-event-review-2026-10-12/trap-validation--dense-reviewed.json')['rows']
    for a in dense:
        o = lookup.get((a['source_id'],a['index']))
        if a['eligible'] and o in singles:
            first.append(o)
    assert len(first)==97
    first_ids = {r['id'] for r in first}
    rank = lambda r: hashlib.sha256(('trap-order-single-expansion-v1:'+r['id']).encode()).hexdigest()
    single_sample = first + sorted([r for r in singles if r['id'] not in first_ids],key=rank)[:200-len(first)]
    two = [r for r in offers if all(d['kind'] in {'monster','mystery'} for d in r['doors'])]
    two_sample = sorted(two,key=lambda r:hashlib.sha256((SALT+':'+r['id']).encode()).hexdigest())[:200]
    assert {r['id'] for r in old_plan['rows'] if r['prevalence_sample']} <= {r['id'] for r in two_sample}
    rows=[]
    for cohort, sample in [('one_eligible',single_sample),('two_eligible',two_sample)]:
        for o in sample:
            rows.append(pack(o)|{'cohort':cohort,'reuse_existing':o['id'] in (first_ids if cohort=='one_eligible' else prev_ids),
                        'work_root':'out/vod-hunt-2026-10-12','era':'2026',
                        'task': 'one_extra' if cohort=='one_eligible' else 'two_extra'})
    old=read(ROOT/'data/vods/full-entry-linkage-2026-10-08.jsonl')
    manifest={}
    for p in ['out/vods/full-reanalysis-2026-10-08/traps/manifest.json','out/vods/full-reanalysis-2026-10-08/traps-recovery/manifest.json']:
        for r in read(ROOT/p)['records']:
            manifest[r['observation_id']]=r
    mm=[]
    for o in old:
        r=make_row(o,[None,None],True)
        if sorted(r['kinds'])!=['monster','mystery'] or exclusion(r):
            continue
        e=manifest[r['id']]
        interval=e['source_interval']
        assert len(interval)==2
        item={k:r.get(k) for k in ['id','source_id','room','stage','gems','phase','run_id','run_class','kinds']}
        item.update(start=interval[0],end=interval[1],source_seconds=r['source_seconds'],
            stone_set_complete=r['stones_known'],era='2024' if o['source_id']=='youtube-DCb80VikGM0' else '2026',
            work_root=Path(e['root']).relative_to(ROOT).as_posix(), reuse_existing=False,
            cohort='monster_mystery',task='role_root' if o['source_id'] in {'2879181344','2880176202','2882738740','youtube-DCb80VikGM0'} else 'role_extra')
        mm.append(item)
    rows += mm
    for i,r in enumerate(rows):
        r['expanded_index']=i
    result={'schema':1,'date':'2026-10-12','supersedes_scope_only':'frozen-plan.json',
        'sampling': {'one_eligible':'Retain all 97 prior independently sampled current-batch one-eligible reviews; add 103 SHA256-ranked previously unsampled ordinary one-eligible pairs. Outcome-blind expansion; two-phase/source mix is reported.',
                     'two_eligible':'Extend original SHA256 ranking from 100 to 200 out of 269 current-batch two-eligible offers. Do not substitute extra role-census offers into this denominator.',
                     'monster_mystery':'Census of all 215 current-event ordinary candidates with known opening exclusion (50 current batch +165 earlier batch), plus five 2024 candidates reported separately. Review type errors and unreadable sides explicitly; do not hide failed candidates to force a target count.'},
        'scope':'Rooms1-5, bosses, shops, trials/forced screens excluded. Only monster/mystery are core eligible options. Golden-companion pairs outside main one-eligible cohort.',
        'rows':rows,'prior_role_ids':[r['id'] for r in old_plan['rows'] if r['role_census']],
        'one_eligible_target':200,'two_eligible_target':200,
        'role_candidates_by_era':{'2026':215,'2024':5},
        'counts':dict(Counter((r['task']+(':reuse' if r['reuse_existing'] else ':review')) for r in rows))}
    target=DEST/'expanded-plan.json'
    if target.exists():
        assert read(target)==result
    else:
        target.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result['counts'],indent=2))


if __name__=='__main__':main()
