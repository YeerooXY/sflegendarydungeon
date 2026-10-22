"""Prepare and explicitly record root's assigned native temporal reviews."""
import argparse
import hashlib
import json
from pathlib import Path

import review_new_vods as native
from sfld_vod.media import write_json

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/vods/trap-order-2026-10-12'
OUT=ROOT/'out/vod-hunt-2026-10-12/trap-order/root'


def prepare():
    plan=json.loads((DATA/'expanded-plan.json').read_text())
    rows=[]
    for r in plan['rows']:
        if r['task']!='role_root':continue
        native.WORK=ROOT/r['work_root']
        start=max(0,r['start']-.375)
        end=max(r['end']+.375,r['start']+.75)
        if end-start>180:raise ValueError('Long offer requires an explicit review plan.')
        name=f"trap-role-{r['expanded_index']}"
        m=native.burst(r['source_id'],start,end,name,8)
        path=native.WORK/r['source_id']/'review-preparation/bursts'/name/'index.json'
        rows.append({**r,'sequence_manifest':path.relative_to(ROOT).as_posix(),
                     'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                     'frames':len(m['frames']),'missing':m['missing_timestamps'],
                     'sheets':[p.relative_to(ROOT).as_posix() for p in sorted(path.parent.glob('sheet-*.jpg'))]})
        write_json(OUT/'sequences.json',{'rows':rows})
        print(f"{r['expanded_index']} {r['source_id']} frames={len(m['frames'])} missing={len(m['missing_timestamps'])}",flush=True)


def record(codes):
    plan=json.loads((OUT/'sequences.json').read_text())
    source={r['expanded_index']:r for r in plan['rows']}
    target=DATA/'role-root-reviewed.json'
    previous=json.loads(target.read_text()) if target.exists() else {'schema':1,'review_complete':False,'rows':[]}
    rows={r['expanded_index']:r for r in previous['rows']}
    # index: L / R / N / B / LU / RU / U; unknown side remains null.
    labels={'L':[True,False],'R':[False,True],'N':[False,False],'B':[True,True],
            'LU':[True,None],'RU':[None,True],'U':[None,None],
            'NLU':[None,False],'NRU':[False,None]}
    for line in codes.split(';'):
        fields=line.strip().split('|')
        if not fields[0]:continue
        index=int(fields[0]);label=fields[1];note=fields[2] if len(fields)>2 else ''
        r=source[index]
        m=json.loads((ROOT/r['sequence_manifest']).read_text())
        flags=labels[label]
        # User input to this helper represents direct visual inspection; no classifier.
        rows[index]={**r,'trap_left':flags[0],'trap_right':flags[1],
            'reviewed_kinds':r['kinds'],'identity_verified':True,'eligible':True,
            'inspected_frame_count':len(m['frames']),'notes':note,
            'verification':'Root directly inspected complete chronological native-frame contact sheets; native details for ambiguity. Unknown retained.',
            'evidence_root':r['work_root'],'evidence':m['frames']}
    previous.update(rows=[rows[i] for i in sorted(rows)],review_complete=set(rows)==set(source),
                    unreviewed_expanded_indices=sorted(set(source)-set(rows)))
    write_json(target,previous)
    print(f'reviewed {len(rows)}/{len(source)}')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--record')
    a=p.parse_args()
    if a.prepare:prepare()
    if a.record:record(a.record)
