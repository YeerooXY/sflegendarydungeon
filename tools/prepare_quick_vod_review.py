"""Read door types and trap hypotheses from the exact images shown for voting."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import hashlib
import json
from pathlib import Path
import time

from sfld_vod.media import sha256,write_json
from sfld_vod.spatial_doors import SpatialDoorRecognizer
from sfld_vod.trap_detector import TrapDetector,consensus
from sfld_vod.full_traps import can_confirm_with_second

ROOT=Path(__file__).resolve().parents[1]
VERSION='1.2.0'
DIRECTORY='quick-predictions-v'+VERSION
_doors={};_traps=None


def temporal_neighbor(row, selected, evidence, maximum_gap=.6):
    """Only a distinct nearby decode inside this candidate can corroborate it.

    Matching counters are not sufficient: a retry or an interior can retain
    the same number. Never borrow evidence from another candidate/room.
    """
    def when(proof):
        return proof.get('decoded_source_seconds', proof['source_seconds'])
    current=when(selected)
    if not row['start']-.02<=current<=row['end']+.02:
        return None
    candidates=[p for p in evidence if p['sha256']!=selected['sha256']
                and selected.get('video_sha256') and p.get('video_sha256')==selected['video_sha256']
                and row['start']-.02<=when(p)<=row['end']+.02
                and 1/120<=abs(when(p)-current)<=maximum_gap]
    return min(candidates,key=lambda p:abs(when(p)-current)) if candidates else None


def temporal_guard(primary, neighbor):
    """Labels still describe the displayed image; a neighbor cannot fill a blank."""
    result=[]
    for side,item in enumerate(primary):
        value=dict(item)
        other=neighbor[side] if neighbor else None
        label=item.get('kind');other_label=other.get('kind') if other else None
        value['temporal_status']='single_frame_only'
        if label is None:
            value['temporal_status']='primary_image_unknown'
        elif other_label is not None and other_label!=label:
            value.update(kind=None,status='unknown',temporal_status='conflicting_neighbor',
                         conflicting_labels=[label,other_label])
        elif other_label==label:
            a,b=item.get('bbox'),other.get('bbox')
            aligned=a is not None and b is not None and all(abs(x-y)<=6 for x,y in zip(a,b))
            value['temporal_status']='two_frames_agree' if aligned else 'same_label_position_changed'
        elif other:
            value['temporal_status']='neighbor_unknown'
        result.append(value)
    return result


def compact_door(item):
    # Keep the winning six checks and alternatives; full candidate diagnostics
    # belong in benchmarks, not in a multi-thousand-image browser payload.
    return {k:v for k,v in item.items() if k!='details'}


def proposed_labels(doors, traps, usable):
    """An abstaining detector must not become its highest-scoring guess."""
    return [{'kind':door['kind'],
             'trap':True if trap['present'] is True else False if usable else None,
             'trap_visual':trap['visual'], 'door_status':door['status'],
             'trap_status':'positive_suggestion' if trap['present'] else
                           'negative_hypothesis_requires_review' if usable else 'unreadable'}
            for door,trap in zip(doors,traps)]


def predict_batch(job):
    import cv2
    global _traps
    cv2.setNumThreads(1)
    root=Path(job['root']);art=ROOT/'out/vod-art'
    if _traps is None:_traps=TrapDetector(art)
    output=[]
    for row in job['rows']:
        layout=root/'layouts'/f'{row["source_id"]}.json'
        token=hashlib.sha256(row['id'].encode()).hexdigest()[:24]
        path=root/DIRECTORY/(token+'.json')
        evidence=sorted(row['evidence'],key=lambda p:abs(p['source_seconds']-row['best_time']))
        if not evidence:raise ValueError('Candidate has no image evidence: '+row['id'])
        selected=evidence[0]
        neighbor=temporal_neighbor(row,selected,evidence)
        identity={'version':VERSION,'primary_image_sha256':selected['sha256'],
                  'layout_sha256':sha256(layout),'art_sha256':job['art_sha256'],
                  'code_sha256':job['code_sha256'],'candidate_start':row['start'],'candidate_end':row['end'],
                  'door_neighbor':neighbor}
        if path.exists():
            old=json.loads(path.read_text())
            if old['inputs']!=identity:raise ValueError('Quick-review inputs changed: '+row['id'])
            output.append(old);continue
        began=time.perf_counter()
        # All frames are already native game crops; no video decode is needed.
        def read(proof):
            image=root/proof['image']
            if sha256(image)!=proof['sha256']:raise ValueError('Changed evidence: '+str(image))
            frame=cv2.imread(str(image))
            if frame is None:raise ValueError('Unreadable evidence: '+str(image))
            return frame
        native=read(selected)
        previous=job.get('previous',{}).get(row['id'])
        reuse=previous and all(previous['inputs'].get(k)==identity[k] for k in
                               ('primary_image_sha256','layout_sha256','art_sha256','candidate_start','candidate_end'))
        reuse=reuse and previous['inputs']['code_sha256'].get('sfld_vod/trap_detector.py')==identity['code_sha256']['sfld_vod/trap_detector.py']
        if str(layout) not in _doors:_doors[str(layout)]=SpatialDoorRecognizer(layout,art)
        door_predictions=[compact_door(d) for d in _doors[str(layout)].doors(native)]
        neighbor_predictions=[compact_door(d) for d in _doors[str(layout)].doors(read(neighbor))] if neighbor else None
        guarded_doors=temporal_guard(door_predictions,neighbor_predictions)
        if reuse:
            trap_predictions=previous['raw_traps'];proofs=previous['evidence']
            usable=any(d['trap_status']!='unreadable' for d in previous['doors'])
        else:
            first=_traps.read(native);frames=[first];proofs=[selected];usable=first.get('usable')
            if can_confirm_with_second(first,_traps.config['positive_threshold']):
                second=next((p for p in evidence[1:] if p['sha256']!=selected['sha256']
                             and row['start']<=p.get('decoded_source_seconds',p['source_seconds'])<=row['end']+.02),None)
                if second is not None:frames.append(_traps.read(read(second)));proofs.append(second)
            trap_predictions=consensus(frames,_traps.config)
        for proof in proofs:
            if sha256(root/proof['image'])!=proof['sha256']:
                raise ValueError('Changed reused trap evidence: '+proof['image'])
        proofs=list(proofs)
        if neighbor and all(p['sha256']!=neighbor['sha256'] for p in proofs):proofs.append(neighbor)
        proposed=proposed_labels(guarded_doors,trap_predictions,usable)
        result={'schema':1,'id':row['id'],'source_id':row['source_id'],'inputs':identity,
                'image':selected,'evidence':proofs,'doors':proposed,
                'raw_doors':door_predictions,'raw_traps':trap_predictions,
                'door_temporal':guarded_doors,'neighbor_doors':neighbor_predictions,
                'unchanged_door_scores_reused':False,'unchanged_trap_scores_reused':bool(reuse),
                'status':'unreviewed_hypotheses','processing_seconds':time.perf_counter()-began,
                'interpretation':'Labels describe the displayed image. Correct accepts both door labels and all explicit trap labels; unknown stays unknown. No automatic observation is added.'}
        write_json(path,result);output.append(result)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,default=ROOT/'out/vod-older-2026-10-12')
    p.add_argument('--catalog',default='monster-mystery-candidates.json')
    p.add_argument('--workers',type=int,default=12);p.add_argument('--limit',type=int)
    args=p.parse_args();root=args.directory.resolve()
    if not 1<=args.workers<=16:raise ValueError('Use 1–16 workers.')
    data=json.loads((root/args.catalog).read_text())
    rows=data['candidates'][:args.limit] if args.limit else data['candidates']
    code_hash={name:sha256(ROOT/'tools'/name) for name in ('prepare_quick_vod_review.py','sfld_vod/spatial_doors.py','sfld_vod/trap_detector.py','sfld_vod/full_traps.py')}
    common={'root':str(root),'art_sha256':sha256(ROOT/'out/vod-art/manifest.json'),'code_sha256':code_hash}
    bundle=root/'quick-predictions.json'
    previous=json.loads(bundle.read_text()) if bundle.exists() else {'predictions':{}}
    jobs=[{**common,'rows':rows[i:i+24],
           'previous':{r['id']:previous['predictions'][r['id']] for r in rows[i:i+24] if r['id'] in previous['predictions']}}
          for i in range(0,len(rows),24)]
    results=[];began=time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(predict_batch,j) for j in jobs]
        for future in as_completed(futures):
            results.extend(future.result())
            print(json.dumps({'processed':len(results),'target':len(rows),'seconds':round(time.perf_counter()-began,1)}),flush=True)
    by_id={r['id']:r for r in results}
    # A human vote belongs to the exact image and labels originally displayed.
    # Keep those predictions so classifier improvements do not erase that work.
    history=previous.get('history',{})
    for ident,old in previous['predictions'].items():
        if ident not in by_id:by_id[ident]=old
        elif old!=by_id[ident] and old not in history.setdefault(ident,[]):history[ident].append(old)
    write_json(bundle,{'schema':1,'version':VERSION,'predictions':by_id,'history':history})
    print(json.dumps({'complete':len(results),'trap_positive_images':sum(any(d['trap'] is True for d in r['doors']) for r in results)}))


if __name__=='__main__':main()
