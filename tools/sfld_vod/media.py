"""Bounded section downloads, resumable work units, and video provenance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()


def seconds(value):
    if isinstance(value, bool):
        raise ValueError("A timestamp must be seconds or HH:MM:SS.")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        parts = value.split(":")
        if not 1 <= len(parts) <= 3:
            raise ValueError("Expected seconds, MM:SS or HH:MM:SS.")
        numbers = [float(p) for p in parts]
        if any(not math.isfinite(p) or p<0 for p in numbers):
            raise ValueError('Timestamp components must be finite and nonnegative.')
        if any(not 0 <= p < 60 for p in numbers[1:]):
            raise ValueError("Minutes and seconds must be below 60.")
        result = sum(p * 60**i for i, p in enumerate(reversed(numbers)))
    else:
        raise ValueError("Invalid timestamp.")
    if not math.isfinite(result) or result < 0:
        raise ValueError("Timestamps must be finite and nonnegative.")
    return result


def load_sources(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("sources"), list):
        raise ValueError("Expected a schema-1 source manifest.")
    seen = set()
    for source in data["sources"]:
        ident = source.get("id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident) or ident in seen:
            raise ValueError("Source IDs must be unique safe filenames.")
        seen.add(ident)
        url = urlparse(source.get("url", ""))
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("Expected a public HTTP(S) video URL without credentials.")
        windows = source.get("windows", [])
        if not windows:
            raise ValueError("Every source needs a time window.")
        end_of_previous = -1
        for start, end in windows:
            start = seconds(start)
            end = math.inf if end is None else seconds(end)
            if start < end_of_previous or start >= end:
                raise ValueError("Time windows must be sorted, disjoint and nonempty.")
            end_of_previous = end
    return data["sources"]


def chunks(windows, duration, chunk_seconds):
    if not math.isfinite(duration) or duration <= 0 or not 10 <= chunk_seconds <= 3600:
        raise ValueError("Invalid duration or chunk size (10-3600 seconds).")
    for start, end in windows:
        start = seconds(start)
        end = duration if end is None else min(seconds(end), duration)
        if start >= end:
            raise ValueError("Requested window starts beyond the video duration.")
        while start < end:
            stop = min(start + chunk_seconds, end)
            yield start, stop
            start = stop


def ffmpeg_path(root):
    installed = shutil.which("ffmpeg")
    if installed:
        return Path(installed)
    binary = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    # Nested scan jobs share the download directory's portable binary.
    for parent in (Path(root).resolve(), *Path(root).resolve().parents):
        shared = parent / "bin" / binary
        if shared.is_file():
            return shared
    import imageio_ffmpeg
    bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
    target = Path(root).resolve() / "bin" / binary
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != bundled.stat().st_size:
        shutil.copy2(bundled, target)
    return target


def probe(source, root, refresh=False):
    from yt_dlp import YoutubeDL
    from yt_dlp.version import __version__
    path = Path(root) / source["id"] / "source.json"
    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["url"] == source["url"]:
            return cached
    with YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                    "socket_timeout": 20, "extractor_retries": 2}) as ydl:
        info = ydl.extract_info(source["url"], download=False)
    if not info or info.get("is_live") or not info.get("duration"):
        raise ValueError("This workflow needs an archived video with a known duration.")
    # Signed media URLs, cookies and raw extractor state are never exported.
    result = {"schema": 1, "id": source["id"], "url": source["url"], "yt_dlp": __version__,
              "inspected_at_unix": time.time(), **{k: info.get(k) for k in
                  ("title", "duration", "upload_date", "timestamp", "uploader", "chapters")},
              "formats": [{k: f.get(k) for k in ("format_id", "width", "height", "fps", "tbr")}
                          for f in info.get("formats", []) if f.get("vcodec") != "none"]}
    write_json(path, result)
    return result


def video_info(path):
    import cv2
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError("Downloaded file is not a readable video.")
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or fps <= 0 or frames <= 0:
            raise ValueError("Cannot establish video duration.")
        return {"width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": fps, "frames": int(frames), "duration": frames / fps}
    finally:
        capture.release()


def fetch_chunk(source, root, start, end, height, ffmpeg):
    if height not in (160, 360, 480, 720, 1080):
        raise ValueError("Choose 160, 360, 480, 720 or 1080 pixels for download height.")
    folder = Path(root).resolve() / source["id"] / f"{height}p"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{start:010.3f}-{end:010.3f}"
    marker = folder / (stem + ".json")
    if marker.exists():
        old = json.loads(marker.read_text(encoding="utf-8"))
        video = folder / old["file"]
        if old["url"] == source["url"] and video.is_file() and sha256(video) == old["sha256"]:
            return {**old, "cached": True}
        raise ValueError("Existing download changed; use a new output directory or restore the original file.")
    log = folder / (stem + ".download.log")
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-progress",
               "--socket-timeout", "20", "--retries", "2", "--fragment-retries", "2",
               "--abort-on-unavailable-fragments", "--force-overwrites",
               "-f", f"best[protocol^=m3u8][height<={height}][vcodec!=none]/bestvideo[protocol^=m3u8][height<={height}][vcodec^=avc1]/bestvideo[height<={height}][vcodec^=avc1]/best[height<={height}][vcodec!=none]/bestvideo[height<={height}]",
               "--download-sections", f"*{start}-{end}",
               "--ffmpeg-location", str(ffmpeg.parent),
               "--downloader-args", "ffmpeg_i:-rw_timeout 20000000",
               "--downloader-args", "ffmpeg_o:-an -sn -loglevel warning",
               "-o", str(folder / (stem + ".%(ext)s")), source["url"]]
    began = time.perf_counter()
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=1800)
    if process.returncode:
        raise RuntimeError(f"Download failed. See {log}; no completed marker was written.")
    videos = [p for p in folder.glob(stem + ".*") if p.suffix in (".mp4", ".mkv", ".webm", ".ts")]
    if len(videos) != 1:
        raise ValueError("Expected exactly one downloaded section.")
    video = videos[0]
    metadata = video_info(video)
    duration_delta = metadata['duration'] - (end - start)
    # HLS stream-copy cuts can retain leading keyframes (observed: 5.1 seconds).
    # Larger discrepancies indicate an incomplete or incorrectly cut download.
    if abs(duration_delta) > 10:
        raise ValueError("Downloaded duration differs by more than ten seconds; inspect the log before using this section.")
    log_text = log.read_text(encoding="utf-8", errors="replace").lower()
    result = {"schema": 1, "source_id": source["id"], "url": source["url"],
              "source_start": start, "source_end": end, "requested_height": height,
              "file": video.name, "sha256": sha256(video), "bytes": video.stat().st_size,
              "duration_delta_seconds": duration_delta,
              "boundary_review_seconds": 10,
              "video": metadata, "download_seconds": time.perf_counter() - began,
              "timing": "Stream-copy section; source timestamps are approximate around keyframe cuts.",
              "transport_warning": any(s in log_text for s in ("corrupt", "failed to open", "error when loading"))}
    write_json(marker, result)
    return result


def acquire(sources, root, height=360, chunk_seconds=600, workers=2, pilot_seconds=None):
    if not 1 <= workers <= 4:
        raise ValueError("Use 1-4 simultaneous downloads.")
    ffmpeg = ffmpeg_path(root)
    work = []
    failures = []
    for source in sources:
        try:
            meta = probe(source, root)
        except Exception as error:
            message = f"{source['id']}: metadata lookup failed: {error}"
            failures.append(message)
            write_json(Path(root)/source["id"]/"source-error.json", {"source_id":source["id"],"error":str(error)})
            print(message,file=sys.stderr,flush=True)
            continue
        windows = source["windows"]
        if pilot_seconds is not None:
            start = seconds(windows[0][0])
            windows = [[start, min(start + pilot_seconds, seconds(windows[0][1]) if windows[0][1] is not None else meta["duration"])]]
        work.extend((source, start, end) for start, end in chunks(windows, meta["duration"], chunk_seconds))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(fetch_chunk, source, root, start, end, height, ffmpeg): (source["id"], start, end)
                 for source, start, end in work}
        for task in as_completed(tasks):
            ident, start, end = tasks[task]
            try:
                result = task.result()
                print(f"{ident} {start:.0f}-{end:.0f}s: {'cached' if result.get('cached') else 'saved'} "
                      f"{result['bytes']/1e6:.1f} MB", flush=True)
            except Exception as error:
                failures.append(f"{ident} {start}-{end}: {error}")
                print(failures[-1], file=sys.stderr, flush=True)
        if failures:
            raise RuntimeError(f"{len(failures)} sections failed; completed sections are reusable.")
