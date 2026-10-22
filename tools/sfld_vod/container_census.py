"""Frozen current-event crate/chest census and bounded native sequence extraction."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path

from .container_reanalysis import ROOT, DEFAULT_PLANS, read_rows, sha256
from .media import write_json

_captures = {}
_verified = set()


def select(output):
    source = ROOT/'data/vods/full-container-rewards-2026-10-08.jsonl'
    originals = {}
    sources = {}
    for plan in DEFAULT_PLANS:
        for src in json.loads(plan.read_text())['sources']:
            for row in read_rows(plan.parent/src['observations']):
                originals[row['id']] = row
            sources[src['source_id']] = plan.parent
    rows = [r for r in read_rows(source) if r['reward_review_status']=='unreviewed'
            and r['source_id'] != 'youtube-DCb80VikGM0'
            and r['initial_style'] in {'wooden_box','bronze','silver','chest_unknown'}]
    rows.sort(key=lambda r: hashlib.sha256(('sfld-container-census-2026-10-09-v1|'+r['observation_id']).encode()).hexdigest())
    clips = {}
    for sid, base in sources.items():
        clips[sid] = []
        for marker in (base/sid/'720p').glob('*.json'):
            clip = json.loads(marker.read_text())
            if clip.get('schema')==1 and 'file' in clip:
                clips[sid].append({**clip, 'path':str((marker.parent/clip['file']).resolve())})
        clips[sid].sort(key=lambda c: (-(c['source_end']-c['source_start']), c['source_start']))
    for index, row in enumerate(rows):
        ident=row['observation_id']; sid=row['source_id']; base=sources[sid]
        row.update(index=index, shard='abc'[index%3], _base=str(base),
                   canonical_evidence=originals[ident].get('evidence', []),
                   canonical_sha256=hashlib.sha256(json.dumps(originals[ident], sort_keys=True).encode()).hexdigest(),
                   layout=json.loads((base/'layouts'/f'{sid}.json').read_text())['crop'],
                   layout_sha256=sha256(base/'layouts'/f'{sid}.json'))
    manifest={'schema':1,'selection':'Census of every still-unreviewed current-event ordinary/chest-unknown appearance candidate. Selection uses no reward scores; all failed/false/skipped candidates retained.',
              'inventory_sha256':sha256(source),'fps':8,'maximum_seconds':30,
              'counts':{'total':len(rows),'by_source':dict(Counter(r['source_id'] for r in rows)),
                        'by_greed':dict(Counter(str('greed' in r['context']['gems']).lower() for r in rows))},
              'rows':rows,'clips':clips}
    output.mkdir(parents=True,exist_ok=True)
    path=output/'manifest.json'
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError('Frozen census differs; use a new directory')
    write_json(path,manifest)
    for shard in 'abc':
        write_json(output/f'shard-{shard}.json',{'manifest_sha256':sha256(path),'rows':[r for r in rows if r['shard']==shard]})
    public={k:v for k,v in manifest.items() if k not in ('rows','clips')}
    public['local_manifest_sha256']=sha256(path)
    public['rows']=[{k:r[k] for k in ('index','shard','observation_id','source_id','source_start','entry_window_start','entry_window_end','context','initial_style','appearance_basis','canonical_sha256','layout_sha256')} for r in rows]
    write_json(ROOT/'data/vods/container-census-manifest-2026-10-09.json',public)
    return manifest


def extract_one(job):
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    row=job['row']; output=Path(job['output']); dest=output/'records'/f"{row['index']:04}"
    dest.mkdir(parents=True,exist_ok=True); marker=dest/'index.json'
    if marker.exists():
        saved=json.loads(marker.read_text())
        if saved['manifest_sha256'] != job['identity']: raise ValueError('Extraction inputs changed')
        return {'index':row['index'],'frames':len(saved['frames']),'cached':True}
    start=max(row['source_start']-.125,0)
    end=min(row['entry_window_end']+.5,start+job['maximum_seconds'])
    clips=job['clips']; frames=[]; missing=[]; rejected=[]
    desired=start; current_clip=None; cap=None; raw=None; decoded=None
    while desired <= end+.00001:
        clip=next((c for c in clips if c['source_start'] <= desired < c['source_end']),None)
        if clip is None:
            missing.append(round(desired,6)); desired+=1/job['fps']; continue
        if current_clip != clip['path']:
            current_clip=clip['path']
            if current_clip not in _verified:
                if sha256(current_clip) != clip['sha256']: raise ValueError('Video changed')
                _verified.add(current_clip)
            if current_clip not in _captures:
                _captures[current_clip]=cv2.VideoCapture(current_clip,cv2.CAP_FFMPEG,[cv2.CAP_PROP_N_THREADS,1])
            cap=_captures[current_clip]
            cap.set(cv2.CAP_PROP_POS_MSEC,(desired-clip['source_start'])*1000)
            raw=None;decoded=None
        attempts=0
        while decoded is None or decoded < desired-.04:
            ok, candidate=cap.read(); attempts+=1
            if not ok:
                raw=None;break
            raw=candidate; decoded=clip['source_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000
            if attempts>2400: raise ValueError('Decoder time did not advance')
        if raw is None:
            missing.append(round(desired,6))
        elif abs(decoded-desired)>.10 or decoded < start-.10 or decoded>end+.10:
            rejected.append({'requested':round(desired,6),'decoded':round(decoded,6),'reason':'seek drift'})
        elif raw.shape[0]<720:
            rejected.append({'requested':round(desired,6),'decoded':round(decoded,6),'reason':'not native720'})
        else:
            h,w=raw.shape[:2];x,y,cw,ch=[round(v*d) for v,d in zip(row['layout'],[w,h,w,h])]
            frame=raw[y:y+ch,x:x+cw]
            path=dest/f'{len(frames):03}.jpg'
            cv2.imwrite(str(path),frame,[cv2.IMWRITE_JPEG_QUALITY,95])
            frames.append({'image':path.relative_to(ROOT/'out/vods').as_posix(),
                           'source_seconds':round(desired,6),'decoded_source_seconds':round(decoded,6),
                           'sha256':sha256(path),'video_sha256':clip['sha256'],'native_height':h,
                           'role':'native_container_census_sequence'})
        desired+=1/job['fps']
    result={'observation_id':row['observation_id'],'source_id':row['source_id'],'index':row['index'],
            'manifest_sha256':job['identity'],'source_start':start,'source_end':end,
            'expected_departure':row['entry_window_end'],'fps':job['fps'],
            'censored_by_duration':end < row['entry_window_end']+.5,'frames':frames,
            'missing_timestamps':missing,'rejected_timestamps':rejected}
    # Contact sheets preserve all sampled frames; individual JPEGs retain native HUD.
    for page,offset in enumerate(range(0,len(frames),36)):
        group=frames[offset:offset+36]; tw,th,cols=300,205,6
        canvas=np.zeros(((len(group)+cols-1)//cols*th,tw*cols,3),np.uint8)
        for k,ev in enumerate(group):
            yy,xx=k//cols*th,k%cols*tw
            frame=cv2.imread(str(ROOT/'out/vods'/ev['image']))
            view=cv2.resize(frame,(tw,th-24))
            canvas[yy+24:yy+th,xx:xx+tw]=view
            cv2.putText(canvas,f"{row['index']} f{offset+k} {ev['decoded_source_seconds']:.3f}",(xx+3,yy+17),0,.43,(255,255,255),1)
        cv2.imwrite(str(dest/f'sheet-{page}.jpg'),canvas,[cv2.IMWRITE_JPEG_QUALITY,95])
    # Original canonical offer beside native beginning and end for identity checks.
    proof=[]
    for ev in row['canonical_evidence']:
        if ev.get('image'):
            path=Path(row['_base'])/ev['image']
            if path.exists(): proof.append((path,'canonical offer')); break
    if frames:
        proof += [(ROOT/'out/vods'/frames[0]['image'],'native beginning'),(ROOT/'out/vods'/frames[-1]['image'],'native end')]
    if proof:
        canvas=np.zeros((360,480*len(proof),3),np.uint8)
        for k,(path,label) in enumerate(proof):
            frame=cv2.imread(str(path))
            if frame is not None:
                canvas[30:360,k*480:(k+1)*480]=cv2.resize(frame,(480,330))
                cv2.putText(canvas,label,(k*480+4,21),0,.6,(255,255,255),1)
        cv2.imwrite(str(dest/'identity.jpg'),canvas,[cv2.IMWRITE_JPEG_QUALITY,95])
    write_json(marker,result)
    return {'index':row['index'],'frames':len(frames),'missing':len(missing),'rejected':len(rejected)}


def identity_pages(work, manifest):
    """Show canonical offer at its timestamp beside native frame and stone HUD."""
    import cv2
    import numpy as np
    output=work/'identity-pages';output.mkdir(parents=True,exist_ok=True)
    grouped={s:[] for s in ('a','b','c','root')};indices=[]
    for row in manifest['rows']:
        record=work/'records'/f"{row['index']:04}"
        sequence=json.loads((record/'index.json').read_text())
        ev=next((e for e in row['canonical_evidence'] if e.get('image')),None)
        if ev is None or not sequence['frames']:continue
        original=Path(row['_base'])/ev['image']
        if sha256(original)!=ev['sha256']:raise ValueError('Canonical offer image changed')
        frame=min(sequence['frames'],key=lambda f:abs(f['decoded_source_seconds']-ev['source_seconds']))
        old=cv2.imread(str(original));native=cv2.imread(str(ROOT/'out/vods'/frame['image']))
        canvas=np.zeros((330,1200,3),np.uint8)
        label=f"{row['index']} {row['source_id']} @{ev['source_seconds']:.3f} vs {frame['decoded_source_seconds']:.3f}"
        cv2.putText(canvas,label,(3,19),0,.48,(255,255,255),1)
        canvas[28:328,:440]=cv2.resize(old,(440,300))
        canvas[28:328,440:880]=cv2.resize(native,(440,300))
        h,w=native.shape[:2]
        hud=native[round(h*.76):h,:round(w*.20)]
        canvas[35:235,890:1190]=cv2.resize(hud,(300,200),interpolation=cv2.INTER_NEAREST)
        for j,g in enumerate(row['context'].get('gems',[]) or ['(no stones)']):
            cv2.putText(canvas,g,(890,255+j*22),0,.52,(255,255,255),1)
        path=record/'identity-aligned.jpg';cv2.imwrite(str(path),canvas,[cv2.IMWRITE_JPEG_QUALITY,95])
        owner='root' if row['shard']=='a' and row['index']>=453 else row['shard']
        grouped[owner].append(canvas)
        indices.append({'index':row['index'],'canonical_sha256':ev['sha256'],
                        'native_sha256':frame['sha256'],'decoded_source_seconds':frame['decoded_source_seconds'],
                        'canonical_source_seconds':ev['source_seconds'],'image_sha256':sha256(path),'owner':owner})
    for owner,images in grouped.items():
        for page,offset in enumerate(range(0,len(images),6)):
            cv2.imwrite(str(output/f'{owner}-{page:02}.jpg'),np.concatenate(images[offset:offset+6]),[cv2.IMWRITE_JPEG_QUALITY,95])
    write_json(output/'index.json',{'manifest_sha256':sha256(work/'manifest.json'),'rows':indices})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,default=ROOT/'out/vods/container-census-2026-10-09')
    p.add_argument('--extract',action='store_true');p.add_argument('--workers',type=int,default=6)
    p.add_argument('--recover-capped',action='store_true',help='Append overlapping native tails for duration-censored sequences; preserve original extraction.')
    p.add_argument('--identity-pages',action='store_true')
    a=p.parse_args()
    if not 1<=a.workers<=12:p.error('Use1–12workers')
    manifest=select(a.work)
    print(json.dumps(manifest['counts']),flush=True)
    if a.identity_pages:identity_pages(a.work,manifest)
    if not (a.extract or a.recover_capped):return
    identity=sha256(a.work/'manifest.json'); results=[]
    rows=manifest['rows'];output=a.work;maximum_seconds=manifest['maximum_seconds']
    if a.recover_capped:
        recovered=[]
        for row in rows:
            path=a.work/'records'/f"{row['index']:04}"/'index.json'
            saved=json.loads(path.read_text())
            if saved['censored_by_duration']:
                tail=dict(row)
                tail['source_start']=saved['source_end']-2+.125
                recovered.append(tail)
        rows=recovered;output=a.work/'recovery';maximum_seconds=600
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures=[pool.submit(extract_one,{'row':row,'clips':manifest['clips'][row['source_id']],
                                         'fps':manifest['fps'],'maximum_seconds':maximum_seconds,
                                         'identity':identity,'output':str(output)}) for row in rows]
        for f in as_completed(futures):
            result=f.result();results.append(result)
            write_json(output/'progress.json',{'processed':len(results),'target':len(futures),'results':results})
            print(json.dumps(result),flush=True)


if __name__=='__main__':main()
