"""Read the key counter ("Nx") at chosen source seconds from a saved VOD.

Whole-frame OCR at 360p misses the small key counter. Its position is fixed
relative to two HUD texts that read well: the room counter ("N/100", bottom left)
and the HP text (bottom centre). Across streams the key counter sits at
room_x + 2.03*(hp_x-room_x), slightly above the room counter's line. That spot is
cropped, upscaled and read on its own.

Anchors come from the stream's OCR series (vod_hud_scan.py). Output rows:
{t, keys} with keys None when the crop does not read as "Nx".
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re
from statistics import median

ROOM = re.compile(r'^\s*(\d{1,3})\s*/\s*100\s*$')
HP = re.compile(r'^\s*\d{1,3}(?:[.,\s]\d{3})+\s*/\s*\d{1,3}(?:[.,\s]\d{3})+\s*$')
KEY = re.compile(r'^\s*(\d{1,2})\s*[xX×]\s*$')


def scan_tokens(scan_folder):
    for path in sorted(Path(scan_folder).glob('*.tokens.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                yield json.loads(line)['tokens']


def whole_video(video):
    """A full VOD as a one-clip list for frames_at."""
    import cv2
    cap = cv2.VideoCapture(str(video));duration = cap.get(cv2.CAP_PROP_FRAME_COUNT)/(cap.get(cv2.CAP_PROP_FPS) or 30);cap.release()
    return [(0.0, duration, str(video))]


def video_tokens(clips, times):
    """OCR tokens (vod_hud_scan.py format) of whole frames at the given source seconds."""
    from rapidocr import RapidOCR
    from vod_auto_offers import frames_at
    engine = RapidOCR(params={'Global.log_level': 'error'})
    for frame in frames_at(clips, times).values():
        h, w = frame.shape[:2];result = engine(frame);tokens = []
        for box, text, score in zip(result.boxes if result.boxes is not None else [], result.txts or [], result.scores or []):
            xs = [q[0] for q in box];ys = [q[1] for q in box]
            tokens.append([text, sum(xs)/4/w, sum(ys)/4/h, (max(ys)-min(ys))/h, float(score)])
        yield tokens


def anchors(token_rows):
    """Median room-counter and HP positions (frame fractions) and room text height."""
    rooms, hps = [], []
    for tokens in token_rows:
        ro = [t for t in tokens if ROOM.match(t[0]) and t[4] >= .8]
        hp = [t for t in tokens if HP.match(t[0]) and t[4] >= .8]
        if ro and hp:rooms.append(ro[0]);hps.append(hp[0])
    if len(rooms) < 20:return None
    # The most common room position excludes "/100" texts elsewhere on screen.
    (px, py), _ = Counter((round(t[1], 1), round(t[2], 1)) for t in rooms).most_common(1)[0]
    keep = [(r, h) for r, h in zip(rooms, hps) if abs(r[1]-px) < .06 and abs(r[2]-py) < .06]
    return dict(room_x=median(r[1] for r, _ in keep), room_y=median(r[2] for r, _ in keep),
                hp_x=median(h[1] for _, h in keep), height=median(r[3] for r, _ in keep), samples=len(keep))


def key_box(anchor, width, height):
    span = anchor['hp_x']-anchor['room_x']
    cx = anchor['room_x']+2.03*span;cy = anchor['room_y']-.043*span*width/height
    half_w = .09*span;half_h = 1.6*anchor['height']
    x0, x1 = int((cx-half_w)*width), int((cx+half_w)*width)
    y0, y1 = int((cy-half_h)*height), int((cy+half_h)*height)
    return max(0, x0), max(0, y0), min(width, x1), min(height, y1)


def effect_box(anchor, width, height):
    """The active blessing/curse icons sit in a row just above the HP text."""
    span = anchor['hp_x']-anchor['room_x']
    cy = anchor['room_y']-.11*span*width/height;half_h = .09*span*width/height
    x0, x1 = anchor['hp_x']-.47*span, anchor['hp_x']+.47*span
    return (max(0, int(x0*width)), max(0, int((cy-half_h)*height)), min(width, int(x1*width)), min(height, int((cy+half_h)*height)))


class EffectReader:
    """Template-match the client's effect icons (out/vod-art effect.*) in the icon row.

    Icons are about 0.096 room-to-HP spans tall. A name counts at score >= threshold;
    a miss is not proof of absence (icons can be covered or scrolled away).
    """
    def __init__(self, art, threshold=.75):
        import cv2
        self.cv = cv2;self.threshold = threshold;self.templates = []
        manifest = json.loads((Path(art)/'manifest.json').read_text(encoding='utf-8'))['assets']
        for key, filename in manifest.items():
            if not key.startswith('effect.'):continue
            raw = cv2.imread(str(Path(art)/filename), cv2.IMREAD_UNCHANGED)
            x, y, w, h = cv2.boundingRect((raw[:, :, 3] > 200).astype('uint8'))
            self.templates.append((key.split('.', 1)[1], raw[y:y+h, x:x+w]))

    def read(self, frame, anchor):
        cv = self.cv
        import numpy as np
        x0, y0, x1, y1 = effect_box(anchor, frame.shape[1], frame.shape[0])
        strip = frame[y0:y1, x0:x1];span = (anchor['hp_x']-anchor['room_x'])*frame.shape[1]
        found = {}
        for name, raw in self.templates:
            best = -1.0
            for scale in (.08, .088, .096, .104, .112):
                hh = max(8, round(scale*span));t = cv.resize(raw, (max(2, round(raw.shape[1]*hh/raw.shape[0])), hh), interpolation=cv.INTER_AREA)
                if t.shape[0] > strip.shape[0] or t.shape[1] > strip.shape[1]:continue
                r = cv.matchTemplate(strip, t[:, :, :3], cv.TM_CCOEFF_NORMED, mask=(t[:, :, 3] > 200).astype('uint8'))
                best = max(best, float(np.nan_to_num(r, nan=-1, posinf=-1, neginf=-1).max()))
            found[name] = round(best, 3)
        return dict(active=sorted(n for n, s in found.items() if s >= self.threshold), scores=found)


def read_keys(clips, times, anchor, engine=None):
    """Key count at each source second; clips as (start, end, path) like frames_at."""
    import cv2
    from vod_auto_offers import frames_at
    if engine is None:
        from rapidocr import RapidOCR
        engine = RapidOCR(params={'Global.log_level': 'error'})
    frames = frames_at(clips, times)
    out = []
    for t in times:
        frame = frames.get(t)
        if frame is None:out.append(dict(t=t, keys=None));continue
        x0, y0, x1, y1 = key_box(anchor, frame.shape[1], frame.shape[0])
        crop = cv2.resize(frame[y0:y1, x0:x1], None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        result = engine(crop);found = None
        for text, score in zip(result.txts or [], result.scores or []):
            m = KEY.match(text)
            if m and score >= .7:found = int(m.group(1))
        out.append(dict(t=t, keys=found))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('video', type=Path);p.add_argument('scan', type=Path, help='OCR series folder of this source')
    p.add_argument('--times', type=float, nargs='+', required=True)
    a = p.parse_args()
    anchor = anchors(scan_tokens(a.scan))
    print(json.dumps(dict(anchor=anchor, reads=read_keys(whole_video(a.video), a.times, anchor) if anchor else [])))


if __name__ == '__main__':
    main()
