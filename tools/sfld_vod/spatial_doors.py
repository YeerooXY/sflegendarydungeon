"""Door suggestions supported by separate visible regions of the client sprite.

Opaque pixels in three horizontal bands locate the sprite. Six non-overlapping
regions then check structure and chromaticity at that *same* position. This
lets a trap obscure one part without treating the rest as a different door.
Scores are image similarities, not calibrated confidence probabilities.
"""
from __future__ import annotations

import json
from pathlib import Path


class SpatialDoorRecognizer:
    def __init__(self, settings_path, art):
        import cv2
        import numpy as np
        self.cv, self.np = cv2, np
        self.settings = json.loads(Path(settings_path).read_text(encoding='utf-8'))
        if self.settings.get('schema') != 1:
            raise ValueError('Unsupported layout schema.')
        heights = self.settings.get('template_heights', self.settings.get('pair_template_heights'))
        if not heights or any(type(h) is not int or h < 6 for h in heights):
            raise ValueError('Template heights must be positive integers.')
        self.heights = sorted(set(heights))
        art = Path(art)
        assets = json.loads((art / 'manifest.json').read_text(encoding='utf-8'))['assets']
        self.raw = [dict(), dict()]
        self.templates = [dict(), dict()]
        for key, filename in assets.items():
            if not key.startswith('door.'):
                continue
            name = key.split('.', 1)[1]
            left = cv2.imread(str(art / filename), cv2.IMREAD_UNCHANGED)
            right_name = assets.get('door_right.' + name)
            right = cv2.imread(str(art / right_name), cv2.IMREAD_UNCHANGED) if right_name else None
            for side, raw in enumerate((left, right if right_name else cv2.flip(left, 1))):
                if raw is None or raw.ndim != 3 or raw.shape[2] != 4:
                    raise ValueError(f'Missing RGBA door sprite: {name}, side {side}')
                x, y, w, h = cv2.boundingRect((raw[:, :, 3] > 180).astype('uint8'))
                if not w or not h:
                    raise ValueError('Empty door sprite: ' + name)
                self.raw[side][name] = raw[y:y+h, x:x+w]
                for height in self.heights:
                    self._template(side, name, height)

    def _template(self, side, name, height):
        key = (name, height)
        if key not in self.templates[side]:
            cv, np = self.cv, self.np
            raw = self.raw[side][name]
            width = max(2, round(raw.shape[1] * height / raw.shape[0]))
            sprite = cv.resize(raw, (width, height), interpolation=cv.INTER_AREA)
            mask = (sprite[:, :, 3] > 230).astype('uint8')
            bgr = sprite[:, :, :3].copy()
            bands, regions = [], []
            for row in range(3):
                y0, y1 = round(height * row / 3), round(height * (row + 1) / 3)
                band_mask = mask[y0:y1]
                if int(band_mask.sum()) >= 16:
                    bands.append((y0, y1, bgr[y0:y1], band_mask))
                for column in range(2):
                    x0, x1 = round(width * column / 2), round(width * (column + 1) / 2)
                    use = mask[y0:y1, x0:x1] > 0
                    if int(use.sum()) < 16:
                        continue
                    values = bgr[y0:y1, x0:x1][use].astype(np.float32)
                    centered = values - values.mean(axis=0)
                    energy = float(np.sum(centered * centered))
                    color = values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1)
                    regions.append((row, column, x0, y0, x1, y1, use, centered, energy, color, values.max(axis=1)))
            self.templates[side][key] = (bgr, mask, bands, regions)
        return self.templates[side][key]

    def _locate(self, area, template):
        cv, np = self.cv, self.np
        bgr, mask, bands, _ = template
        h, w = bgr.shape[:2]
        if h > area.shape[0] or w > area.shape[1] or len(bands) < 2:
            return None
        scores = []
        for y0, y1, band, band_mask in bands:
            crop = area[y0:area.shape[0]-h+y1]
            response = cv.matchTemplate(crop, band, cv.TM_CCOEFF_NORMED, mask=band_mask)
            scores.append(np.nan_to_num(response, nan=-1., posinf=-1., neginf=-1.))
        ordered = np.sort(np.stack(scores), axis=0)
        # One occluded band must not erase two independently matching bands.
        response = .8 * ordered[-2:].mean(axis=0) + .2 * np.maximum(ordered[0], 0)
        _, score, _, location = cv.minMaxLoc(response)
        return float(score), location

    def _verify(self, area, template, location):
        cv, np = self.cv, self.np
        bgr, mask, _, regions = template
        x, y = location
        h, w = bgr.shape[:2]
        crop = area[y:y+h, x:x+w]
        checks = []
        for row, column, x0, y0, x1, y1, use, centered, energy, colors, brightness in regions:
            values = crop[y0:y1, x0:x1][use].astype(np.float32)
            obs = values - values.mean(axis=0)
            denom = (energy * float(np.sum(obs * obs))) ** .5
            structure = float(np.sum(centered * obs) / denom) if denom > 1e-5 else -1.
            chroma = values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1)
            light = np.minimum(brightness, values.max(axis=1))
            visible = light >= 12
            color_score = float(np.average(np.sum(chroma[visible] * colors[visible], axis=1), weights=light[visible])) if visible.sum() >= 8 else 0.
            checks.append({'row': row, 'column': column, 'pixels': int(use.sum()),
                           'structure': round(structure, 4), 'color': round(color_score, 4),
                           'support': structure >= .48 and color_score >= .90})
        supported = [r for r in checks if r['support']]
        values = sorted((max(0., r['structure']) * r['color'] for r in checks), reverse=True)
        score = (.8 * sum(values[:4]) / 4 + .2 * sum(values) / len(values)) if values else 0.
        hsv = cv.cvtColor(crop, cv.COLOR_BGR2HSV)
        use = mask > 0
        red = (hsv[:, :, 0] < 12) | (hsv[:, :, 0] > 170)
        red &= (hsv[:, :, 1] >= 140) & (hsv[:, :, 2] >= 18)
        yellow = (hsv[:, :, 0] >= 14) & (hsv[:, :, 0] <= 38)
        yellow &= (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 65)
        return {'score': score, 'regions': checks, 'support_regions': len(supported),
                'support_rows': len({r['row'] for r in supported}),
                'support_columns': len({r['column'] for r in supported}),
                'red_support': float(red[use].mean()), 'golden_support': float(yellow[use].mean()),
                'bbox': [x, y, w, h]}

    def doors(self, frame):
        cv, np = self.cv, self.np
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError('Door recognition requires a uint8 BGR image.')
        frame = cv.resize(frame, (320, 224), interpolation=cv.INTER_AREA)
        results = []
        for side, (x, y, w, h) in enumerate(self.settings['door_search']):
            area = frame[y:y+h, x:x+w]
            candidates = {}
            for name in self.raw[side]:
                best = None
                for height in self.heights:
                    template = self._template(side, name, height)
                    match = self._locate(area, template)
                    if match is not None and (best is None or match[0] > best[0]):
                        best = (match[0], height, match[1])
                if best is not None:
                    candidates[name] = best
            ranked = sorted(candidates, key=lambda n: candidates[n][0], reverse=True)
            verified = []
            for name in ranked[:6]:
                _, height, location = candidates[name]
                best = None
                for refined_height in range(max(6, height-4), height+5, 2):
                    template = self._template(side, name, refined_height)
                    # Refining scale locally is cheaper than repeating all full searches.
                    th, tw = template[0].shape[:2]
                    cx, cy = location[0] + (self._template(side, name, height)[0].shape[1]-tw)//2, location[1]+(height-th)//2
                    left, top = max(0, cx-4), max(0, cy-4)
                    right, bottom = min(area.shape[1], cx+tw+5), min(area.shape[0], cy+th+5)
                    local = area[top:bottom, left:right]
                    match = self._locate(local, template)
                    if match is None:
                        continue
                    point = (match[1][0]+left, match[1][1]+top)
                    details = self._verify(area, template, point)
                    if best is None or details['score'] > best['score']:
                        best = details
                if best is not None:
                    best['name'] = name
                    # Glow color is indispensable: normalized correlation alone
                    # can confuse the red monster with the yellow golden portal.
                    best['color_gate'] = (name != 'golden' or best['golden_support'] >= .45) and (
                        name != 'monster' or best['red_support'] >= .35)
                    verified.append(best)
            verified.sort(key=lambda r: r['score'] if r['color_gate'] else -1, reverse=True)
            if not verified:
                results.append({'kind': None, 'status': 'unknown', 'method': 'spatial_regions_v1'})
                continue
            best = verified[0]
            rival = next((r for r in verified[1:] if r['color_gate']), None)
            margin = best['score'] - (rival['score'] if rival else 0)
            region_gate = (best['support_regions'] >= 4 and best['score'] >= .62) or (
                best['support_regions'] >= 3 and best['score'] >= .72) or (
                best['name'] == 'golden' and best['support_regions'] >= 3 and
                best['golden_support'] >= .90 and best['score'] >= .60)
            accepted = (best['color_gate'] and region_gate and margin >= .10 and
                        best['support_rows'] >= 2 and best['support_columns'] == 2)
            results.append({'kind': best['name'] if accepted else None, 'status': 'unreviewed_suggestion' if accepted else 'unknown',
                            'score': round(best['score'], 4), 'margin': round(margin, 4),
                            'alternatives': [[r['name'], round(r['score'], 4)] for r in verified[:3]],
                            'regions': best['regions'], 'support_regions': best['support_regions'],
                            'bbox': [x+best['bbox'][0], y+best['bbox'][1], *best['bbox'][2:]],
                            'red_support': round(best['red_support'], 4), 'golden_color_support': round(best['golden_support'], 4),
                            'method': 'spatial_regions_v1', 'details': verified})
        return results
