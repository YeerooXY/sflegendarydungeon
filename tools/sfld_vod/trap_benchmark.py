"""Evaluate fixed trap suggestions against a source-disjoint native review set."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time

from .trap_detector import TrapDetector, config_hash


def metrics(rows):
    """Abstained positives count as misses; unlabelled truth stays unscored."""
    counts = {'labelled_sides': 0, 'unlabelled_sides': 0, 'positives': 0, 'negatives': 0,
              'true_positive': 0, 'false_positive': 0, 'missed_positive': 0,
              'abstained': 0, 'predicted_positive_unlabelled': 0}
    for truth, prediction in rows:
        if truth is None:
            counts['unlabelled_sides'] += 1
            counts['predicted_positive_unlabelled'] += prediction is True
            continue
        counts['labelled_sides'] += 1
        counts['positives' if truth else 'negatives'] += 1
        counts['abstained'] += prediction is None
        counts['true_positive'] += truth and prediction is True
        counts['false_positive'] += not truth and prediction is True
        counts['missed_positive'] += truth and prediction is not True
    tp, fp = counts['true_positive'], counts['false_positive']
    return {**counts, 'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/counts['positives'] if counts['positives'] else None,
            'coverage': (counts['labelled_sides']-counts['abstained'])/counts['labelled_sides'] if counts['labelled_sides'] else None,
            'false_positive_rate': fp/counts['negatives'] if counts['negatives'] else None}


def validate_manifest(manifest, evidence_root):
    if manifest.get('schema') != 1 or not manifest.get('records'):
        raise ValueError('Benchmark needs a nonempty schema-1 manifest.')
    cfg = manifest['frozen_config']
    if config_hash(cfg) != manifest['frozen_config_sha256']:
        raise ValueError('Frozen detector settings changed.')
    root = Path(evidence_root).resolve()
    ids, hashes, source_splits, groups = set(), {}, {}, {}
    for record in manifest['records']:
        ident = record['observation_id']; split = record['split']
        if ident in ids or split not in ('development', 'held_out'):
            raise ValueError('Duplicate visit or unsupported benchmark split.')
        ids.add(ident)
        source = record['source_id']; group = (source, record['run_id'])
        if source in source_splits and source_splits[source] != split:
            raise ValueError('Source leakage between development and held-out data.')
        if group in groups and groups[group] != split:
            raise ValueError('Run leakage between development and held-out data.')
        source_splits[source] = groups[group] = split
        if len(record['truth']) != 2 or any(x is not None and type(x) is not bool for x in record['truth']):
            raise ValueError('Exactly two Boolean-or-unknown side labels are required.')
        evidence = record['evidence']
        if len(evidence) < 2 or len({e['source_seconds'] for e in evidence}) != len(evidence):
            raise ValueError('Each visit requires distinct source-frame times.')
        for item in evidence:
            path = (root/item['image']).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('Missing frame or evidence path outside root.')
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != item['sha256']:
                raise ValueError('Evidence image hash changed.')
            if actual in hashes:
                if hashes[actual] == ident:
                    raise ValueError('Identical evidence hash reused within one visit.')
                raise ValueError('An evidence frame is reused by multiple visits.')
            hashes[actual] = ident
    return manifest


class LegacyTrapDetector:
    """Use the unchanged legacy trap_hints implementation at 320x224."""
    def __init__(self, art):
        import cv2
        import numpy as np
        from .vision import Recognizer
        self.cv = cv2
        self.recognizer = Recognizer.__new__(Recognizer)
        self.recognizer.cv = cv2
        self.recognizer.traps = [[], []]
        art = Path(art); assets = json.loads((art/'manifest.json').read_text(encoding='utf-8'))['assets']
        for key, filename in assets.items():
            if not key.startswith('trap.'):
                continue
            label = key.split('.', 1)[1]
            for side in (0, 1):
                alternate = assets.get('trap_right.'+label) if side else None
                raw = cv2.imread(str(art/(alternate or filename)), cv2.IMREAD_UNCHANGED)
                if side and not alternate:
                    raw = cv2.flip(raw, 1)
                x, y, w, h = cv2.boundingRect((raw[:, :, 3]>180).astype(np.uint8))
                raw = raw[y:y+h, x:x+w]
                for scale in (.14, .16, .18):
                    height = max(4, round(raw.shape[0]*scale))
                    width = max(2, round(raw.shape[1]*height/raw.shape[0]))
                    template = cv2.resize(raw, (width, height), interpolation=cv2.INTER_AREA)
                    self.recognizer.traps[side].append((label, cv2.cvtColor(template[:, :, :3], cv2.COLOR_BGR2GRAY)))

    def predict_frames(self, frames):
        hints = [self.recognizer.trap_hints(self.cv.resize(f, (320,224), interpolation=self.cv.INTER_AREA)) for f in frames]
        # Give the baseline its original generous any-frame positive rule.
        return [True if any(h[side]['present'] is True for h in hints) else None for side in (0, 1)]


def evaluate(manifest, evidence_root, art, split=None):
    import cv2
    validate_manifest(manifest, evidence_root)
    detector = TrapDetector(art, manifest['frozen_config'])
    if manifest.get('asset_sha256') != detector.asset_hashes:
        raise ValueError('Artwork changed since the benchmark was frozen.')
    legacy = LegacyTrapDetector(art)
    output = []; grouped = defaultdict(lambda: {'new': [], 'legacy': [], 'new_seconds': 0., 'legacy_seconds': 0., 'new_cpu_seconds': 0., 'legacy_cpu_seconds': 0., 'frames': 0, 'offers': 0})
    for record in manifest['records']:
        if split and record['split'] != split:
            continue
        frames = [cv2.imread(str(Path(evidence_root)/e['image'])) for e in record['evidence']]
        began = time.perf_counter(); cpu_began = time.process_time()
        old = legacy.predict_frames(frames)
        old_seconds = time.perf_counter()-began; old_cpu_seconds = time.process_time()-cpu_began
        prediction = detector.predict_frames(frames)
        current = [s['present'] for s in prediction['sides']]
        output.append({**record, 'legacy': old, 'prediction': prediction})
        for key in (record['split'], 'source:'+record['source_id']):
            group = grouped[key]
            group['new'].extend(zip(record['truth'], current)); group['legacy'].extend(zip(record['truth'], old))
            group['new_seconds'] += prediction['processing_seconds']; group['legacy_seconds'] += old_seconds
            group['new_cpu_seconds'] += prediction['cpu_seconds']; group['legacy_cpu_seconds'] += old_cpu_seconds
            group['frames'] += len(frames); group['offers'] += 1
    summaries = {}
    for key, group in grouped.items():
        summaries[key] = {**{k: group[k] for k in ('offers', 'frames', 'new_seconds', 'legacy_seconds', 'new_cpu_seconds', 'legacy_cpu_seconds')},
                          'new': metrics(group['new']), 'legacy': metrics(group['legacy']),
                          'new_seconds_per_offer': group['new_seconds']/group['offers']}
    return {'schema': 1, 'frozen_config_sha256': config_hash(detector.config),
            'asset_sha256': detector.asset_hashes, 'split_summaries': summaries, 'records': output,
            'opencv_version': cv2.__version__, 'opencv_threads': cv2.getNumThreads(),
            'meaning': 'Detector recall/precision on manually reviewed sides, not trap prevalence. Unknown truth is unscored; unknown predictions on positives are misses.',
            'limitations': ['Small same-theme source holdout; other themes and layouts are untested.',
                            'No absence predictions, so coverage is intentionally low.',
                            'Frames already come from reviewed offers; this does not test automatic visit extraction.',
                            'Speed excludes video decoding and includes matching, with templates cached across offers.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--art', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--split', choices=('development', 'held_out'))
    args = parser.parse_args(argv)
    report = evaluate(json.loads(args.manifest.read_text(encoding='utf-8')), args.evidence_root, args.art, args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report['split_summaries'], indent=2))
    return 0
