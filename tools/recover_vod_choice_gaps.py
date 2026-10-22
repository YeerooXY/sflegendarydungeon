"""Recover review images from counter sections missed by a pair-type filter.

Counter sections are coverage anchors, not verified unique choices. Keep an
image even when no confident door pair can be found, and mark that limitation.
No room numbers, labels or observations are fabricated for unreadable footage.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import hashlib
import json
from pathlib import Path
import time

from sfld_vod.door_recognition import MaskedDoorRecognizer
from sfld_vod.media import sha256, write_json
from sfld_vod.room_sequence import SEQUENCE_DIR
from sfld_vod.vision import rect

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.0.1'
DIRECTORY = 'choice-recovery-v' + VERSION


def missing_spans(catalog, rows):
    represented = {r.get('frame_room_sequence', {}).get('span_id') for r in rows
                   if r.get('frame_room_sequence')}
    return [s for s in catalog['spans'] if s['stable'] and s['id'] not in represented]


def sample_times(span, clip_start, clip_end):
    # The first image can still be fading in. Also inspect later points when a
    # stone dialog or another interior precedes the actual choice screen.
    start = max(span['start'], clip_start)
    end = min(span['end'], clip_end - .05)
    times = {min(end, start + n) for n in (.125, .375, .625, 1., 1.5, 2.5)}
    times.update(start + (end-start)*f for f in (.35, .65, .9))
    return sorted(t for t in times if start <= t <= end and
                  not any(a <= t <= b for a,b in span.get('blank_intervals', [])))


def recover_clip(job):
    import cv2
    cv2.setNumThreads(1)
    root = Path(job['root']); sid = job['source']['id']
    layout_path = root / 'layouts' / (sid + '.json')
    layout = json.loads(layout_path.read_text())
    recognizer = MaskedDoorRecognizer(layout_path, ROOT / 'out/vod-art')
    marker = Path(job['marker']); clip = json.loads(marker.read_text())
    video = marker.parent / clip['file']
    if sha256(video) != clip['sha256']:
        raise ValueError('Changed source video: ' + str(video))
    cap = cv2.VideoCapture(str(video)); output = []
    folder = root / sid / DIRECTORY; folder.mkdir(parents=True, exist_ok=True)
    try:
        for span in job['spans']:
            token = hashlib.sha256(span['id'].encode()).hexdigest()[:24]
            target = folder / (token + '.json')
            inputs = {'version': VERSION, 'video_sha256': clip['sha256'],
                      'layout_sha256': sha256(layout_path), 'span_id': span['id'],
                      'span_start': span['start'], 'span_end': span['end'],
                      'code_sha256': job['code_sha256']}
            if target.exists():
                cached = json.loads(target.read_text())
                if cached['inputs'] != inputs:
                    raise ValueError('Recovery inputs changed; preserve earlier version.')
                output.append(cached['candidate']); continue
            sampled = []
            for stamp in sample_times(span, clip['source_start'], clip['source_end']):
                cap.set(cv2.CAP_PROP_POS_MSEC, (stamp-clip['source_start'])*1000)
                ok, frame = cap.read()
                if not ok: continue
                decoded = clip['source_start'] + cap.get(cv2.CAP_PROP_POS_MSEC)/1000
                # Do not attach a neighboring counter to an imprecise seek.
                if not span['start'] <= decoded <= span['end'] + .04: continue
                x,y,w,h = rect(layout['crop'], frame.shape[1], frame.shape[0])
                native = frame[y:y+h, x:x+w]
                # Fast unmasked scores choose a frame only. The normal masked
                # reader subsequently classifies the exact published image.
                doors = recognizer.doors(native, selection_only=True)
                rank = (sum(d['kind'] is not None for d in doors),
                        min(d['score'] for d in doors), sum(d['score'] for d in doors))
                sampled.append((rank, stamp, decoded, native, doors, frame.shape[0]))
            if not sampled:
                raise ValueError('No recoverable image for ' + span['id'])
            sampled.sort(key=lambda s:s[0], reverse=True)
            best = sampled[0]
            # Keep the selected image and nearest distinct sample for trap
            # suggestions; no cross-room temporal evidence is borrowed.
            other = min(sampled[1:], key=lambda s:abs(s[2]-best[2]), default=None)
            evidence = []
            for number, sample in enumerate([best] + ([other] if other else [])):
                _, stamp, decoded, native, _, height = sample
                dest = folder / (token + f'-{number}.jpg')
                cv2.imwrite(str(dest), native, [cv2.IMWRITE_JPEG_QUALITY, 97])
                evidence.append({'image':dest.relative_to(root).as_posix(), 'sha256':sha256(dest),
                                 'source_seconds':stamp, 'decoded_source_seconds':decoded,
                                 'video_sha256':clip['sha256'], 'native_height':int(height)})
            counter = {k:span.get(k) for k in ('room','sequence_id','status')}
            counter.update(span_id=span['id'], span_ids=[span['id']],
                           possible_rooms=[span['room']], proofs=span['proofs'][:2])
            source = job['source']; url = source['url']
            row = {'id':sid+':coverage:'+token, 'source_id':sid,
                   'start':span['start'], 'end':span['end'], 'best_time':best[1],
                   'video':video.relative_to(root).as_posix(), 'video_sha256':clip['sha256'],
                   'clip_start':clip['source_start'], 'clip_end':clip['source_end'],
                   'evidence':evidence, 'thumbnail':evidence[0]['image'],
                   'thumbnail_sha256':evidence[0]['sha256'],
                   'source_url':url, 'source_link':url+('&' if '?' in url else '?')+'t='+str(int(best[1]))+'s',
                   'uploader':source.get('uploader'), 'upload_date':source.get('upload_date'),
                   'frame_room_sequence':counter, 'room_sequence':counter,
                   'status':'unreviewed_counter_gap_recovery', 'verified_fields':[],
                   'room':None, 'gems':[], 'traps':[], 'duplicate_of':None,
                   'recovery':{'span_id':span['id'], 'sampled_frames':len(sampled),
                               'accepted_door_suggestions':best[0][0],
                               'selection':'Highest two-door support among sampled native frames. May still be an interior or transition; review required.'}}
            write_json(target, {'inputs':inputs, 'candidate':row}); output.append(row)
    finally:
        cap.release()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT/'out/vod-older-2026-10-12')
    parser.add_argument('--workers', type=int, default=12)
    parser.add_argument('--source', action='append')
    args = parser.parse_args(); root = args.directory.resolve()
    if not 1 <= args.workers <= 16: raise ValueError('Use 1-16 workers.')
    original = json.loads((root/'monster-mystery-candidates.json').read_text())
    counter = json.loads((root/SEQUENCE_DIR/'catalog.json').read_text())
    missing = missing_spans(counter, original['candidates'])
    if args.source: missing = [s for s in missing if s['source_id'] in args.source]
    sources = {s['id']:s for s in original['sources']}
    hashes = {p:sha256(ROOT/'tools'/p) for p in ('recover_vod_choice_gaps.py','sfld_vod/door_recognition.py')}
    jobs = {}; unresolved = []
    for span in missing:
        sid = span['source_id']; marker = None
        # Resolve the exact video that supplied this counter, never another POV.
        video_hashes = {p['video_sha256'] for p in span['proofs']}
        for candidate in sorted((root/sid).glob('*/[0-9]*.json')):
            clip = json.loads(candidate.read_text())
            if clip.get('sha256') in video_hashes and clip.get('source_start',1e99) <= span['start'] < clip.get('source_end',-1):
                marker = candidate; break
        if marker is None:
            unresolved.append({'span_id':span['id'], 'reason':'counter clip not found'}); continue
        key = str(marker)
        if key not in jobs:
            jobs[key] = {'root':str(root), 'marker':str(marker), 'source':sources[sid],
                         'spans':[], 'code_sha256':hashes}
        jobs[key]['spans'].append(span)
    recovered = []; began = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(recover_clip, job) for job in jobs.values()]
        for future in as_completed(futures):
            recovered.extend(future.result())
            print(json.dumps({'recovered':len(recovered), 'target':len(missing),
                              'seconds':round(time.perf_counter()-began,1)}), flush=True)
    rows = sorted(original['candidates']+recovered, key=lambda r:(r['source_id'],r['best_time'],r['id']))
    payload = {**original, 'candidates':rows,
               'coverage':{'original_targeted_views':len(original['candidates']),
                           'added_counter_sections':len(recovered), 'unresolved':unresolved,
                           'scope':'Targeted views plus recovered readable counter sections. Not all choices are verified, and obscured counters can still hide gaps.'}}
    write_json(root/'room-choice-candidates.json', payload)
    print(json.dumps(payload['coverage']))


if __name__ == '__main__': main()
