"""Attribute entry routes of reviewed offers with a locked door from the key counter.

A locked door costs one key on entry (double-locked: two); every other door costs
none, and loot only adds keys. So after an offer with a locked side, a stable key
drop of exactly the lock's cost before the next offer means the locked door was
taken, and an unchanged stable count after entering the next room means the other
door was. Anything else stays unknown. The Lockpick blessing opens locked doors
for free, so an unchanged count while its icon is active is ambiguous
(lockpick_active); the icons are read on the offer screen with EffectReader.

The key counter is read with vod_key_reader's crop on the ledger's own 720p clips
(their nominal seconds are the ledger's time base; 360p is too small for streams
whose game area fills part of the frame), once per --every seconds from just
before the offer to the next offer (at most --span seconds). A value counts when
two consecutive reads agree. Output: one row per offer with the raw key series,
keys before, the first stable change and the attributed side. These are machine
suggestions for review, not verified choices.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_COST = {'locked': 1, 'double_locked': 2}
CLIP_DIRS = ('out/vods/{}/720p', 'out/vod-discovery/{}/720p')


def offers(ledger, source):
    rows = [json.loads(line) for line in Path(ledger).read_text(encoding='utf-8').splitlines() if line.strip()]
    rows = [r for r in rows if r['source_id'] == source and r['status'] == 'decision' and not r.get('duplicate_of')]
    rows.sort(key=lambda r: r['source_seconds'])
    return rows


def clips(source):
    found = []
    for pattern in CLIP_DIRS:
        for marker in sorted((ROOT/pattern.format(source)).glob('*.json')):
            video = marker.with_suffix('.mp4')
            if video.exists():
                m = json.loads(marker.read_text());found.append((m['source_start'], m['source_end'], str(video)))
    return found


def stable(reads):
    """Values read on two consecutive samples, in order, with the time first seen."""
    out = []
    for a, b in zip(reads, reads[1:]):
        if a['keys'] is not None and a['keys'] == b['keys'] and (not out or out[-1][1] != a['keys']):
            out.append((a['t'], a['keys'], b['t']))
        elif out and a['keys'] is not None and a['keys'] == b['keys']:
            out[-1] = (out[-1][0], out[-1][1], b['t'])  # extend the last confirmed value
    return out


def attribute(row, series, settle):
    kinds = [d['kind'] for d in row['doors']]
    locked = [i for i, k in enumerate(kinds) if k in LOCK_COST]
    t0 = row['source_seconds'];values = stable(series)
    result = dict(id=row['id'], doors=kinds, keys=[r['keys'] for r in series], keys_before=None, change=None, side=None, basis=None)
    if len(locked) != 1:
        result['basis'] = 'no_single_lock';return result
    # The count before the choice: the first confirmed value, read on the offer screen
    # itself (a key is spent within a second of choosing).
    if not values or values[0][0] > t0+.5:
        result['basis'] = 'no_reading_at_offer';return result
    before = values[0][1];result['keys_before'] = before
    later = values[1:]
    cost = LOCK_COST[kinds[locked[0]]];other = 1-locked[0]
    change = next((v for v in later if v[1] != before), None)
    if change is not None:
        delta = change[1]-before
        if delta == -cost:
            result.update(change=delta, side=locked[0], basis='lock_cost_spent', change_t=change[0])
        elif delta > 0:
            result.update(change=delta, side=other, basis='keys_gained_without_spending', change_t=change[0])
        else:
            result.update(change=delta, basis='unexpected_change', change_t=change[0])
    elif max([v[2] for v in values if v[1] == before], default=t0) >= max(t0+settle, series[-1]['t']-6):
        # The same count confirmed near the next offer (so after entering), with no drop: the free door.
        result.update(change=0, side=other, basis='no_key_spent')
    else:
        result['basis'] = 'unreadable_after_entry'
    return result


def mark_effects(rows, sources, anchor, art):
    """Read active effect icons on each offer screen; Lockpick makes no-key-spent ambiguous."""
    from vod_auto_offers import frames_at
    from vod_key_reader import EffectReader
    reader = EffectReader(art)
    frames = frames_at(sources, sorted({round(r['t']+.3, 2) for r in rows}))
    for r in rows:
        frame = frames.get(round(r['t']+.3, 2))
        r['effects'] = reader.read(frame, anchor)['active'] if frame is not None else None
        if r['basis'] in ('no_key_spent', 'keys_gained_without_spending') and (r['effects'] is None or 'lockpick' in r['effects']):
            r.update(basis='lockpick_active' if r['effects'] else 'effects_unreadable', side=None)
    return rows


def run_source(source, ledger, span, every, settle, art, existing=None):
    import sys
    sys.path.insert(0, str(ROOT/'tools'))
    from vod_key_reader import anchors, read_keys, video_tokens
    all_rows = offers(ledger, source)
    rows = [r for r in all_rows if any(d['kind'] in LOCK_COST for d in r['doors'])]
    sources = clips(source)
    inside = lambda t: any(a <= t < b for a, b, _ in sources)
    sample = sorted({round(r['source_seconds']+1, 2) for r in all_rows[::max(1, len(all_rows)//120)] if inside(r['source_seconds']+1)})
    anchor = anchors(video_tokens(sources, sample))
    if anchor is None:return source, None
    if existing:
        # Only add effect icons to an earlier key pass.
        return source, mark_effects(json.loads(Path(existing).read_text()), sources, anchor, art)
    plans = []
    for r in rows:
        t0 = r['source_seconds']
        if not inside(t0):continue
        later = [o['source_seconds'] for o in all_rows if o['source_seconds'] > t0+1]
        end = min(t0+span, later[0]+1 if later else t0+span)  # the next offer screen still shows this room's count
        n = max(2, int((end-t0)/every)+1)
        plans.append((r, [round(t0-1+k*every, 2) for k in range(n+1)]))
    times = sorted({t for _, ts in plans for t in ts})
    reads = {x['t']: x for x in read_keys(sources, times, anchor)}
    out = [dict(source=source, t=r['source_seconds'], **attribute(r, [reads[t] for t in ts], settle)) for r, ts in plans]
    return source, mark_effects(out, sources, anchor, art)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ledger', type=Path, default=ROOT/'data/vods/observations-2026-10-08.jsonl')
    p.add_argument('--output', type=Path, required=True);p.add_argument('--sources', nargs='+', required=True)
    p.add_argument('--span', type=float, default=40);p.add_argument('--every', type=float, default=1)
    p.add_argument('--settle', type=float, default=3, help='seconds after the offer an unchanged count must be confirmed')
    p.add_argument('--art', type=Path, default=ROOT/'out/vod-art')
    p.add_argument('--effects-only', action='store_true', help='add effect icons to existing outputs without rereading keys')
    p.add_argument('--workers', type=int, default=4)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as pool:
        futures = {pool.submit(run_source, s, str(a.ledger), a.span, a.every, a.settle, str(a.art),
                               str(a.output/f'{s}.json') if a.effects_only else None): s
                   for s in a.sources if not a.effects_only or (a.output/f'{s}.json').exists()}
        for f in as_completed(futures):
            try:
                source, rows = f.result()
            except Exception as error:
                print(json.dumps({'source': futures[f], 'error': f'{type(error).__name__}: {error}'}), flush=True);continue
            if rows is None:
                print(json.dumps({'source': source, 'skipped': 'no HUD anchors'}), flush=True);continue
            (a.output/f'{source}.json').write_text(json.dumps(rows, indent=1))
            print(json.dumps({'source': source, 'offers': len(rows), 'basis': Counter(r['basis'] for r in rows)}), flush=True)


if __name__ == '__main__':
    main()
