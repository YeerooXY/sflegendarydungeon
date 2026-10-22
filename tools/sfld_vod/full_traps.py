"""Checkpointed native trap scan of every reviewed choice, without relabeling truth."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import time

from .media import sha256, write_json
from .temporal import normalize_generation, observation_counts
from .trap_detector import DEFAULT_CONFIG, config_hash, consensus

_detector = None
TIMING_POLICY = {'maximum_seek_error_seconds': 0.10, 'require_offer_interval': True}


def timing_problem(record):
    """A decoded image is not evidence for a visit merely because we requested it."""
    evidence = record.get('evidence', [])
    if not evidence:
        return None
    interval = record.get('source_interval', [])
    valid = lambda n: type(n) in (int, float) and math.isfinite(n)
    if len(interval) != 2 or not all(valid(n) for n in interval) or interval[1] <= interval[0]:
        return 'Missing or invalid bounded offer interval.'
    for frame in evidence:
        requested, decoded = frame.get('source_seconds'), frame.get('decoded_source_seconds')
        if not valid(requested) or not valid(decoded):
            return 'Missing or invalid decoded timestamp.'
        if abs(requested-decoded) > TIMING_POLICY['maximum_seek_error_seconds']:
            return 'Decoded frame exceeds the 0.10-second seek tolerance.'
        if not interval[0] <= decoded < interval[1]:
            return 'Decoded frame falls outside its offer interval.'
    return None


def can_confirm_with_second(first, threshold):
    return first.get('usable') and any(m['score'] >= threshold for side in first['sides']
                                      for m in side.get('candidates', []))


def build_manifest(plans, art):
    records = []; sources = []; excluded = Counter()
    for plan_path in map(Path, plans):
        root = plan_path.parent.resolve(); plan = json.loads(plan_path.read_text(encoding='utf-8'))
        for source in plan['sources']:
            sid = source['source_id']; ledger = root/source['observations']
            rows = [normalize_generation(json.loads(s)) for s in ledger.read_text(encoding='utf-8').splitlines() if s.strip()]
            observation_counts(rows)
            # Offers, not clips, are the counting unit. Bounded proof downloads
            # may overlap the full source sections; choose exactly one covering
            # clip per visit and never concatenate those overlapping intervals.
            clips = []
            for marker in (root/sid/'720p').glob('*.json'):
                clip = json.loads(marker.read_text(encoding='utf-8'))
                if clip.get('schema') == 1 and 'file' in clip:
                    clips.append((marker, clip))
            clips.sort(key=lambda pc: (-(pc[1]['source_end']-pc[1]['source_start']), str(pc[0])))
            timeline_path = root/f'{sid}-timeline.json'
            timeline = json.loads(timeline_path.read_text(encoding='utf-8')) if timeline_path.exists() else {}
            candidates = {r['id']: r for r in timeline.get('candidates', [])}
            layout = root/'layouts'/f'{sid}.json'
            sources.append({'source_id': sid, 'root': str(root), 'ledger_sha256': sha256(ledger),
                            'layout_sha256': sha256(layout), 'decision_count': sum(r['status']=='decision' for r in rows)})
            for row in rows:
                if row['status'] != 'decision':
                    excluded[row['status']] += 1; continue
                candidate = candidates.get(row.get('candidate_id'), {})
                interval = row.get('source_interval') or []
                start = row.get('source_start', interval[0] if interval else candidate.get('start', row['source_seconds']))
                end = row.get('source_end', interval[1] if len(interval)>1 else candidate.get('end'))
                item = {'observation_id': row['id'], 'source_id': sid, 'source_seconds': row['source_seconds'],
                        'root': str(root), 'layout': str(layout), 'generation': row.get('generation'),
                        'context': row['context'], 'doors': row['doors'],
                        'eligible_ordinary': bool(observation_counts([row])['coverage'].get('eligible_decisions')),
                        'verified_traps': [d.get('trap') for d in row['doors']] if 'traps' in row['verified_fields'] else [None, None],
                        'source_interval': [start, end], 'preparation_error': None}
                if (any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end))
                        or end-start < .12):
                    item['preparation_error'] = 'No sufficiently long bounded offer interval.'
                else:
                    stamps = [start+.35*(end-start), start+.70*(end-start)]
                    match = next(((p,c) for p,c in clips if c['source_start'] <= stamps[0] and stamps[1] < c['source_end']), None)
                    if match is None:
                        item['preparation_error'] = 'Native clip coverage missing or sample crosses clip boundary.'
                    else:
                        marker, clip = match
                        item.update(stamps=stamps, video=str((marker.parent/clip['file']).resolve()),
                                    video_sha256=clip['sha256'], clip_start=clip['source_start'])
                records.append(item)
    ids = [r['observation_id'] for r in records]
    if len(set(ids)) != len(ids): raise ValueError('Overlapping canonical ledgers.')
    return {'schema': 2, 'timing_policy': TIMING_POLICY,
            'scope': 'Every canonical decision across the supplied plans; nonordinary phases retained separately.',
            'sources': sources, 'excluded_nondecisions': dict(excluded),
            'art': str(Path(art).resolve()), 'art_manifest_sha256': sha256(Path(art)/'manifest.json'),
            'detector_config': DEFAULT_CONFIG, 'detector_config_sha256': config_hash(DEFAULT_CONFIG),
            'records': records}


def _worker(job):
    global _detector
    import cv2
    from .trap_detector import TrapDetector
    from .vision import rect
    cv2.setNumThreads(1)
    if _detector is None: _detector = TrapDetector(job['art'])
    output = Path(job['output']); output.mkdir(parents=True, exist_ok=True)
    cap = None; current = None; checked = set(); results = []
    for item in job['items']:
        token = hashlib.sha256(item['observation_id'].encode()).hexdigest()[:24]
        target = output/(token+'.json')
        if target.exists():
            cached = json.loads(target.read_text(encoding='utf-8'))
            if cached['scan_identity'] != job['identity']: raise ValueError('Cached scan configuration changed.')
            results.append(cached); continue
        result = {k:v for k,v in item.items() if k not in ('root','layout','video','preparation_error')}
        result.update(scan_identity=job['identity'], status='unresolved', evidence=[], machine_sides=[None,None],
                      reason=item['preparation_error'])
        if not item['preparation_error']:
            began = time.perf_counter()
            video = Path(item['video'])
            if video not in checked:
                if sha256(video) != item['video_sha256']: raise ValueError('Native video hash changed.')
                checked.add(video)
            if current != video:
                if cap is not None: cap.release()
                cap = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1]); current = video
            layout = json.loads(Path(item['layout']).read_text(encoding='utf-8'))
            frames = []; times = []
            for i, stamp in enumerate(item['stamps']):
                cap.set(cv2.CAP_PROP_POS_MSEC, (stamp-item['clip_start'])*1000)
                ok, raw = cap.read()
                if not ok or raw.shape[0] < 720: break
                x,y,w,h = rect(layout['crop'],raw.shape[1],raw.shape[0]); frame = raw[y:y+h,x:x+w]
                frames.append(frame); times.append(item['clip_start']+cap.get(cv2.CAP_PROP_POS_MSEC)/1000)
                image = output/f'{token}-{i}.jpg'
                if not cv2.imwrite(str(image),frame,[cv2.IMWRITE_JPEG_QUALITY,97]): raise RuntimeError('Cannot write native proof.')
                result['evidence'].append({'image': image.name, 'sha256': sha256(image), 'source_seconds': stamp,
                    'decoded_source_seconds': times[-1], 'native_height': raw.shape[0], 'crop_size':[w,h],
                    'role':'full_inventory_native_offer_sample'})
            if len(frames) != 2:
                result['reason'] = 'Native frame unavailable.'
            elif timing_problem(result):
                result['reason'] = timing_problem(result)
            elif times[0] == times[1] or result['evidence'][0]['sha256'] == result['evidence'][1]['sha256']:
                result['reason'] = 'Repeated decoded frame or identical image evidence.'
            else:
                first = _detector.read(frames[0])
                if can_confirm_with_second(first, _detector.config['positive_threshold']):
                    second = _detector.read(frames[1]); shortcut = False
                else:
                    second = {'usable':False,'reason':'First frame cannot support any two-frame positive.','sides':[{},{}]}; shortcut = True
                prediction = consensus([first,second],_detector.config)
                result.update(status='machine_suggestions_only', reason=None,
                    machine_sides=[p['present'] for p in prediction], prediction=prediction,
                    first_frame_usable=first.get('usable',False), second_match_skipped=shortcut,
                    processing_seconds=time.perf_counter()-began)
            # No-match is deliberately not a verified false label.
        temporary = target.with_suffix('.tmp')
        write_json(temporary,result); temporary.replace(target); results.append(result)
    if cap is not None: cap.release()
    return results


def run(manifest, output, workers=4):
    if type(workers) is not int or not 1 <= workers <= 16: raise ValueError('Use 1-16 bounded workers.')
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    identity = hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    marker=output/'manifest.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')) != manifest:
        raise ValueError('Inputs changed; use a new output directory.')
    write_json(marker,manifest)
    groups=defaultdict(list)
    for item in manifest['records']: groups[item.get('video','missing')].append(item)
    jobs=[]
    for video,items in groups.items():
        for start in range(0,len(items),32):
            jobs.append({'art':manifest['art'],'identity':identity,'items':items[start:start+32],
                         'output':str(output/'records')})
    completed=[]; began=time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures=[executor.submit(_worker,job) for job in jobs]
        for future in as_completed(futures):
            completed.extend(future.result())
            progress={'processed':len(completed),'target':len(manifest['records']),
                      'machine_positive_pairs':sum(any(t is True for t in r['machine_sides']) for r in completed),
                      'unresolved':sum(r['status']=='unresolved'for r in completed),'seconds':round(time.perf_counter()-began,1)}
            write_json(output/'progress.json',progress)
            print(json.dumps(progress),flush=True)
    byid={r['observation_id']:r for r in completed}
    ordered=[byid[r['observation_id']]for r in manifest['records']]
    (output/'predictions.jsonl').write_text(''.join(json.dumps(r)+'\n'for r in ordered),encoding='utf-8')
    return progress


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,action='append',required=True)
    p.add_argument('--art',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workers',type=int,default=4);p.add_argument('--limit',type=int)
    a=p.parse_args();manifest=build_manifest(a.plan,a.art)
    if a.limit is not None:
        if a.limit<1: p.error('Limit must be positive.')
        manifest['records']=manifest['records'][:a.limit];manifest['limited_test']=True
    result=run(manifest,a.output,a.workers);print(json.dumps(result))


if __name__=='__main__':main()
