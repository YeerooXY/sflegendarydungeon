"""Collect every door screen in a full 360p VOD and save a review sheet for it.

One sequential decode per video at --fps frames per second of the game crop.
Rooms are separated by dark fades. After each fade, the first frames are checked
for doors (cheap unmasked pass); a positive check opens a door point that lasts
until the next fade, which is the entry into the chosen room. Per point:

* a review sheet (JPEG, 960x448): the first and the last door frame (the cursor
  sits on the clicked door), then frames 0.4, 0.8, 1.4 and 2.5 s after the fade,
  labelled. A reviewer (vod_door_review.py) reads the doors, the chosen side and
  the room contents from it. The unmasked door pass is kept as doors_hint.
* repeated screens: a door frame nearly identical to an earlier point's within
  --repeat-window seconds, HUD strip (room counter, HP) included, is marked
  repeat_of that point: the same offer seen again after a menu, a shop or a death.
  Different rooms with the same door pair differ in the counter or the HP.

The crop comes from the stream layout or its HUD anchors (vod_room_outcomes).
The room counter, when the OCR series has it, is attached for reference only.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZE = (320, 224)
AFTER = (.4, .8, 1.4, 2.5)


def scan(video, layout, layout_path, art, fps, out_dir, repeat_window, dark_ratio=.5, start=0, end=None):
    import cv2, numpy as np
    from sfld_vod.door_recognition import MaskedDoorRecognizer
    doors = MaskedDoorRecognizer(layout_path, art)
    x, y, w, h = layout['crop']
    cap = cv2.VideoCapture(str(video));rate = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(rate/fps)));frame_no = 0
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(start*rate));frame_no = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
    bright_ref = deque(maxlen=int(60*fps))
    points = [];open_point = None;pending = [];dark = True;since_fade = None
    thumbs = []  # (t, thumbnail, index) of door points, for repeat detection
    out_dir.mkdir(parents=True, exist_ok=True)

    def close(point, t_fade):
        first = point.pop('first_frame');last = point.pop('last_frame')
        point.update(fade_t=round(t_fade, 3), door_seconds=round(t_fade-point['t'], 2),
                     doors_hint=[p['kind'] for p in doors.doors(first, selection_only=True)])
        gray = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY).astype('float32')
        thumb = cv2.resize(gray[:190], (32, 19), interpolation=cv2.INTER_AREA);hud = gray[196:]
        for t0, th, hd, idx in reversed(thumbs):
            if point['t']-t0 > repeat_window:break
            if float(np.abs(th-thumb).mean()) < 5 and float(np.abs(hd-hud).mean()) < 4:
                point['repeat_of'] = idx;break
        thumbs.append((point['t'], thumb, hud, point['index']))
        point['sheet_tiles'] = [first, last];point['tiles_due'] = [t_fade+a for a in AFTER]
        return point

    def finish_sheet(point):
        tiles = point.pop('sheet_tiles');point.pop('tiles_due', None)
        while len(tiles) < 2+len(AFTER):tiles.append(np.zeros((SIZE[1], SIZE[0], 3), np.uint8))
        labels = ['DOOR SCREEN (first frame)', 'DOOR SCREEN (last frame, at the click)']+[f'+{a:g} s after entering' for a in AFTER]
        for img, label in zip(tiles, labels):
            cv2.rectangle(img, (0, 0), (SIZE[0], 14), (0, 0, 0), -1)
            cv2.putText(img, label, (3, 11), cv2.FONT_HERSHEY_SIMPLEX, .38, (255, 255, 255), 1)
        sheet = np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:6])])
        name = f"{point['index']:05d}.jpg";cv2.imwrite(str(out_dir/name), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        point['sheet'] = name

    while True:
        ok = cap.grab()
        if not ok:break
        frame_no += 1
        if frame_no % step:continue
        ok, frame = cap.retrieve()
        if not ok:break
        t = frame_no/rate;H, W = frame.shape[:2]
        if end is not None and t > end:break
        crop = cv2.resize(frame[max(0, int(y*H)):int((y+h)*H), max(0, int(x*W)):int((x+w)*W)], SIZE, interpolation=cv2.INTER_AREA)
        b = float(crop.mean())
        ref = float(np.median(bright_ref)) if len(bright_ref) >= 10 else 50.
        is_dark = b < dark_ratio*ref
        if not is_dark:bright_ref.append(b)
        # Frames after a fade for points already closed.
        for p in list(pending):
            while p['tiles_due'] and t >= p['tiles_due'][0]:
                p['sheet_tiles'].append(crop.copy());p['tiles_due'].pop(0)
            if not p['tiles_due']:finish_sheet(p);pending.remove(p)
        if is_dark and not dark:
            # A fade begins: it ends the open door screen (the entry).
            if open_point is not None:
                pending.append(close(open_point, t));points.append(open_point);open_point = None
            since_fade = None
        elif not is_dark and dark:
            since_fade = t
        dark = is_dark
        if not is_dark and open_point is None and since_fade is not None and t-since_fade <= .6:
            if any(p['kind'] for p in doors.doors(crop, selection_only=True)):
                open_point = dict(index=len(points), t=round(t, 3), first_frame=crop, last_frame=crop)
                since_fade = None
        if open_point is not None:
            open_point['last_frame'] = crop
    cap.release()
    for p in pending:finish_sheet(p)
    return points


def run_source(source, args):
    import sys
    sys.path.insert(0, str(ROOT/'tools'))
    import cv2
    cv2.setNumThreads(1)
    from vod_room_outcomes import layout_for
    video = Path(args.videos)/source/'full-360p.mp4'
    if not video.exists():return source, None, 'no video'
    layout, layout_path = layout_for(source, args.layouts, args.scans, Path(args.output)/'layouts', video)
    if layout is None:return source, None, 'no layout or HUD anchors'
    points = scan(video, layout, layout_path, args.art, args.fps, Path(args.output)/source, args.repeat_window,
                  start=args.start, end=args.end)
    # Room counter at the door screen, from the OCR series when it has one.
    try:
        from vod_auto_offers import series
        reads, _ = series(Path(args.scans)/source)
        valued = [(r['t'], r['room']) for r in reads if r['room'] is not None]
        for p in points:
            near = [room for t, room in valued if p['t']-1 <= t <= p['fade_t']+1]
            p['room'] = max(set(near), key=near.count) if near else None
    except Exception:
        pass
    return source, points, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--scans', default=str(ROOT/'out/vod-hud-full-2026-10-23'))
    p.add_argument('--videos', default=str(ROOT/'out/vod-hunt-2026-10-23'))
    p.add_argument('--layouts', default=str(ROOT/'out/vod-hunt-2026-10-12/layouts'))
    p.add_argument('--art', default=str(ROOT/'out/vod-art'))
    p.add_argument('--output', type=Path, required=True);p.add_argument('--sources', nargs='*')
    p.add_argument('--fps', type=float, default=8)
    p.add_argument('--repeat-window', type=float, default=180)
    p.add_argument('--start', type=float, default=0);p.add_argument('--end', type=float, help='tests: stop at this second')
    p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    sources = a.sources or sorted(d.name for d in Path(a.videos).iterdir() if (d/'full-360p.mp4').exists())
    sources = [s for s in sources if not (a.output/f'{s}.json').exists()]
    with ProcessPoolExecutor(a.workers) as pool:
        futures = {pool.submit(run_source, s, a): s for s in sources}
        for f in as_completed(futures):
            try:
                source, points, skipped = f.result()
            except Exception as error:
                print(json.dumps({'source': futures[f], 'error': f'{type(error).__name__}: {error}'}), flush=True);continue
            if points is None:
                print(json.dumps({'source': source, 'skipped': skipped}), flush=True);continue
            (a.output/f'{source}.json').write_text(json.dumps(points, indent=1))
            print(json.dumps({'source': source, 'door_points': len(points), 'repeats': sum('repeat_of' in q for q in points),
                              'with_door_hint': sum(any(q['doors_hint']) for q in points)}), flush=True)


if __name__ == '__main__':
    main()
