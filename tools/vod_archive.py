"""Archive whole public VODs at low resolution before the platform expires them.

Reads out/vod-hunt-2026-10-23/availability.json (from probe_all.py) and saves each
available Twitch VOD as <id>/full-<height>p.mp4 with a small provenance record.
Downloads resume; finished files are skipped. Full streams include non-dungeon
content: later scans decide which seconds are dungeon play.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def archive(row, out, height, ffmpeg):
    folder = out/row['id'];folder.mkdir(parents=True, exist_ok=True)
    target = folder/f'full-{height}p.mp4';record = folder/f'full-{height}p.json'
    if target.exists() and record.exists():
        return row['id'], 'present'
    started = time.time()
    command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-progress',
               '--concurrent-fragments', '8', '--ffmpeg-location', ffmpeg, '-f',
               # Some VODs skip mid resolutions (e.g. only 124p and 1080p): fall back to the next size up.
               f'best[height<={height}][height>=240]/best[height<=720]/best', '--merge-output-format', 'mp4',
               '--retries', '10', '--fragment-retries', '20', '--socket-timeout', '60',
               '-o', str(target), row['url']]
    with (folder/f'full-{height}p.download.log').open('a', encoding='utf-8') as log:
        code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
    if code or not target.exists():
        return row['id'], f'failed ({code})'
    record.write_text(json.dumps(dict(id=row['id'], url=row['url'], uploader=row.get('uploader'), height=height,
        bytes=target.stat().st_size, seconds=round(time.time()-started), duration=row.get('duration')), indent=1))
    return row['id'], 'saved'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--availability', type=Path, default=ROOT/'out/vod-hunt-2026-10-23/availability.json')
    p.add_argument('--output', type=Path, default=ROOT/'out/vod-hunt-2026-10-23')
    p.add_argument('--height', type=int, default=360);p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    rows = [r for r in json.loads(a.availability.read_text()) if r['ok'] and 'twitch.tv' in r['url']]
    rows.sort(key=lambda r: r['id'])  # oldest first: they expire first
    with ThreadPoolExecutor(a.workers) as pool:
        for ident, status in pool.map(lambda r: archive(r, a.output, a.height, ffmpeg), rows):
            print(json.dumps({'id': ident, 'status': status}), flush=True)


if __name__ == '__main__':
    main()
