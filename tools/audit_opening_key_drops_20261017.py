"""Prepare native before/during/after strips for manual key-drop review.

This does not classify outcomes or update simulation odds.
"""
from pathlib import Path
import json
import sys
import cv2
import numpy as np

from sfld_vod.batch import select_clips
from sfld_vod.vision import rect
from sfld_vod.media import sha256

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vod-hunt-2026-10-12'
OUT = ROOT / 'out/opening-audit-2026-10-17/keys'

def prepare(source, start, end):
    rows = [json.loads(l) for l in (ROOT / 'data/vods/reviewed-offers-2026-10-12.jsonl').read_text().splitlines()]
    rows = sorted((r for r in rows if r['source_id'] == source and r.get('status') == 'decision'),key=lambda r:r['start'])
    clips = select_clips(WORK/source/'720p')
    crop = json.loads((WORK/'layouts'/f'{source}.json').read_text())['crop']
    target = OUT/f'{source}-{int(start)}-{int(end)}'
    target.mkdir(parents=True,exist_ok=True)
    cv2.setNumThreads(1)
    cap=None; current=None; output=[]
    for i,row in enumerate(rows[:-1]):
        nxt=rows[i+1]
        if not start<=row['start']<end or not row.get('room') or row['room']>24:continue
        if nxt['start']-row['end']>20:continue
        times=[row['end']-.1]+np.linspace(row['end']+.1,nxt['start']-.1,5).tolist()+[nxt['start']+.12]
        evidence=[]; thumbs=[]
        ident=f"{row['start']:.3f}-r{row['room']:02d}"
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
            dest=target/f'{ident}-{n}.png';cv2.imwrite(str(dest),native)
            evidence.append({'image':dest.relative_to(ROOT).as_posix(),'source_seconds':t,'decoded_source_seconds':actual,'sha256':sha256(dest),'video_sha256':c['sha256']})
            tile=np.zeros((340,350,3),np.uint8)
            tile[:250]=cv2.resize(native,(350,250))
            # Lower HUD enlarged enough to read effect icons, HP, and key count.
            tile[250:320]=cv2.resize(native[int(h*.83):],(350,70))
            cv2.putText(tile,f'{n}: {t:.3f}',(4,337),cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1,cv2.LINE_AA)
            thumbs.append(tile)
        if not thumbs:continue
        sheet=np.concatenate(thumbs,axis=1)
        cv2.imwrite(str(target/f'{ident}.jpg'),sheet,[cv2.IMWRITE_JPEG_QUALITY,97])
        output.append({'offer_id':row['id'],'source_id':source,'room':row['room'],'start':row['start'],'end':row['end'],'next_start':nxt['start'],'next_room':nxt['room'],'sheet':(target/f'{ident}.jpg').relative_to(ROOT).as_posix(),'evidence':evidence,'verification':'unreviewed'})
    if cap:cap.release()
    (target/'index.json').write_text(json.dumps(output,indent=2))
    print(target,len(output),flush=True)

if __name__=='__main__':
    prepare(sys.argv[1],float(sys.argv[2]),float(sys.argv[3]))
