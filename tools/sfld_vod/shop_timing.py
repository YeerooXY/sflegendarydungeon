"""Count reviewed shop visits and exact counter positions without inferring a spawn law."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics

from .container_reanalysis import DEFAULT_PLANS, ROOT, read_rows, sha256
from .media import write_json

HISTORICAL = {'youtube-DCb80VikGM0'}


def is_shop(row):
    return any(d.get('kind') == 'shop' for d in (row.get('doors') or []))


def known_room(row):
    room = row.get('context', {}).get('room')
    return room if type(room) is int and 1 <= room <= 100 else None


def visit_identity(row):
    context = row.get('context', {})
    # Equal stage alone cannot identify a visit: that would assume the quota
    # which this report is meant to investigate. Unknown counters need a visit ID.
    room = known_room(row)
    if not context.get('run_id'):
        return (row['source_id'], row['id'])
    if room is not None:
        return (row['source_id'], context['run_id'], 'room', room)
    return (row['source_id'], context['run_id'], 'visit', context.get('visit_id') or row['id'])


def summarize(visits):
    stages = {}
    for stage in range(1, 5):
        group = [v for v in visits if v['context'].get('stage') == stage]
        exact = [v['context']['room'] for v in group if known_room(v) is not None]
        stages[str(stage)] = {
            'visits': len(group), 'exact_room_count': len(exact),
            'unknown_room_count': len(group) - len(exact),
            'minimum_room': min(exact) if exact else None,
            'maximum_room': max(exact) if exact else None,
            'median_room': statistics.median(exact) if exact else None,
            'mean_room': statistics.mean(exact) if exact else None,
            'room_counts': dict(sorted(Counter(exact).items())),
            'stage_offset_counts': dict(sorted(Counter(r - 25 * (stage - 1) for r in exact).items())),
        }
    return {'visits': len(visits), 'exact_room_count': sum(known_room(v) is not None for v in visits),
            'by_stage': stages, 'by_source': dict(sorted(Counter(v['source_id'] for v in visits).items()))}


def audit(rows):
    ids, identities, visits = set(), set(), []
    excluded = Counter()
    for row in sorted(rows, key=lambda r: (r['source_id'], r.get('source_seconds', 0), r['id'])):
        if row['id'] in ids:
            raise ValueError('Duplicate observation ID: ' + row['id'])
        ids.add(row['id'])
        if not is_shop(row):
            continue
        if row.get('status') != 'decision':
            excluded['nondecision_shop_labels'] += 1
            continue
        if 'doors' not in row.get('verified_fields', []):
            excluded['unverified_shop_doors'] += 1
            continue
        context = row.get('context', {})
        room = known_room(row)
        if room is not None and context.get('stage') != (room - 1) // 25 + 1:
            raise ValueError('Shop stage/counter mismatch: ' + row['id'])
        identity = visit_identity(row)
        if identity in identities:
            excluded['repeated_shop_visit'] += 1
            continue
        identities.add(identity)
        visits.append({k: row[k] for k in ('id', 'source_id', 'source_seconds', 'context', 'doors',
                                           'verified_fields', 'evidence') if k in row})
    current = [v for v in visits if v['source_id'] not in HISTORICAL]
    historical = [v for v in visits if v['source_id'] in HISTORICAL]
    passages = defaultdict(list)
    for row in rows:
        context = row.get('context', {})
        if (row.get('status') == 'decision' and 'doors' in row.get('verified_fields', [])
                and row['source_id'] not in HISTORICAL and context.get('run_id')
                and context.get('stage') == 1 and known_room(row) is not None):
            passages[(row['source_id'], context['run_id'])].append(row)
    complete = []
    for (source, run), group in sorted(passages.items()):
        counters = {known_room(row) for row in group}
        if counters != set(range(1, 26)):
            continue
        complete.append({'source_id': source, 'run_id': run, 'unique_verified_door_counters': 25,
                         'shop_observation_ids': [row['id'] for row in group if is_shop(row)],
                         'observations': [{'id': row['id'], 'room': known_room(row),
                                           'source_seconds': row['source_seconds']}
                                          for row in sorted(group, key=lambda r: r['source_seconds'])]})
    return {'schema': 1, 'current_event': summarize(current), 'historical_separate': summarize(historical),
            'excluded': dict(excluded), 'visits': visits,
            'stage1_counter_complete_passages': complete,
            'stage1_counter_complete_summary': {
                'passages': len(complete),
                'with_any_observed_shop': sum(bool(p['shop_observation_ids']) for p in complete),
                'without_observed_shop': sum(not p['shop_observation_ids'] for p in complete),
                'definition': 'At least one decision with verified door kinds at each exact displayed counter 1 through 25 in the same source and reviewed run segment. This is counter coverage, not a proof of continuous playback or the character event-run class.'},
            'limitations': [
                'No uniformity or independence claim. Observed extrema are not proven hard spawn limits.',
                'This counts shop door offers once per reviewed visit; menu draws and rerolls do not add visits.',
                'Unreadable counters are retained in visit coverage but excluded from timing statistics.',
                'Observed passage number does not establish the first run of an event.',
                'Most observations cluster within a few players and one current event; selection and version effects remain.',
                'Curse-for-keys NPCs are not Key Master blessing shops.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/vod-shop-timing-2026-10-09.json')
    parser.add_argument('--native-review', type=Path, default=ROOT / 'data/vods/shop-timing-review-2026-10-09.json')
    args = parser.parse_args()
    rows, inputs = [], []
    for plan in DEFAULT_PLANS:
        for source in json.loads(plan.read_text(encoding='utf-8-sig'))['sources']:
            path = plan.parent / source['observations']
            rows.extend(read_rows(path))
            inputs.append({'source_id': source['source_id'], 'path': path.relative_to(ROOT).as_posix(),
                           'sha256': sha256(path)})
    report = audit(rows)
    report['inputs'] = inputs
    if args.native_review.exists():
        review = json.loads(args.native_review.read_text(encoding='utf-8-sig'))
        by_id = {row['id']: row for row in rows}
        for frame in review['frames']:
            if frame['observation_id'] not in by_id:
                raise ValueError('Native check references an unknown observation')
            if frame['room'] != known_room(by_id[frame['observation_id']]):
                raise ValueError('Native counter check conflicts with canonical row')
            if frame['sha256'] != sha256(ROOT / frame['image']):
                raise ValueError('Native review image changed')
            if frame['native_height'] < 720 or abs(frame['decoded_source_seconds'] - frame['source_seconds']) > .1:
                raise ValueError('Native check has an unsuitable source frame')
        report['native_review'] = {'path': args.native_review.relative_to(ROOT).as_posix(),
                                   'sha256': sha256(args.native_review), 'frame_count': len(review['frames']),
                                   'conclusions': review['conclusions']}
    report['primary_source'] = {
        'checked_on': '2026-10-09',
        'url': 'https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/57-legendary-dungeon/',
        'finding': 'The official manual describes buying blessings at the Key Master shop but supplies no position distribution or shop-room ranges.'}
    write_json(args.output, report)
    print(json.dumps({k: report[k] for k in ('current_event', 'historical_separate', 'stage1_counter_complete_summary')}, indent=2))


if __name__ == '__main__':
    main()
