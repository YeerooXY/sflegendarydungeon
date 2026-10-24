"""Room-content odds from reviewed door points (vod_door_points.py + vod_door_review.py).

The entered side comes from game rules where they decide it, not from the
reviewer, whose side reading is the weakest label:
* a cursed, sacrifice or hungry door always holds its own chest, so that chest
  means the door was entered, and anything else means the other door was;
* a monster door always holds a monster: a monster beside it is that door,
  anything else is the other door;
* a locked (or unlocked) door never holds a monster: a monster beside a mystery
  door is the mystery door's;
* a wall is never entered.
Other pairs use the reviewer's side. Destiny, epic, double-locked, boss, shop and
trial screens are left out (their side or contents need another pass). Repeated
screens and low-confidence or unknown labels are skipped. Labels are unreviewed
machine reads of 360p video; chest kinds in particular are confused.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHEST = {'cursed': 'cursed_chest', 'sacrifice': 'sacrifice_chest', 'hungry': 'sated_chest'}
SKIP = {'boss', 'shop', 'trial', 'exit_trial', 'destiny', 'epic', 'double_locked'}
PHYSICAL = {'locked', 'unlocked', 'cursed', 'sacrifice', 'blessing', 'hungry', 'wall'}


def stage(room):
    return None if room is None else min(4, 1+(room-1)//25)


def attribute(r):
    """(entered door, other door, how) or None."""
    L, R, c, e = r['left_door'], r['right_door'], r['contents'], r['entered']
    pair = {L, R}
    if pair & SKIP or (L == R and L != 'mystery'):return None
    if 'wall' in pair:return (R if L == 'wall' else L), 'wall', 'wall'
    if pair & set(CHEST):
        s = sorted(pair & set(CHEST))[0];o = (pair-{s} or {s}).pop()
        return (s, o, 'chest rule') if c == CHEST[s] else (o, s, 'chest rule')
    if 'monster' in pair:
        o = (pair-{'monster'}).pop()
        return ('monster', o, 'monster rule') if c in ('monster', 'undead') else (o, 'monster', 'monster rule')
    if pair in ({'locked', 'mystery'}, {'unlocked', 'mystery'}) and c in ('monster', 'undead'):
        return 'mystery', (pair-{'mystery'}).pop(), 'locked rule'
    if pair == {'mystery'}:return 'mystery', 'mystery', 'double mystery'
    if e == 'unknown':return None
    return ((L, R) if e == 'left' else (R, L))+('reviewer',)


def key(d, o):
    if d != 'mystery':return 'locked' if d == 'unlocked' else d
    o = 'locked' if o == 'unlocked' else o
    return f'mystery beside {o}'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--points', type=Path, default=ROOT/'out/vod-door-points-2026-10-24')
    p.add_argument('--json', type=Path, help='write the counts here')
    a = p.parse_args()
    contents = defaultdict(Counter);by_stage = defaultdict(Counter);pairs = Counter();how = Counter();golden = Counter()
    sources = set()
    for f in sorted(a.points.glob('*.reviews.jsonl')):
        source = f.name.split('.')[0];rooms = {q['sheet']: q.get('room') for q in json.loads((a.points/f'{source}.json').read_text())
                                              if q.get('sheet')}
        for line in f.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            if 'unknown' in (r['left_door'], r['right_door']) or r['contents'] == 'unknown' or r['confidence'] == 'low':continue
            sources.add(source)
            fam = lambda d:'door' if d in PHYSICAL else d
            if not {r['left_door'], r['right_door']} & {'boss', 'shop', 'trial', 'exit_trial'}:
                pairs['/'.join(sorted((fam(r['left_door']), fam(r['right_door']))))] += 1
            got = attribute(r)
            if got is None:continue
            d, o, rule = got;how[rule] += 1;k = key(d, o)
            contents[k][r['contents']] += 1
            st = stage(rooms.get(r['sheet']))
            if st and k.startswith('mystery beside'):by_stage[(k, st)][r['contents'] in ('monster', 'undead')] += 1
            if d == 'golden':golden[r['golden_room']] += 1
    print(json.dumps({'sources': len(sources), 'attribution': dict(how)}))
    t = sum(pairs.values());print(f'\nDoor pairs (n={t}, walls count as physical doors)')
    for k, v in pairs.most_common():print(f'  {k:20}{v:7} {v/t:6.1%}')
    print('\nContents of the entered room')
    for k, c in sorted(contents.items(), key=lambda kv:-sum(kv[1].values())):
        t = sum(c.values())
        if t >= 30:print(f'  {k:26}{t:6}  '+', '.join(f'{x} {n/t:.0%}' for x, n in c.most_common(8)))
    print('\nMonster share of mystery rooms by stage (n)')
    for k in sorted({k for k, _ in by_stage}):
        cells = [by_stage[(k, s)] for s in range(1, 5)]
        print(f'  {k:26}'+'  '.join(f'S{s}: {c[True]/max(1,sum(c.values())):.0%} ({sum(c.values())})' for s, c in enumerate(cells, 1)))
    t = sum(golden.values());print(f'\nGolden rooms (n={t})\n  '+', '.join(f'{x} {n/t:.1%}' for x, n in golden.most_common()))
    if a.json:
        a.json.write_text(json.dumps({'pairs': pairs, 'contents': contents, 'golden': golden, 'attribution': how,
                                      'by_stage': {f'{k}|{s}': dict(v) for (k, s), v in by_stage.items()}}, indent=1, default=dict))


if __name__ == '__main__':
    main()
