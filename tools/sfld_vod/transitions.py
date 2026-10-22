"""Native door-to-interior review bursts with conservative temporal selection.

This is a frame picker, not a click detector or a room classifier. Brightness
recovery and agreement across neighboring frames avoid the common mistake of
saving only a dark transition. Selection never establishes settled HUD values,
the chosen side, or whether combat has already happened.
"""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path
import time

from .media import ffmpeg_path, sha256, video_info, write_json
from .vision import frames, rect

VERSION = "1.0.0"
DEFAULTS = {"offer_similarity": .78, "brightness_fraction": .78,
            "contrast_fraction": .60, "neighbor_similarity": .88,
            "maximum_gap_seconds": .18, "early_change_similarity": .90}


def _descriptor(gray, mask=None):
    import numpy as np
    value = (gray[mask] if mask is not None else gray.ravel()).astype(np.float32)
    value -= value.mean()
    norm = float(np.linalg.norm(value))
    return value / max(norm, 1e-6)


def frame_features(frame):
    """Measure at low resolution; keep the original image as review evidence."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(cv2.resize(frame, (320, 224), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY)
    mask = np.zeros((224, 320), dtype=bool)
    # Static outer walls and divider distinguish the choice scene. HUD, pointer
    # position, hover highlights and displayed keys are not selection evidence.
    for x, y, w, h in ((0, 32, 41, 138), (138, 32, 44, 138), (290, 32, 30, 138)):
        mask[y:y+h, x:x+w] = True
    interior = gray[15:174, 15:305]
    return {"brightness": float(interior.mean()), "contrast": float(interior.std()),
            "offer_vector": _descriptor(gray, mask),
            "interior_vector": _descriptor(interior)}


def select_transition(samples, source_end, settings=None):
    """Select the first readable, agreeing image pair from the first interior.

    samples contain source_seconds and features produced by frame_features.
    The best exposed pre/end image anchors the offer mask. Stop on the first
    return to that choice background, so later visits never supply a missing
    initial frame. Unknown/brief transitions retain a review frame but cannot
    pass the multiframe gate. No room-type legality rules are used.
    """
    config = {**DEFAULTS, **(settings or {})}
    if any(not 0 < config[k] <= 1 for k in ("offer_similarity", "brightness_fraction",
                                           "contrast_fraction", "neighbor_similarity", "early_change_similarity")):
        raise ValueError("Similarity and exposure fractions must be in (0,1].")
    if config["maximum_gap_seconds"] <= 0:
        raise ValueError("maximum_gap_seconds must be positive.")
    ordered = sorted(enumerate(samples), key=lambda p: p[1]["source_seconds"])
    before = [(i, s) for i, s in ordered if s["source_seconds"] <= source_end]
    result = {"status": "unknown", "selected_index": None, "confirmation_index": None,
              "fallback_index": None, "before_index": None, "departure_seconds": None,
              "early_index": None, "earlier_content_may_differ": None,
              "return_seconds": None, "chosen_side": None, "initial_content": None,
              "resource_values_settled": False, "metrics": [], "reason": None}
    if not before:
        result["reason"] = "missing_pre_entry_offer"
        return result
    ref_index, reference = max(before, key=lambda p: p[1]["features"]["brightness"])
    result["before_index"] = ref_index
    if reference["features"]["brightness"] < 15 or reference["features"]["contrast"] < 8:
        result["reason"] = "offer_reference_dark_or_unreadable"
        return result
    ref = reference["features"]["offer_vector"]
    epoch = []
    entered = False
    for index, sample in ordered:
        if sample["source_seconds"] <= source_end:
            continue
        value = sample["features"]
        similarity = float(value["offer_vector"] @ ref)
        metric = {"index": index, "source_seconds": sample["source_seconds"],
                  "brightness": round(value["brightness"], 3),
                  "contrast": round(value["contrast"], 3),
                  "offer_similarity": round(similarity, 4)}
        result["metrics"].append(metric)
        looks_offer = similarity >= config["offer_similarity"]
        if looks_offer:
            if entered:
                result["return_seconds"] = sample["source_seconds"]
                break
            continue
        entered = True
        if result["departure_seconds"] is None:
            result["departure_seconds"] = sample["source_seconds"]
        epoch.append((index, sample))
    if not epoch:
        result["reason"] = "no_visible_departure"
        return result
    peak_brightness = max(s["features"]["brightness"] for _, s in epoch)
    peak_contrast = max(s["features"]["contrast"] for _, s in epoch)
    result["fallback_index"] = max(epoch, key=lambda p: p[1]["features"]["brightness"])[0]
    result["early_index"] = next((i for i, s in epoch
        if s["features"]["brightness"] >= max(8, .3 * peak_brightness)), epoch[0][0])
    if peak_brightness < 15 or peak_contrast < 8:
        result["reason"] = "interior_remains_dark_or_unreadable"
        return result

    def exposed(sample):
        value = sample["features"]
        return (value["brightness"] >= config["brightness_fraction"] * peak_brightness
                and value["contrast"] >= config["contrast_fraction"] * peak_contrast)

    for (index, current), (following_index, following) in zip(epoch, epoch[1:]):
        gap = following["source_seconds"] - current["source_seconds"]
        agreement = float(current["features"]["interior_vector"] @ following["features"]["interior_vector"])
        if (0 < gap <= config["maximum_gap_seconds"] and exposed(current)
                and exposed(following) and agreement >= config["neighbor_similarity"]):
            result.update(status="unreviewed_multiframe_candidate", selected_index=index,
                          confirmation_index=following_index, reason="first_readable_agreeing_pair",
                          neighbor_similarity=round(agreement, 4))
            # A rapidly defeated monster can disappear before exposure settles.
            # Preserve an earlier view and require manual sequence review if two
            # earlier, partially exposed frames differ from the readable pair.
            earlier = [(i, s) for i, s in epoch if s["source_seconds"] < current["source_seconds"]
                       and s["features"]["brightness"] >= .4 * current["features"]["brightness"]]
            changed = [(i, s) for i, s in earlier if
                float(s["features"]["interior_vector"] @ current["features"]["interior_vector"])
                < config["early_change_similarity"]]
            result["earlier_content_may_differ"] = len(changed) >= 2
            if result["earlier_content_may_differ"]:
                result["status"] = "unreviewed_temporal_change"
                result["reason"] = "earlier_frames_differ_review_initial_content"
            return result
    result["reason"] = "no_readable_agreeing_pair_before_next_choice"
    return result


def unique_items(items):
    """Collapse exact repeated candidate IDs only, never identical-looking rooms."""
    seen = {}
    result = []
    duplicates = []
    for item in items:
        ident = item.get("observation_id") or item.get("candidate_id")
        if not ident:
            raise ValueError("Every item needs an observation_id or candidate_id.")
        if ident in seen:
            if item != seen[ident]:
                raise ValueError(f"Conflicting duplicate candidate: {ident}")
            duplicates.append(ident)
            continue
        seen[ident] = item
        result.append(item)
    return result, duplicates


def analyze_queue(root, queue_path, output, settings=None):
    """Analyze existing hashed native frames, producing suggestions only."""
    import cv2
    root = Path(root).resolve()
    queue_path = Path(queue_path).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    items, duplicates = unique_items(queue["items"])
    started = time.perf_counter()
    results = []
    for item in items:
        loaded = []
        for evidence in item["evidence"]:
            path = (root / evidence["image"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Evidence image must be inside root.")
            if sha256(path) != evidence["sha256"]:
                raise ValueError(f"Evidence image changed: {path}")
            image = cv2.imread(str(path))
            if image is None:
                raise ValueError(f"Unreadable image: {path}")
            loaded.append({"source_seconds": evidence["source_seconds"],
                           "features": frame_features(image)})
        selected = select_transition(loaded, item["source_end"], settings)
        for role, index_key in (("before", "before_index"), ("entry_candidate", "selected_index"),
                                ("entry_confirmation", "confirmation_index"), ("review_fallback", "fallback_index"),
                                ("early_transition", "early_index")):
            index = selected[index_key]
            selected[role] = item["evidence"][index] if index is not None else None
        results.append({"observation_id": item.get("observation_id") or item["candidate_id"],
                        "source_id": item["source_id"], "source_end": item["source_end"],
                        "context": item.get("context"), "doors": item.get("doors"),
                        "selection": selected,
                        "resource_review": {"status": "unreviewed", "keys_before": None,
                            "keys_after": None, "lockpick_before": None, "lockpick_after": None,
                            "blessing_slots_before": None, "blessing_slots_after": None,
                            "cost_and_reward_may_overlap": True,
                            "note": "Read all blessing slots and the entire transition. Brightness and equal counters do not establish settled entry costs or chosen side."}})
    report = {"schema": 1, "version": VERSION, "queue_sha256": sha256(queue_path),
              "settings": {**DEFAULTS, **(settings or {})}, "rows": results,
              "duplicate_candidate_ids_ignored": duplicates,
              "summary": dict(Counter(row["selection"]["status"] for row in results)),
              "elapsed_seconds": round(time.perf_counter() - started, 3),
              "limitations": ["All selections remain unreviewed image suggestions, not outcome or action labels.",
                              "Agreement does not prove the absence of a short earlier encounter or settled resource costs.",
                              "Source layouts and static decision backgrounds require validation on each new presentation."]}
    write_json(output / "transitions.json", report)
    write_review(root, output, report)
    return report


def write_review(root, output, report):
    """A local filmstrip keeps early and settled views next to each other."""
    sections = []
    for row in report["rows"]:
        selection = row["selection"]
        pictures = []
        for key, title in (("before", "Offer"), ("early_transition", "Early transition"),
                           ("entry_candidate", "Readable candidate"), ("entry_confirmation", "Next frame")):
            evidence = selection.get(key)
            if evidence:
                target = os.path.relpath(root / evidence["image"], output).replace("\\", "/")
                pictures.append(f'<figure><figcaption>{title}: {evidence["source_seconds"]:.3f}s</figcaption>'
                                f'<a href="{html.escape(target, quote=True)}"><img src="{html.escape(target, quote=True)}"></a></figure>')
        sections.append(f'<section><h2>{html.escape(row["observation_id"])}</h2>'
                        f'<p>{html.escape(selection["reason"] or "unknown")}. Side and initial content remain unverified.</p>'
                        f'<div class="strip">{"".join(pictures)}</div></section>')
    page = ('<!doctype html><meta charset="utf-8"><title>Transition evidence review</title>'
            '<style>body{background:#171c23;color:#eee;font:16px system-ui;margin:24px}a{color:#adcaff}'
            '.strip{display:grid;grid-template-columns:repeat(4,minmax(230px,1fr));overflow:auto;gap:12px}'
            'figure{margin:0}img{width:100%}section{border-top:1px solid #556;margin-top:24px}figcaption{margin:8px 0}</style>'
            '<h1>Transition evidence review</h1><p>Check the early image and readable pair together. '
            'A monster can disappear before the screen finishes fading in. Equal key counts do not prove '
            'settled entry costs; combat rewards may already have arrived.</p>' + ''.join(sections))
    (output / "review.html").write_text(page, encoding="utf-8")


def extract_queue(root, manifest_path, output, fps=8, before=.25, after=2.0):
    """Decode bounded native crops sequentially; refine unknown visits at 30 fps.

    The manifest uses source_id, source_end and an observation_id per visit.
    Source clips and layouts use the existing VOD cache conventions. Frame
    timestamps retain the existing CFR/rendition-alignment uncertainty.
    """
    import cv2
    from .batch import select_clips
    if not .25 <= fps <= 30 or not 0 < before <= 2 or not .25 <= after <= 10:
        raise ValueError("fps must be .25..30, before 0..2, after .25..10 seconds.")
    root = Path(root).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not output.is_relative_to(root):
        raise ValueError("Extraction output must be inside the VOD root.")
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items, duplicates = unique_items(manifest["items"])
    marker = output / "queue.json"
    identity = {"manifest_sha256": sha256(manifest_path), "fps": fps,
                "before": before, "after": after, "version": VERSION}
    if marker.exists():
        old = json.loads(marker.read_text(encoding="utf-8"))
        if old.get("inputs") != identity:
            raise ValueError("Extraction inputs changed; use a new output directory.")
        return old
    result = {"schema": 1, "inputs": identity, "items": [], "missing": [],
              "duplicate_candidate_ids_ignored": duplicates}
    clip_cache = {}
    for index, item in enumerate(items):
        source = str(item["source_id"])
        if source not in clip_cache:
            clip_cache[source] = select_clips(root / source / "720p")
        layout_path = root / "layouts" / f"{source}.json"
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        start = float(item["source_end"]) - before
        end = float(item["source_end"]) + after
        match = next(((p, m) for p, m in clip_cache[source]
                      if m["source_start"] <= start and end <= m["source_end"]), None)
        if match is None:
            result["missing"].append({"observation_id": item.get("observation_id") or item.get("candidate_id"),
                                      "reason": "No single cached native clip covers the bounded window."})
            continue
        path, clip = match
        video = path.parent / clip["file"]
        info = video_info(video)
        _, _, width, height = rect(layout["crop"], info["width"], info["height"])
        row = {k: v for k, v in item.items() if k != "evidence"}
        row["layout_sha256"] = sha256(layout_path)
        row["sampling_passes"] = []
        for rate in sorted(set((fps, 30))):
            folder = output / f"{index:05d}" / f"{rate:g}fps"
            folder.mkdir(parents=True, exist_ok=True)
            evidence = []
            samples = []
            for n, (local, image) in enumerate(frames(video, layout["crop"], rate,
                    ffmpeg_path(output), folder / "decode.log", start - clip["source_start"],
                    end - start, size=(width, height))):
                target = folder / f"{n:04d}.jpg"
                if not cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 97]):
                    raise RuntimeError(f"Cannot write {target}")
                timestamp = clip["source_start"] + local
                evidence.append({"image": target.relative_to(root).as_posix(), "sha256": sha256(target),
                                 "source_seconds": round(timestamp, 6), "role": "native_transition_burst",
                                 "video": video.relative_to(root).as_posix(), "video_sha256": clip["sha256"],
                                 "native_height": info["height"], "crop_size": [width, height], "fps": rate})
                samples.append({"source_seconds": timestamp, "features": frame_features(image)})
            prediction = select_transition(samples, item["source_end"])
            row["sampling_passes"].append({"fps": rate, "frames": len(evidence), "status": prediction["status"]})
            row["evidence"] = evidence
            if prediction["status"] == "unreviewed_multiframe_candidate":
                break
        result["items"].append(row)
    write_json(marker, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="Build adaptive native transition bursts from cached VOD clips.")
    extract.add_argument("--root", type=Path, required=True)
    extract.add_argument("--manifest", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--fps", type=float, default=8)
    extract.add_argument("--before", type=float, default=.25)
    extract.add_argument("--after", type=float, default=2)
    analyze = sub.add_parser("analyze", help="Pick readable entry image pairs from an existing native evidence queue.")
    analyze.add_argument("--root", type=Path, required=True)
    analyze.add_argument("--queue", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "extract":
        queue = extract_queue(args.root, args.manifest, args.output, args.fps, args.before, args.after)
        report = analyze_queue(args.root, args.output / "queue.json", args.output)
        print(json.dumps({"extracted_visits": len(queue["items"]), "missing": len(queue["missing"]),
                          "summary": report["summary"], "output": str(args.output / "transitions.json")}))
    else:
        report = analyze_queue(args.root, args.queue, args.output)
        print(json.dumps({"summary": report["summary"], "elapsed_seconds": report["elapsed_seconds"],
                          "output": str(args.output / "transitions.json")}))
