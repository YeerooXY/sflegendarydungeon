"""Low-cost decision-background candidates, independent of door labels.

This preserves scenes whose animated mystery glyph temporarily disappears. It
does not count rooms: portals, special encounters and interrupted views can
produce candidates and require review. All source calibration lives in an
external JSON file; no VOD-specific references are built into this module.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from .media import ffmpeg_path, sha256, write_json
from .vision import frames

VERSION = "1.1.0"
DEFAULT_REGIONS = [[0,32,41,138], [138,32,44,138], [290,32,30,138]]


def make_scene_reference(video, layout_path, output, clip_seconds):
    """Write a 320x224 cropped source frame for an externally chosen timestamp."""
    import cv2
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    layout = json.loads(Path(layout_path).read_text(encoding="utf-8"))
    stream = frames(video, layout["crop"], 8, ffmpeg_path(output.parent),
                    output.with_suffix(".decode.log"), clip_seconds, .25)
    try:
        _, frame = next(stream)
    finally:
        stream.close()
    if not cv2.imwrite(str(output), frame):
        raise RuntimeError(f"Could not write scene reference: {output}")
    return {"image": str(output), "sha256": sha256(output),
            "video_sha256": sha256(video), "clip_seconds": clip_seconds}


class SceneRecognizer:
    """Masked normalized correlation against source-specific scene references.

    Configuration example (image paths relative to the JSON file):
      {"schema":1,"threshold":0.65,
       "references":[{"image":"stage1.png","stage":1}, ...],
       "mask_regions":[[0,32,41,138],[138,32,44,138],[290,32,30,138]]}

    Reference images must be visibly reviewed cropped 320x224 decision frames.
    The default mask selects outer walls and the central divider; each source
    must validate its own mask/threshold, including excluded-frame recall.
    """
    def __init__(self, config_path):
        import cv2
        import numpy as np
        self.cv = cv2
        self.np = np
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        if self.config.get("schema") != 1:
            raise ValueError("Unsupported scene configuration schema.")
        self.threshold = float(self.config.get("threshold", .65))
        self.appearance_split_threshold = self.config.get("appearance_split_threshold")
        if self.appearance_split_threshold is not None and self.appearance_split_threshold <= 0:
            raise ValueError("appearance_split_threshold must be positive or omitted.")
        self.appearance_split_samples = int(self.config.get("appearance_split_samples", 2))
        if not 1 <= self.appearance_split_samples <= 10:
            raise ValueError("appearance_split_samples must be between 1 and 10.")
        if not 0 < self.threshold <= 1:
            raise ValueError("Scene threshold must be in (0,1].")
        self.mask = np.zeros((224,320), dtype=np.uint8)
        for x,y,w,h in self.config.get("mask_regions", DEFAULT_REGIONS):
            if min(x,y) < 0 or min(w,h) <= 0 or x+w > 320 or y+h > 224:
                raise ValueError("Scene mask rectangle is outside 320x224 frame.")
            self.mask[y:y+h,x:x+w] = 1
        self.mask = self.mask > 0
        self.reference_metadata = []
        vectors = []
        for entry in self.config.get("references", []):
            path = (self.config_path.parent / entry["image"]).resolve()
            digest = sha256(path)
            if entry.get("sha256") and entry["sha256"] != digest:
                raise ValueError(f"Scene reference changed: {path}")
            frame = cv2.imread(str(path))
            if frame is None or frame.shape[:2] != (224,320):
                raise ValueError(f"Scene reference must be 320x224: {path}")
            vectors.append(self.descriptor(frame))
            self.reference_metadata.append({**entry,"image":str(path),"sha256":digest})
        if not vectors:
            raise ValueError("At least one scene reference is required.")
        self.references = np.stack(vectors)

    def descriptor(self, frame):
        vector = self.cv.cvtColor(frame, self.cv.COLOR_BGR2GRAY)[self.mask].astype(self.np.float32)
        vector -= vector.mean()
        norm = self.np.linalg.norm(vector)
        if norm < 1e-6:
            return vector * 0
        return vector / norm

    def score(self, frame):
        scores = self.references @ self.descriptor(frame)
        index = int(self.np.argmax(scores))
        return self.reference_metadata[index].get("stage"), float(scores[index]), index

    def appearance(self, frame):
        """Door-interior descriptor with global brightness/contrast removed.

        Optional splitting is intentionally only a candidate boundary. Large
        tooltip/animation changes may also split a held view, so source users
        should validate a threshold (24 is a reasonable initial experiment).
        """
        gray = self.cv.cvtColor(frame, self.cv.COLOR_BGR2GRAY)
        regions = self.config.get("appearance_regions", [[49,29,88,158],[194,29,88,158]])
        chunks = []
        for x,y,w,h in regions:
            if min(x,y) < 0 or min(w,h) <= 0 or x+w > 320 or y+h > 224:
                raise ValueError("Appearance rectangle is outside 320x224 frame.")
            chunks.append(self.cv.resize(gray[y:y+h,x:x+w],(12,20)).ravel())
        value = self.np.concatenate(chunks).astype(self.np.float32)
        value -= value.mean()
        return value * (64/max(8,float(value.std())))


def scan_scenes(video, layout_path, config_path, output, source_start=0,
                source_url=None, fps=8, gap_frames=2,
                after_offsets=(.25,.5,.75,1,1.5,2,3,5,8,12,20,35,60)):
    """Decode the ENTIRE clip and save background epochs plus adjacent frames.

    Returns the metadata written to output/scenes.json. Epoch timestamps are
    absolute source seconds; image paths are relative to output. No internal
    HUD gate, door-score threshold, or legal-pair rule prunes the frame stream.
    Callers may parallelize clips with at most two native workers. The output
    remains unreviewed, and separate clips may split one held decision.
    """
    import cv2
    if not 1 <= gap_frames <= 30:
        raise ValueError("gap_frames must be between 1 and 30.")
    if list(after_offsets) != sorted(set(after_offsets)) or any(t < 0 for t in after_offsets):
        raise ValueError("After-frame offsets must be unique, sorted, nonnegative seconds.")
    cv2.setNumThreads(1)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "frames").mkdir(exist_ok=True)
    layout = json.loads(Path(layout_path).read_text(encoding="utf-8"))
    recognizer = SceneRecognizer(config_path)
    identity = {"video_sha256":sha256(video),"layout_sha256":sha256(layout_path),
                "config_sha256":sha256(config_path),"references":recognizer.reference_metadata,
                "source_start":source_start,"source_url":source_url,"fps":fps,
                "gap_frames":gap_frames,"after_offsets":list(after_offsets),"version":VERSION}
    marker = output / "scenes.json"
    if marker.exists():
        old = json.loads(marker.read_text(encoding="utf-8"))
        if old["inputs"] != identity:
            raise ValueError("Scene scan inputs changed; preserve evidence using a new output directory.")
        return old
    began = time.perf_counter()
    active = None
    tail = None
    nondecision = 0
    last_decision_frame = None
    appearance_reference = None
    appearance_changes = 0
    epochs = []
    samples = 0
    matched = 0

    def save(name, frame):
        if not cv2.imwrite(str(output/name), frame, [cv2.IMWRITE_JPEG_QUALITY,95]):
            raise RuntimeError(f"Could not write evidence image: {name}")
        return name

    for local_t,frame in frames(video, layout["crop"], fps, ffmpeg_path(output), output/"decode.log"):
        samples += 1
        absolute_t = source_start + local_t
        stage,score,reference_index = recognizer.score(frame)
        if score >= recognizer.threshold:
            matched += 1
            nondecision = 0
            appearance = None
            split_difference = None
            if recognizer.appearance_split_threshold is not None:
                appearance = recognizer.appearance(frame)
                if active is not None and appearance_reference is not None:
                    difference = float(recognizer.np.mean(recognizer.np.abs(appearance-appearance_reference)))
                    appearance_changes = appearance_changes+1 if difference >= recognizer.appearance_split_threshold else 0
                    if appearance_changes >= recognizer.appearance_split_samples:
                        active["last_image"] = save(f"frames/{len(epochs)-1:04d}-last-door.jpg",last_decision_frame)
                        active["end_boundary_reason"] = "appearance_change_candidate"
                        active["appearance_change_distance"] = difference
                        active = None
                        split_difference = difference
            last_decision_frame = frame.copy()
            if active is None:
                tail = None
                appearance_reference = appearance
                appearance_changes = 0
                active = {"source_id":layout.get("source_id"),"start":absolute_t,"end":absolute_t,
                          "best_time":absolute_t,"stage_suggestion":stage,"scene_score":score,
                          "reference_index":reference_index,"samples":0,"evidence":[],
                          "review_status":"unreviewed_scene_candidate"}
                if split_difference is not None:
                    active["start_boundary_reason"] = "appearance_change_candidate"
                    active["appearance_change_distance"] = split_difference
                epochs.append(active)
            active["end"] = absolute_t
            active["samples"] += 1
            if score >= active["scene_score"]:
                active.update(scene_score=score,best_time=absolute_t,stage_suggestion=stage,
                              reference_index=reference_index)
                if appearance is not None and appearance_changes == 0:
                    appearance_reference = appearance
                active["image"] = save(f"frames/{len(epochs)-1:04d}-door.jpg", frame)
        else:
            if active is not None:
                if nondecision == 0:
                    active["last_image"] = save(f"frames/{len(epochs)-1:04d}-last-door.jpg",last_decision_frame)
                nondecision += 1
                if nondecision >= gap_frames:
                    tail = active
                    active = None
            if tail is not None:
                after = absolute_t-tail["end"]
                index = len(tail["evidence"])
                if index < len(after_offsets) and after >= after_offsets[index]:
                    name = save(f"frames/{len(epochs)-1:04d}-after-{index:02d}.jpg",frame)
                    tail["evidence"].append({"source_seconds":absolute_t,"image":name,"after_end":after})
    if active is not None and last_decision_frame is not None:
        active["last_image"] = save(f"frames/{len(epochs)-1:04d}-last-door.jpg",last_decision_frame)
        active["clip_boundary_review"] = True
    for index,epoch in enumerate(epochs):
        epoch["id"] = f"{layout.get('source_id')}:{epoch['start']:.3f}:scene:{identity['video_sha256'][:12]}"
        epoch["local_index"] = index
        if epoch["start"]-source_start < 1/fps:
            epoch["clip_boundary_review"] = True
    log = (output/"decode.log").read_text(encoding="utf-8",errors="replace")
    result = {"schema":1,"inputs":identity,"source_id":layout.get("source_id"),
              "source_start":source_start,"decoded_frames":samples,"matched_frames":matched,
              "epochs":epochs,"decode_warnings":bool(log.strip()),
              "processing_seconds":time.perf_counter()-began,
              "interpretation":"Unreviewed source-background candidates; not unique room counts. "
              "Special rooms, portals, cuts and interrupted views need review. No odds inferred."}
    write_json(marker,result)
    return result
