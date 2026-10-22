"""Inventory retained clips independently of download-job success summaries."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from sfld_vod.batch import select_clips
from sfld_vod.media import sha256, write_json

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'out/vod-hunt-2026-10-12'

def main():
    import cv2
    cv2.setNumThreads(1)
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true');args=parser.parse_args()
    cache_path=WORK/'clip-validation-cache.json'
    cache=json.loads(cache_path.read_text()) if cache_path.exists() else {}
    def check(item):
        marker,clip=item; video=marker.parent/clip['file'];digest=clip['sha256']
        if not args.verify: return None
        if digest in cache and cache[digest]['bytes']==video.stat().st_size: return cache[digest]
        correct_hash=sha256(video)==digest;cap=cv2.VideoCapture(str(video))
        duration=cap.get(cv2.CAP_PROP_FRAME_COUNT)/max(1,cap.get(cv2.CAP_PROP_FPS));frames=[]
        for at in [.2,duration/2,max(.2,duration-.5)]:
            cap.set(cv2.CAP_PROP_POS_MSEC,at*1000);ok,frame=cap.read()
            frames.append({'clip_seconds':at,'decoded':bool(ok and frame is not None)})
        cap.release()
        return {'bytes':video.stat().st_size,'hash_matches':correct_hash,'sample_frames':frames,
                'passed':correct_hash and all(f['decoded'] for f in frames),
                'scope':'Full file hash plus start/middle/end frame decoding; not every frame decoded.'}
    sources=[];jobs=[]
    for meta in sorted(WORK.glob('*/source.json')):
        clips=select_clips(meta.parent/'720p')
        if not clips: continue
        data=json.loads(meta.read_text());sid=meta.parent.name;review=meta.parent/'review-preparation/manual-offers.json'
        source={'id':sid,'url':data['url'],'channel':data.get('uploader'),'date':data.get('upload_date'),
                'cohort':'F9_early_event' if data.get('upload_date')=='20260917' else 'September_2026_main_event',
                'cohort_basis':'Upload-date classification; F9 world identity is not established for every early-date recording.',
                'seconds_retained':sum(c['source_end']-c['source_start'] for _,c in clips),
                'clips':[],'manual_candidate_review':review.exists(),'manual_review_file':str(review.relative_to(ROOT)) if review.exists() else None,
                'scene_candidates_unverified':0}
        for m,c in clips:
            source['clips'].append({'file':str((m.parent/c['file']).relative_to(ROOT)),
                'marker':str(m.relative_to(ROOT)),'sha256':c['sha256'],'bytes':(m.parent/c['file']).stat().st_size,
                'source_start':c['source_start'],'source_end':c['source_end'],
                'video':c.get('video'),'validation':None})
            jobs.append((m,c,source['clips'][-1]))
        for scene in list((meta.parent/'scenes').glob('*/scenes.json'))+list((meta.parent/'expansion-scenes').glob('*/*/scenes.json')):
            source['scene_candidates_unverified']+=len(json.loads(scene.read_text())['epochs'])
        sources.append(source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=pool.map(check,[(m,c) for m,c,_ in jobs])
        for (_,c,record),result in zip(jobs,results):
            record['validation']=result
            if result: cache[c['sha256']]=result
    if args.verify: write_json(cache_path,cache)
    clips=[c for s in sources for c in s['clips']]
    result={'schema':1,'collected_on':'2026-10-12','latest_event_source':'https://home.sfgame.net/en/blog/recurring-events/legendary-dungeon-september/',
            'event_main_dates':['2026-09-18','2026-09-27'],'sources':sources,'total_vods':len(sources),
            'total_clips':len(clips),'total_hours':sum(s['seconds_retained'] for s in sources)/3600,
            'total_bytes':sum(c['bytes'] for c in clips),'cohort_counts':dict(Counter(s['cohort'] for s in sources)),
            'channels':sorted({s['channel'] for s in sources}),
            'validation_failures':[c['file'] for c in clips if c['validation'] and not c['validation']['passed']],
            'validated_clips':sum(c['validation'] is not None for c in clips),
            'note':'Retained window duration includes menus and other gameplay. Candidate views are not verified unique rooms. F9 stays separate. Chapter/title is not proof of first-run status.'}
    write_json(WORK/'acquisition-inventory.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['sources','note']},indent=2))
    if result['validation_failures']: raise SystemExit(1)

if __name__=='__main__': main()
