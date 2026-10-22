"""Low-resolution, streaming candidate extraction. Predictions require review.

FFmpeg decodes sequentially in native code. OpenCV matches cached, small
templates only when the two door regions change. No frame-per-file dump, cloud
inference, or assumed legal-pair filter is used.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import subprocess
import time

from . import VERSION
from .media import ffmpeg_path, sha256, video_info, write_json


def rect(value, width, height):
    if len(value) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise ValueError("A crop needs four normalized numbers: x, y, width, height.")
    x, y, w, h = value
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x and 0 < h <= 1 - y):
        raise ValueError("Crop is outside the video.")
    return round(x * width), round(y * height), max(1, round(w * width)), max(1, round(h * height))


def frames(path, crop, fps, ffmpeg, log, start=0, duration=None, size=(320,224)):
    """Yield a bounded raw frame stream, with timestamps relative to the clip.

    CFR sampling timestamps have <= 1/fps sampling quantization. This does not
    establish frame-perfect alignment of a stream-copy download with its VOD.
    """
    import numpy as np
    info = video_info(path)
    x, y, w, h = rect(crop, info["width"], info["height"])
    if not 0.25 <= fps <= 30:
        raise ValueError("Scan rate must be between 0.25 and 30 fps.")
    output_width,output_height=size
    if any(type(v) is not int or not 32<=v<=1920 for v in size):
        raise ValueError('Frame dimensions must be integers between 32 and 1920.')
    frame_bytes=output_width*output_height*3
    command = [str(ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "warning", "-threads", "2",
               "-ss", str(start), "-i", str(Path(path).resolve())]
    if duration is not None:
        command += ["-t", str(duration)]
    command += ["-an", "-sn", "-vf", f"fps={fps},crop={w}:{h}:{x}:{y},scale={output_width}:{output_height}:flags=area",
                "-threads", "2", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    with Path(log).open("wb") as error_log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_log, bufsize=frame_bytes*4)
        try:
            i = 0
            while True:
                raw = process.stdout.read(frame_bytes)
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise RuntimeError("Truncated decoded frame; inspect the decode log.")
                yield start + i / fps, np.frombuffer(raw, dtype=np.uint8).reshape(output_height,output_width,3)
                i += 1
            if process.wait() != 0:
                raise RuntimeError("Video decoding failed; inspect the decode log.")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()


def merge_intervals(intervals, gap=0):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def calibrate(video, output, crop, sample_seconds, source_id, source_start):
    import cv2
    output = Path(output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_path(output.parent)
    stream = frames(video, crop, 1, ffmpeg, output.with_suffix(".decode.log"), sample_seconds, 1)
    try:
        _, frame = next(stream)
    finally:
        stream.close()
    # The familiar monster-face HUD is present across dungeon themes and scenes.
    # The reference frame must be visibly in the dungeon; review calibration.png.
    anchor = frame[189:211, 7:31].copy()
    image = output.with_suffix(".anchor.png")
    cv2.imwrite(str(image), anchor)
    cv2.imwrite(str(output.with_suffix(".calibration.png")), frame)
    settings = {"schema": 1, "source_id": source_id, "crop": crop,
                "anchor": image.name, "anchor_sha256": sha256(image),
                "anchor_search": [0, 177, 49, 47], "gate_threshold": .70,
                "template_heights": [130,138,146],
                "door_search": [[49,29,88,158],[194,29,88,158]],
                "calibration_video_sha256": sha256(video),
                "calibration_source_seconds": source_start + sample_seconds,
                "note": "Layout calibration only; detector accuracy has not been established."}
    write_json(output, settings)
    return settings


def add_anchor(settings_path, image_path):
    """Add a reviewed HUD appearance from a full-stream audit frame."""
    import cv2
    settings_path=Path(settings_path);settings=json.loads(settings_path.read_text(encoding='utf-8'))
    raw=cv2.imread(str(image_path))
    if raw is None:
        raise ValueError('Missing full-stream reference image.')
    x,y,w,h=rect(settings['crop'],raw.shape[1],raw.shape[0])
    frame=cv2.resize(raw[y:y+h,x:x+w],(320,224),interpolation=cv2.INTER_AREA)
    anchor=frame[189:211,7:31].copy()
    if cv2.cvtColor(anchor,cv2.COLOR_BGR2GRAY).std()<2:
        raise ValueError('Unusable HUD reference.')
    extras=settings.setdefault('additional_anchors',[])
    reference_hash=sha256(image_path)
    if any(a.get('reference_sha256')==reference_hash for a in extras):
        return settings
    target=settings_path.with_suffix(f'.anchor-{len(extras)+1}.png')
    cv2.imwrite(str(target),anchor)
    extras.append({'anchor':target.name,'anchor_sha256':sha256(target),'reference_sha256':reference_hash})
    write_json(settings_path,settings)
    return settings


def read_anchors(settings_path, settings):
    import cv2
    anchors=[]
    for item in [settings,*settings.get('additional_anchors',[])]:
        path=Path(settings_path).parent/item['anchor']
        if sha256(path)!=item['anchor_sha256']:
            raise ValueError('Calibration anchor changed; recalibrate explicitly.')
        anchor=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
        if anchor is None or anchor.std()<2:
            raise ValueError('Unusable HUD anchor.')
        anchors.append(anchor)
    return anchors


class Recognizer:
    def __init__(self, settings_path, art):
        import cv2
        self.cv = cv2
        self.settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
        if self.settings.get("schema") != 1:
            raise ValueError("Unsupported layout schema.")
        self.anchors = read_anchors(settings_path,self.settings)
        art = Path(art)
        manifest = json.loads((art / "manifest.json").read_text(encoding="utf-8"))["assets"]
        self.templates = [[], []]
        self.traps = [[], []]
        for key, filename in manifest.items():
            if not key.startswith(("door.", "trap.")):
                continue
            raw = cv2.imread(str(art / filename), cv2.IMREAD_UNCHANGED)
            if raw is None or raw.ndim != 3 or raw.shape[2] != 4:
                raise ValueError(f"Missing RGBA door sprite: {filename}")
            x,y,w,h = cv2.boundingRect((raw[:,:,3] > 180).astype("uint8"))
            left = raw[y:y+h,x:x+w]
            category,label = key.split(".",1)
            right_file = manifest.get(category + "_right." + label)
            if right_file:
                right = cv2.imread(str(art/right_file),cv2.IMREAD_UNCHANGED)
                rx,ry,rw,rh = cv2.boundingRect((right[:,:,3]>180).astype("uint8"))
                right = right[ry:ry+rh,rx:rx+rw]
            else:
                right = cv2.flip(left,1)
            for side,raw in enumerate((left,right)):
                heights = self.settings["template_heights"] if category=="door" else [max(4,round(raw.shape[0]*s)) for s in (.14,.16,.18)]
                for height in heights:
                    width = max(2,round(raw.shape[1] * height / raw.shape[0]))
                    template = cv2.resize(raw, (width,height), interpolation=cv2.INTER_AREA)
                    gray = cv2.cvtColor(template[:,:,:3], cv2.COLOR_BGR2GRAY)
                    (self.templates if category=="door" else self.traps)[side].append((label,gray))
        self.threshold = .52
        self.margin = .10

    def gate(self, gray):
        x,y,w,h = self.settings["anchor_search"]
        area = gray[y:y+h,x:x+w]
        return max(float(self.cv.minMaxLoc(self.cv.matchTemplate(area, anchor, self.cv.TM_CCOEFF_NORMED))[1])
                   for anchor in self.anchors)

    def doors(self, frame):
        cv = self.cv
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        choices = []
        for side,(x,y,w,h) in enumerate(self.settings["door_search"]):
            area = gray[y:y+h,x:x+w]; scores = {}
            for label,template in self.templates[side]:
                if template.shape[0] > h or template.shape[1] > w:
                    continue
                score = cv.minMaxLoc(cv.matchTemplate(area, template, cv.TM_CCOEFF_NORMED))[1]
                scores[label] = max(float(score), scores.get(label, -1))
            ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
            label,score = ordered[0]
            margin = score - ordered[1][1]
            choices.append({"kind": label if score >= self.threshold and margin >= self.margin else None,
                            "score": round(score,4), "margin": round(margin,4),
                            "alternatives": [[k,round(v,4)] for k,v in ordered[:3]],
                            "trap": None, "trap_kind": None})
        return choices

    def trap_hints(self, frame):
        gray = self.cv.cvtColor(frame,self.cv.COLOR_BGR2GRAY)
        result = []
        for side in range(2):
            area = gray[5:210,side*160:(side+1)*160]; scores={}
            for label,template in self.traps[side]:
                if template.shape[0]>area.shape[0] or template.shape[1]>area.shape[1]:
                    continue
                score=self.cv.minMaxLoc(self.cv.matchTemplate(area,template,self.cv.TM_CCOEFF_NORMED))[1]
                scores[label]=max(score,scores.get(label,-1))
            best=max(scores,key=scores.get) if scores else None
            result.append({"present":True if best and scores[best]>=.88 else None,
                           "visual":best if best and scores[best]>=.88 else None,
                           "score":round(scores[best],4) if best else None})
        return result


def gate_video(video, settings_path, output, fps=1, padding=3):
    import cv2
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    anchors=read_anchors(settings_path,settings)
    ffmpeg = ffmpeg_path(output)
    began = time.perf_counter(); hits = []; samples = 0
    duration = video_info(video)["duration"]
    x,y,w,h = settings["anchor_search"]
    for t,frame in frames(video, settings["crop"], fps, ffmpeg, output / "gate-decode.log"):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score = max(cv2.minMaxLoc(cv2.matchTemplate(gray[y:y+h,x:x+w],anchor,cv2.TM_CCOEFF_NORMED))[1]
                    for anchor in anchors)
        samples += 1
        if score >= settings["gate_threshold"]:
            hits.append([max(0,t-padding), min(duration,t+1/fps+padding)])
    log = (output / "gate-decode.log").read_text(encoding="utf-8", errors="replace")
    result = {"schema":1, "detector_version":VERSION, "source_id":settings["source_id"],
              "video_sha256":sha256(video), "layout_sha256":sha256(settings_path),
              "fps":fps, "samples":samples, "duration":duration,
              "intervals":merge_intervals(hits, gap=2), "decode_warnings":bool(log.strip()),
              "processing_seconds":time.perf_counter()-began,
              "meaning":"Candidate dungeon intervals, not validated coverage. Audit rejected spans too."}
    write_json(output / "gate.json",result)
    return result


class Episodes:
    """Tentative visual episodes. Review resolves duplicate/repeated decisions."""
    def __init__(self, emit, gap=.5):
        self.emit, self.gap = emit, gap
        self.active = None

    def push(self, t, choices, frame, fingerprint):
        if choices is None:
            if self.active and t-self.active["end"] > self.gap:
                self.flush()
            return
        signature = tuple(c["kind"] or c["alternatives"][0][0] for c in choices)
        if self.active and (signature != self.active["signature"] or t-self.active["end"] > self.gap):
            self.flush()
        quality = sum(c["score"] for c in choices)
        if self.active is None:
            self.active = {"start":t,"end":t,"signature":signature,"samples":0,"quality":-1}
        item = self.active; item["end"] = t; item["samples"] += 1
        if quality > item["quality"]:
            item.update(quality=quality, choices=choices, frame=frame.copy(), best_time=t, fingerprint=fingerprint)

    def flush(self):
        if self.active:
            self.emit(self.active)
            self.active = None


def scan(video, settings_path, output, art, source_start=0, source_url=None, fps=8):
    import cv2
    import numpy as np
    output = Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    video = Path(video).resolve()
    marker = output / "scan.json"
    input_hash = sha256(video); layout_hash = sha256(settings_path)
    art_hash = sha256(Path(art) / "manifest.json")
    identity = {"video_sha256":input_hash,"layout_sha256":layout_hash,"art_manifest_sha256":art_hash,
                "fps":fps,"source_start":source_start,"source_url":source_url,"detector_version":VERSION}
    if marker.exists():
        cached = json.loads(marker.read_text(encoding="utf-8"))
        if cached["inputs"] != identity:
            raise ValueError("Scan inputs changed. Use a new output directory to preserve earlier evidence.")
        return cached
    began = time.perf_counter()
    recognizer = Recognizer(settings_path, art)
    cv2.setNumThreads(1)  # Avoid nested thread oversubscription with FFmpeg/workers.
    gate_path = output / "gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.exists() else None
    if not gate or gate["video_sha256"] != input_hash or gate["layout_sha256"] != layout_hash:
        gate = gate_video(video,settings_path,output)
    ffmpeg = ffmpeg_path(output)
    candidates=[]; samples=0; classified=0; guesses=Counter()
    (output / "frames").mkdir(exist_ok=True)
    def emit(item):
        index=len(candidates)
        filename=f"frames/{index:06d}.jpg"
        cv2.imwrite(str(output/filename), item["frame"], [cv2.IMWRITE_JPEG_QUALITY,92])
        ident=f"{recognizer.settings['source_id']}:{source_start+item['best_time']:.3f}:{input_hash[:12]}"
        record={"schema":1,"id":ident,"source_id":recognizer.settings["source_id"],"source_url":source_url,
                "source_seconds":source_start+item["best_time"],"clip_seconds":item["best_time"],
                "interval":[source_start+item["start"],source_start+item["end"]],
                "image":filename,"image_sha256":sha256(output/filename),"observed_frames":item["samples"],
                "suggested_doors":item["choices"],"room":None,"run_number_event":None,"gems":None,
                "trap_hints":recognizer.trap_hints(item["frame"]),
                "review_status":"pending","boundary_review":item["start"]<10 or item["end"]>gate["duration"]-10,
                "note":"Visual candidate, not a counted decision. Trap absence and context are unknown. "
                       "Consecutive identical choices may merge without a readable counter or transition."}
        candidates.append(record)
        guesses.update([" + ".join(item["signature"])])
    episodes=Episodes(emit)
    for block,(start,end) in enumerate(gate["intervals"]):
        old_signature=None; choices=None
        for t,frame in frames(video,recognizer.settings["crop"],fps,ffmpeg,output/f"scan-decode-{block}.log",start,end-start):
            samples += 1
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            if recognizer.gate(gray)<recognizer.settings["gate_threshold"]:
                episodes.push(t,None,frame,None); old_signature=None; continue
            # Small descriptors of only the offered choices ignore moving chat/camera.
            areas=[gray[y:y+h,x:x+w] for x,y,w,h in recognizer.settings["door_search"]]
            descriptor=np.concatenate([cv2.resize(a,(16,32)).ravel() for a in areas]).astype(np.int16)
            if old_signature is None or np.mean(np.abs(descriptor-old_signature))>4:
                choices=recognizer.doors(frame); classified += 1; old_signature=descriptor
            # Keep uncertain partners for review instead of enforcing simulator pair rules.
            plausible=choices and max(c["score"] for c in choices)>=.65 and min(c["score"] for c in choices)>=.30
            episodes.push(t,choices if plausible else None,frame,None)
        episodes.flush()
    temp=output/"candidates.jsonl.tmp"
    with temp.open("w",encoding="utf-8") as stream:
        for record in candidates:
            stream.write(json.dumps(record,allow_nan=False)+"\n")
    temp.replace(output/"candidates.jsonl")
    logs=[p for p in output.glob("*decode*.log") if p.stat().st_size]
    result={"schema":1,"inputs":identity,"source_id":recognizer.settings["source_id"],
            "source_start":source_start,"video":str(video),"video_seconds":gate["duration"],
            "candidate_dungeon_seconds":sum(b-a for a,b in gate["intervals"]),
            "sampled_frames":samples,"template_classifications":classified,"candidates":len(candidates),
            "unreviewed_pair_guesses":dict(guesses),"decode_logs_with_warnings":[p.name for p in logs],
            "processing_seconds":time.perf_counter()-began,
            "calibrated":False,"interpretation":"Candidate extraction only. No game probabilities inferred."}
    write_json(marker,result)
    return result
