"""Find monster/mystery offer candidates in public POVs; never infer trap labels.

Decode every downloaded frame interval at a fixed sample rate. The inexpensive
red-eye gate and two sprite classes do not depend on whether a trap is visible.
Keep opening rooms and uncertain/duplicate views in the queue for human review.
Neither candidate counts nor OCR suggestions are probability observations.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time

from sfld_vod.media import ffmpeg_path, sha256, write_json

VERSION = "1.3.0"
SCAN_DIR = 'pair-scans-v'+VERSION
EVIDENCE_DIR = 'pair-evidence-v'+VERSION
ROOT = Path(__file__).resolve().parents[1]


def source_link(url, seconds):
    separator = '&' if '?' in url else '?'
    return url + separator + 't=' + str(max(0, int(seconds))) + 's'


class PairFinder:
    def __init__(self, layout, art):
        import cv2
        import numpy as np
        self.cv, self.np = cv2, np
        self.layout = layout
        assets = json.loads((art/'manifest.json').read_text())['assets']
        self.templates = [[], []]
        self.partial_templates = [[], []]
        for kind in ('monster', 'mystery'):
            original = cv2.imread(str(art/assets['door.'+kind]), cv2.IMREAD_UNCHANGED)
            right = assets.get('door_right.'+kind)
            images = [original, cv2.imread(str(art/right), cv2.IMREAD_UNCHANGED) if right else cv2.flip(original, 1)]
            for side, raw in enumerate(images):
                x,y,w,h = cv2.boundingRect((raw[:,:,3] > 180).astype('uint8'))
                raw = raw[y:y+h,x:x+w,:3]
                for height in layout.get('pair_template_heights', [126,132,138,144,150]):
                    width = round(raw.shape[1]*height/raw.shape[0])
                    sprite = cv2.resize(raw,(width,height),interpolation=cv2.INTER_AREA)
                    # These two door interiors are opaque. Unmasked matching is
                    # much cheaper than the general masked physical-door matcher.
                    self.templates[side].append((kind, sprite))
                    # Trap overlays can obscure the top, bottom or middle of
                    # an otherwise visible door. Match remaining regions too.
                    # Partial matches need a higher correlation than full ones.
                    if kind == 'monster':
                        patches=[(.12,.29,.88,.54),(.09,.29,.50,.52),(.49,.30,.94,.55)]
                    else:
                        patches=[(.08,.08,.94,.55),(.15,.36,.87,.85)]
                    for x0,y0,x1,y1 in patches:
                        px,py=round(width*x0),round(height*y0)
                        patch=sprite[py:round(height*y1),px:round(width*x1)]
                        self.partial_templates[side].append((kind,patch,px,py,width,height))

    def partial_score(self, area, record):
        """Search only positions at which the corresponding whole door fits.

        A full sprite of (w,h) has (area_w-w+1)*(area_h-h+1)
        placements. The patch retains those exact geometric placements instead
        of sliding a tiny eye across the entire doorway and its surroundings.
        """
        _,patch,px,py,width,height=record
        ah,aw=area.shape[:2];ph,pw=patch.shape[:2]
        if width>aw or height>ah:return -1.0
        search=area[py:py+ah-height+ph,px:px+aw-width+pw]
        return float(self.cv.minMaxLoc(self.cv.matchTemplate(search,patch,self.cv.TM_CCOEFF_NORMED))[1])

    def score(self, frame, threshold=.44):
        cv,np = self.cv,self.np
        areas = [frame[y:y+h,x:x+w] for x,y,w,h in self.layout['door_search']]
        # A generous gate proposes red monster eyes, including dim transitions.
        # Recall remains unvalidated; sampled negatives are exported separately.
        red = []
        glow = []
        for area in areas:
            b,g,r = (area[:,:,k].astype('int16') for k in range(3))
            red.append(int(((r > 65) & (r > b+30) & (r > g+20)).sum()))
            glow.append(float(((r > 70) & (r > 2*b+10) & (r > 2*g+10)).mean()))
        if max(red) < 15:
            return None
        scores = []
        for side,area in enumerate(areas):
            values = {'monster':-1.0,'mystery':-1.0}
            for kind,sprite in self.templates[side]:
                if kind != 'monster':
                    continue
                if sprite.shape[0] > area.shape[0] or sprite.shape[1] > area.shape[1]:
                    continue
                score = float(cv.minMaxLoc(cv.matchTemplate(area,sprite,cv.TM_CCOEFF_NORMED))[1])
                values[kind] = max(values[kind],score)
            scores.append(values)
        for side,area in enumerate(areas):
            if scores[side]['monster'] >= threshold:
                continue
            for record in self.partial_templates[side]:
                if record[0] != 'monster':continue
                value=self.partial_score(area,record)-.20
                scores[side]['monster']=max(scores[side]['monster'],value)
        fallback=[False,False]
        for side in range(2):
            # A blood rack can hide both eyes. Keep a red-glow + mystery
            # candidate rather than silently dropping that kind of trap.
            # This is deliberately permissive and remains explicitly unverified.
            if scores[side]['monster'] < threshold and glow[side] >= .045:
                scores[side]['monster']=threshold
                fallback[side]=True
        # A pair's score is the minimum of its two side scores. When the
        # monster cannot pass, scoring its partner cannot rescue that pair.
        # This is an exact acceptance-preserving shortcut, not another gate.
        for side,area in enumerate(areas):
            if scores[1-side]['monster'] < threshold:
                continue
            for kind,sprite in self.templates[side]:
                if kind != 'mystery' or sprite.shape[0] > area.shape[0] or sprite.shape[1] > area.shape[1]:
                    continue
                score = float(cv.minMaxLoc(cv.matchTemplate(area,sprite,cv.TM_CCOEFF_NORMED))[1])
                scores[side][kind] = max(scores[side][kind],score)
            if scores[side]['mystery'] < threshold:
                for record in self.partial_templates[side]:
                    if record[0] != 'mystery':continue
                    value=self.partial_score(area,record)-.10
                    scores[side]['mystery']=max(scores[side]['mystery'],value)
        forward = min(scores[0]['monster'],scores[1]['mystery'])
        reverse = min(scores[0]['mystery'],scores[1]['monster'])
        side = 0 if forward >= reverse else 1
        return {'score':max(forward,reverse),'monster_side':side,'sides':scores,
                'occlusion_fallback':fallback[side]}


def scan_clip(job):
    import cv2
    from sfld_vod.vision import frames
    root, source_id, marker, fps, threshold = job
    root, marker = Path(root), Path(marker)
    cv2.setNumThreads(1)
    clip = json.loads(marker.read_text())
    video = marker.parent/clip['file']
    if sha256(video) != clip['sha256']:
        raise ValueError(f'Video fingerprint changed: {video}')
    layout_path = root/'layouts'/f'{source_id}.json'
    layout = json.loads(layout_path.read_text())
    out = root/source_id/SCAN_DIR/marker.stem
    out.mkdir(parents=True,exist_ok=True)
    target = out/'scan.json'
    inputs = {'version':VERSION,'video_sha256':clip['sha256'],
              'layout_sha256':sha256(layout_path),'fps':fps,'threshold':threshold,
              'art_sha256':sha256(ROOT/'out/vod-art/manifest.json')}
    if target.exists():
        old = json.loads(target.read_text())
        if old['inputs'] != inputs:
            raise ValueError('Scan inputs changed; preserve the old evidence in a separate output directory.')
        return old
    finder = PairFinder(layout,ROOT/'out/vod-art')
    rows, controls = [], []
    active = None
    best_frame = None
    began = time.perf_counter()
    scanned, gate_matches, positive = 0,0,0
    # Deterministic time controls check the eye gate too. They are NOT a sampled
    # set of room choices and do not supply a room-frequency denominator.
    next_control = 15.0

    def finish():
        nonlocal active,best_frame
        if active is None:
            return
        name = f'candidate-{len(rows):04d}.jpg'
        cv2.imwrite(str(out/name),best_frame,[cv2.IMWRITE_JPEG_QUALITY,96])
        active['thumbnail'] = (out/name).relative_to(root).as_posix()
        active['thumbnail_sha256'] = sha256(out/name)
        active['id'] = source_id+':mu:'+hashlib.sha256((clip['sha256']+':'+str(active['start'])).encode()).hexdigest()[:16]
        rows.append(active)
        active,best_frame = None,None

    for stamp,frame in frames(video,layout['crop'],fps,ffmpeg_path(root),out/'decode.log'):
        scanned += 1
        absolute = clip['source_start']+stamp
        result = finder.score(frame,threshold)
        gate_matches += result is not None
        accepted = result is not None and result['score'] >= threshold
        if stamp >= next_control:
            name = f'control-{len(controls):03d}.jpg'
            cv2.imwrite(str(out/name),frame,[cv2.IMWRITE_JPEG_QUALITY,95])
            controls.append({'source_seconds':absolute,'image':(out/name).relative_to(root).as_posix(),
                             'sha256':sha256(out/name),'accepted':accepted,'scores':result})
            next_control += 30
        if not accepted:
            if active and absolute-active['end'] > 3/fps:
                finish()
            continue
        positive += 1
        if active and (result['monster_side'] != active['monster_side'] or absolute-active['end'] > 3/fps):
            finish()
        if active is None:
            active = {'source_id':source_id,'start':absolute,'end':absolute,'best_time':absolute,
                      'score':result['score'],'monster_side':result['monster_side'],
                      'occlusion_fallback':result.get('occlusion_fallback',False),
                      'scores':result['sides'],'positive_frames':[],
                      'clip_marker':marker.relative_to(root).as_posix(),
                      'video':video.relative_to(root).as_posix(),'video_sha256':clip['sha256'],
                      'clip_start':clip['source_start'],'clip_end':clip['source_end'],
                      'status':'unreviewed_candidate','room':None,'gems':None,
                      'traps':[None,None],'duplicate_of':None}
            best_frame = frame.copy()
        active['end'] = absolute
        active['positive_frames'].append(absolute)
        if result['score'] > active['score']:
            active.update(score=result['score'],scores=result['sides'],best_time=absolute,
                          occlusion_fallback=result.get('occlusion_fallback',False))
            best_frame = frame.copy()
    finish()
    result = {'schema':1,'source_id':source_id,'inputs':inputs,
              'source_start':clip['source_start'],'source_end':clip['source_end'],
              'scanned_frames':scanned,'gate_matches':gate_matches,'positive_frames':positive,
              'candidates':rows,'controls':controls,'processing_seconds':time.perf_counter()-began,
              'interpretation':'Unverified views, including openings, animation and repeated views. No trap or room-frequency estimate.'}
    write_json(target,result)
    return result


def scan(root, sources=None, workers=2, fps=8, threshold=.44):
    from sfld_vod.batch import select_clips
    root = Path(root).resolve()
    if not 1 <= workers <= 16 or not 1 <= fps <= 30 or not 0 < threshold < 1:
        raise ValueError('Expected 1-16 workers, 1-30 fps, and a probability threshold.')
    jobs=[]
    for source in sorted(root.glob('*/source.json')):
        ident=source.parent.name
        if sources and ident not in sources:
            continue
        if not (root/'layouts'/f'{ident}.json').exists():
            print(ident+': layout missing; not scanned',flush=True)
            continue
        for marker,_ in select_clips(source.parent/'720p'):
            jobs.append((str(root),ident,str(marker),fps,threshold))
    if not jobs:
        raise ValueError('No downloaded sections with calibrated layouts.')
    failures=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks={pool.submit(scan_clip,j):j for j in jobs}
        for task in as_completed(tasks):
            j=tasks[task]
            try:
                r=task.result()
                print(f'{j[1]} {r["source_start"]:.0f}-{r["source_end"]:.0f}: {len(r["candidates"])} candidates, {r["processing_seconds"]:.1f}s',flush=True)
            except Exception as e:
                failures.append({'source':j[1],'marker':j[2],'error':str(e)})
                print(str(failures[-1]),flush=True)
    write_json(root/'pair-scan-failures.json',failures)
    if failures:
        raise RuntimeError(f'{len(failures)} sections failed; completed scans are reusable.')


def enrich(root, sources=None):
    """Attach native evidence; OCR is advisory and never sets reviewed fields."""
    import cv2
    from sfld_vod.vision import rect
    from sfld_vod.hud import CounterReader,GemReader
    root=Path(root).resolve();cv2.setNumThreads(1)
    counter=CounterReader();gems=GemReader(ROOT/'out/vod-art')
    for source in sorted(root.glob('*/source.json')):
        ident=source.parent.name
        if sources and ident not in sources:continue
        scans=sorted((source.parent/SCAN_DIR).glob('*/scan.json'))
        if not scans:continue
        layout=json.loads((root/'layouts'/f'{ident}.json').read_text())
        out=source.parent/EVIDENCE_DIR;out.mkdir(exist_ok=True)
        target=out/'queue.json'
        saved=json.loads(target.read_text()) if target.exists() else {'schema':1,'source_id':ident,'rows':[]}
        done={r['id']:r for r in saved['rows']}
        for scan_path in scans:
            data=json.loads(scan_path.read_text())
            cap=None
            try:
                for row in data['candidates']:
                    if row['id'] in done:continue
                    if cap is None:cap=cv2.VideoCapture(str(root/row['video']))
                    stamps=sorted(set([max(row['clip_start'],row['start']-.375),
                                      row['start'],row['best_time'],row['end'],
                                      min(row['clip_end']-.1,row['end']+.375)]))
                    proof=[];best=None
                    for number,stamp in enumerate(stamps):
                        cap.set(cv2.CAP_PROP_POS_MSEC,(stamp-row['clip_start'])*1000)
                        ok,raw=cap.read()
                        if not ok:continue
                        decoded=row['clip_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000
                        x,y,w,h=rect(layout['crop'],raw.shape[1],raw.shape[0]);native=raw[y:y+h,x:x+w]
                        dest=out/(row['id'].split(':')[-1]+f'-{number}.jpg')
                        cv2.imwrite(str(dest),native,[cv2.IMWRITE_JPEG_QUALITY,97])
                        proof.append({'image':dest.relative_to(root).as_posix(),'sha256':sha256(dest),
                                      'source_seconds':stamp,'decoded_source_seconds':decoded,
                                      'video_sha256':row['video_sha256'],'native_height':int(raw.shape[0])})
                        if stamp==row['best_time']:best=native
                    addition={**row,'evidence':proof,'verified_fields':[],
                              'room_suggestion':counter.read(best) if best is not None else None,
                              'gem_suggestions':gems.read(best) if best is not None else None}
                    done[row['id']]=addition
            finally:
                if cap is not None:cap.release()
            saved['rows']=sorted(done.values(),key=lambda r:(r['start'],r['id']))
            write_json(target,saved)
            print(f'{ident}: {len(saved["rows"])} candidates have native evidence',flush=True)


def enrich_parallel(root, sources=None, workers=4):
    root=Path(root).resolve()
    if not 1<=workers<=8:raise ValueError('Use 1-8 evidence workers.')
    identifiers=[p.parent.name for p in sorted(root.glob('*/source.json'))
                 if (not sources or p.parent.name in sources) and list((p.parent/SCAN_DIR).glob('*/scan.json'))]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(enrich,root,[sid]):sid for sid in identifiers}
        for job in as_completed(jobs):
            job.result()


def native_color_support(root, row):
    """Prioritize reviews without dropping low-support evidence.

    Gray mystery glyphs can correlate with partial eye templates. The central
    doorway should also contain red pixels for a visible monster. All views
    are kept, including dark/occluded ones without this cue.
    """
    import cv2
    best=[0.,0.]
    for e in row.get('evidence',[]):
        if not row['start']-.05<=e['source_seconds']<=row['end']+.05:continue
        raw=cv2.imread(str(root/e['image']))
        if raw is None:raise ValueError('Missing native image '+e['image'])
        frame=cv2.resize(raw,(320,224)).astype('float32')
        for side,x in enumerate((42,186)):
            box=frame[45:155,x+18:x+84]
            b,g,r=(box[:,:,i] for i in range(3))
            best[side]=max(best[side],float(((r>45)&(r>1.6*g+5)&(r>1.6*b+5)).mean()))
    supported=best[row['monster_side']]>=.003
    return {'red_fraction_by_side':best,'monster_color_supported':supported,
            'status':'unreviewed_priority_cue','interpretation':'Absence of the cue is not proof of a non-monster; dark/obscured candidates remain in the queue.'}


def catalog(root):
    root=Path(root).resolve();sources=[];rows=[];controls=[]
    from sfld_vod.room_sequence import SEQUENCE_DIR,associate_candidates
    sequence_file=root/SEQUENCE_DIR/'catalog.json'
    sequence=json.loads(sequence_file.read_text()) if sequence_file.exists() else None
    for source_path in sorted(root.glob('*/source.json')):
        source=json.loads(source_path.read_text());ident=source['id']
        queue=source_path.parent/EVIDENCE_DIR/'queue.json'
        source_rows=json.loads(queue.read_text())['rows'] if queue.exists() else []
        scanned=0;scanrows=0
        for path in sorted((source_path.parent/SCAN_DIR).glob('*/scan.json')):
            d=json.loads(path.read_text());scanned+=d['source_end']-d['source_start'];scanrows+=len(d['candidates'])
            controls.extend({'source_id':ident,**r} for r in d['controls'])
        sources.append({k:source.get(k) for k in ('id','url','title','uploader','upload_date','duration')}|
                       {'scanned_seconds':scanned,'candidates':scanrows,'enriched_candidates':len(source_rows)})
        frame_links=associate_candidates([{'id':r['id'],'best_time':r['best_time']} for r in source_rows],
                                         [s for s in sequence['spans'] if s['source_id']==ident]) if sequence else {}
        for row in source_rows:
            row['source_url']=source['url'];row['uploader']=source['uploader'];row['upload_date']=source['upload_date']
            row['source_link']=source_link(source['url'],row['best_time'])
            row['mode_suggestion']='ultimate' if 'ultimate' in source['title'].lower() else 'unknown'
            row['nearby_candidate_ids']=[r['id'] for r in source_rows if r['id']!=row['id'] and abs(r['start']-row['start'])<6]
            row['color_support']=native_color_support(root,row)
            if sequence:
                row['room_sequence']=sequence['candidate_links'].get(row['id'])
                row['frame_room_sequence']=frame_links.get(row['id'])
            rows.append(row)
    data={'schema':1,'version':VERSION,'sources':sources,'candidates':rows,'controls':controls,
          'rules':{'eligible':'Ordinary room >5, not 25/50/75/100, not trial/shop/stone choice; both offered sides must be readable monster/mystery.',
                   'unknowns':'Unreadable trap/stone/room state stays unknown. Do not infer absent traps from glare, transition or a facecam.',
                   'dedup':'One room-choice visit per observation. Use room counter, health, keys, effects, stones and adjacent footage; identical-looking doors alone are insufficient.',
                   'era':'Keep event, source, Standard/Ultimate mode, run class and stones as strata. Playa describes identical gameplay with multiplied Ultimate rewards. Upload date is not the game-version date.'}}
    if sequence:
        data['room_sequence']={k:sequence[k] for k in ('version','sources','blocks','rules')}
        data['room_sequence']['spans']=[{k:s.get(k) for k in ('id','source_id','video_source_id','view_id','sequence_id','room','start','end','stable','status')}|
                                        {'proofs':s['proofs'][:2]} for s in sequence['spans'] if s['room'] is not None]
    write_json(root/'monster-mystery-candidates.json',data)
    html=(ROOT/'web/vod_monster_mystery_review.html').read_text(encoding='utf-8')
    (root/'monster-mystery-detail.html').write_text(html,encoding='utf-8')
    quick=root/'quick-predictions.json'
    if quick.exists():
        html=(ROOT/'web/vod_quick_review.html').read_text(encoding='utf-8')
    (root/'monster-mystery.html').write_text(html,encoding='utf-8')
    print(json.dumps({'sources':len(sources),'scanned_hours':sum(s['scanned_seconds'] for s in sources)/3600,
                      'native_candidates':len(rows),'monster_color_supported':sum(r['color_support']['monster_color_supported'] for r in rows),
                      'negative_control_frames':sum(not r['accepted'] for r in controls)},indent=2))
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['scan','enrich','catalog'])
    parser.add_argument('--directory',type=Path,default=ROOT/'out/vod-older-2026-10-12')
    parser.add_argument('--source',action='append')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--fps',type=float,default=8)
    parser.add_argument('--threshold',type=float,default=.44)
    args=parser.parse_args()
    if args.command=='scan':scan(args.directory,args.source,args.workers,args.fps,args.threshold)
    elif args.command=='enrich':enrich_parallel(args.directory,args.source,args.workers)
    else:catalog(args.directory)


if __name__=='__main__':main()
