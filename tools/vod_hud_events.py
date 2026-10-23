"""Turn OCR token series (vod_hud_scan.py) into deduplicated HP and key events.

A value counts only once it is read identically on --stable consecutive samples, so a
screen shown many times, or revisited after a break, adds nothing: only a change
between two stable values is an event. A change of maximum HP starts a new segment
(another character, or a stat change), and so does a gap longer than --max-gap.

The room counter is the client's "N/100" text (sfld_vod.hud.parse_counter's
format); its usual position per source is learned so other "/100" texts are
ignored. Overlays hide it in some streams. The key count position is learned as the
small number on the HP row, far to its right. These are suggestions for review, not
verified observations.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re

HP = re.compile(r'^\s*(\d{1,3}(?:[.,\s]\d{3})*)\s*/\s*(\d{1,3}(?:[.,\s]\d{3})*)\s*$')
ROOM = re.compile(r'^\s*(\d{1,3})\s*/\s*100\s*$')


def number(text):
    return int(re.sub(r'[.,\s]', '', text))


def samples(folder):
    rows = {}
    for path in sorted(Path(folder).glob('*.tokens.jsonl.gz')):
        if not path.with_suffix('.done').exists():continue  # still being scanned
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line);rows.setdefault(row['t'], row)  # overlapping clips: keep one sample per second
    return [rows[t] for t in sorted(rows)]


def hp_of(row):
    found = []
    for text, x, y, h, score in row['tokens']:
        m = HP.match(text)
        if m and score >= .8:
            cur, top = number(m.group(1)), number(m.group(2))
            # Maximum HP runs to millions; "37/100" is the room counter, not HP.
            if 0 <= cur <= top and top >= 1000:found.append((y, x, h, cur, top))
    return max(found) if found else None  # the HUD line is the lowest match


def learn_positions(rows):
    """Most common x of small integers on the HP row (keys) and in the upper right (room)."""
    keys, rooms = Counter(), Counter()
    for row in rows:
        hp = hp_of(row)
        if not hp:continue
        y0, x0, h0 = hp[0], hp[1], hp[2]
        for text, x, y, h, score in row['tokens']:
            if not text.isdigit() or score < .8:continue
            if abs(y-y0) < 2*h0 and x-x0 > .2 and len(text) <= 3:keys[round(x, 2)] += 1
        for text, x, y, h, score in row['tokens']:
            if ROOM.match(text) and score >= .8:rooms[(round(x, 1), round(y, 1))] += 1
    return (keys.most_common(1)[0][0] if keys else None), (rooms.most_common(1)[0][0] if rooms else None)


def read(row, key_x, room_xy):
    hp = hp_of(row)
    if not hp:return None
    y0, h0 = hp[0], hp[2];key = room = None
    for text, x, y, h, score in row['tokens']:
        if not text.isdigit() or score < .8:continue
        if key_x is not None and abs(x-key_x) < .02 and abs(y-y0) < 2*h0:key = int(text)
    for text, x, y, h, score in row['tokens']:
        m = ROOM.match(text)
        if m and room_xy and score >= .8 and abs(x-room_xy[0]) < .06 and abs(y-room_xy[1]) < .06 and 1 <= int(m.group(1)) <= 100:
            room = int(m.group(1))
    words = [t[0] for t in row['tokens'] if sum(c.isalpha() for c in t[0]) >= 4]
    return dict(t=row['t'], hp=hp[3], max=hp[4], keys=key, room=room, words=words)


def stable_runs(reads, field, need):
    runs = []
    for r in reads:
        value = (r[field], r['max']) if field == 'hp' else (r[field], r['max'])
        if r[field] is None:continue
        if runs and runs[-1]['value'] == value:
            runs[-1]['end'] = r['t'];runs[-1]['n'] += 1
        else:
            runs.append(dict(value=value, start=r['t'], end=r['t'], n=1))
    return [run for run in runs if run['n'] >= need]


def events(source, rows, stable, max_gap, context):
    key_x, room_xy = learn_positions(rows)
    reads = [r for r in (read(row, key_x, room_xy) for row in rows) if r]
    by_t = {r['t']: r for r in reads};times = sorted(by_t)
    out = []
    for field in ('hp', 'keys'):
        runs = stable_runs(reads, field, stable)
        for a, b in zip(runs, runs[1:]):
            (va, ma), (vb, mb) = a['value'], b['value']
            if ma != mb or b['start']-a['end'] > max_gap:continue
            near = [by_t[t] for t in times if a['end']-context <= t <= b['start']+2]
            rooms = [r['room'] for r in near if r['room']]
            out.append(dict(source=source, field=field, t_before=a['end'], t_after=b['start'], before=va, after=vb,
                            max_hp=ma, change=(vb-va)/ma if field == 'hp' else vb-va,
                            room=Counter(rooms).most_common(1)[0][0] if rooms else None,
                            words=sorted({w for r in near for w in r['words']})[:40]))
    return dict(source=source, key_x=key_x, room_xy=room_xy, samples=len(rows), hud_samples=len(reads), events=out)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('scans', type=Path, help='vod_hud_scan output directory');p.add_argument('--output', type=Path, required=True)
    p.add_argument('--stable', type=int, default=2);p.add_argument('--max-gap', type=float, default=30)
    p.add_argument('--context', type=float, default=12)
    a = p.parse_args()
    results = []
    for folder in sorted(x for x in a.scans.iterdir() if x.is_dir()):
        rows = samples(folder)
        if rows:results.append(events(folder.name, rows, a.stable, a.max_gap, a.context))
    a.output.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    total = sum(len(r['events']) for r in results)
    print(json.dumps(dict(sources=len(results), hud_samples=sum(r['hud_samples'] for r in results), events=total)))


if __name__ == '__main__':
    main()
