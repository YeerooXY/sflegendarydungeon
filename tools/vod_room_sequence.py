"""Read every downloaded interval's room counter and expose coverage gaps.

Eight parallel workers are suitable for the current 16-core host. Counter
recognition is independent of the monster/mystery filter and samples 8 fps.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
from pathlib import Path
import time

from sfld_vod.batch import select_clips,subtract_intervals
from sfld_vod.media import ffmpeg_path,sha256,video_info,write_json
from sfld_vod.room_sequence import (VERSION,SEQUENCE_DIR,NativeCounterReader,
                                    fingerprint,same_counter,build_spans,sequence_blocks,associate_candidates)
from sfld_vod.vision import frames,rect

ROOT=Path(__file__).resolve().parents[1]
_reader=None


def scan_clip(job):
    import cv2
    global _reader
    if isinstance(job,dict):
        root,source_id,marker,fps=(job[k] for k in ('root','source_id','marker','fps'))
        view_id=job.get('view_id',source_id)
    else:
        root,source_id,marker,fps=job;job={};view_id=source_id
    root,marker=Path(root),Path(marker);cv2.setNumThreads(1)
    clip=json.loads(marker.read_text());video=marker.parent/clip['file']
    layout_path=root/'layouts'/f'{view_id}.json'
    layout=json.loads(layout_path.read_text())
    start=job.get('start',clip['source_start']);end=job.get('end',clip['source_end'])
    name=marker.stem
    if view_id!=source_id:name=view_id+'/'+name
    if start!=clip['source_start'] or end!=clip['source_end'] or marker.parent.name!='720p':
        name+='-'+marker.parent.name+f'-{start:.3f}-{end:.3f}'
    counter_box=job.get('counter_box',[0,.935,.15,.065])
    counter_location=job.get('counter_location','bottom')
    if counter_location!='bottom':name+='-counter-'+counter_location
    folder=root/source_id/SEQUENCE_DIR/name;folder.mkdir(parents=True,exist_ok=True)
    inputs={'version':VERSION,'video_sha256':clip['sha256'],'layout_sha256':sha256(layout_path),'fps':fps}
    if view_id!=source_id or start!=clip['source_start'] or end!=clip['source_end'] or marker.parent.name!='720p':
        inputs.update(view_id=view_id,start=start,end=end)
    if counter_location!='bottom':inputs.update(counter_box=counter_box,counter_location=counter_location)
    target=folder/'scan.json'
    if target.exists():
        previous=json.loads(target.read_text())
        if previous['inputs']!=inputs:raise ValueError('Room scan inputs changed; preserve earlier version.')
        # Earlier completed prototype sections used a stricter whole-band
        # cache and therefore ran more OCR calls. Their actual readings remain
        # valid and their original evidence is kept, with the method named.
        previous.setdefault('ocr_cache_method','whole_band_shape_and_luminance_v1')
        return previous
    if sha256(video)!=clip['sha256']:raise ValueError('Changed source video: '+str(video))
    if _reader is None:_reader=NativeCounterReader()
    x,y,w,h=layout['crop']
    bx,by,bw,bh=counter_box;cx=x+w*bx;cy=y+h*by
    crop=[cx,cy,min(w*bw,1-cx),min(h*bh,1-cy)]
    info=video_info(video);_,_,cw,ch=rect(crop,info['width'],info['height'])
    scale=max(1,32/cw,32/ch)
    shape=(max(32,round(cw*scale)),max(32,round(ch*scale)))
    samples=[];reads=[];reference=None;last_read=-100.;same_reads=0;reading=None
    began=time.perf_counter()
    duration=end-start
    for stamp,image in frames(video,crop,fps,ffmpeg_path(root),folder/'decode.log',start=start-clip['source_start'],duration=duration,size=shape):
        absolute=clip['source_start']+stamp
        if absolute>=end:break
        key=fingerprint(image)
        changed=reference is None or not same_counter(reference,key)
        if changed:same_reads=0
        fresh=(changed or (same_reads<2 and absolute-last_read>=.25) or absolute-last_read>=5)
        if fresh:
            reading=_reader.read(image)
            path=folder/f'counter-{len(reads):05d}.png'
            cv2.imwrite(str(path),image)
            proof={'image':path.relative_to(root).as_posix(),'sha256':sha256(path),
                   'source_seconds':absolute,'video_sha256':clip['sha256'],
                   'counter':reading,'crop_in_source':crop,'native_height':info['height'],
                   'native_width':info['width'],'counter_crop_scaling':'aspect_preserved'}
            reads.append(proof);reference=key;last_read=absolute;same_reads+=1
        samples.append({'time':absolute,'room':reading['room'],'fresh':fresh,
                        'proof':proof if fresh else None})
    result={'schema':1,'source_id':source_id,'view_id':view_id,'counter_location':counter_location,'inputs':inputs,'source_start':start,
            'source_end':end,'samples':samples,'reads':reads,
            'sample_count':len(samples),'ocr_calls':len(reads),'ocr_cache_method':'tight_glyph_shape_v2',
            'processing_seconds':time.perf_counter()-began}
    write_json(target,result)
    return result


def inventory_jobs(root,sources=None,fps=8):
    root=Path(root).resolve()
    jobs=[]
    for p in sorted(root.glob('*/source.json')):
        sid=p.parent.name
        if sources and sid not in sources:continue
        layouts=[root/'layouts'/f'{sid}.json']
        if not layouts[0].is_file():layouts=sorted((root/'layouts').glob(sid+'-*.json'))
        native=select_clips(p.parent/'720p')
        covered=[(c['source_start'],c['source_end']) for _,c in native]
        clips=[(m,c,c['source_start'],c['source_end']) for m,c in native]
        for marker,clip in select_clips(p.parent/'360p'):
            for start,end in subtract_intervals([(clip['source_start'],clip['source_end'])],covered):
                clips.append((marker,clip,start,end))
        for layout in layouts:
            for marker,clip,start,end in clips:
                jobs.append({'root':str(root),'source_id':sid,'view_id':layout.stem,
                             'marker':str(marker),'fps':fps,'start':start,'end':end})
    return jobs


def scan(root,sources=None,workers=8,fps=8):
    root=Path(root).resolve()
    if not 1<=workers<=16 or not 1<=fps<=16:raise ValueError('Expected 1–16 workers and fps.')
    jobs=inventory_jobs(root,sources,fps)
    if not jobs:raise ValueError('No source clips with layouts.')
    execute_jobs(jobs,workers)


def execute_jobs(jobs,workers=8):
    failures=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(scan_clip,j):j for j in jobs}
        for future in as_completed(pending):
            try:result=future.result()
            except Exception as error:
                failure={'job':pending[future],'error':str(error)};failures.append(failure)
                print(json.dumps(failure),flush=True);continue
            print(f"{result['source_id']} {result['source_start']:.0f}-{result['source_end']:.0f}: "
                  f"{result['ocr_calls']} counter reads / {result['sample_count']} frames, "
                  f"{result['processing_seconds']:.1f}s",flush=True)
    if failures:raise RuntimeError(f'{len(failures)} room-counter sections failed; completed sections are reusable.')


def build(root):
    root=Path(root).resolve();sources=[];all_spans=[];all_blocks=[];links={}
    visibility_path=ROOT/'data/vods/room-counter-2026-10-12/visibility-reviewed.json'
    visibility={r['view_id']:r for r in json.loads(visibility_path.read_text())['rows']} if visibility_path.exists() else {}
    from vod_monster_mystery import EVIDENCE_DIR
    for source in sorted(root.glob('*/source.json')):
        sid=source.parent.name
        scans=[]
        for p in sorted((source.parent/SEQUENCE_DIR).rglob('scan.json')):
            refined=p.with_name('refined.json')
            if refined.exists():
                result=json.loads(refined.read_text())
                if result['refinement_source_sha256']!=sha256(p):raise ValueError('Stale counter refinement: '+str(p))
            else:result=json.loads(p.read_text())
            scans.append(result)
        if not scans:continue
        # A reviewed relocated counter replaces the obstructed bottom reader;
        # its duplicate rendering is never treated as another playthrough.
        alternate=[s for s in scans if s.get('counter_location','bottom')!='bottom']
        if alternate:
            bottom=[s for s in scans if s.get('counter_location','bottom')=='bottom']
            missing=subtract_intervals([(s['source_start'],s['source_end']) for s in bottom],
                                       [(s['source_start'],s['source_end']) for s in alternate])
            if missing:raise ValueError(f'Alternate counter extraction incomplete for {sid}: {missing}')
            scans=alternate
        view_ids=sorted({s.get('view_id',sid) for s in scans})
        spans=[];blocks=[]
        for view_id in view_ids:
            selected=[s for s in scans if s.get('view_id',sid)==view_id]
            fps=selected[0]['inputs']['fps']
            samples=[r for s in selected for r in s['samples']]
            view_spans=build_spans(samples,view_id,fps)
            if visibility.get(view_id,{}).get('automatic_anchor_veto'):
                for span in view_spans:
                    if span['room'] is not None:
                        span['stable']=False;span['status']='obstructed_counter_needs_review'
            view_blocks=sequence_blocks(view_spans,view_id)
            for span in view_spans:span['video_source_id']=sid;span['view_id']=view_id
            for block in view_blocks:block['video_source_id']=sid;block['view_id']=view_id
            spans.extend(view_spans);blocks.extend(view_blocks)
        queue=source.parent/EVIDENCE_DIR/'queue.json'
        rows=json.loads(queue.read_text())['rows'] if queue.exists() else []
        assigned=associate_candidates(rows,spans)
        links.update(assigned)
        source_record={'source_id':sid,'scanned_seconds':sum(s['source_end']-s['source_start'] for s in scans),
                       'sampled_seconds':sum(s['sample_count']/s['inputs']['fps'] for s in scans),
                       'decode_shortfalls':[{'start':s['source_start'],'requested_end':s['source_end'],
                                             'last_sample_end':s['samples'][-1]['time']+1/s['inputs']['fps'] if s['samples'] else s['source_start']}
                                            for s in scans if s['sample_count']/s['inputs']['fps']<s['source_end']-s['source_start']-.001],
                       'sample_count':sum(s['sample_count'] for s in scans),'ocr_calls':sum(s['ocr_calls'] for s in scans),
                       'stable_counter_spans':sum(s['stable'] for s in spans),
                       'tentative_counter_spans':sum(s['room'] is not None and not s['stable'] for s in spans),
                       'unknown_counter_seconds':sum(s['end']-s['start']+1/fps for s in spans if s['room'] is None),
                       'sequence_blocks':len(blocks),'views':view_ids,
                       'counter_locations':sorted({s.get('counter_location','bottom') for s in scans}),'candidates':len(rows),
                       'visibility_status':{view:visibility.get(view,{}).get('status','not_audited') for view in view_ids},
                       'automatic_anchor_veto':any(visibility.get(view,{}).get('automatic_anchor_veto') for view in view_ids),
                       'anchored_candidates':sum(s['status']=='temporal_agreement' for s in assigned.values()),
                       'candidate_review_groups':len(set(s['group_id'] for s in assigned.values())),
                       'gap_rooms':sum(len(b['missing_between_anchors']) for b in blocks)}
        sources.append(source_record)
        all_spans.extend(spans);all_blocks.extend(blocks)
        print(json.dumps(source_record),flush=True)
    result={'schema':1,'version':VERSION,'sources':sources,'spans':all_spans,'blocks':all_blocks,'candidate_links':links,
            'rules':['Numbers require the visible /100 denominator, agreement of two preprocessing variants, and temporal corroboration.',
                     'The full footage counter pass is independent of the target-pair detector.',
                     'Sequence blocks are not verified run/character identities; decreases and long visibility gaps need review.',
                     'Missing numbers remain gaps. They are not filled using presumed progression.',
                     'Grouping never deletes candidates or labels their traps; repeated phases and boss retries can share a counter.',
                     'Seeing a room counter does not establish that its decision screen was visible.']}
    folder=root/SEQUENCE_DIR;folder.mkdir(exist_ok=True)
    write_json(folder/'catalog.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['scan','build'])
    parser.add_argument('--directory',type=Path,default=ROOT/'out/vod-older-2026-10-12')
    parser.add_argument('--source',action='append')
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--fps',type=int,default=8)
    args=parser.parse_args()
    if args.command=='scan':scan(args.directory,args.source,args.workers,args.fps)
    else:build(args.directory)


if __name__=='__main__':main()
