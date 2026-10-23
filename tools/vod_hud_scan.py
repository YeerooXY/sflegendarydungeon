"""OCR every text token on sampled VOD frames, once, for later dungeon measurements.

Each video is decoded by ffmpeg at --every seconds and --height pixels, and every
token rapidocr finds is kept with its centre, height (as fractions of the frame)
and score. Nothing is interpreted here: HP, keys, shop prices and room counters
are parsed from these series later, so the expensive pass never needs repeating.
Output: <output>/<source>/<clip-start>.tokens.jsonl.gz plus a .done marker.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def jobs(sources):
    """(source id, video path, source seconds at clip start) for every saved video."""
    found = []
    for folder in sources:
        folder = Path(folder)
        for marker in sorted(folder.glob('720p/*.json')):
            video = marker.with_suffix('.mp4')
            if video.exists() and not video.with_suffix('.mp4.part').exists():
                found.append((folder.name, str(video), float(json.loads(marker.read_text())['source_start'])))
        for video in sorted(folder.glob('full-*p.mp4')):
            if video.with_suffix('.json').exists():
                found.append((folder.name, str(video), 0.0))
    return found


def scan(source, video, start, output, every, height):
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    import numpy as np
    from rapidocr import RapidOCR
    import imageio_ffmpeg
    target = Path(output)/source/f'{int(start):06d}.tokens.jsonl.gz';done = target.with_suffix('.done')
    if done.exists():
        return source, video, 'present'
    target.parent.mkdir(parents=True, exist_ok=True)
    engine = RapidOCR(params={'Global.log_level': 'error', 'EngineConfig.onnxruntime.intra_op_num_threads': 1,
                              'EngineConfig.onnxruntime.inter_op_num_threads': 1})
    probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-i', video], capture_output=True, text=True).stderr
    import re
    w, h = map(int, re.search(r'Video:.*?(\d{3,5})x(\d{3,5})', probe).groups())
    width = int(round(w*height/h/2))*2
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error', '-i', video, '-vf',
               f'fps=1/{every},scale={width}:{height}', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    size = width*height*3;index = 0;samples = 0
    with gzip.open(target, 'wt', encoding='utf-8') as stream:
        while True:
            raw = process.stdout.read(size)
            if len(raw) < size:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
            result = engine(frame);tokens = []
            for box, text, score in zip(result.boxes if result.boxes is not None else [], result.txts or [], result.scores or []):
                xs = [p[0] for p in box];ys = [p[1] for p in box]
                tokens.append([text, round(float(sum(xs))/4/width, 4), round(float(sum(ys))/4/height, 4),
                               round(float(max(ys)-min(ys))/height, 4), round(float(score), 3)])
            stream.write(json.dumps({'t': round(start+index*every, 2), 'tokens': tokens}, ensure_ascii=False)+'\n')
            index += 1;samples += 1
    process.wait()
    done.write_text(json.dumps({'video': video, 'samples': samples, 'every': every, 'height': height}))
    return source, video, f'scanned {samples}'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('sources', nargs='+', type=Path, help='source folders (720p clips or full-*p.mp4)')
    p.add_argument('--output', type=Path, default=ROOT/'out/vod-hud-2026-10-23')
    p.add_argument('--every', type=float, default=2.0);p.add_argument('--height', type=int, default=360)
    p.add_argument('--workers', type=int, default=12)
    a = p.parse_args()
    todo = jobs(a.sources)
    print(json.dumps({'videos': len(todo)}), flush=True)
    with ProcessPoolExecutor(a.workers) as pool:
        futures = [pool.submit(scan, s, v, t, str(a.output), a.every, a.height) for s, v, t in todo]
        for f in as_completed(futures):
            try:
                print(json.dumps(dict(zip(('source', 'video', 'status'), f.result()))), flush=True)
            except Exception as error:
                print(json.dumps({'error': f'{type(error).__name__}: {error}'}), flush=True)


if __name__ == '__main__':
    main()
