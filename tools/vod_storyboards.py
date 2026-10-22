"""Cache public Twitch storyboards for VOD discovery, without signed media URLs.

These sparse previews locate footage, not gameplay observations. Their times are
approximate and must be checked against downloaded video before annotation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from sfld_vod.media import sha256, write_json


def preview(source, root, samples=40):
    import cv2
    import numpy as np
    from yt_dlp import YoutubeDL

    if not re.fullmatch(r"[0-9]+", source["id"]):
        raise ValueError("Expected a numeric Twitch VOD ID")
    url = urlparse(source["url"])
    if (url.scheme != "https" or url.hostname not in ("twitch.tv", "www.twitch.tv")
            or url.username or url.password or url.path != f"/videos/{source['id']}"):
        raise ValueError("Expected the matching public Twitch VOD URL")
    folder = root / source["id"] / "storyboards"
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / "index.json"
    if marker.exists():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if saved["url"] != source["url"]:
            raise ValueError("Source URL differs from existing storyboard cache")
        if saved.get("requested_samples", len(saved["samples"])) != samples:
            raise ValueError("Sample count differs from cache; choose a new output directory")
        for item in saved["files"]:
            if sha256(folder / item["file"]) != item["sha256"]:
                raise ValueError("Storyboard cache hash mismatch")
        return saved
    with YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 20,
                    "extractor_retries": 1, "noplaylist": True}) as downloader:
        info = downloader.extract_info(source["url"], download=False)
        formats = [f for f in info["formats"] if f.get("protocol") == "mhtml"]
        if not formats:
            raise ValueError("No public storyboard available")
        fmt = max(formats, key=lambda f: f.get("width", 0))
        width, height = int(fmt["width"]), int(fmt["height"])
        columns, rows = int(fmt["columns"]), int(fmt["rows"])
        tile_count = round(float(fmt["fps"]) * float(info["duration"]))
        tiles, files, elapsed = [], [], 0.0
        for number, fragment in enumerate(fmt["fragments"]):
            target = folder / f"sprite-{number:03d}.jpg"
            with downloader.urlopen(fragment["url"]) as response:
                content = response.read()
            decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
            if decoded is None or decoded.shape[:2] != (rows * height, columns * width):
                raise ValueError("Unexpected storyboard sprite geometry")
            target.write_bytes(content)
            files.append({"file": target.name, "sha256": sha256(target)})
            spacing = fragment["duration"] / (rows * columns)
            for index in range(rows * columns):
                if len(tiles) >= tile_count:
                    break
                x, y = index % columns * width, index // columns * height
                tiles.append((elapsed + index * spacing, decoded[y:y+height, x:x+width]))
            elapsed += fragment["duration"]
    selected = np.linspace(0, len(tiles) - 1, min(samples, len(tiles)), dtype=int)
    sheet_columns, title_height, caption_height = 5, 38, 22
    sheet_rows = (len(selected) + sheet_columns - 1) // sheet_columns
    sheet = np.full((title_height + sheet_rows * (height + caption_height),
                     sheet_columns * width, 3), 24, np.uint8)
    heading = f"{source['id']} | {info.get('uploader', '')} | {info.get('upload_date', '')} | approximate preview times"
    cv2.putText(sheet, heading.encode('ascii', 'replace').decode(), (7, 24),
                cv2.FONT_HERSHEY_SIMPLEX, .47, (230, 230, 230), 1, cv2.LINE_AA)
    samples_metadata = []
    for number, index in enumerate(selected):
        at, tile = tiles[int(index)]
        x, y = number % sheet_columns * width, title_height + number // sheet_columns * (height + caption_height)
        sheet[y:y+height, x:x+width] = tile
        seconds = round(at)
        stamp = f"{number:02d} | {seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}"
        cv2.putText(sheet, stamp, (x+4, y+height+16), cv2.FONT_HERSHEY_SIMPLEX,
                    .44, (230, 230, 230), 1, cv2.LINE_AA)
        samples_metadata.append({"tile": number, "source_seconds_approximate": at,
                                 "storyboard_tile_index": int(index)})
    target = folder / "overview.jpg"
    if not cv2.imwrite(str(target), sheet):
        raise RuntimeError("Could not write overview")
    files.append({"file": target.name, "sha256": sha256(target)})
    result = {"schema": 1, "source_id": source["id"], "url": source["url"],
              "title": info.get("title"), "channel": info.get("uploader"),
              "duration_seconds": info["duration"], "upload_date": info.get("upload_date"),
              "format": fmt["format_id"], "files": files, "samples": samples_metadata,
              "requested_samples": samples,
              "status": "unreviewed_discovery_preview",
              "timing": "Approximate storyboard tile spacing; verify against native video.",
              "limitation": "Sparse sampling can miss short runs. Tiles are not unique room observations."}
    write_json(marker, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=3)
    args = parser.parse_args()
    if not 5 <= args.samples <= 200:
        parser.error("Use 5 to 200 preview samples")
    sources = json.loads(args.catalog.read_text(encoding="utf-8"))["sources"]
    sources = [s for s in sources if s.get("status") == "available" and
               (not args.only or s["id"] in args.only)]
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = {pool.submit(preview, s, args.directory, args.samples): s["id"] for s in sources}
        for future in as_completed(tasks):
            source_id = tasks[future]
            try:
                result = future.result()
                print(f"{source_id}: {len(result['samples'])} preview tiles", flush=True)
            except Exception as error:
                failures.append({"source_id": source_id, "error": str(error)})
                print(f"{source_id}: preview failed: {error}", flush=True)
    write_json(args.directory / "storyboard-failures.json", failures)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
