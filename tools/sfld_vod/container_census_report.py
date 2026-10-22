"""Audit and summarize a frozen container census without changing model defaults."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import math
from pathlib import Path

from .container_reanalysis import ROOT, FLAGS, ORDINARY, merge_reviews, read_rows, sha256, validate
from .media import write_json

COHORT = 'current_ordinary_census_2026_10_09'


def wilson(positive, total):
    if not total:
        return {'positive': positive, 'denominator': total, 'rate': None, 'wilson95': None}
    z = 1.959963984540054
    p = positive / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return {'positive': positive, 'denominator': total, 'rate': p,
            'wilson95': [max(0., center - half), min(1., center + half)]}


def blessing(row):
    flags = row['reward_flags']
    values = [flags.get('persistent_blessing'), flags.get('instant_healing')]
    if any(v is True for v in values):
        return True
    return False if all(v is False for v in values) else None


def counts(values):
    return dict(sorted(Counter(str(x) for x in values).items()))


def eligible(row):
    return (row['interaction'] == 'opened' and row['initial_style'] in ORDINARY
            and row['reward_complete'] and row['context'].get('stone_status') == 'verified'
            and row.get('identity_review', {}).get('status') not in {'mismatch', 'unknown'}
            and row.get('stone_context_review', {}).get('status') != 'unknown')


def summarize(rows):
    complete = [r for r in rows if eligible(r)]
    flags = {}
    for key in (*FLAGS, 'any_blessing'):
        values = [blessing(r) if key == 'any_blessing' else r['reward_flags'].get(key) for r in complete]
        positive, unknown = sum(v is True for v in values), sum(v is None for v in values)
        flags[key] = {**wilson(positive, len(values) - unknown), 'unknown': unknown,
                      'rate_basis': 'Known outcomes among complete eligible openings',
                      'identification_bounds': [positive / len(values), (positive + unknown) / len(values)] if values else None}
        if key in {'epic_item', 'legendary_item'} and unknown:
            flags[key]['rate'] = None
            flags[key]['wilson95'] = None
            flags[key]['rate_basis'] = 'Suppressed: unidentified item rarities can bias the known-outcome denominator; use identification bounds.'
    joint = Counter()
    for r in complete:
        b, i, g = blessing(r), r['reward_flags']['item'], r['reward_flags']['gold']
        if None not in (b, i, g):
            joint[str(int(b) + 2 * int(i) + 4 * int(g))] += 1
    return {'visits': len(rows), 'interactions': counts(r['interaction'] for r in rows),
            'complete_ordinary_openings': len(complete), 'rewards': flags,
            'joint_blessing_item_gold_mask': {str(k): joint[str(k)] for k in range(8)},
            'styles': counts(r['initial_style'] for r in rows),
            'sources': counts(r['source_id'] for r in complete),
            'stages': counts(r['context'].get('stage') for r in complete),
            'observed_runs': len({(r['source_id'], r['context'].get('run_id')) for r in complete}),
            'run_class': counts(r['context'].get('run_class') for r in complete)}


def progress_stratum(row):
    context = row['context']
    room = context.get('room')
    if isinstance(room, (int, float)) and 1 <= room <= 5:
        return 'known_rooms_1_to_5'
    if isinstance(room, (int, float)) and room > 5 or context.get('stage') in {2, 3, 4}:
        return 'known_beyond_room_5'
    return 'opening_status_unknown'


def split(rows):
    return {name: summarize([r for r in rows if ('greed' in r['context'].get('gems', [])) == greed])
            for name, greed in [('without_greed', False), ('with_greed', True)]}


def strata(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[str(key(row))].append(row)
    return {name: split(group) for name, group in sorted(groups.items())}


def matched_comparison(rows):
    """Greed-group standardization on observed source/stage/style overlap only."""
    groups = defaultdict(lambda: [[], []])
    for row in rows:
        if not eligible(row) or blessing(row) is None or 'hero' in row['context'].get('gems', []):
            continue
        key = (row['source_id'], str(row['context'].get('stage')), row['initial_style'])
        groups[key][int('greed' in row['context'].get('gems', []))].append(row)
    result = []
    for key, (base, greed) in sorted(groups.items()):
        if not base or not greed:
            continue
        result.append({'source_id': key[0], 'stage': key[1], 'style': key[2],
                       'without_greed': wilson(sum(blessing(r) for r in base), len(base)),
                       'with_greed': wilson(sum(blessing(r) for r in greed), len(greed))})
    n = sum(s['with_greed']['denominator'] for s in result)
    p0 = sum(s['without_greed']['rate'] * s['with_greed']['denominator'] for s in result) / n if n else None
    p1 = sum(s['with_greed']['positive'] for s in result) / n if n else None
    return {'method': 'Source/stage/style overlap; baseline weighted by Greed opening counts. Hero excluded. Descriptive only; other stones, runs and player choices remain confounded.',
            'matched_greed_openings': n, 'standardized_baseline': p0, 'observed_greed': p1,
            'difference': p1 - p0 if n else None, 'ratio': p1 / p0 if p0 else None,
            'relative_20_percent_prediction': min(1., p0 * 1.2) if n else None,
            'additive_20_percentage_point_prediction': min(1., p0 + .2) if n else None,
            'strata': result}


def combine(baseline, manifest, reviews, allow_partial=False):
    """Refuse overlaps, scope drift and unverified negative rewards before counting."""
    frozen = {r['observation_id']: r for r in manifest['rows']}
    if len(frozen) != len(manifest['rows']):
        raise ValueError('Duplicate frozen candidate IDs')
    original = {r['observation_id']: r for r in baseline}
    if len(original) != len(baseline):
        raise ValueError('Duplicate baseline IDs')
    seen = set()
    for review in reviews:
        ident = review['observation_id']
        if ident in seen or ident not in frozen or ident not in original:
            raise ValueError(f'Duplicate or out-of-scope review: {ident}')
        seen.add(ident)
        if original[ident]['reward_review_status'] != 'unreviewed':
            raise ValueError(f'Review would overwrite previous evidence: {ident}')
        if review['source_id'] != frozen[ident]['source_id']:
            raise ValueError(f'Source mismatch: {ident}')
        cohort = review.get('native_cohort', {})
        if cohort.get('name') != COHORT or cohort.get('index') != frozen[ident]['index']:
            raise ValueError(f'Wrong cohort identity: {ident}')
        if manifest.get('local_manifest_sha256') and cohort.get('manifest_sha256') != manifest['local_manifest_sha256']:
            raise ValueError(f'Wrong frozen manifest proof: {ident}')
        if not review.get('evidence'):
            raise ValueError(f'Missing native evidence: {ident}')
        native = [e for e in review['evidence'] if e.get('role') != 'canonical_offer_identity_reference']
        if not native:
            raise ValueError(f'Missing native evidence: {ident}')
        for proof in native:
            if proof.get('native_height', 0) < 720 or not proof.get('video_sha256') or not proof.get('sha256'):
                raise ValueError(f'Incomplete native proof: {ident}')
            if not isinstance(proof.get('decoded_source_seconds'), (int, float)):
                raise ValueError(f'Missing decoded timestamp: {ident}')
        for proof in review['evidence']:
            if not proof.get('sha256'):
                raise ValueError(f'Missing image hash: {ident}')
        if not review.get('complete') and any(v is False for v in review.get('reward_flags', {}).values()):
            raise ValueError(f'Incomplete review asserts a negative reward: {ident}')
    if seen != set(frozen) and not allow_partial:
        raise ValueError(f'Census incomplete: {len(seen)}/{len(frozen)} reviewed')
    rows = copy.deepcopy(baseline)
    merge_reviews(rows, [], [], reviews)
    for row in rows:
        context = row.get('stone_context_review', {})
        if context.get('status') == 'corrected':
            if not isinstance(context.get('observed_gems'), list):
                raise ValueError('Stone correction lacks observed gems')
            row['canonical_context_before_correction'] = copy.deepcopy(row['context'])
            row['context']['gems'] = sorted(context['observed_gems'])
            row['context']['stone_status'] = 'verified'
        elif context.get('status') == 'confirmed' and 'observed_gems' in context:
            if sorted(context['observed_gems']) != sorted(row['context'].get('gems', [])):
                raise ValueError('Confirmed stone context differs from canonical context; record correction explicitly')
    validate(rows)
    return rows


def report(rows, manifest, reviewed_ids):
    frozen = {r['observation_id']: r for r in manifest['rows']}
    candidates = set(frozen)
    new = [r for r in rows if r['observation_id'] in reviewed_ids]
    current = [r for r in rows if r['source_id'] != 'youtube-DCb80VikGM0']
    current_ordinary = [r for r in current if r['initial_style'] in ORDINARY]
    no_hero = [r for r in current_ordinary if 'hero' not in r['context'].get('gems', [])]
    return {'schema': 1, 'cohort': COHORT,
            'completion_status': 'complete' if candidates == reviewed_ids else 'in_progress',
            'coverage': {'frozen_candidates': len(candidates), 'new_reviewed': len(new),
                         'remaining_census': len(candidates - reviewed_ids),
                         'all_container_candidates': len(rows),
                         'all_reward_reviewed': sum(r['reward_review_status'] != 'unreviewed' for r in rows),
                         'remaining_current_event_candidates': sum(r['reward_review_status'] == 'unreviewed' for r in current)},
            'new_census': summarize(new), 'new_census_by_greed': split(new),
            'current_event_ordinary_all_reviewed': split(current_ordinary),
            'current_event_ordinary_excluding_hero': split(no_hero),
            'current_event_by_style': strata(current_ordinary, lambda r: r['initial_style']),
            'current_event_by_style_excluding_hero': strata(no_hero, lambda r: r['initial_style']),
            'current_event_by_source': strata(current_ordinary, lambda r: r['source_id']),
            'current_event_by_stage': strata(current_ordinary, lambda r: r['context'].get('stage')),
            'current_event_by_progress': strata(current_ordinary, progress_stratum),
            'current_event_by_full_stone_set': strata(current_ordinary, lambda r: ','.join(sorted(r['context'].get('gems', []))) or '(none)'),
            'matched_comparison_excluding_hero': matched_comparison(current_ordinary),
            'census_review_audit': {
                'identity_status': counts(r.get('identity_review', {}).get('status') for r in new),
                'stone_status': counts(r.get('stone_context_review', {}).get('status') for r in new),
                'incomplete_ids': [r['observation_id'] for r in new if r['interaction'] in {'opened', 'mimic_defeated'} and not r['reward_complete']],
                'stone_corrections': [{'observation_id': r['observation_id'], 'before': r['canonical_context_before_correction']['gems'], 'after': r['context']['gems']} for r in new if 'canonical_context_before_correction' in r],
                'initial_style_corrections': [{'observation_id': r['observation_id'], 'before': frozen[r['observation_id']]['initial_style'], 'after': r['initial_style']} for r in new if r['initial_style'] != frozen[r['observation_id']]['initial_style']]},
            'limits': ['Census covers recognized ordinary/chest-unknown candidates in available current-event clips, not every room or all possible missed objects.',
                       'Skipped, false, mimic, incomplete and special-container visits are not ordinary loot failures.',
                       'Historical event, barrels, corpses, arena and special chests are excluded from these ordinary-loot estimates.',
                       'Gold, items and blessings may occur together; marginal reward percentages need not sum to 100%.',
                       'Wilson intervals describe binomial sampling only; outcomes share players, runs and sources. They are not a causal confidence interval for Greed.',
                       'Run first-versus-later-event class is often unknown; full stone sets remain separate in the report.',
                       'The official Greed wording does not identify relative versus percentage-point arithmetic. Model defaults are unchanged.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, default=ROOT/'data/vods/full-container-rewards-2026-10-08.jsonl')
    p.add_argument('--manifest', type=Path, default=ROOT/'data/vods/container-census-manifest-2026-10-09.json')
    p.add_argument('--reviews', type=Path, nargs='+', default=[ROOT/f'data/vods/container-census-{s}-2026-10-09.jsonl' for s in ('a', 'b', 'c', 'root')])
    p.add_argument('--output', type=Path, default=ROOT/'docs/vod-container-census-2026-10-09.json')
    p.add_argument('--dataset', type=Path, default=ROOT/'data/vods/full-container-rewards-2026-10-09.jsonl')
    p.add_argument('--allow-partial', action='store_true')
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if sha256(args.baseline) != manifest['inventory_sha256']:
        raise ValueError('Frozen baseline hash changed')
    reviews = [r for path in args.reviews for r in read_rows(path)]
    rows = combine(read_rows(args.baseline), manifest, reviews, args.allow_partial)
    result = report(rows, manifest, {r['observation_id'] for r in reviews})
    result['inputs'] = [{'path': path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path), 'sha256': sha256(path)}
                        for path in [args.baseline, args.manifest, *args.reviews]]
    write_json(args.output, result)
    args.dataset.parent.mkdir(parents=True, exist_ok=True)
    args.dataset.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in rows), encoding='utf-8')
    print(json.dumps(result['coverage']))


if __name__ == '__main__':
    main()
