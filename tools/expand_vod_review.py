"""Prepare expansion previews and scan only explicitly reviewed native layouts."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from sfld_vod.batch import select_clips
from sfld_vod.media import load_sources, sha256, write_json

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'out/vod-hunt-2026-10-12'

def sources():
    return load_sources(ROOT/'examples/vod-expansion-2026-10-12.json')+load_sources(ROOT/'examples/vod-additional-2026-10-12.json')

def previews():
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    dest=WORK/'expansion-native-previews';dest.mkdir(exist_ok=True)
    rows=[]
    for s in sources():
        clips=select_clips(WORK/s['id']/'720p')
        midpoint=sum(s['windows'][0])/2
        for t in [midpoint-180,midpoint,midpoint+180]:
            matches=[(m,c) for m,c in clips if c['source_start']<=t<c['source_end']]
            if not matches: continue
            m,c=matches[0];image=dest/f'{s["id"]}-{t:.1f}.jpg'
            if not image.exists():
                cap=cv2.VideoCapture(str(m.parent/c['file']));cap.set(cv2.CAP_PROP_POS_MSEC,(t-c['source_start'])*1000);ok,frame=cap.read();cap.release()
                if not ok: raise ValueError('Native preview decode failed')
                cv2.imwrite(str(image),frame,[cv2.IMWRITE_JPEG_QUALITY,96])
            rows.append({'source_id':s['id'],'channel':s.get('channel'),'source_seconds':t,'image':str(image.relative_to(WORK)),
                         'image_sha256':sha256(image),'video_sha256':c['sha256']})
    write_json(dest/'index.json',rows)
    for offset in range(0,len(rows),6):
        sheet=np.zeros((900,960,3),np.uint8)
        for i,r in enumerate(rows[offset:offset+6]):
            x=i%2*480;y=i//2*300
            sheet[y:y+270,x:x+480]=cv2.resize(cv2.imread(str(WORK/r['image'])),(480,270))
            cv2.putText(sheet,f'{r["source_id"]} | {r["source_seconds"]:.1f}',(x+3,y+289),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1,cv2.LINE_AA)
        cv2.imwrite(str(dest/f'sheet-{offset:03}.jpg'),sheet,[cv2.IMWRITE_JPEG_QUALITY,95])
    print(f'{len(rows)} native previews / {len({r["source_id"] for r in rows})} VODs',flush=True)

def scan():
    from sfld_vod.scenes import scan_scenes
    plans=json.loads((WORK/'expansion-layout-review.json').read_text())['views']
    tasks=[]
    for view in plans:
        if view['status']!='visually_reviewed_crop': continue
        sid=view['source_id'];name=view.get('view_id',sid)
        layout=WORK/'layouts'/f'{name}.json'
        if not layout.exists():
            write_json(layout,{'schema':1,'source_id':name,'crop':view['crop'],'note':'Scene candidates only; no HUD or door predictions promoted.'})
        config=WORK/'2885348704/scene-config.json'
        for marker,clip in select_clips(WORK/sid/'720p'):
            output=WORK/sid/'expansion-scenes'/name/marker.stem
            tasks.append((sid,name,marker.parent/clip['file'],layout,config,output,clip))
    results=[];failures=[]
    def one(t):
        sid,name,video,layout,config,out,clip=t
        r=scan_scenes(video,layout,config,out,clip['source_start'],f'https://www.twitch.tv/videos/{sid}',8,2)
        return {'source_id':sid,'view_id':name,'source_start':clip['source_start'],'source_end':clip['source_end'],
                'scene_file':str((out/'scenes.json').relative_to(WORK)),'candidates':len(r['epochs']),
                'status':'unreviewed_candidates_only'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        fs={pool.submit(one,t):t for t in tasks}
        for f in as_completed(fs):
            try:
                r=f.result();results.append(r);print(f'{r["view_id"]}: {r["source_start"]:.0f} {r["candidates"]} candidates',flush=True)
            except Exception as e:
                t=fs[f];failures.append({'source_id':t[0],'view_id':t[1],'error_type':type(e).__name__})
            write_json(WORK/'expansion-scenes-summary.json',{'clips':results,'failures':failures})
    if failures: raise SystemExit(1)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['previews','scan']);args=p.parse_args()
    previews() if args.action=='previews' else scan()
