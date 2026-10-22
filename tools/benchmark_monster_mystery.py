"""Check target-pair recall on previously verified native offer sequences.

Positive-only recall check, stratified by known trap placement. This is not a
precision estimate or a validation of older-event transfer.
"""
from collections import Counter
import json
from pathlib import Path
import time

from sfld_vod.media import sha256,write_json
from vod_monster_mystery import PairFinder,VERSION

ROOT=Path(__file__).resolve().parents[1]


def main():
    import cv2
    cv2.setNumThreads(1)
    audit=ROOT/'data/vods/trap-order-2026-10-12'
    annotations={}
    for name in ('monster-mystery-reviewed.json','role-extra-reviewed.json','role-root-reviewed.json'):
        data=json.loads((audit/name).read_text())
        for row in data['rows']:
            annotations[row['id']]=(data,row)
    report=json.loads((ROOT/'docs/trap-order-2026-10-12.json').read_text())
    layout={'door_search':[[42,20,102,173],[186,20,102,173]],
            'pair_template_heights':[122,128,134,140,146,152]}
    finder=PairFinder(layout,ROOT/'out/vod-art')
    rows=[];began=time.perf_counter()
    for known in report['role_rows']:
        if known['era']!='2026':continue
        doc,row=annotations[known['id']]
        expected=known['kinds'].index('monster')
        flags=known['sides']
        category='unknown_trap' if None in flags else ('monster_trap' if flags[expected] else 'mystery_trap' if flags[1-expected] else 'no_trap')
        evidence=row.get('evidence',[])
        inside=[e for e in evidence if row.get('start',-1)<=e['source_seconds']<=row.get('end',1e10)]
        # Missing interval evidence is explicit, not a detector failure.
        scores=[];checked=[]
        for e in inside:
            evidence_root=e.get('evidence_root',row.get('evidence_root',doc.get('evidence_root',row.get('work_root'))))
            if evidence_root is None:raise ValueError('Missing evidence root: '+known['id'])
            image=ROOT/evidence_root/e['image']
            if sha256(image)!=e['sha256']:raise ValueError('Changed benchmark image: '+str(image))
            raw=cv2.imread(str(image))
            frame=cv2.resize(raw,(320,224),interpolation=cv2.INTER_AREA)
            result=finder.score(frame)
            checked.append({'image':str(image.relative_to(ROOT)).replace('\\','/'),'sha256':e['sha256']})
            if result:scores.append(result)
        hits=[s for s in scores if s['score']>=.44 and s['monster_side']==expected]
        rows.append({'id':known['id'],'source_id':known['source_id'],'stratum':category,
                     'evidence_frames':len(checked),'found':bool(hits),
                     'best_score':max((s['score'] for s in scores if s['monster_side']==expected),default=None),
                     'evidence':checked})
    summary={}
    for category in sorted({r['stratum'] for r in rows}):
        group=[r for r in rows if r['stratum']==category]
        summary[category]={'total':len(group),'with_evidence':sum(bool(r['evidence_frames']) for r in group),
                           'found':sum(r['found'] for r in group)}
    result={'schema':1,'detector_version':VERSION,'threshold':.44,'summary':summary,'rows':rows,
            'processing_seconds':time.perf_counter()-began,
            'limitations':['Development recall check on an existing corpus used to adjust recognition. Not a held-out accuracy guarantee; does not estimate precision or prevalence.',
                           'Older themes, overlays and missing between-sample offers can have different recall.',
                           'No outcome/stone labels from this check are added to the older-video dataset.']}
    write_json(ROOT/f'data/vods/monster-mystery-detector-benchmark-2026-10-12-v{VERSION}.json',result)
    print(json.dumps({'summary':summary,'seconds':result['processing_seconds'],'misses':[r['id'] for r in rows if not r['found']]},indent=2))


if __name__=='__main__':main()
