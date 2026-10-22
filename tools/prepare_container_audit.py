"""Extract native evidence for a bounded container/selection audit (no labels)."""
import json,sys,hashlib
from pathlib import Path
import cv2,numpy as np
from sfld_vod.batch import select_clips

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'out/vods'
OUT=BASE/'container-audit-2026-10-08'
OUT.mkdir(exist_ok=True)
IDS='O27 O83 O93 O129 O153 O181 O226 O235 O245 O266 O284 O338 O366 R14 R47 R50 O389 O418 O424 R80 O430 O443 O500 O621 O627'.split()

def extract(source,cases,folder,cols=5,width=340):
    dest=OUT/folder;dest.mkdir(exist_ok=True)
    clips=[(p.parent/c['file'],c) for p,c in select_clips(BASE/source/'720p')]
    layout=json.loads((BASE/'layouts'/f'{source}.json').read_text())['crop']
    cap=None;current=None
    for case in cases:
        frames=[]
        for j,t in enumerate(case.pop('times')):
            path,clip=next((p,c) for p,c in clips if c['source_start']<=t<c['source_end'])
            if path!=current:
                if cap:cap.release()
                cap=cv2.VideoCapture(str(path));current=path
            cap.set(cv2.CAP_PROP_POS_MSEC,(t-clip['source_start'])*1000);ok,frame=cap.read();assert ok,(path,t)
            h,w=frame.shape[:2];x,y,cw,ch=[round(layout[k]*[w,h,w,h][k]) for k in range(4)]
            crop=frame[y:y+ch,x:x+cw]
            name=f"{case['key']}-{j:03}.jpg";target=dest/name
            cv2.imwrite(str(target),crop,[cv2.IMWRITE_JPEG_QUALITY,98])
            frames.append({'image':target.relative_to(BASE).as_posix(),'source_seconds':round(t,6),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        case['frames']=frames
        # One native-size companion per frame, compact contact sheets for chronology.
        rowheight=round(width*ch/cw)+24
        for page,start in enumerate(range(0,len(frames),cols*5)):
            group=frames[start:start+cols*5]
            canvas=np.zeros((int(np.ceil(len(group)/cols))*rowheight,cols*width,3),np.uint8)
            for k,f in enumerate(group):
                yy=(k//cols)*rowheight;xx=(k%cols)*width
                tile=cv2.resize(cv2.imread(str(BASE/f['image'])),(width,rowheight-24))
                canvas[yy+24:yy+rowheight,xx:xx+width]=tile
                cv2.putText(canvas,f"{case['key']} {start+k} {f['source_seconds']:.3f}",(xx+2,yy+17),0,.45,(255,255,255),1,cv2.LINE_AA)
            cv2.imwrite(str(dest/f"{case['key']}-sheet-{page}.jpg"),canvas,[cv2.IMWRITE_JPEG_QUALITY,97])
        print(case['key'],len(frames),flush=True)
    if cap:cap.release()
    (dest/'index.json').write_text(json.dumps(cases,indent=2),encoding='utf-8')

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='anomalies':
        cases=[{'key':str(i),'observation_id':f'2878232329:manual-scene:{i}','times':list(np.arange(t-.2,t+.55,1/30))} for i,t in [(812,8940.125),(981,10374.125)]]
        extract('2878232329',cases,'beside-monster',6)
    else:
        rows=[json.loads(x) for x in (BASE/'2881153716/ultra-review/observations.jsonl').read_text().splitlines()]
        decisions=sorted([r for r in rows if r['status']=='decision'],key=lambda r:r['source_seconds'])
        cases=[]
        for i,r in enumerate(decisions):
            key=r['id'].split(':')[-1]
            in_stage2=10494<=r['source_seconds']<=10586 and r['generation']=='ordinary'
            if mode=='greed-onsets' and in_stage2:
                times=[r['source_end']+x for x in [0,.125,.25,.5,.75]]
            elif (mode=='containers' and key in IDS) or (mode=='greed-transitions' and key in ['O315','O316','O318','O324','O326','O335']):
                end=min(next((n['source_seconds'] for n in decisions[i+1:] if n['source_seconds']>r['source_end']),r['source_end']+12),r['source_end']+12)
                times=list(np.arange(r['source_end'],end+.25,.125))
            else:continue
            cases.append({'key':key,'observation_id':r['id'],'context':r['context'],'doors':r['doors'],'times':times})
        extract('2881153716',cases,mode)
