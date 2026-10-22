"""Native offer recognition for review; predictions never become observations."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import quote

from .media import sha256, write_json
from .traps import local_path

VERSION = 'native-offer-review-2'


def validate_items(queue, root):
    """Require distinct native evidence timestamps and immutable image hashes."""
    items = queue.get('items', [])
    if not items:
        raise ValueError('The queue must contain offer items.')
    seen = set()
    for item in items:
        ident = item.get('observation_id') or item.get('id')
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError('Offer IDs must be explicit and unique.')
        seen.add(ident)
        sid = item.get('source_id')
        if not isinstance(sid, str) or not sid or Path(sid).name != sid or sid in ('.', '..'):
            raise ValueError('Every offer needs a safe source_id.')
        evidence = item.get('evidence', [])
        stamps = set()
        images = set()
        hashes = set()
        if len(evidence) < 2:
            raise ValueError('Review recognition needs at least two native frames per offer.')
        for ev in evidence:
            stamp = ev.get('source_seconds')
            if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
                raise ValueError('Evidence needs a finite source timestamp.')
            stamps.add(stamp)
            if type(ev.get('native_height')) is not int or ev['native_height'] < 720:
                raise ValueError('Use crops decoded from native 720p or higher footage.')
            path = local_path(root, ev['image'])
            if path in images:
                raise ValueError('Each support frame must have its own evidence file.')
            images.add(path)
            if sha256(path) != ev['sha256']:
                raise ValueError('Native evidence hash mismatch.')
            if ev['sha256'] in hashes:
                raise ValueError('Copied image evidence cannot supply a second temporal vote.')
            hashes.add(ev['sha256'])
        if len(stamps) != len(evidence):
            raise ValueError('Repeated copies of one timestamp are not independent frame support.')
        decoded = [ev['decoded_source_seconds'] for ev in evidence if ev.get('decoded_source_seconds') is not None]
        if any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in decoded):
            raise ValueError('Invalid decoded frame timestamp.')
        if len(set(decoded)) != len(decoded):
            raise ValueError('Repeated decoded frames cannot supply temporal support.')
    return items


def prepare(root, source_id, scene_files, output):
    """Extract two interior samples per candidate offer, without outcome labels."""
    import cv2
    from .batch import select_clips
    from .vision import rect
    root = Path(root).resolve(); output = Path(output).resolve()
    if not output.is_relative_to(root):
        raise ValueError('Evidence output must stay inside the declared root.')
    if not source_id or Path(source_id).name != source_id or source_id in ('.', '..'):
        raise ValueError('Expected a safe source_id.')
    if (output/'queue.json').exists():
        raise ValueError('Queue exists; reuse it or choose a new extraction directory.')
    layout_path = local_path(root, f'layouts/{source_id}.json')
    layout = json.loads(layout_path.read_text(encoding='utf-8'))
    clips = select_clips(local_path(root, f'{source_id}/720p'))
    candidates = []
    for scene in map(Path, scene_files):
        data = json.loads(scene.read_text(encoding='utf-8'))
        for epoch in data['epochs']:
            if epoch.get('source_id') is not None and epoch['source_id'] != source_id:
                raise ValueError('Scene source_id does not match the requested VOD.')
            start, end = epoch['start'], epoch['end']
            if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in (start, end)) or end < start:
                raise ValueError('Invalid candidate interval.')
            candidates.append({'observation_id': epoch.get('id') or f'{source_id}:{start:.3f}:scene',
                'source_id': source_id, 'start': start, 'end': end,
                'scene_sha256': sha256(scene)})
    if len({c['observation_id'] for c in candidates}) != len(candidates):
        raise ValueError('Scene inputs overlap; choose one scan version per clip.')
    output.mkdir(parents=True, exist_ok=True); (output/'frames').mkdir(exist_ok=True)
    items = []; unresolved = []; cap = None; current = None; checked_videos = set()
    try:
        for candidate in sorted(candidates, key=lambda c: c['start']):
            start, end = candidate['start'], candidate['end']
            if end-start < .12:
                unresolved.append({**candidate, 'reason': 'Offer too brief for two separate interior samples.'})
                continue
            evidence = []
            for stamp in (start+.35*(end-start), start+.70*(end-start)):
                match = next(((p, c) for p, c in clips if c['source_start'] <= stamp < c['source_end']), None)
                if match is None:
                    break
                marker, clip = match
                if clip['video']['height'] < 720:
                    break
                video = local_path(root, str((marker.parent/clip['file']).relative_to(root)))
                if video not in checked_videos:
                    if sha256(video) != clip['sha256']:
                        raise ValueError('Native video changed.')
                    checked_videos.add(video)
                if video != current:
                    if cap is not None: cap.release()
                    cap = cv2.VideoCapture(str(video)); current = video
                cap.set(cv2.CAP_PROP_POS_MSEC, (stamp-clip['source_start'])*1000)
                ok, raw = cap.read()
                if not ok: break
                decoded_seconds = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
                decoded_source = (clip['source_start']+decoded_seconds
                                  if math.isfinite(decoded_seconds) and decoded_seconds >= 0 else None)
                x, y, w, h = rect(layout['crop'], raw.shape[1], raw.shape[0])
                frame = raw[y:y+h, x:x+w]
                name = hashlib.sha256(f'{candidate["observation_id"]}:{stamp:.6f}'.encode()).hexdigest()[:20]+'.png'
                image_path = output/'frames'/name
                if not cv2.imwrite(str(image_path), frame):
                    raise RuntimeError('Cannot save native crop.')
                evidence.append({'image': image_path.relative_to(root).as_posix(), 'sha256': sha256(image_path),
                    'source_seconds': stamp, 'native_height': raw.shape[0], 'crop_size': [w, h],
                    'video_sha256': clip['sha256'], 'role': 'native_offer_interior_sample',
                    'decoded_source_seconds': decoded_source,
                    'timing': 'source_seconds is the requested seek. decoded_source_seconds uses the decoder-reported clip position; stream-copy alignment to the original VOD remains approximate.'})
            if len(evidence) != 2:
                unresolved.append({**candidate, 'reason': 'Missing native footage or decode failure.'})
            elif evidence[0]['decoded_source_seconds'] is not None and evidence[0]['decoded_source_seconds'] == evidence[1]['decoded_source_seconds']:
                unresolved.append({**candidate, 'reason': 'Both requested samples decoded the same frame; no independent temporal support.'})
            elif evidence[0]['sha256'] == evidence[1]['sha256']:
                unresolved.append({**candidate, 'reason': 'Identical image evidence cannot establish separate temporal support.'})
            else:
                items.append({**candidate, 'evidence': evidence})
    finally:
        if cap is not None: cap.release()
    queue = {'schema': 1, 'source_id': source_id, 'layout_sha256': sha256(layout_path),
        'items': items, 'unresolved_candidates': unresolved,
        'note': 'Candidate frames only. Confirm both samples show the same stable offer. Missing candidates remain explicit; no room identity, stones or selection is verified.'}
    write_json(output/'queue.json', queue)
    return queue


def recognize(root, queue_path, art, output):
    import cv2
    from .door_recognition import stable_doors
    from .spatial_doors import SpatialDoorRecognizer
    from .trap_detector import TrapDetector
    from .hud import GemReader
    root = Path(root).resolve(); output = Path(output).resolve(); art = Path(art).resolve()
    if not output.is_relative_to(root):
        raise ValueError('Review output must stay inside the evidence root.')
    queue_path = Path(queue_path)
    queue = json.loads(queue_path.read_text(encoding='utf-8'))
    items = validate_items(queue, root)
    if (output/'suggestions.json').exists():
        raise ValueError('Predictions already exist; use a new directory to preserve the comparison.')
    cv2.setNumThreads(1)
    output.mkdir(parents=True, exist_ok=True)
    door_readers = {}; layout_hashes = {}; traps = TrapDetector(art); gems = GemReader(art)
    result = {'schema': 1, 'version': VERSION, 'queue_sha256': sha256(queue_path),
        'art_manifest_sha256': sha256(art/'manifest.json'), 'layout_sha256': layout_hashes,
        'art_files_sha256': {name: sha256(local_path(art, name)) for name in sorted(set(
            json.loads((art/'manifest.json').read_text(encoding='utf-8'))['assets'].values()))},
        'calibrated': False, 'canonical_observations_modified': False,
        'implementation_sha256': {name: sha256(Path(__file__).with_name(name)) for name in (
            'recognize.py', 'door_recognition.py', 'spatial_doors.py', 'trap_detector.py', 'hud.py')},
        'door_parameters': {'method': 'spatial_regions_v1', 'regions': 6, 'margin': .10,
                            'minimum_agreement': 2, 'scores_are_probabilities': False},
        'unresolved_candidates': queue.get('unresolved_candidates', []), 'rows': [],
        'note': 'Suggestions only. A missing trap match is unknown, never absence. Stone slots are incomplete unless reviewed. No selection is inferred from room contents or cursor hover.'}
    began = time.perf_counter()
    for item in items:
        sid = item['source_id']; ident = item.get('observation_id') or item['id']
        if sid not in door_readers:
            layout = local_path(root, f'layouts/{sid}.json')
            door_readers[sid] = SpatialDoorRecognizer(layout, art)
            layout_hashes[sid] = sha256(layout)
        native = [cv2.imread(str(local_path(root, e['image']))) for e in item['evidence']]
        if any(frame is None for frame in native):
            raise ValueError(f'Cannot decode native offer evidence: {ident}')
        if len({frame.shape for frame in native}) != 1:
            raise ValueError('Offer frames must use the same crop geometry.')
        door_frames = [door_readers[sid].doors(frame) for frame in native]
        trap_prediction = traps.predict_frames(native)
        row = {'observation_id': ident, 'source_id': sid, 'status': 'unreviewed_suggestions',
            'verified_fields': [], 'evidence': item['evidence'],
            'doors': stable_doors(door_frames), 'door_frame_predictions': door_frames,
            'traps': trap_prediction, 'stone_frame_predictions': [gems.read(frame) for frame in native],
            'stone_set_complete': False, 'chosen_side': None}
        result['rows'].append(row)
        if len(result['rows']) % 10 == 0:
            write_json(output/'partial-suggestions.json', result)
    result['processing_seconds'] = time.perf_counter()-began
    write_json(output/'suggestions.json', result)
    render_review(result, root, output/'review.html')
    return result


def render_review(result, root, target):
    """Small local gallery; human confirmation remains in the sequence browser."""
    esc = html.escape
    chunks = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
        '<title>Native recognition review</title><style>body{font:16px system-ui;max-width:1500px;margin:24px auto;padding:0 20px;background:#101c22;color:#eee}a{color:#edc676}article{border-top:1px solid #567;padding:20px 0}img{width:49%;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}input{font:inherit;padding:8px;width:80%}</style>',
        '<h1>Native recognition review</h1><p>Predictions need review. Unknown trap presence does not mean absent. No predictions have been added to measured odds.</p>',
        '<label>Filter by source, offer ID or suggested label <input id="filter" type="search"></label>']
    for row in result['rows']:
        summary = {'doors': row['doors'], 'traps': row['traps'],
                   'stone_frame_predictions': row['stone_frame_predictions']}
        payload = json.dumps(summary, indent=2)
        search = (row['observation_id']+' '+payload).lower()
        chunks += [f'<article data-search="{esc(search, quote=True)}"><h2>{esc(row["observation_id"])}</h2>']
        labels = []
        for side, name in enumerate(('Left', 'Right')):
            door = row['doors'][side].get('kind') or 'unknown door'
            trap = row['traps']['sides'][side]
            status = ('trap suggested: '+str(trap.get('visual') or 'unspecified')
                      if trap.get('present') is True else 'trap unknown')
            labels.append(f'{name}: {door}; {status}')
        chunks.append('<p>Suggestions: '+esc(' | '.join(labels))+'</p>')
        for ev in row['evidence']:
            relative = os.path.relpath(local_path(root, ev['image']), target.parent).replace('\\', '/')
            url = esc(quote(relative, safe='/.'), quote=True)
            chunks.append(f'<a href="{url}"><img loading="lazy" src="{url}" alt="Native offer at {ev["source_seconds"]:.3f} seconds"></a>')
        chunks.append(f'<details><summary>Suggestions and confidence</summary><pre>{esc(payload)}</pre></details></article>')
    chunks += ['<script>document.getElementById("filter").addEventListener("input",e=>{const q=e.target.value.toLowerCase();document.querySelectorAll("article").forEach(a=>a.hidden=!a.dataset.search.includes(q))})</script></html>']
    target.write_text('\n'.join(chunks), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='Extract native evidence from scene candidates')
    prep.add_argument('--root', type=Path, required=True); prep.add_argument('--source-id', required=True)
    prep.add_argument('--scenes', type=Path, nargs='+', required=True); prep.add_argument('--output', type=Path, required=True)
    run = commands.add_parser('run', help='Suggest doors, traps and stone icons on native offer frames')
    run.add_argument('--root', type=Path, required=True); run.add_argument('--queue', type=Path, required=True)
    run.add_argument('--art', type=Path, required=True); run.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.root, args.source_id, args.scenes, args.output)
        print(json.dumps({'offers': len(result['items']), 'unresolved_candidates': len(result['unresolved_candidates']), 'queue': str(args.output/'queue.json')}))
    else:
        result = recognize(args.root, args.queue, args.art, args.output)
        print(json.dumps({'offers': len(result['rows']), 'processing_seconds': result['processing_seconds'], 'review': str(args.output/'review.html')}))


if __name__ == '__main__':
    main()
