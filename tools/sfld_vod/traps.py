"""Native-frame trap review queues and conflict-checked, non-mutating patches.

Run ``python -m sfld_vod.traps prepare --help`` with tools on PYTHONPATH.
Suggestions never become observations. Absence requires an explicit visual
review of both the upper and lower trap areas in two stable native frames.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from .batch import select_clips
from .media import sha256, write_json
from .vision import rect


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def fingerprint(row):
    """Trap patch conflicts ignore unrelated later outcome annotations."""
    ctx = row.get('context', {})
    value = {key: row.get(key) for key in ('id', 'candidate_id', 'source_id', 'source_seconds', 'status', 'doors')}
    value['identity'] = {key: ctx.get(key) for key in ('run_id', 'room', 'visit_id', 'phase', 'offer_attempt')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def systematic(rows, count):
    """Chronological midpoint sampling without reusing rows; not random inference."""
    if type(count) is not int or count < 0:
        raise ValueError('Sample count must be a nonnegative integer.')
    rows = sorted(rows, key=lambda r: (r['source_seconds'], r['id']))
    if count >= len(rows):
        return rows
    return [rows[math.floor((i + .5) * len(rows) / count)] for i in range(count)]


def select_sample(rows, total, positive_count=0):
    if not 0 <= positive_count <= total:
        raise ValueError('Positive stratum must fit inside total sample size.')
    decisions = [r for r in rows if r.get('status') == 'decision' and len(r.get('doors', [])) == 2]
    positives = [r for r in decisions if any(d.get('trap') is True for d in r['doors'])]
    chosen = [(r, 'existing_present') for r in systematic(positives, positive_count)]
    used = {r['id'] for r, _ in chosen}
    remaining = [r for r in decisions if r['id'] not in used and (positive_count == 0 or not any(d.get('trap') is True for d in r['doors']))]
    chosen += [(r, 'remaining_chronological') for r in systematic(remaining, total - len(chosen))]
    return sorted(chosen, key=lambda pair: (pair[0]['source_seconds'], pair[0]['id']))


def local_path(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Evidence must stay under its declared root.')
    return path


def prepare(root, plan_path, output, art=None):
    """Extract full gameplay crops at native resolution; canonical files are read only."""
    import cv2
    import numpy as np
    root = Path(root).resolve(); output = Path(output).resolve()
    if not output.is_relative_to(root):
        raise ValueError('Output must be under evidence root.')
    output.mkdir(parents=True, exist_ok=True)
    plan = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    queue = []; inventory = []; sheet_tiles = []
    for source in plan['sources']:
        sid = source['id']; ledger = local_path(root, source['observations'])
        rows = read_rows(ledger); layout_path = local_path(root, source['layout'])
        layout = json.loads(layout_path.read_text(encoding='utf-8'))
        clips = select_clips(root / sid / '720p')
        hint_reader = None
        if art:
            from .vision import Recognizer
            hint_reader = Recognizer(layout_path, art)
        inventory.append({'source_id': sid, 'observations': source['observations'], 'ledger_sha256': sha256(ledger),
                          'decisions': sum(r.get('status') == 'decision' for r in rows),
                          'trap_values': dict(Counter(str(tuple(d.get('trap') for d in r['doors'])) for r in rows if r.get('status') == 'decision'))})
        for row, stratum in select_sample(rows, source['samples'], source.get('positive_samples', 0)):
            index = len(queue); start = row.get('source_interval', [row['source_seconds']])[0]
            end = row.get('source_end', row.get('source_interval', [start, start])[1])
            # Interior samples reduce transition risk; the reviewer must still confirm identity.
            if end > start:
                times = [start + (end - start) * .35, start + (end - start) * .7]
            else:
                times = [row['evidence'][0]['source_seconds']]
            evidence = []; images = []; hints = []
            for j, stamp in enumerate(times):
                marker, clip = next((p, c) for p, c in clips if c['source_start'] <= stamp < c['source_end'])
                if clip['video']['height'] < 720:
                    raise ValueError('Native review requires a 720p or higher source.')
                video = marker.parent / clip['file']
                cap = cv2.VideoCapture(str(video)); cap.set(cv2.CAP_PROP_POS_MSEC, (stamp - clip['source_start']) * 1000)
                ok, full = cap.read(); cap.release()
                if not ok:
                    raise ValueError(f'Cannot decode native evidence for {row["id"]}.')
                x, y, w, h = rect(layout['crop'], full.shape[1], full.shape[0]); frame = full[y:y+h, x:x+w]
                path = output / 'frames' / f'{index:03}-{j}.jpg'; path.parent.mkdir(exist_ok=True)
                if not cv2.imwrite(str(path), frame):
                    raise RuntimeError('Cannot save native evidence.')
                ev = {'image': path.relative_to(root).as_posix(), 'sha256': sha256(path), 'source_seconds': stamp,
                      'role': 'native_pre_entry_trap_review', 'native_height': full.shape[0], 'crop_size': [w, h],
                      'video': video.relative_to(root).as_posix(), 'video_sha256': clip['sha256'],
                      'timing': clip.get('timing', 'Clip-relative timestamps; verify matching offer visually.')}
                evidence.append(ev); images.append(cv2.resize(frame, (400, 280), interpolation=cv2.INTER_AREA))
                if hint_reader:
                    hints.append(hint_reader.trap_hints(cv2.resize(frame, (320, 224), interpolation=cv2.INTER_AREA)))
            item = {'index': index, 'source_id': sid, 'observation_id': row['id'], 'candidate_id': row.get('candidate_id'),
                    'source_seconds': row['source_seconds'], 'source_interval': [start, end],
                    'observations': source['observations'], 'expected_fingerprint': fingerprint(row),
                    'before': [d.get('trap') for d in row['doors']], 'door_kinds': [d.get('kind') for d in row['doors']],
                    'sampling_stratum': stratum, 'evidence': evidence, 'unverified_legacy_hints': hints,
                    'sheet': f'sheet-{index//6:02}.jpg', 'tile': index % 6}
            queue.append(item)
            if len(images) == 1:
                images.append(images[0])
            tile = np.concatenate(images, axis=1)
            tile = cv2.copyMakeBorder(tile, 34, 0, 0, 0, cv2.BORDER_CONSTANT, value=(25,25,25))
            cv2.putText(tile, f'{index:03} {sid} {row["id"].split(":")[-1]} {item["door_kinds"]}', (5,22), 0, .48, (255,255,255), 1)
            sheet_tiles.append(tile)
    for page in range((len(sheet_tiles) + 5)//6):
        sheet = np.concatenate(sheet_tiles[page*6:(page+1)*6], axis=0)
        cv2.imwrite(str(output/f'sheet-{page:02}.jpg'), sheet)
    result = {'schema': 1, 'evidence_root': str(root), 'scope': 'visible_pre_entry_trap_overlay',
              'sampling': 'Chronological midpoint sample within declared strata; illustrative review, not an unbiased prevalence estimate.',
              'inventory': inventory, 'items': queue}
    write_json(output/'queue.json', result)
    template = [{'observation_id': q['observation_id'], 'expected_fingerprint': q['expected_fingerprint'],
                 'evidence_sha256': [e['sha256'] for e in q['evidence']],
                 'doors': [{'trap': None, 'visual': None, 'reason': ''} for _ in range(2)]}
                for q in queue]
    (output/'annotation-template.jsonl').write_text(''.join(json.dumps(a)+'\n' for a in template), encoding='utf-8')
    return result


ABSENCE_CHECKS = ('same_offer', 'stable_decision', 'upper_area_visible', 'floor_area_visible', 'native_detail_readable')


def make_patch(queue, annotations, rows_by_id, root):
    """Validate human labels and return patches; never writes canonical observations."""
    items = {q['observation_id']: q for q in queue['items']}; seen = set(); result = []
    for annotation in annotations:
        ident = annotation['observation_id']
        if ident in seen:
            raise ValueError('Duplicate trap review.')
        seen.add(ident)
        item = items[ident]; row = rows_by_id[ident]
        if row.get('status') != 'decision' or fingerprint(row) != item['expected_fingerprint']:
            raise ValueError('Trap target changed; inspect before rebasing the patch.')
        evidence = item['evidence']
        if (annotation.get('expected_fingerprint') != item['expected_fingerprint']
                or annotation.get('evidence_sha256') != [e['sha256'] for e in evidence]):
            raise ValueError('Review does not match the queued target and evidence hashes.')
        if not evidence:
            raise ValueError('Trap review requires native image evidence.')
        for ev in evidence:
            stamp = ev.get('source_seconds')
            if type(stamp) not in (int, float) or not math.isfinite(stamp):
                raise ValueError('Evidence needs a finite source timestamp.')
            if sha256(local_path(root, ev['image'])) != ev['sha256']:
                raise ValueError('Trap evidence changed.')
            if type(ev.get('native_height')) is not int or ev['native_height'] < 720:
                raise ValueError('Trap review requires native 720p image evidence.')
        doors = annotation.get('doors')
        if not isinstance(doors, list) or len(doors) != 2:
            raise ValueError('Review both door positions explicitly.')
        values = []; visuals = []
        for door in doors:
            value = door.get('trap')
            if value is not None and type(value) is not bool:
                raise ValueError('Trap value must be true, false, or unknown.')
            if not isinstance(door.get('reason'), str) or not door['reason'].strip():
                raise ValueError('Every side needs a visual reason, including unknowns.')
            if value is False:
                checks = door.get('checks', {})
                native = all(type(e.get('native_height')) is int and e['native_height'] >= 720 for e in evidence)
                if (len({e['source_seconds'] for e in evidence}) < 2 or not native
                        or not all(checks.get(key) is True for key in ABSENCE_CHECKS)):
                    raise ValueError('Absence requires two native frames and explicit visibility/stability checks.')
            if value is True and not door.get('visual'):
                raise ValueError('Presence needs a described visible trap, not an outcome inference.')
            if value is not True and door.get('visual'):
                raise ValueError('An absent or unknown trap cannot have an asserted visual kind.')
            values.append(value); visuals.append(door.get('visual'))
        result.append({'schema': 1, 'observation_id': ident, 'candidate_id': item.get('candidate_id'),
                       'source_id': item['source_id'], 'source_seconds': item['source_seconds'],
                       'expected_fingerprint': item['expected_fingerprint'], 'before': item['before'],
                       'set': {'door_traps': values, 'door_trap_visuals': visuals},
                       'verified_fields_add': ['traps'] if any(v is not None for v in values) else [],
                       'scope': 'visible_pre_entry_trap_overlay', 'review': annotation,
                       'evidence': evidence})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare'); prep.add_argument('--root', type=Path, required=True)
    prep.add_argument('--plan', type=Path, required=True); prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--art', type=Path)
    check = commands.add_parser('patch'); check.add_argument('--queue', type=Path, required=True)
    check.add_argument('--annotations', type=Path, required=True); check.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.root, args.plan, args.output, args.art)
        print(f'Prepared {len(result["items"])} review pairs; canonical rows unchanged.')
    else:
        queue = json.loads(args.queue.read_text(encoding='utf-8')); root = Path(queue['evidence_root'])
        rows = {}
        for name in {i['observations'] for i in queue['items']}:
            rows.update({r['id']: r for r in read_rows(local_path(root, name))})
        result = make_patch(queue, read_rows(args.annotations), rows, root)
        write_json(args.output, {'schema': 1, 'scope': queue['scope'], 'patches': result})
        print(f'Validated {len(result)} review patches; canonical rows unchanged.')


if __name__ == '__main__':
    main()
