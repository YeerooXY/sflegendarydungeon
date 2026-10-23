"""Automatic door-offer census from saved VOD clips, one record per displayed room.

The OCR series (vod_hud_scan.py) gives the room counter ("N/100"). The door
screen of room N already carries counter N and appears when the counter changes,
so frames at --rate per second from --before seconds before the first reading of a
new value to --after seconds after it are that room's door choice (bounded by the
last reading of the old value; earlier frames show the previous room). They are
decoded in one sequential pass per clip, cropped with the stream's layout and read
by the alpha-masked door recognizer. A side needs two agreeing frames
(stable_doors), otherwise it stays unknown. Stones are read too.

Repeated views of a room never add records: one record per counter value change.
Labels are unreviewed suggestions; the recognizer abstains rather than guess, so
per-class recall must be checked against reviewed offers before estimating rates.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter
import gzip
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ROOM = re.compile(r'^\s*(\d{1,3})\s*/\s*100\s*$')
KEY = re.compile(r'^\s*(\d{1,2})\s*[xX×]\s*$')
HP = re.compile(r'^\s*(\d{1,3}(?:[.,\s]\d{3})+)\s*/\s*(\d{1,3}(?:[.,\s]\d{3})+)\s*$')


def series(scan_folder):
    rows = {}
    for path in sorted(Path(scan_folder).glob('*.tokens.jsonl.gz')):
        if not path.with_suffix('.done').exists():continue
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line);rows.setdefault(row['t'], row)
    rows = [rows[t] for t in sorted(rows)]
    position = Counter((round(t[1], 1), round(t[2], 1)) for r in rows for t in r['tokens'] if ROOM.match(t[0]) and t[4] >= .8)
    if not position:return [], None
    (px, py), _ = position.most_common(1)[0]
    out = []
    for r in rows:
        room = [t for t in r['tokens'] if ROOM.match(t[0]) and t[4] >= .8 and abs(t[1]-px) < .06 and abs(t[2]-py) < .06]
        value = int(ROOM.match(room[0][0]).group(1)) if room else None
        key = [int(KEY.match(t[0]).group(1)) for t in r['tokens'] if room and KEY.match(t[0]) and t[1] > room[0][1] and abs(t[2]-room[0][2]) < .08]
        hp = [t for t in r['tokens'] if HP.match(t[0]) and t[4] >= .8]
        hp = (int(re.sub(r'\D', '', HP.match(hp[0][0]).group(1))), int(re.sub(r'\D', '', HP.match(hp[0][0]).group(2)))) if hp else None
        out.append(dict(t=r['t'], room=value, keys=key[0] if key else None, hp=hp))
    return out, (px, py)


def segments(reads):
    """First time of each new counter value: two consecutive equal readings, or one
    reading that is exactly the next room (fast rooms last less than two samples)."""
    out = [];current = None;last_seen = None;valued = [r for r in reads if r['room'] is not None]
    for a, b in zip(valued, valued[1:]+[dict(room=None)]):
        step = current is not None and a['room'] == current+1
        if (a['room'] == b['room'] or step) and a['room'] != current and 1 <= a['room'] <= 100:
            out.append(dict(room=a['room'], previous=current, t=a['t'], since=last_seen, keys=a['keys'], hp=a['hp']))
            current = a['room']
        if a['room'] == current:last_seen = a['t']
    return out


def clips_of(source_folder):
    clips = []
    for marker in sorted(Path(source_folder).glob('720p/*.json')):
        video = marker.with_suffix('.mp4')
        if video.exists() and not Path(str(video)+'.part').exists():
            m = json.loads(marker.read_text());clips.append((m['source_start'], m['source_end'], str(video)))
    return clips


def frames_at(clips, times, transform=None):
    """Decode the requested source seconds, one sequential pass per clip; transform
    (e.g. a crop) is applied on decode so whole frames are not kept."""
    import cv2
    got = {}
    for start, end, video in clips:
        wanted = sorted(t for t in times if start <= t < end)
        if not wanted:continue
        cap = cv2.VideoCapture(video);fps = cap.get(cv2.CAP_PROP_FPS) or 30;frame_no = 0;i = 0
        while i < len(wanted):
            target = int(round((wanted[i]-start)*fps))
            if target-frame_no > 5*fps:
                # Seek near the target, then decode forward to it; positions are re-read after seeking.
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target-int(2*fps)))
                frame_no = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            if frame_no < target-1:
                if not cap.grab():break
                frame_no += 1;continue
            ok, frame = cap.read();frame_no += 1
            if not ok:break
            got[wanted[i]] = transform(frame) if transform else frame;i += 1
        cap.release()
    return got


def crop(frame, layout):
    from sfld_vod.vision import rect  # layout crop is x, y, width, height as fractions
    x, y, w, h = rect(layout['crop'], frame.shape[1], frame.shape[0])
    return frame[y:y+h, x:x+w]


# Door calibration in the 320x224 game crop. It is identical in every calibrated
# layout because it depends only on the crop, whose aspect is the game's (~1.40).
DOOR_CALIBRATION = dict(template_heights=[130, 138, 146], door_search=[[49, 29, 88, 158], [194, 29, 88, 158]])


def completed_layout(layout_path, folder):
    """A crop-only layout gets the shared door calibration when its crop has the game's aspect."""
    layout = json.loads(Path(layout_path).read_text())
    if 'template_heights' in layout or 'pair_template_heights' in layout:return Path(layout_path)
    x, y, w, h = layout['crop']
    if not 1.36 <= w/h*16/9 <= 1.44:return None
    target = Path(folder)/Path(layout_path).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(layout, **DOOR_CALIBRATION, calibration='shared default (crop-only layout)'), indent=1))
    return target


