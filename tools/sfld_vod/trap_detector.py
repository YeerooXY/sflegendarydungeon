"""Conservative native-crop trap suggestions, never observation verification.

Masked coarse-to-fine matching keeps transparent sprite pixels out of the
correlation. A small blur locates thin ropes despite scale mismatch; local
unblurred refinement rejects lookalike background detail. Only repeated,
spatially consistent positive evidence is promoted to a suggestion. Failure to
match is UNKNOWN, not evidence that a doorway is clear.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time


DEFAULT_CONFIG = {
    'schema': 1, 'width': 800, 'height': 560,
    'scales': [.30, .325, .35, .375, .40, .425, .45, .475, .50],
    'coarse_threshold': .68, 'positive_threshold': .80,
    'refine_offsets': [-.015, -.01, -.005, 0, .005, .01, .015],
    'aspect_factors': [.96, 1, 1.04],
    'search_y': [40, 470], 'refine_radius': 24,
    'minimum_frames': 2, 'position_tolerance': 24,
    'minimum_input_width': 600, 'minimum_input_height': 400,
    'minimum_aspect': 1.2, 'maximum_aspect': 1.6,
    'minimum_contrast': 8,
}


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _validate(config):
    if set(config) != set(DEFAULT_CONFIG) or config['schema'] != 1:
        raise ValueError('Trap detector configuration has unsupported fields or schema.')
    for name in ('width', 'height', 'refine_radius', 'minimum_frames', 'position_tolerance',
                 'minimum_input_width', 'minimum_input_height'):
        if type(config[name]) is not int or config[name] < 1:
            raise ValueError(f'{name} must be a positive integer.')
    if config['width'] % 2 or config['minimum_frames'] < 2:
        raise ValueError('An even width and at least two supporting frames are required.')
    for name in ('coarse_threshold', 'positive_threshold'):
        if not isinstance(config[name], (int, float)) or not 0 < config[name] <= 1:
            raise ValueError(f'{name} must be in (0, 1].')
    for name in ('scales', 'aspect_factors'):
        if not config[name] or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in config[name]):
            raise ValueError(f'{name} requires positive finite values.')
    if not config['refine_offsets'] or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in config['refine_offsets']):
        raise ValueError('Refinement offsets must be finite.')
    if len(config['search_y']) != 2 or not 0 <= config['search_y'][0] < config['search_y'][1] <= config['height']:
        raise ValueError('Search bounds must lie within the normalized frame.')


def consensus(frame_results, config):
    """Require subtype and location agreement; no-match never becomes absence."""
    result = []
    for side in (0, 1):
        candidates = [f['sides'][side] for f in frame_results if f.get('usable')]
        matches = {}
        for index, candidate in enumerate(candidates):
            for match in candidate.get('candidates', []):
                if match['score'] >= config['positive_threshold']:
                    matches.setdefault(match['visual'], []).append((index, match))
        groups = []
        for visual, occurrences in matches.items():
            for _, center in occurrences:
                cx = center['bbox'][0] + center['bbox'][2] / 2
                cy = center['bbox'][1] + center['bbox'][3] / 2
                group = []
                for index, match in occurrences:
                    mx = match['bbox'][0] + match['bbox'][2] / 2
                    my = match['bbox'][1] + match['bbox'][3] / 2
                    if math.hypot(mx-cx, my-cy) <= config['position_tolerance']:
                        group.append((index, match))
                # One frame can provide at most one vote for a subtype.
                by_frame = {}
                for index, match in group:
                    if index not in by_frame or by_frame[index]['score'] < match['score']:
                        by_frame[index] = match
                if by_frame:
                    groups.append((len(by_frame), min(m['score'] for m in by_frame.values()), visual, list(by_frame.values())))
        groups.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best = groups[0] if groups else (0, 0, None, [])
        competing = {g[2] for g in groups if g[0] >= config['minimum_frames']}
        positive = best[0] >= config['minimum_frames'] and len(competing) == 1
        ranked = sorted((m for c in candidates for m in c.get('candidates', [])), key=lambda x: x['score'], reverse=True)
        result.append({
            'present': True if positive else None,
            'visual': best[2] if positive else None,
            'score': round(best[1] if positive else (ranked[0]['score'] if ranked else 0), 5),
            'support_frames': best[0],
            'reason': ('consistent_positive_match' if positive else
                       'conflicting_subtypes' if len(competing) > 1 else
                       'insufficient_usable_frames' if len(candidates) < config['minimum_frames'] else
                       'no_consistent_positive_match'),
            'candidates': ranked[:3],
        })
    return result


class TrapDetector:
    """Accept BGR native gameplay crops showing the same, already grouped offer.

    This component does not determine whether frames belong to one visit. That
    grouping and frame timestamp/hash validation belong to the caller. The
    expected crop is the full dungeon panel, including its bottom health HUD,
    rather than the full streamer desktop or an individual doorway.
    """
    def __init__(self, art_directory, config=None):
        import cv2
        import numpy as np
        self.cv, self.np = cv2, np
        self.config = json.loads(json.dumps(DEFAULT_CONFIG if config is None else config))
        _validate(self.config)
        art = Path(art_directory).resolve()
        manifest = json.loads((art / 'manifest.json').read_text(encoding='utf-8'))
        assets = manifest['assets']
        self.sprites = [dict(), dict()]
        self.templates = [[], []]
        self.asset_hashes = {}
        self._refined_templates = {}
        for key, filename in sorted(assets.items()):
            if not key.startswith('trap.'):
                continue
            label = key.split('.', 1)[1]
            for side in (0, 1):
                right_name = assets.get('trap_right.' + label) if side else None
                name = right_name or filename
                path = (art / name).resolve()
                if not path.is_relative_to(art):
                    raise ValueError('Trap sprite path escapes the artwork directory.')
                raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if raw is None or raw.ndim != 3 or raw.shape[2] != 4:
                    raise ValueError(f'Missing RGBA trap sprite: {name}')
                self.asset_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
                if side and not right_name:
                    raw = cv2.flip(raw, 1)
                mask = (raw[:, :, 3] > 180).astype('uint8')
                if np.count_nonzero(mask) < 25:
                    raise ValueError(f'Empty trap sprite: {name}')
                x, y, w, h = cv2.boundingRect(mask)
                raw = raw[y:y+h, x:x+w]
                self.sprites[side][label] = raw
                for scale in self.config['scales']:
                    gray, alpha = self._template(side, label, scale, scale)
                    blur = cv2.GaussianBlur(gray, (5, 5), 1.3)
                    self.templates[side].append((label, scale, blur, alpha))
        if not self.templates[0]:
            raise ValueError('Artwork manifest has no trap sprites.')

    def _template(self, side, label, sx, sy):
        key = (side, label, round(sx, 7), round(sy, 7))
        if key not in self._refined_templates:
            raw = self.sprites[side][label]
            resized = self.cv.resize(raw, None, fx=sx, fy=sy, interpolation=self.cv.INTER_AREA)
            gray = self.cv.cvtColor(resized[:, :, :3], self.cv.COLOR_BGR2GRAY)
            mask = (resized[:, :, 3] > 180).astype('uint8') * 255
            self._refined_templates[key] = gray, mask
        return self._refined_templates[key]

    def _match(self, area, gray, mask):
        if gray.shape[0] > area.shape[0] or gray.shape[1] > area.shape[1] or not self.np.count_nonzero(mask):
            return -1., (0, 0)
        raw = self.cv.matchTemplate(area, gray, self.cv.TM_CCOEFF_NORMED, mask=mask)
        # Constant pixels under a mask produce NaN/Inf, never a perfect match.
        scores = self.np.nan_to_num(raw, nan=-1., posinf=-1., neginf=-1.)
        _, score, _, location = self.cv.minMaxLoc(scores)
        return float(min(1., score)), location

    def read(self, frame):
        cv, cfg = self.cv, self.config
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != self.np.uint8:
            raise ValueError('Trap recognition requires a uint8 BGR image.')
        height, width = frame.shape[:2]
        if (width < cfg['minimum_input_width'] or height < cfg['minimum_input_height'] or
                not cfg['minimum_aspect'] <= width / height <= cfg['maximum_aspect']):
            return {'usable': False, 'reason': 'unsupported_crop_or_resolution', 'sides': [{}, {}]}
        image = cv.resize(frame, (cfg['width'], cfg['height']), interpolation=cv.INTER_AREA)
        gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
        y0, y1 = cfg['search_y']
        if gray[y0:y1].std() < cfg['minimum_contrast']:
            return {'usable': False, 'reason': 'low_contrast_or_transition', 'sides': [{}, {}]}
        blurred = cv.GaussianBlur(gray, (5, 5), 1.3)
        sides = []
        half = cfg['width'] // 2
        for side in (0, 1):
            xbase = half * side
            area = gray[y0:y1, xbase:xbase+half]
            coarse_area = blurred[y0:y1, xbase:xbase+half]
            coarse = {}
            for label, scale, template, mask in self.templates[side]:
                score, xy = self._match(coarse_area, template, mask)
                if label not in coarse or score > coarse[label]['score']:
                    coarse[label] = {'score': score, 'scale': scale, 'xy': xy, 'size': template.shape[::-1]}
            matches = []
            for label, proposal in coarse.items():
                if proposal['score'] < cfg['coarse_threshold']:
                    continue
                px, py = proposal['xy']; pw, ph = proposal['size']; radius = cfg['refine_radius']
                x = max(0, px-radius); y = max(0, py-radius)
                local = area[y:min(area.shape[0], py+ph+radius), x:min(area.shape[1], px+pw+radius)]
                best = {'score': -1., 'visual': label, 'coarse_score': round(proposal['score'], 5)}
                for offset in cfg['refine_offsets']:
                    sy = proposal['scale'] + offset
                    for aspect in cfg['aspect_factors']:
                        sx = sy * aspect
                        template, mask = self._template(side, label, sx, sy)
                        score, xy = self._match(local, template, mask)
                        if score > best['score']:
                            best.update(score=round(score, 5), bbox=[xbase+x+xy[0], y0+y+xy[1], template.shape[1], template.shape[0]],
                                        scale=[round(sx, 5), round(sy, 5)])
                if best['score'] >= 0:
                    matches.append(best)
            matches.sort(key=lambda m: m['score'], reverse=True)
            sides.append({'candidates': matches[:3], 'coarse_best_score': round(max(v['score'] for v in coarse.values()), 5)})
        return {'usable': True, 'reason': 'native_crop_matched', 'sides': sides}

    def predict_frames(self, frames):
        began = time.perf_counter()
        cpu_began = time.process_time()
        results = [self.read(frame) for frame in frames]
        return {'schema': 1, 'method': 'alpha_masked_multiscale_temporal_v1',
                'config_sha256': config_hash(self.config), 'frame_count': len(results),
                'usable_frame_count': sum(bool(r['usable']) for r in results),
                'sides': consensus(results, self.config), 'frames': results,
                'processing_seconds': time.perf_counter()-began,
                'cpu_seconds': time.process_time()-cpu_began,
                'status': 'machine_suggestion_not_verified',
                'limitation': 'No matching overlay remains unknown; this does not establish trap absence or visit identity.'}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--art', required=True, type=Path)
    parser.add_argument('--frames', required=True, type=Path, nargs='+', help='Two or more native crops of one offer.')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if len(args.frames) < 2 or len({path.resolve() for path in args.frames}) != len(args.frames):
        parser.error('Supply at least two distinct frame files from the same offer.')
    evidence = [{'image': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in args.frames]
    if len({item['sha256'] for item in evidence}) != len(evidence):
        parser.error('Identical image content cannot count as multiple supporting frames, even under different file names.')
    import cv2
    config = json.loads(args.config.read_text(encoding='utf-8')) if args.config else None
    detector = TrapDetector(args.art, config)
    frames = [cv2.imread(str(path)) for path in args.frames]
    result = detector.predict_frames(frames)
    result['evidence'] = evidence
    result['asset_sha256'] = detector.asset_hashes
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'sides', 'frame_count', 'processing_seconds')}))
    return 0
