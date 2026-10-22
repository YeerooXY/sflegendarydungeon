"""Fixed-scope native sequence extraction for an independent manual key audit.

Offers and their previously reviewed stones only determine scope. No outcomes
are classified here; every retained candidate must receive a manual disposition.
"""
from pathlib import Path
import json
import sys
import hashlib
import cv2
import numpy as np

from sfld_vod.batch import select_clips
from sfld_vod.vision import rect
from sfld_vod.media import sha256

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vod-hunt-2026-10-12'
OUT = ROOT / 'out/key-drop-extension-2026-10-17/lootenant'
DATA = ROOT / 'data/vods/key-drop-extension-2026-10-17'
SOURCE = '2878439002'
WINDOWS = [
    {'run_id': '2878439002:opening-3656', 'start': 3764.0, 'end': 4150.0},
    {'run_id': '2878439002:opening-10672', 'start': 10808.0, 'end': 11075.0},
]

def main():
    records = [json.loads(l) for l in (ROOT / 'data/vods/reviewed-offers-2026-10-12.jsonl').read_text().splitlines()]
    records = sorted([r for r in records if r['source_id'] == SOURCE and r.get('status') == 'decision'], key=lambda r: r['start'])
    selected = []
    for i,row in enumerate(records[:-1]):
        window = next((w for w in WINDOWS if w['start'] <= row['start'] < w['end']), None)
        if window is not None:
            nxt = records[i+1]
            selected.append({**row, 'audit_run_id': window['run_id'], 'next_start': nxt['start'], 'next_room': nxt['room']})
    DATA.mkdir(parents=True, exist_ok=True)
    manifest = {
        'schema': 1, 'source_id': SOURCE, 'fixed_windows': WINDOWS,
        'selection_basis': 'Two full later runs with known cumulative gems excluding Spying and Lodestone, chosen before inspecting key rewards; all stage 2-4 retained decisions included, including bosses and retries for explicit exclusion.',
        'candidate_count': len(selected), 'candidate_offer_ids': [r['id'] for r in selected],
        'original_60_ledger_sha256': sha256(ROOT / 'data/vods/opening-audit-2026-10-17/key-drops.json'),
    }
    scope_path = DATA / 'lootenant-scope.json'
    if scope_path.exists():
        assert json.loads(scope_path.read_text()) == manifest, 'Frozen scope changed'
    else:
        scope_path.write_text(json.dumps(manifest, indent=2) + '\n')
    OUT.mkdir(parents=True, exist_ok=True)
    clips = select_clips(WORK / SOURCE / '720p')
    crop = json.loads((WORK / 'layouts' / f'{SOURCE}.json').read_text())['crop']
    cv2.setNumThreads(1)
    cap = None
    current = None
    output = []
    for ordinal,row in enumerate(selected):
        if len(sys.argv)>1 and ordinal<int(sys.argv[1]):
            continue
        ident = f"{ordinal:03d}-{row['start']:.3f}-r{row['room']:02d}"
        max_end = min(row['next_start']-.1, row['end']+20)
        times = [row['end']-.1] + np.linspace(row['end']+.1, max_end, 7).tolist() + [row['next_start']+.12]
        evidence=[]; thumbs=[]
        for n,t in enumerate(times):
            found=next(((p,c) for p,c in clips if c['source_start']<=t<c['source_end']),None)
            if found is None:continue
            p,c=found
            if current != p:
                if cap:cap.release()
                cap=cv2.VideoCapture(str(p.parent/c['file']));current=p
            cap.set(cv2.CAP_PROP_POS_MSEC,(t-c['source_start'])*1000)
            ok,raw=cap.read()
            if not ok:continue
            actual=c['source_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            x,y,w,h=rect(crop,raw.shape[1],raw.shape[0]);native=raw[y:y+h,x:x+w]
            dest=OUT/f'{ident}-{n}.png';cv2.imwrite(str(dest),native)
            evidence.append({'image':dest.relative_to(ROOT).as_posix(),'source_seconds':t,'decoded_source_seconds':actual,'sha256':sha256(dest),'video':(p.parent/c['file']).relative_to(ROOT).as_posix(),'video_sha256':c['sha256']})
            tile=np.zeros((340,350,3),np.uint8)
            tile[:250]=cv2.resize(native,(350,250))
            tile[250:320]=cv2.resize(native[int(h*.83):],(350,70))
            cv2.putText(tile,f'{n}: {t:.3f}',(4,337),cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1,cv2.LINE_AA)
            thumbs.append(tile)
        sheet=OUT/f'{ident}.jpg'
        if thumbs:cv2.imwrite(str(sheet),np.concatenate(thumbs,axis=1),[cv2.IMWRITE_JPEG_QUALITY,97])
        output.append({'ordinal':ordinal,'offer_id':row['id'],'source_id':SOURCE,'run_id':row['audit_run_id'],'room':row['room'],'stage':row['stage'],'start':row['start'],'end':row['end'],'next_start':row['next_start'],'next_room':row['next_room'],'offered_doors':row['doors'],'previously_reviewed_gems':row['gems'],'previously_reviewed_stone_set_complete':row['stone_set_complete'],'sheet':sheet.relative_to(ROOT).as_posix(),'evidence':evidence,'verification':'unreviewed'})
        print(ordinal,ident,flush=True)
    if cap:cap.release()
    (OUT/'index.json').write_text(json.dumps(output,indent=2)+'\n')
    print('Finished',len(output),flush=True)

if __name__=='__main__':main()
