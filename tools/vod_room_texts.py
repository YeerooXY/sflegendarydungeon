"""Collect the flavor texts the game shows when a dungeon room is entered.

Only tokens inside the game area count: per sample the area spans from the room
counter ("N/100", bottom left) to the mirror of that distance around the HP text
(bottom centre), which excludes chat, donation banners and creator codes. A room
entry is a stable +1 change of the room counter; the texts first shown within
--window seconds after it are that room's texts. Repeated views of one room do not
add entries. Output: one row per entry, plus a frequency table of texts with one
example (source, second) each, for labelling.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re

ROOM = re.compile(r'^\s*(\d{1,3})\s*/\s*100\s*$')
HP = re.compile(r'^\s*\d{1,3}(?:[.,\s]\d{3})+\s*/\s*\d{1,3}(?:[.,\s]\d{3})+\s*$')


def normalise(text):
    return re.sub(r'\s+', ' ', text).strip().rstrip('.!').lower()


def rows_of(folder):
    rows = {}
    for path in sorted(folder.glob('*.tokens.jsonl.gz')):
        if not path.with_suffix('.done').exists():continue
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line);rows.setdefault(row['t'], row)
    return [rows[t] for t in sorted(rows)]


def game_texts(row, box):
    left, right, room_y = box
    return {normalise(t[0]) for t in row['tokens'] if t[4] >= .85 and left <= t[1] <= right and t[2] < room_y-.03
            and sum(c.isalpha() for c in t[0]) >= 10 and ' ' in t[0].strip()}


def entries(folder, window):
    rows = rows_of(folder);box = None;readings = []
    for row in rows:
        room = [t for t in row['tokens'] if ROOM.match(t[0]) and t[4] >= .8]
        hp = [t for t in row['tokens'] if HP.match(t[0]) and t[4] >= .8]
        if room and hp:
            r, h = room[0], hp[0]
            box = (r[1]-.03, 2*h[1]-r[1]+.04, r[2])
        readings.append((row, int(ROOM.match(room[0][0]).group(1)) if room else None, box))
    out = [];stable = None;since = 0
    for i, (row, value, box) in enumerate(readings):
        if value is None or box is None:continue
        # Require two consecutive equal readings before accepting a new room value.
        nxt = next((v for _, v, _ in readings[i+1:i+3] if v is not None), None)
        if nxt != value:continue
        if stable is not None and value == stable+1:
            before = set().union(*(game_texts(r, b) for r, _, b in readings[max(0, i-3):i] if b)) if i else set()
            after = set()
            for r, _, b in readings[i:]:
                if r['t'] > row['t']+window:break
                if b:after |= game_texts(r, b)
            out.append(dict(source=folder.name, t=row['t'], room=value, texts=sorted(after-before)))
        if value != stable:stable = value
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('scans', type=Path);p.add_argument('--output', type=Path, required=True)
    p.add_argument('--window', type=float, default=8)
    a = p.parse_args()
    rows = []
    for folder in sorted(x for x in a.scans.iterdir() if x.is_dir()):rows += entries(folder, a.window)
    counts = Counter();example = {}
    for e in rows:
        for t in e['texts']:
            counts[t] += 1;example.setdefault(t, (e['source'], e['t']))
    table = [dict(text=t, entries=c, example=example[t]) for t, c in counts.most_common()]
    a.output.write_text(json.dumps(dict(entries=rows, texts=table), ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(dict(entries=len(rows), with_text=sum(1 for e in rows if e['texts']), distinct_texts=len(table))))


if __name__ == '__main__':
    main()
