"""Acquire bounded public Twitch HLS sections without ffmpeg's remote seek.

Some fMP4 VODs decode correctly as init+fragments but fail during remote HLS
seeking. Retain source segment hashes and remux locally. No access URLs, cookies,
or extractor tokens are persisted. This utility does not support encrypted HLS.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urljoin

from sfld_vod.media import chunks, ffmpeg_path, load_sources, probe, sha256, video_info, write_json


def playlist(text, base):
    elapsed = 0.0
    duration = None
    initialization = None
    segments = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXT-X-KEY:') and 'METHOD=NONE' not in line:
            raise ValueError('Encrypted playlists are unsupported')
        if line.startswith(('#EXT-X-BYTERANGE:', '#EXT-X-DISCONTINUITY')):
            raise ValueError('Byte ranges and discontinuities require separate review')
        if line.startswith('#EXT-X-MAP:'):
            match = re.search(r'URI="([^"]+)"', line)
            if not match or initialization is not None:
                raise ValueError('Expected one initialization segment')
            initialization = urljoin(base, match.group(1))
        elif line.startswith('#EXTINF:'):
            duration = float(line.split(':', 1)[1].split(',')[0])
        elif line and not line.startswith('#'):
            if duration is None or not math.isfinite(duration) or duration <= 0:
                raise ValueError('Missing segment duration')
            segments.append({'index': len(segments), 'start': elapsed,
                             'end': elapsed+duration, 'url': urljoin(base, line)})
            elapsed += duration
            duration = None
    if not segments:
        raise ValueError('Expected media segments')
    return initialization, segments


def acquire_source(source, root, workers=4, height=720):
    from yt_dlp import YoutubeDL
    meta = probe(source, root)
    folder = root/source['id']/f'{height}p'
    folder.mkdir(parents=True, exist_ok=True)
    fragment_folder = root/source['id']/'public-fragments'
    fragment_folder.mkdir(exist_ok=True)
    ffmpeg = ffmpeg_path(root)
    with YoutubeDL({'quiet': True, 'no_warnings': True, 'socket_timeout': 20,
                    'extractor_retries': 1, 'noplaylist': True}) as downloader:
        info = downloader.extract_info(source['url'], download=False)
        available = [f for f in info['formats'] if f.get('height', 0)
                     and f.get('protocol', '').startswith('m3u8')]
        formats = [f for f in available if f['height'] <= height]
        if not available:
            raise ValueError('No public HLS video format')
        # Some small channels retain only their original 1080p stream.
        # Preserve native resolution; the marker records actual dimensions.
        fmt = (max(formats, key=lambda f: (f['height'], f.get('fps', 0))) if formats
               else min(available, key=lambda f: (f['height'], -f.get('fps', 0))))
        with downloader.urlopen(fmt['url']) as response:
            init_url, segments = playlist(response.read().decode(), fmt['url'])

        def get_file(path, url):
            if path.exists() and path.stat().st_size:
                return path
            for attempt in range(3):
                try:
                    with downloader.urlopen(url) as response:
                        data = response.read()
                    if not data:
                        raise ValueError('Empty media fragment')
                    temporary = path.with_suffix('.part')
                    temporary.write_bytes(data)
                    temporary.replace(path)
                    return path
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(.5)

        init = get_file(fragment_folder/f'{fmt["format_id"]}-init.mp4', init_url) if init_url else None
        results = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start, end in chunks(source['windows'], meta['duration'], 600):
                stem = f'{start:010.3f}-{end:010.3f}'
                marker = folder/(stem+'.json')
                if marker.exists():
                    cached = json.loads(marker.read_text())
                    if cached['url'] != source['url'] or sha256(folder/cached['file']) != cached['sha256']:
                        raise ValueError('Existing completed clip differs')
                    results.append(cached)
                    continue
                selected = [s for s in segments if s['end'] > start+1e-6 and s['start'] < end-1e-6]
                if not selected or selected[0]['start'] > start+1e-6 or selected[-1]['end'] < end-1e-6:
                    raise ValueError('Playlist does not cover requested window')
                began = time.monotonic()
                paths = list(pool.map(lambda s: get_file(fragment_folder/f'{fmt["format_id"]}-{s["index"]:06d}.mp4', s['url']), selected))
                joined = fragment_folder/(stem+'.joined.mp4')
                with joined.open('wb') as output:
                    for part in ([init] if init else []) + paths:
                        with part.open('rb') as inp:
                            while block := inp.read(1024*1024):
                                output.write(block)
                target = folder/(stem+'.mp4')
                command = [str(ffmpeg), '-hide_banner', '-loglevel', 'warning', '-y',
                           '-i', str(joined), '-ss', str(max(0, start-selected[0]['start'])),
                           '-t', str(end-start), '-map', '0:v:0', '-an', '-sn', '-c:v', 'copy',
                           '-avoid_negative_ts', 'make_zero', str(target)]
                log = folder/(stem+'.local-remux.log')
                with log.open('w') as stream:
                    result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=90)
                if result.returncode:
                    raise RuntimeError('Local remux failed; inspect the retained log')
                video = video_info(target)
                delta = video['duration']-(end-start)
                if abs(delta) > 10:
                    raise ValueError('Remux duration differs by more than ten seconds')
                result = {'schema': 1, 'source_id': source['id'], 'url': source['url'],
                          'source_start': start, 'source_end': end, 'requested_height': height,
                          'file': target.name, 'sha256': sha256(target), 'bytes': target.stat().st_size,
                          'duration_delta_seconds': delta, 'boundary_review_seconds': 10,
                          'video': video, 'download_seconds': time.monotonic()-began,
                          'transport_warning': False, 'acquisition': 'public_hls_fragments_local_remux',
                          'format_id': fmt['format_id'], 'initialization_sha256': sha256(init) if init else None,
                          'transport': 'fragmented_mp4' if init else 'mpeg_ts',
                          'segments': [{k: s[k] for k in ['index', 'start', 'end']} | {'sha256': sha256(p)}
                                       for s, p in zip(selected, paths)],
                          'timing': 'Source intervals follow EXTINF durations; stream-copy boundaries require native verification.'}
                write_json(marker, result)
                results.append(result)
                # Keep original fragments and hashes; the joined temporary is reproducible.
                joined.unlink()
                print(f'{source["id"]} {start:.0f}-{end:.0f}: {target.stat().st_size/1e6:.1f} MB', flush=True)
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--only')
    parser.add_argument('--workers', type=int, choices=range(1,5), default=4)
    parser.add_argument('--report', default='expansion-acquisition.json')
    args = parser.parse_args()
    root = args.directory.resolve()
    results = []; failures = []
    for source in load_sources(args.sources):
        if args.only and args.only != source['id']:
            continue
        try:
            results.extend(acquire_source(source, root, args.workers))
        except Exception as error:
            # Exception strings from network clients can contain signed URLs.
            failures.append({'source_id': source['id'], 'error_type': type(error).__name__})
            print(source['id'], 'failed:', type(error).__name__, flush=True)
        if Path(args.report).name != args.report:
            parser.error('Report must be a filename inside the acquisition directory')
        write_json(root/args.report, {'clips': results, 'failures': failures})
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