def census(source, scans, hunt, layouts, art, rate, before, after, limit=None, filled=None):
    import cv2
    cv2.setNumThreads(1)
    from sfld_vod.door_recognition import MaskedDoorRecognizer, stable_doors
    from sfld_vod.hud import GemReader
    layout_path = Path(layouts)/f'{source}.json'
    if not layout_path.exists():return source, None
    layout_path = completed_layout(layout_path, filled) if filled else layout_path
    if layout_path is None:return source, None
    layout = json.loads(layout_path.read_text())
    reads, position = series(Path(scans)/source)
    segs = segments(reads)[:limit]
    if not segs:return source, []
    doors = MaskedDoorRecognizer(layout_path, art);gems = GemReader(art)
    def window(s):
        # The door screen of room N carries counter N and starts when it changes; earlier
        # frames show the previous room (its doors or its fight), so stay close to the change.
        begin = max(s['t']-before, s['since'] if s['since'] is not None else s['t']-before)
        n = int((s['t']+after-begin)*rate)+1
        return [round(begin+k/rate, 3) for k in range(n)]
    times = sorted({t for s in segs for t in window(s)})
    images = frames_at(clips_of(Path(hunt)/source), times, lambda f: crop(f, layout).copy())
    out = []
    for s in segs:
        shots = [images.get(t) for t in window(s)]
        shots = [f for f in shots if f is not None]
        if len(shots) < 2:continue
        pair = stable_doors([doors.doors(f) for f in shots])
        stones = gems.read(cv2.resize(shots[0], (640, 448)))
        out.append(dict(source=source, t=s['t'], room=s['room'], previous_room=s['previous'], keys=s['keys'],
                        hp=s['hp'], doors=[p['kind'] for p in pair], door_detail=pair,
                        stones=stones.get('gems_suggested'), frames=len(shots)))
    return source, out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scans', type=Path, default=ROOT/'out/vod-hud-2026-10-23')
    p.add_argument('--hunt', type=Path, default=ROOT/'out/vod-hunt-2026-10-12')
    p.add_argument('--layouts', type=Path, default=ROOT/'out/vod-hunt-2026-10-12/layouts')
    p.add_argument('--art', type=Path, default=ROOT/'out/vod-art')
    p.add_argument('--output', type=Path, required=True);p.add_argument('--sources', nargs='*')
    p.add_argument('--rate', type=float, default=4.0, help='frames per second searched for the door screen')
    p.add_argument('--before', type=float, default=.5, help='seconds searched before the first reading of a room')
    p.add_argument('--after', type=float, default=1.5, help='seconds searched after it')
    p.add_argument('--limit', type=int, help='only the first N rooms per source (tests)')
    p.add_argument('--workers', type=int, default=12)
    a = p.parse_args()
    sources = a.sources or sorted(x.name for x in a.scans.iterdir() if x.is_dir())
    a.output.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as pool:
        futures = {pool.submit(census, s, str(a.scans), str(a.hunt), str(a.layouts), str(a.art), a.rate, a.before, a.after, a.limit, str(a.output/'layouts')): s for s in sources}
        for f in as_completed(futures):
            try:
                source, rows = f.result()
            except Exception as error:
                print(json.dumps({'source': futures[f], 'error': f'{type(error).__name__}: {error}'}), flush=True);continue
            if rows is None:
                print(json.dumps({'source': source, 'skipped': 'no layout'}), flush=True);continue
            (a.output/f'{source}.json').write_text(json.dumps(rows, indent=1))
            known = sum(all(d) for d in (r['doors'] for r in rows))
            print(json.dumps({'source': source, 'rooms': len(rows), 'complete_pairs': known}), flush=True)


if __name__ == '__main__':
    main()
