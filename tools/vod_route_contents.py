"""Tabulate what was behind each door once the route is attributed (vod_route_keys.py).

Joins attributed routes with the ledger's following-scene observation (the first
scene seen after the offer). Strong bases: lock_cost_spent (locked door taken) and
no_key_spent (other door taken, count confirmed after entry). keys_gained_without_
spending is reported separately: a missed drop followed by loot would mislabel it.
Each offer counts once; ledger duplicates are already excluded upstream.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRONG = {'lock_cost_spent', 'no_key_spent'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--routes', type=Path, default=ROOT/'out/vod-route-keys-2026-10-23')
    p.add_argument('--ledger', type=Path, default=ROOT/'data/vods/observations-2026-10-08.jsonl')
    p.add_argument('--weak', action='store_true', help='also count keys_gained_without_spending')
    a = p.parse_args()
    ledger = {}
    for line in a.ledger.read_text(encoding='utf-8').splitlines():
        if line.strip():
            r = json.loads(line);ledger[r['id']] = r
    rows = [r for f in sorted(a.routes.glob('*.json')) for r in json.loads(f.read_text())]
    use = STRONG | ({'keys_gained_without_spending'} if a.weak else set())
    check = Counter();table = defaultdict(Counter);taken = Counter()
    for r in rows:
        if r['basis'] not in use:continue
        row = ledger[r['id']]
        if 'chosen_side' in row.get('verified_fields', []) and row.get('chosen_side') is not None:
            check['agree' if row['chosen_side'] == r['side'] else 'disagree'] += 1
        kinds = r['doors'];entered = kinds[r['side']];other = kinds[1-r['side']]
        pair = ' + '.join(sorted(kinds))
        taken[(pair, entered)] += 1
        scene = (row.get('outcome') or {}).get('following_scene_observation') or 'unknown'
        table[(entered, 'beside '+other)][scene] += 1
    print('verified chosen sides:', dict(check))
    print('\nchoice counts by pair:')
    for (pair, entered), n in sorted(taken.items()):print(f'  {pair:28s} took {entered:14s} {n}')
    print('\nfollowing scene by entered door:')
    for key in sorted(table, key=lambda k: -sum(table[k].values())):
        total = sum(table[key].values())
        print(f'  {key[0]:13s} {key[1]:22s} n={total:4d}  ' + ', '.join(f'{s} {c} ({c/total:.0%})' for s, c in table[key].most_common(8)))


if __name__ == '__main__':
    main()
