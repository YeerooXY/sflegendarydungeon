"""Room-counter and gem suggestions. These are evidence aids, not reviewed facts."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .media import sha256


GEM_NAMES=('rabbit','moonstone','spying','pendant','greasy','gambler','greed','hero',
           'time_traveler','thirsty','pearl','blood','explorer','misadventurer',
           'devil','deceit','lodestone','bull','hick','rusty')


class GemReader:
    """Compare each of three HUD slots with all local client gem sprites.

    The same slot may be occluded, darkened, empty, or unreadable. A low-scoring
    slot is unknown, never automatically an empty slot or an unmodified run.
    """
    def __init__(self, art):
        import cv2
        self.cv=cv2; art=Path(art)
        self.manifest_hash=sha256(art/'manifest.json')
        manifest=json.loads((art/'manifest.json').read_text(encoding='utf-8'))['assets']
        self.templates=[]
        for name in GEM_NAMES:
            path=art/manifest['gem.'+name]
            raw=cv2.imread(str(path),cv2.IMREAD_UNCHANGED)
            x,y,w,h=cv2.boundingRect((raw[:,:,3]>200).astype('uint8'))
            raw=raw[y:y+h,x:x+w,:3]
            for height in (27,28,29,30,31,32,34):
                width=round(raw.shape[1]*height/raw.shape[0])
                self.templates.append((name,cv2.resize(raw,(width,height),interpolation=cv2.INTER_AREA)))
        self.boxes=((0,326,56,64),(28,344,66,64),(40,382,70,65))

    def read(self, frame):
        cv=self.cv
        game=cv.resize(frame,(640,448),interpolation=cv.INTER_AREA)
        slots=[]
        for index,(x,y,w,h) in enumerate(self.boxes):
            area=game[y:y+h,x:x+w];scores={};positions={}
            for name,template in self.templates:
                th,tw=template.shape[:2]
                if th>area.shape[0] or tw>area.shape[1]:continue
                _,score,_,position=cv.minMaxLoc(cv.matchTemplate(area,template,cv.TM_CCOEFF_NORMED))
                if score>scores.get(name,-1):
                    scores[name]=float(score);positions[name]=(x+position[0],y+position[1],tw,th)
            ordered=sorted(scores.items(),key=lambda pair:pair[1],reverse=True)
            name,score=ordered[0];margin=score-ordered[1][1]
            slots.append({'slot':index+1,'gem':name if score>=.72 and margin>=.08 else None,
                          'score':round(score,4),'margin':round(margin,4),
                          'alternatives':[[k,round(v,4)] for k,v in ordered[:4]],
                          'box':list(positions[name]),'status':'suggestion' if score>=.72 and margin>=.08 else 'unknown'})
        return {'slots':slots,'gems_suggested':[s['gem'] for s in slots if s['gem']],
                'complete':False,'status':'unreviewed',
                'note':'Unreadable and empty-looking slots remain unknown. Review all slots before claiming a complete stone set.'}


def parse_counter(text):
    """Require the /100 denominator; do not infer a missing leading digit."""
    compact=re.sub(r'\s+','',text).replace('／','/')
    match=re.fullmatch(r'(\d{1,3})/100',compact)
    if match and 0<=int(match.group(1))<=100:
        return int(match.group(1))
    return None


class CounterReader:
    def __init__(self):
        from rapidocr import RapidOCR
        self.engine=RapidOCR(params={'Global.use_det':False,'Global.use_cls':False,'Global.log_level':'error',
                                    'EngineConfig.onnxruntime.intra_op_num_threads':1,
                                    'EngineConfig.onnxruntime.inter_op_num_threads':1})

    def read(self,frame):
        import cv2
        game=cv2.resize(frame,(640,448),interpolation=cv2.INTER_AREA)
        crop=game[422:448,:80]
        variants=[cv2.resize(crop,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC),
                  cv2.resize(crop[3:-2,:72],None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)]
        readings=[]
        for variant in variants:
            result=self.engine(variant,use_det=False,use_cls=False,use_rec=True)
            for text,score in zip(result.txts or [],result.scores or []):
                readings.append({'text':text,'score':round(float(score),4),'displayed_room':parse_counter(text)})
        accepted=[r for r in readings if r['displayed_room'] is not None and r['score']>=.80]
        values={r['displayed_room'] for r in accepted}
        value=next(iter(values)) if len(values)==1 and len(accepted)>=2 else None
        return {'displayed_room':value,'status':'suggestion' if value is not None else 'unknown',
                'readings':readings,'note':'Two preprocessing variants must agree. OCR confidence is not accuracy; verify against adjacent frames and visible counter.'}


def enrich_scenes(root, source_id, scene_files, output, art=None):
    """Read native 720p best/last frames; never mark suggestions as verified.

    Stream-copy offsets can differ by rendition. The exact extracted image is
    retained so a reviewer can check that it corresponds to the intended view.
    Low-resolution upscaling is deliberately not a fallback for counter OCR.
    """
    import cv2
    from .batch import select_clips
    from .vision import rect
    from .media import write_json
    root=Path(root).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    from .room_sequence import attach_shared_sequence
    (output/'frames').mkdir(exist_ok=True)
    layout=json.loads((root/'layouts'/(source_id+'.json')).read_text(encoding='utf-8'))
    clips=select_clips(root/source_id/'720p')
    counter=CounterReader();gems=GemReader(art) if art else None
    cap=None;current=None;rows=[];missing=[]
    try:
        for marker in map(Path,scene_files):
            data=json.loads(marker.read_text(encoding='utf-8'))
            for i,epoch in enumerate(data['epochs']):
                ident=epoch.get('id') or f"{source_id}:{epoch['start']:.3f}:scene"
                samples=[]
                for role,stamp in [('best',epoch['best_time']),('last',epoch['end'])]:
                    match=next(((p,c) for p,c in clips if c['source_start']<=stamp<c['source_end']),None)
                    if not match:
                        missing.append(stamp);continue
                    path,clip=match
                    if current!=path:
                        if cap is not None:cap.release()
                        cap=cv2.VideoCapture(str(path.parent/clip['file']));current=path
                    cap.set(cv2.CAP_PROP_POS_MSEC,(stamp-clip['source_start'])*1000)
                    ok,raw=cap.read()
                    if not ok:
                        missing.append(stamp);continue
                    x,y,w,h=rect(layout['crop'],raw.shape[1],raw.shape[0])
                    frame=cv2.resize(raw[y:y+h,x:x+w],(640,448),interpolation=cv2.INTER_AREA)
                    name=f'frames/{stamp:012.3f}-{role}.jpg';image=output/name
                    cv2.imwrite(str(image),frame,[cv2.IMWRITE_JPEG_QUALITY,97])
                    samples.append({'role':role,'source_seconds':stamp,'image':name,'image_sha256':sha256(image),
                                    'counter':counter.read(frame),'gems':gems.read(frame) if gems else None,
                                    'video_sha256':clip['sha256']})
                values=[s['counter']['displayed_room'] for s in samples]
                row={'candidate_id':ident,'scene_file':str(marker),'scene_index':i,
                     'source_id':source_id,'start':epoch['start'],'end':epoch['end'],'samples':samples,
                     'counter_agreement':len(values)==2 and values[0] is not None and values[0]==values[1],
                     'possible_multiple_rooms':len(values)==2 and all(v is not None for v in values) and values[0]!=values[1],
                     'review_status':'unreviewed_suggestions'}
                rows.append(row)
            # Checkpoint after each clip; other reviewers can use completed data.
            shared_rows=[{'id':r['candidate_id'],'start':r['start'],'end':r['end'],
                          'best_time':r['samples'][0]['source_seconds'] if r['samples'] else r['start']} for r in rows]
            attach_shared_sequence(shared_rows,root,source_id)
            for row,shared in zip(rows,shared_rows):
                if 'counter_sequence' in shared:row['counter_sequence']=shared['counter_sequence']
            write_json(output/'suggestions.json',{'schema':1,'source_id':source_id,'rows':rows,'missing_native_frames':missing,
                                                'note':'OCR/template suggestions only. Verify against the saved native frame; rendition timestamps can differ.'})
            print(f'{source_id} HUD {marker.parent.name}: {len(rows)} total candidates',flush=True)
    finally:
        if cap is not None:cap.release()
    return rows
