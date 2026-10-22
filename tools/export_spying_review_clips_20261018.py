"""Export short, locally cached native-resolution footage for manual review."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import subprocess
import cv2

from sfld_vod.batch import select_clips

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/spying-key-audit-2026-10-18/review-clips'
JOBS = [
    ('maexel-one-hit-key-outcomes', '2878297081', 7720.5, 7737.5),
    ('n3-room36-zero-key-kill', '2885348704', 13063.5, 13070.5),
    ('n3-room82-zero-key-kill', '2885348704', 13451.5, 13459.5),
    ('lootenant-deceit-failed-escape', '2878439002', 4787.5, 4793.5),
]


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def export(job):
    label, sid, start, end = job
    folder = ROOT / 'out/vod-hunt-2026-10-12' / sid / '720p'
    matches = [c for _, c in select_clips(folder)
               if c['source_start'] <= start and c['source_end'] >= end]
    if len(matches) != 1:
        raise ValueError(f'{label}: expected one covering clip, found {len(matches)}')
    clip = matches[0]
    original = folder / clip['file']
    original_sha = sha(original)
    assert original_sha == clip['sha256'], f'{label}: original hash mismatch'
    target = OUT / f'{label}.mp4'
    args = [str(ROOT / 'out/vod-tools/ffmpeg.exe'), '-hide_banner', '-loglevel', 'error',
            '-y', '-ss', str(start - clip['source_start']), '-i', str(original),
            '-t', str(end - start), '-map', '0:v:0', '-an', '-c:v', 'libx264',
            '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart', str(target)]
    subprocess.run(args, check=True, capture_output=True)
    capture = cv2.VideoCapture(str(target))
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = capture.get(cv2.CAP_PROP_FPS)
    first_ok, first = capture.read()
    capture.set(cv2.CAP_PROP_POS_FRAMES, frames - 1)
    last_ok, _ = capture.read()
    capture.release()
    assert first_ok and last_ok and fps > 0
    assert abs(frames / fps - (end - start)) < .1
    decoded = {'first_frame': first_ok, 'last_frame': last_ok, 'frames': frames,
               'fps': fps, 'duration': frames / fps,
               'width': first.shape[1], 'height': first.shape[0]}
    return {'label': label, 'source_id': sid, 'source_url': clip['url'],
            'source_start': start, 'source_end': end,
            'original': original.relative_to(ROOT).as_posix(),
            'original_sha256': original_sha,
            'file': target.relative_to(ROOT).as_posix(), 'sha256': sha(target),
            'processing': 'Native resolution and frame rate; H.264 CRF18; audio omitted.',
            'timing': clip.get('timing'), 'transport_warning': clip.get('transport_warning'),
            'decode_verification': decoded}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(export, JOBS))
    (OUT / 'manifest.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'clips': len(rows), 'manifest': str(OUT / 'manifest.json')}))


if __name__ == '__main__':
    main()
