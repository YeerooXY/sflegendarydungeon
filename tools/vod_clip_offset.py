"""Measure the time offset between a saved clip and the same stream's full VOD.

Clips cut by yt-dlp from Twitch start on a segment boundary, so a clip's nominal
source_start can differ from the true one by up to a segment (10 s). A frame from
the clip, downscaled to the full VOD's size, is compared against every full-VOD
frame within +-search seconds; the best match gives full_time - clip_time.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def match(clip, clip_start, full, at, search):
    """Best and runner-up (outside +-1 s of the best) full_time - clip_time for one clip frame."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(clip));cap.set(cv2.CAP_PROP_POS_MSEC, at*1000);ok, frame = cap.read();cap.release()
    if not ok:return None
    target = None;scores = []
    video = cv2.VideoCapture(str(full));fps = video.get(cv2.CAP_PROP_FPS) or 30
    video.set(cv2.CAP_PROP_POS_MSEC, max(0, clip_start+at-search)*1000);base = video.get(cv2.CAP_PROP_POS_MSEC)/1000
    for j in range(int(2*search*fps)):
        ok, f = video.read()
        if not ok:break
        if target is None:target = cv2.resize(frame, (f.shape[1], f.shape[0])).astype(np.int16)
        scores.append((float(np.abs(f.astype(np.int16)-target).mean()), base+j/fps-(clip_start+at)))
    video.release()
    if not scores:return None
    best = min(scores);rival = min((s for s in scores if abs(s[1]-best[1]) > 1), default=(float('inf'), None))
    return best[1], best[0], rival[0]


def offset(clip, clip_start, full, points=(20, 140, 260, 380, 500), search=30.0):
    """Median offset over frames whose match is clearly better than any other moment."""
    found = []
    for at in points:
        m = match(clip, clip_start, full, at, search)
        if m and (m[1] < 1 or m[2] > 1.15*m[1]):found.append(m[0])
    if not found:return None
    found.sort();middle = found[len(found)//2]
    agree = [f for f in found if abs(f-middle) < .15]
    if len(agree) < 2:return None  # one match alone can be a repeated screen
    return dict(offset=round(middle, 3), points=len(found), agreeing=len(agree))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('full', type=Path);p.add_argument('clips', type=Path, nargs='+', help='clip .mp4 with a .json marker (source_start)')
    a = p.parse_args()
    for clip in a.clips:
        start = json.loads(clip.with_suffix('.json').read_text())['source_start'] if clip.with_suffix('.json').exists() else float(clip.stem.split('-')[0])
        print(json.dumps(dict(clip=str(clip), source_start=start, **(offset(clip, start, a.full) or {}))), flush=True)


if __name__ == '__main__':
    main()
