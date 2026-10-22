"""Contextual preference points from the frozen adviser, not measured win odds."""
from itertools import combinations, permutations
import numpy as np


def tier(points):
    return next(grade for threshold, grade in ((90, 'S'), (75, 'A'), (60, 'B'), (40, 'C'), (20, 'D'), (0, 'F')) if points >= threshold)


def rate_stones(policy, o, vector, profile):
    offered = o.get('gem_offers', [])
    if o['phase'] != 'gems' or len(offered) != 3:
        return None
    pool_key = 'gems_first_run' if o['run_number'] == 1 else 'gems_later_runs'
    pool = profile.get(pool_key, profile.get('gems', ','.join(offered))).split(',')
    previous = set(o['offered_gems']) - set(offered)
    exclude = profile.get('exclude_offered_gems', 'false') == 'true'
    pool = sorted(set(offered) | {g.strip() for g in pool if g.strip() not in o['gems'] and (not exclude or g.strip() not in previous)})
    names = {n: i for i, n in enumerate(policy.names)}
    # Balance every offer position and third-stone context. This avoids making
    # a mediocre stone S-tier solely because its two actual companions are poor.
    menus = [order for triple in combinations(pool, 3) if set(triple) & set(offered) for order in permutations(triple)]
    totals = {g: [0., 0] for g in offered}
    blank = vector[0].copy()
    for name, i in names.items():
        if name.startswith('gem_offers.') or (name.startswith('offered_gem.') and name.split('.', 1)[1] not in previous):
            blank[i] = 0
    action_ids = [policy.actions.index('gem_' + str(i)) for i in range(3)]
    for start in range(0, len(menus), 512):
        chunk = menus[start:start+512]
        batch = np.tile(blank, (len(chunk), 1))
        for row, menu in enumerate(chunk):
            for position, gem in enumerate(menu):
                batch[row, names[f'gem_offers.{position}.{gem}']] = 1
                batch[row, names[f'offered_gem.{gem}']] = 1
        with policy.torch.inference_mode():
            logits = policy.model(policy.torch.as_tensor(batch))[0][:, action_ids].numpy()
        for row, menu in enumerate(chunk):
            for i, gem in enumerate(menu):
                if gem not in totals:
                    continue
                for j in range(3):
                    if i == j:
                        continue
                    difference = np.clip(logits[row, i] - logits[row, j], -60, 60)
                    totals[gem][0] += float(1 / (1 + np.exp(-difference)))
                    totals[gem][1] += 1
    entries = []
    for gem in offered:
        total, count = totals[gem]
        points = round(100 * total / count, 1)
        entries.append({'gem': gem, 'points': points, 'tier': tier(points)})
    return {'method': 'balanced_pairwise_preference_v1', 'entries': entries,
            'pool_size': len(pool), 'owned': list(o['gems']), 'room': o['room'],
            'notice': 'Preference for your current stones from the tested adviser. Points are not win odds or measured extra clears.'}
