"""Door choice and the room behind it, read automatically from full 360p VODs.

Each room counter change (vod_auto_offers.segments on the full-archive OCR series)
starts a door screen. One sequential decode per video reads 8 frames per second of
the game crop from just before that change until the next one (at most --span s):

* The door screen lasts until the entry fade, the first frame darker than 45% of
  the screen's brightness. Its doors are read as in the census.
* The chosen side is the mouse cursor (a saturated yellow arrow) on the last door
  frames before the fade: the click lands on the chosen door. No cursor, or a
  cursor between the doors, leaves the side unknown.
* The room behind it is read from frames after the fade with the client's
  encounter sprites (alpha-masked, scaled like the doors). A frame that matches no
  sprite stays unknown; monsters have too many sprites to template and are left to
  a later pass.
* Keys before and after come from the OCR series, so offers with a locked side can
  check the side reader: a key spent means the locked door was taken.

The crop comes from the stream's layout or, without one, from its HUD anchors:
across all 33 hand-made layouts the game crop is span/0.451 wide (span = HP text
minus room counter x), starts 0.052 crop widths left of the counter, has the
game's 1.40 aspect and puts the counter at 0.966 of its height.

Rows are unreviewed machine suggestions with their raw scores.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZE = (320, 224)
DOOR_CENTRES = (93, 238)  # door_search box centres in the 320x224 crop
DOOR_SCALE = 138/847  # door sprite raw height -> calibrated template height


def crop_from_anchor(a):
    span = a['hp_x']-a['room_x'];w = span/.451;h = w*16/9/1.40
    return [float(a['room_x']-.052*w), float(a['room_y']-.966*h), float(w), float(h)]


def layout_for(source, layouts, scans, out_folder, video=None):
    """The stream's layout with door calibration, or one derived from its HUD anchors."""
    from vod_auto_offers import completed_layout, DOOR_CALIBRATION
    from vod_key_reader import anchors, scan_tokens
    path = Path(layouts)/f'{source}.json'
    if path.exists():
        filled = completed_layout(path, out_folder)
        if filled:return json.loads(Path(filled).read_text()), Path(filled)
    derived = Path(out_folder)/f'{source}.json'
    if derived.exists():return json.loads(derived.read_text()), derived
    a = anchors(scan_tokens(Path(scans)/source)) if any((Path(scans)/source).glob('*.done')) else None
    if a is None and video:
        # No finished OCR series: read the HUD on frames spread over the video.
        from vod_key_reader import video_tokens, whole_video
        clips = whole_video(video);end = clips[0][1]
        a = anchors(video_tokens(clips, [round(end*(k+.5)/240, 2) for k in range(240)]))
    if a is None:return None, None
    layout = dict(schema=1, source_id=source, crop=crop_from_anchor(a), **DOOR_CALIBRATION,
                  calibration='derived from HUD anchors', anchor={k: float(v) for k, v in a.items()})
    target = Path(out_folder)/f'{source}.json';target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(layout, indent=1))
    return layout, target


def windows_frames(video, windows, fps, layout):
    """Yield (index, [(t, crop)]) per window in one sequential decode."""
    import cv2
    x, y, w, h = layout['crop']
    cap = cv2.VideoCapture(str(video));rate = cap.get(cv2.CAP_PROP_FPS) or 30
    frame_no = 0
    for index, (begin, end) in enumerate(windows):
        wanted = [begin+k/fps for k in range(int((end-begin)*fps)+1)]
        got = []
        for t in wanted:
            target = int(round(t*rate))
            if target < frame_no:continue
            if target-frame_no > 10*rate:
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target-int(2*rate)));frame_no = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            while frame_no < target:
                if not cap.grab():break
                frame_no += 1
            ok, frame = cap.read();frame_no += 1
            if not ok:break
            H, W = frame.shape[:2]
            part = frame[max(0, int(y*H)):int((y+h)*H), max(0, int(x*W)):int((x+w)*W)]
            got.append((round(t, 3), cv2.resize(part, SIZE, interpolation=cv2.INTER_AREA)))
        yield index, got
    cap.release()


class CursorReader:
    """The yellow arrow cursor: a saturated yellow blob about 2-3.5x wider than tall."""
    def __init__(self):
        import cv2, numpy as np
        self.cv, self.np = cv2, np

    def find(self, img):
        cv, np = self.cv, self.np
        hsv = cv.cvtColor(img, cv.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 0] >= 20) & (hsv[:, :, 0] <= 36) & (hsv[:, :, 1] >= 150) & (hsv[:, :, 2] >= 200)).astype(np.uint8)
        mask[:18] = 0;mask[205:] = 0
        n, labels, stats, _ = cv.connectedComponentsWithStats(mask)
        found = []
        for i in range(1, n):
            X, Y, W, H, A = stats[i]
            if 10 <= W <= 34 and 4 <= H <= 14 and 1.8*H <= W <= 4*H and .3 <= A/(W*H) <= .8:
                found.append((int(X+W/2), int(Y+H/2)))
        return found[0] if len(found) == 1 else None


