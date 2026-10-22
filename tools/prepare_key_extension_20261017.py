"""Native frames for an outcome-blind, source-bounded key-drop extension.

Frames and ledger labels are locators, not verified combat observations.
"""
from pathlib import Path
import argparse
import json
import hashlib
import cv2
import numpy as np
from sfld_vod.batch import select_clips
from sfld_vod.vision import rect

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'out/vod-hunt-2026-10-12'
OUT = ROOT/'out/key-drop-extension-2026-10-17'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source):
    rows = [json.loads(line) for line in (ROOT/'data/vods/reviewed-offers-2026-10-12.jsonl').read_text().splitlines()]
    rows = sorted([r for r in rows if r['source_id']==source and r['status']=='decision'], key=lambda r:r['start'])
    folder = OUT/source
    folder.mkdir(parents=True,exist_ok=True)
    clips = select_clips(WORK/source/'720p')
    crop = json.loads((WORK/'layouts'/f'{source}.json').read_text())['crop']
    candidates, exclusions = [], []
    for row, nxt in zip(rows,rows[1:]):
        reason = None
        if not row['stone_set_complete']: reason='incomplete_stone_labels'
        elif set(row['gems']) & {'spying','lodestone'}: reason='key_modifying_stone_locator'
        elif any(d['kind'] in {'boss','trial','exit_trial'} for d in row['doors']): reason='boss_or_trial_offer'
        elif not .25 <= nxt['start']-row['end'] <= 40: reason='transition_too_short_or_long'
        item={'offer_id':row['id'],'source_id':source,'start':row['start'],'end':row['end'],
              'next_start':nxt['start'],'room_locator':row['room'],'stage_locator':row['stage'],
              'gems_locator':row['gems'],'doors_locator':row['doors']}
        if reason: exclusions.append({**item,'excluded_before_review':reason})
        else: candidates.append(item)
    # Freeze candidate eligibility before reading key-counter outcomes.
    (folder/'scope.json').write_text(json.dumps({'source':source,'method':'All eligible transitions in this source, fixed before key-outcome review.',
        'candidates':candidates,'pre_review_exclusions':exclusions},indent=2)+'\n',encoding='utf-8')
    cap=None; current=None
    cv2.setNumThreads(1)
    for number,row in enumerate(candidates):
        times=[row['end']-.1]+np.linspace(row['end']+.1,row['next_start']-.1,6).tolist()+[row['next_start']+.12]
        evidence=[]
        canvas=np.zeros((3*360,3*400,3),np.uint8)
        for index,t in enumerate(times):
            match=next(((p,c) for p,c in clips if c['source_start']<=t<c['source_end']),None)
            if match is None: continue
            p,c=match
            if current!=p:
                if cap:cap.release()
                cap=cv2.VideoCapture(str(p.parent/c['file']));current=p
            cap.set(cv2.CAP_PROP_POS_MSEC,(t-c['source_start'])*1000)
            ok,raw=cap.read()
            if not ok:continue
            actual=c['source_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            assert abs(actual-t)<.11,(t,actual)
            x,y,w,h=rect(crop,raw.shape[1],raw.shape[0]);native=raw[y:y+h,x:x+w]
            path=folder/f'{number:03}-{index}.png';cv2.imwrite(str(path),native)
            evidence.append({'index':index,'image':path.relative_to(ROOT).as_posix(),
                'source_seconds':t,'decoded_source_seconds':actual,'sha256':sha(path),'video_sha256':c['sha256']})
            tile=np.zeros((360,400,3),np.uint8)
            tile[:280]=cv2.resize(native,(400,280))
            # Keep resource digits readable without hiding the uncropped evidence.
            tile[280:340,:260]=cv2.resize(native[int(h*.83):,int(w*.28):int(w*.7)],(260,60))
            tile[280:340,260:]=cv2.resize(native[int(h*.83):,int(w*.82):],(140,60))
            cv2.putText(tile,f'{number:03}/{index} {t:.3f}',(6,356),cv2.FONT_HERSHEY_SIMPLEX,.47,(255,255,255),1,cv2.LINE_AA)
            yy=index//3*360;xx=index%3*400;canvas[yy:yy+360,xx:xx+400]=tile
        cv2.putText(canvas,f"{number:03} start {row['start']:.3f} stage {row['stage_locator']}",
                    (810,780),cv2.FONT_HERSHEY_SIMPLEX,.47,(255,255,255),1,cv2.LINE_AA)
        sheet=folder/f'{number:03}.jpg';cv2.imwrite(str(sheet),canvas,[cv2.IMWRITE_JPEG_QUALITY,96])
        row.update(evidence=evidence,sheet=sheet.relative_to(ROOT).as_posix(),review='unreviewed')
    if cap:cap.release()
    (folder/'index.json').write_text(json.dumps(candidates,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'source':source,'candidates':len(candidates),'excluded_before_review':len(exclusions),'folder':str(folder)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');args=parser.parse_args()
    prepare(args.source)
