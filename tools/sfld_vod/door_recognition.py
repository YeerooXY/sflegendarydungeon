"""Alpha-masked door suggestions; independent of gems and legal-pair rules.

The client artwork has transparent surroundings. Correlating those pixels as
black, as the original scanner does, penalizes doors against the actual room
background. This matcher excludes transparent pixels, retains color, and
searches intermediate sprite scales. It remains a suggestion, never a label.
"""
from __future__ import annotations

import json
from pathlib import Path

from .media import sha256


class MaskedDoorRecognizer:
    def __init__(self, settings_path, art, threshold=.52, margin=.10):
        import cv2
        import numpy as np
        self.cv, self.np = cv2, np
        self.settings = json.loads(Path(settings_path).read_text(encoding='utf-8'))
        if self.settings.get('schema') != 1:
            raise ValueError('Unsupported layout schema.')
        if not 0 <= threshold <= 1 or not 0 <= margin <= 2:
            raise ValueError('Invalid recognition thresholds.')
        self.threshold, self.margin = threshold, margin
        art = Path(art)
        self.art_sha256 = sha256(art / 'manifest.json')
        manifest = json.loads((art / 'manifest.json').read_text(encoding='utf-8'))['assets']
        heights = self.settings.get('template_heights', self.settings.get('pair_template_heights'))
        if not heights or any(type(h) is not int or h < 4 for h in heights):
            raise ValueError('Template heights must be positive integers.')
        self.templates = [[], []]
        self.selection_templates = [[], []]
        for key, filename in manifest.items():
            if not key.startswith('door.'):
                continue
            name = key.split('.', 1)[1]
            left = cv2.imread(str(art / filename), cv2.IMREAD_UNCHANGED)
            if left is None or left.ndim != 3 or left.shape[2] != 4:
                raise ValueError(f'Missing RGBA door sprite: {key}, side 0')
            right_name = manifest.get('door_right.' + name)
            right = cv2.imread(str(art / right_name), cv2.IMREAD_UNCHANGED) if right_name else None
            for side, raw in enumerate((left, right if right_name else cv2.flip(left, 1))):
                if raw is None or raw.ndim != 3 or raw.shape[2] != 4:
                    raise ValueError(f'Missing RGBA door sprite: {key}, side {side}')
                x, y, w, h = cv2.boundingRect((raw[:, :, 3] > 180).astype('uint8'))
                if not w or not h:
                    raise ValueError(f'Empty door sprite: {key}')
                raw = raw[y:y+h, x:x+w]
                for height in range(max(4, min(heights)-4), max(heights)+5, 2):
                    width = max(2, round(raw.shape[1] * height / raw.shape[0]))
                    sprite = cv2.resize(raw, (width, height), interpolation=cv2.INTER_AREA)
                    mask = (sprite[:, :, 3] > 230).astype('uint8')
                    if int(mask.sum()) < 8:
                        continue
                    self.templates[side].append((name, sprite[:, :, :3], mask))
                    if height in heights:
                        self.selection_templates[side].append((name, sprite[:, :, :3], mask))

    def doors(self, frame, *, selection_only=False):
        """Read a game crop at any resolution and return two abstaining scores."""
        cv, np = self.cv, self.np
        frame = cv.resize(frame, (320, 224), interpolation=cv.INTER_AREA)
        choices = []
        for side, (x, y, w, h) in enumerate(self.settings['door_search']):
            area = frame[y:y+h, x:x+w]
            scores = {}
            matches = {}
            templates = self.selection_templates[side] if selection_only else self.templates[side]
            for name, template, mask in templates:
                if template.shape[0] > area.shape[0] or template.shape[1] > area.shape[1]:
                    continue
                response = (cv.matchTemplate(area, template, cv.TM_CCOEFF_NORMED) if selection_only else
                            cv.matchTemplate(area, template, cv.TM_CCOEFF_NORMED, mask=mask))
                response = np.nan_to_num(response, nan=-1, posinf=-1, neginf=-1)
                _, score, _, location = cv.minMaxLoc(response)
                if name not in scores or score > scores[name]:
                    scores[name] = float(score)
                    matches[name] = (location, template, mask)
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            if len(ranked) < 2:
                raise ValueError('At least two usable door classes are required.')
            name, score = ranked[0]
            margin = score - ranked[1][1]
            accepted = score >= self.threshold and margin >= self.margin
            color_support = None
            if name == 'golden':
                # CCOEFF subtracts each channel's mean. A dark red monster can
                # therefore correlate with the bright yellow portal silhouette.
                # Require the portal's yellow color in its matched opaque area;
                # a trap or a legal-pair assumption is not a substitute for it.
                (mx, my), template, mask = matches[name]
                region = area[my:my+template.shape[0], mx:mx+template.shape[1]]
                hsv = cv.cvtColor(region, cv.COLOR_BGR2HSV)
                yellow = ((hsv[:, :, 0] >= 14) & (hsv[:, :, 0] <= 38) &
                          (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 65))
                color_support = float(yellow[mask > 0].mean())
                accepted = accepted and color_support >= .45
            choices.append({'kind': name if accepted else None, 'score': round(score, 4),
                'margin': round(margin, 4), 'alternatives': [[k, round(v, 4)] for k, v in ranked[:3]],
                'trap': None, 'trap_kind': None, 'status': 'unreviewed_suggestion' if accepted else 'unknown',
                'golden_color_support': round(color_support, 4) if color_support is not None else None,
                'method': 'unmasked_frame_selection_only' if selection_only else
                          'alpha_masked_color_multiscale_with_golden_color_check'})
        return choices


def stable_doors(samples, minimum_agreement=2):
    """Require repeated accepted labels and abstain on any accepted conflict.

    Samples must belong to one already established offer. This function does
    not merge scenes or infer a repeated room from its visual similarity.
    """
    if type(minimum_agreement) is not int or minimum_agreement < 2:
        raise ValueError('At least two frames must agree.')
    if any(len(pair) != 2 for pair in samples):
        raise ValueError('Each frame must have two door predictions.')
    result = []
    for side in range(2):
        known = [pair[side] for pair in samples if pair[side].get('kind') is not None]
        labels = {item['kind'] for item in known}
        accepted = len(labels) == 1 and len(known) >= minimum_agreement
        result.append({'kind': next(iter(labels)) if accepted else None,
            'supporting_frames': len(known), 'sampled_frames': len(samples),
            'conflicting_labels': sorted(labels) if len(labels) > 1 else [],
            'status': 'unreviewed_suggestion' if accepted else 'unknown'})
    return result