class RoomReader:
    """Best alpha-masked encounter sprite in a frame, scaled like the doors.

    Matching runs at half resolution (160x112) to keep a full archive affordable."""
    def __init__(self, art, scales=(.9, 1, 1.12)):
        import cv2, numpy as np
        self.cv, self.np = cv2, np;self.templates = []
        manifest = json.loads((Path(art)/'manifest.json').read_text(encoding='utf-8'))['assets']
        for key, filename in manifest.items():
            if not key.startswith('encounter.'):continue
            raw = cv2.imread(str(Path(art)/filename), cv2.IMREAD_UNCHANGED)
            x, y, w, h = cv2.boundingRect((raw[:, :, 3] > 180).astype('uint8'));raw = raw[y:y+h, x:x+w]
            for s in scales:
                hh = round(raw.shape[0]*DOOR_SCALE*s/2);ww = round(raw.shape[1]*DOOR_SCALE*s/2)
                if hh < 8 or ww < 8 or hh > SIZE[1]//2 or ww > SIZE[0]//2:continue
                t = cv2.resize(raw, (ww, hh), interpolation=cv2.INTER_AREA)
                mask = (t[:, :, 3] > 230).astype('uint8')
                if mask.sum() >= 20:self.templates.append((key.split('.', 1)[1], t[:, :, :3], mask))

    def read(self, img):
        cv, np = self.cv, self.np;best = {}
        img = cv.resize(img, (SIZE[0]//2, SIZE[1]//2), interpolation=cv.INTER_AREA)
        for name, t, mask in self.templates:
            r = np.nan_to_num(cv.matchTemplate(img, t, cv.TM_CCOEFF_NORMED, mask=mask), nan=-1, posinf=-1, neginf=-1)
            best[name] = max(best.get(name, -1), float(r.max()))
        return best


def label_room(score_list, threshold, margin):
    """Mean score per class over the frames; the best class if clear, else None."""
    if not score_list:return None, {}
    mean = {k: sum(s[k] for s in score_list)/len(score_list) for k in score_list[0]}
    ranked = sorted(mean.items(), key=lambda kv: -kv[1])
    top, second = ranked[0], ranked[1]
    accept = top[1] >= threshold and top[1]-second[1] >= margin
    return (top[0] if accept else None), dict(ranked[:4])


def door_screen(frames, doors, lo, hi):
    """First door screen in [lo, hi]. A cheap unmasked pass at 4 frames per second
    finds frames with a door; up to four of them are then read with the masked
    templates and must agree (stable_doors)."""
    from sfld_vod.door_recognition import stable_doors
    screen = [i for i, (t, f) in enumerate(frames) if lo <= t <= hi and i % 2 == 0 and f.mean() > 25
              and any(p['kind'] for p in doors.doors(f, selection_only=True))]
    if not screen:return None
    run_idx = [screen[0]]
    for i in screen[1:]:
        if frames[i][0]-frames[run_idx[-1]][0] > 1.5:break
        run_idx.append(i)
    pick = run_idx[::max(1, len(run_idx)//4)][:4]
    hits = [(i, doors.doors(frames[i][1])) for i in pick]
    hits = [(i, r) for i, r in hits if any(p['kind'] for p in r)]
    if not hits:return None
    return run_idx[0], run_idx[-1], stable_doors([r for _, r in hits]) if len(hits) >= 2 else None


def analyse(anchor, frames, doors, cursor, rooms, args, before=2.5, after=1.5):
    """Door screen near the anchor time, the click side, and the room after the fade."""
    import numpy as np
    row = dict()
    if len(frames) < 4:return dict(row, status='no_frames')
    found = door_screen(frames, doors, anchor-before, anchor+after)
    if found is None:return dict(row, status='no_door_screen')
    first, last, pair = found
    row.update(door_t=frames[first][0], doors=[p['kind'] for p in pair] if pair else [None, None])
    bright = [float(f.mean()) for _, f in frames]
    ref = float(np.median(bright[first:last+1]))
    fade = next((i for i in range(last+1, len(frames)) if bright[i] < .45*ref), None)
    if fade is None:return dict(row, status='no_entry_fade')
    row.update(fade_t=frames[fade][0], door_seconds=round(frames[fade][0]-frames[first][0], 2))
    side = None;cursor_at = None
    for t, f in reversed(frames[first:fade]):
        if t < frames[fade][0]-1:break
        c = cursor.find(f)
        if c:
            cursor_at = [t, c[0], c[1]]
            d = [abs(c[0]-x) for x in DOOR_CENTRES]
            if min(d) <= 60:side = int(np.argmin(d))
            break
    row.update(side=side, cursor=cursor_at)
    after_frames = [(t, f) for (t, f), b in zip(frames[fade:], bright[fade:]) if frames[fade][0]+.4 <= t <= frames[fade][0]+3 and b >= .5*ref]
    scores = [rooms.read(f) for _, f in after_frames[2::4][:2]]
    label, top = label_room(scores, args.room_threshold, args.room_margin)
    row.update(content=label, content_scores={k: round(v, 3) for k, v in top.items()}, content_frames=len(scores), status='ok')
    return row


def run_source(source, args):
    import sys
    sys.path.insert(0, str(ROOT/'tools'))
    import cv2
    cv2.setNumThreads(1)
    from vod_auto_offers import series, segments
    from sfld_vod.door_recognition import MaskedDoorRecognizer
    video = Path(args.videos)/source/'full-360p.mp4'
    if not video.exists():return source, None, 'no video'
    layout, layout_path = layout_for(source, args.layouts, args.scans, Path(args.output)/'layouts')
    if layout is None:return source, None, 'no layout or HUD anchors'
    reads, _ = series(Path(args.scans)/source)
    segs = segments(reads)[:args.limit]
    if not segs:return source, None, 'no room segments'
    doors = MaskedDoorRecognizer(layout_path, args.art);cursor = CursorReader();rooms = RoomReader(args.art)
    # The counter is sampled every 2 s, so the door screen of room N can start up to
    # 2.5 s before its first reading; windows run to that point of the next room.
    windows = []
    for i, s in enumerate(segs):
        end = segs[i+1]['t']-2.5 if i+1 < len(segs) else s['t']+args.span
        windows.append((max(0, s['t']-2.5), max(s['t'], min(end, s['t']+args.span))))
    out = []
    for i, frames in windows_frames(video, windows, args.fps, layout):
        s, nxt = segs[i], (segs[i+1] if i+1 < len(segs) else None)
        out.append(dict(source=source, t=s['t'], room=s['room'], keys_before=s['keys'], keys_next=nxt['keys'] if nxt else None,
                        hp=s['hp'], hp_next=nxt['hp'] if nxt else None, **analyse(s['t'], frames, doors, cursor, rooms, args)))
    return source, out, None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--scans', default=str(ROOT/'out/vod-hud-full-2026-10-23'))
    p.add_argument('--videos', default=str(ROOT/'out/vod-hunt-2026-10-23'))
    p.add_argument('--layouts', default=str(ROOT/'out/vod-hunt-2026-10-12/layouts'))
    p.add_argument('--art', default=str(ROOT/'out/vod-art'))
    p.add_argument('--output', type=Path, required=True);p.add_argument('--sources', nargs='*')
    p.add_argument('--fps', type=float, default=8);p.add_argument('--span', type=float, default=45)
    p.add_argument('--room-threshold', type=float, default=.55);p.add_argument('--room-margin', type=float, default=.05)
    p.add_argument('--limit', type=int, help='only the first N rooms per source (tests)')
    p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    sources = a.sources or sorted(d.name for d in Path(a.scans).iterdir() if (d/'000000.tokens.jsonl.done').exists())
    sources = [s for s in sources if not (a.output/f'{s}.json').exists()]
    with ProcessPoolExecutor(a.workers) as pool:
        futures = {pool.submit(run_source, s, a): s for s in sources}
        for f in as_completed(futures):
            try:
                source, rows, skipped = f.result()
            except Exception as error:
                print(json.dumps({'source': futures[f], 'error': f'{type(error).__name__}: {error}'}), flush=True);continue
            if rows is None:
                print(json.dumps({'source': source, 'skipped': skipped}), flush=True);continue
            (a.output/f'{source}.json').write_text(json.dumps(rows, indent=1))
            ok = [r for r in rows if r.get('status') == 'ok']
            print(json.dumps({'source': source, 'rooms': len(rows), 'entered': len(ok), 'side_read': sum(r['side'] is not None for r in ok),
                              'content_read': sum(r['content'] is not None for r in ok), 'status': Counter(r['status'] for r in rows)}), flush=True)


if __name__ == '__main__':
    main()
