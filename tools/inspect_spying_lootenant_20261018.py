"""Native follow-ups and enlarged HUD crops for the fixed Spying audit."""
from pathlib import Path
import json,sys,cv2,numpy as np
from sfld_vod.batch import select_clips
from sfld_vod.vision import rect
from sfld_vod.media import sha256
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'out/spying-key-audit-2026-10-18/lootenant'
WORK=ROOT/'out/vod-hunt-2026-10-12'

def huds():
    ix=json.loads((OUT/'index.json').read_text())
    labels=json.loads((ROOT/'data/vods/spying-key-audit-2026-10-18/lootenant-manual-working.json').read_text())
    for label in labels:
        if label['reason']!='verify_kill_hud':continue
        r=ix[label['ordinal']];tiles=[]
        for n in [0,2,3,8]:
            im=cv2.imread(str(ROOT/r['evidence'][n]['image']));h,w=im.shape[:2]
            tile=im[int(h*.76):,:].copy()
            cv2.putText(tile,'ordinal %d frame %d t=%.3f'%(r['ordinal'],n,r['evidence'][n]['source_seconds']),(180,20),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
            tiles.append(tile)
        cv2.imwrite(str(OUT/('hud-%03d.jpg'%r['ordinal'])),np.concatenate(tiles,axis=0),[cv2.IMWRITE_JPEG_QUALITY,98])

def dense(ordinal,start,end,step):
    dest=OUT/'dense'/('%03d'%ordinal);dest.mkdir(parents=True,exist_ok=True)
    clips=select_clips(WORK/'2878439002/720p')
    crop=json.loads((WORK/'layouts/2878439002.json').read_text())['crop']
    cap=None;current=None;records=[];tiles=[]
    cv2.setNumThreads(1)
    for n,t in enumerate(np.arange(start,end+.00001,step)):
        p,c=next((p,c) for p,c in clips if c['source_start']<=t<c['source_end'])
        if p!=current:
            if cap:cap.release()
            cap=cv2.VideoCapture(str(p.parent/c['file']));current=p
        cap.set(cv2.CAP_PROP_POS_MSEC,(t-c['source_start'])*1000);ok,im=cap.read()
        if not ok:continue
        actual=c['source_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        x,y,w,h=rect(crop,im.shape[1],im.shape[0]);im=im[y:y+h,x:x+w]
        fn=dest/('%08.3f.png'%t);cv2.imwrite(str(fn),im)
        records.append({'image':fn.relative_to(ROOT).as_posix(),'source_seconds':float(t),'decoded_source_seconds':actual,'sha256':sha256(fn),'video':(p.parent/c['file']).relative_to(ROOT).as_posix(),'video_sha256':c['sha256']})
        tile=np.zeros((340,350,3),np.uint8);tile[:250]=cv2.resize(im,(350,250));tile[250:320]=cv2.resize(im[int(h*.83):],(350,70));cv2.putText(tile,str(round(float(t),3)),(4,337),cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1,cv2.LINE_AA);tiles.append(tile)
    if cap:cap.release()
    (dest/'index.json').write_text(json.dumps(records,indent=2)+'\n')
    for k in range(0,len(tiles),9):cv2.imwrite(str(dest/('strip-%03d.jpg'%k)),np.concatenate(tiles[k:k+9],axis=1),[cv2.IMWRITE_JPEG_QUALITY,98])
    print('Saved',len(records),str(dest))

if __name__=='__main__':
    if sys.argv[1]=='huds':huds()
    elif sys.argv[1]=='dense':dense(int(sys.argv[2]),float(sys.argv[3]),float(sys.argv[4]),float(sys.argv[5]))
