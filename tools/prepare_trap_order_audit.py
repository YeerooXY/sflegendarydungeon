"""Freeze outcome-independent trap review cohorts before temporal inspection."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from summarize_vod_cumulative_2026_10_12 import ROOT, ordinary_new

DEST = ROOT / 'data/vods/trap-order-2026-10-12'
SALT = 'trap-order-two-eligible-2026-10-12-v1'


def read(path):
    text = path.read_text(encoding='utf-8-sig')
    return [json.loads(s) for s in text.splitlines()] if path.suffix == '.jsonl' else json.loads(text)


def pack(r):
    return {k: r.get(k) for k in ('id', 'source_id', 'index', 'start', 'end', 'room', 'stage',
                                  'gems', 'stone_set_complete', 'run_id', 'run_class', 'phase')} | {
        'kinds': [d['kind'] for d in r['doors']],
        'review_instruction': 'Read both traps directly across the offer; null if not resolved. Do not infer absence from an ineligible-type rule.',
    }


def prepare():
    offers_file = ROOT / 'data/vods/reviewed-offers-2026-10-12.jsonl'
    offers = [r for r in read(offers_file) if ordinary_new(r)]
    two = [r for r in offers if all(d['kind'] in {'monster', 'mystery'} for d in r['doors'])]
    assert len({r['id'] for r in two}) == len(two)
    ranked = sorted(two, key=lambda r: hashlib.sha256((SALT + ':' + r['id']).encode()).hexdigest())
    sample = ranked[:100]
    sample_ids = {r['id'] for r in sample}
    role = [r for r in two if sorted(d['kind'] for d in r['doors']) == ['monster', 'mystery']]
    union = {r['id']: r for r in sample + role}
    rows = []
    for i, r in enumerate(sorted(union.values(), key=lambda r: (r['source_id'], r['start']))):
        rows.append(pack(r) | {'audit_index': i,
            'prevalence_sample': r['id'] in sample_ids,
            'role_census': r in role,
            'assigned_cohort': 'monster_mystery' if r in role else 'double_mystery'})
    prior = ROOT / 'data/vods/latest-event-review-2026-10-12/mystery-sequence-review.json'
    # Select every previously verified double-mystery entry, irrespective of outcome,
    # side, gems or traps. Separate outcome-observed cohort, NOT a prevalence sample.
    lookup = {r['id']: r for r in offers}
    matched = []
    for r in read(prior)['rows']:
        o = lookup.get(r['id'])
        if (o and r['status'] == 'verified_mystery_entry'
                and [d['kind'] for d in o['doors']] == ['mystery', 'mystery']):
            matched.append(pack(o) | {'audit_index': len(matched),
                'existing_sequence_manifest': r.get('sequence_manifest'),
                'review_instruction': 'Directly review both offered traps and selected side from zoom/opening transition. Do not infer side from contents. Also label initial living monster versus corpse before search. Keep unresolved side null.'})
    result = {'schema': 1, 'date': '2026-10-12', 'salt': SALT,
        'sampling': 'SHA256-ranked simple sample of 100 ordinary two-eligible offers, plus separate complete monster/mystery role census. No selection by traps or contents.',
        'exclusions': 'Opening rooms 1-5, bosses at 25/50/75/100, shop/trial/forced phases; unknown room only admitted with verified ordinary phase and stage 2-4. Physical doors, walls and golden alternatives are outside the core two-eligible population.',
        'population_two_eligible': len(two), 'population_pair_counts': dict(Counter('/'.join(sorted(d['kind'] for d in r['doors'])) for r in two)),
        'prevalence_n': len(sample), 'role_census_n': len(role),
        'inputs_sha256': {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in (offers_file, prior)},
        'rows': rows, 'matched_entry_rows': matched}
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / 'frozen-plan.json'
    if target.exists():
        assert read(target) == result, 'Do not overwrite an already frozen cohort.'
    else:
        target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'population': len(two), 'sample': len(sample), 'role': len(role),
                      'assigned': dict(Counter(r['assigned_cohort'] for r in rows)), 'matched_entries': len(matched)}))


if __name__ == '__main__':
    prepare()
