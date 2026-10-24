"""Review door-point sheets (vod_door_points.py) with a low-cost vision model.

Each sheet shows a door screen (first frame, last frame at the click) and the
first 2.5 s after entering. The model names both doors, the entered side and the
initial contents of the room, from fixed vocabularies, with legend images of the
client's door and content sprites. Calls go through the Codex CLI
(`codex exec -m gpt-6-luna`, read-only sandbox, JSON schema output), --batch
sheets per call, --workers calls at once. Repeated screens (repeat_of) are not
reviewed. Results append to <points>/<source>.reviews.jsonl, so a stopped run
resumes; rows are unreviewed machine labels.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DOORS = ['mystery', 'locked', 'double_locked', 'unlocked', 'epic', 'monster', 'golden', 'cursed', 'sacrifice',
         'blessing', 'destiny', 'hungry', 'wall', 'boss', 'shop', 'trial', 'exit_trial', 'unknown']
CONTENTS = ['monster', 'undead', 'boss', 'trial_monster', 'empty', 'barrel', 'crate', 'bronze', 'silver', 'epic',
            'skeleton', 'sacrifice_chest', 'cursed_chest', 'sated_chest', 'prize', 'shop', 'golden_room', 'other', 'unknown']
GOLDEN = ['fountain', 'cleansing_fountain', 'rocks', 'lava', 'narrator', 'wishing_well', 'sewers', 'sarcophagus',
          'locked_sarcophagus', 'wood', 'wheel', 'spider', 'souls', 'arcane', 'curse_shop', 'armory', 'rps', 'locker',
          'beta', 'tube', 'shakes', 'pigs', 'other', 'none']

PROMPT = """You label screenshots from the game Shakes & Fidget, Legendary Dungeon mode.

Image 1 is a legend of the door types (labelled sprites). Image 2 is a legend of room
contents (labelled sprites). Every following image is one review sheet, in order:
{names}

A sheet has six tiles. Top row: the door screen's first frame, the door screen's last
frame (the moment of the click), then +0.4 s after entering. Bottom row: +0.8 s,
+1.4 s and +2.5 s after entering. The door screen shows a left and a right door.

For each sheet report:
- left_door, right_door: the door types, from the legend names. "wall" is a solid
  wall with no door. A plain wooden door with a lock is "locked"; "unlocked" and
  "double_locked" are rare variants, only when the door clearly matches them. Use
  "unknown" when you cannot tell.
- entered: "left", "right" or "unknown". The player's mouse cursor is a small yellow
  arrow; on the last door frame it is on or next to the door being clicked. If one
  side is a wall, the other door is the one entered. Use what the room after
  entering shows as a check (for example a key door opening, a golden room).
- contents: what is in the room right after entering, before anything happens.
  * "monster": any creature or person standing in the room: robed wizards, old
    witches, warriors, adventurers, goblins, beasts, flying things. Most monsters in
    this dungeon look like people. A red flashing screen border means the player is
    being hit, so a monster is there. The monster legend sprite is only one example.
  * "undead": a zombie carrying a lantern. "boss": a stage boss fight (rooms 25, 50,
    75, 100), usually a large figure behind a boss door.
  * "empty": an empty corridor with no creature and no object.
  * "shop": a merchant menu screen with item cards and prices. A person standing in
    a corridor is a monster, not a shop.
  * Objects: barrel, crate, chests (bronze, silver, epic = golden chest), skeleton
    (a lying corpse), sacrifice/cursed/sated chests: match the content legend. A
    chest that later turns out to be a mimic is still the chest it looked like.
  * "golden_room": a special room; name it in golden_room.
  If the tiles after entering already show the next door screen, use the earliest
  tile that shows the room. Use "unknown" when no tile shows it.
- golden_room: the special room's name from the list, else "none".
- confidence: "high", "medium" or "low".
- note: at most 12 words, only if something is unusual (menus, overlays, death).

Answer only with the JSON object the schema asks for, one item per sheet, in the
same order, with the sheet name in "sheet"."""


def schema():
    item = {'type': 'object', 'additionalProperties': False,
            'required': ['sheet', 'left_door', 'right_door', 'entered', 'contents', 'golden_room', 'confidence', 'note'],
            'properties': {'sheet': {'type': 'string'}, 'left_door': {'type': 'string', 'enum': DOORS},
                           'right_door': {'type': 'string', 'enum': DOORS},
                           'entered': {'type': 'string', 'enum': ['left', 'right', 'unknown']},
                           'contents': {'type': 'string', 'enum': CONTENTS},
                           'golden_room': {'type': 'string', 'enum': GOLDEN},
                           'confidence': {'type': 'string', 'enum': ['high', 'medium', 'low']},
                           'note': {'type': 'string'}}}
    return {'type': 'object', 'additionalProperties': False, 'required': ['items'],
            'properties': {'items': {'type': 'array', 'items': item}}}


def review_batch(sheets, legend, model, effort, timeout):
    codex = shutil.which('codex')
    with tempfile.TemporaryDirectory() as work:
        work = Path(work);(work/'schema.json').write_text(json.dumps(schema()))
        command = [codex, 'exec', '-m', model, '-c', f'model_reasoning_effort="{effort}"', '--ephemeral',
                   '--skip-git-repo-check', '-s', 'read-only', '-C', str(work), '--output-schema', str(work/'schema.json'),
                   '-o', str(work/'out.json'), '-i', str(legend/'doors.jpg'), '-i', str(legend/'contents.jpg')]
        for s in sheets:command += ['-i', str(s)]
        names = '\n'.join(f'{i+3}. {s.parent.name}/{s.name}' for i, s in enumerate(sheets))
        run = subprocess.run(command+['-'], input=PROMPT.format(names=names), capture_output=True, text=True,
                             encoding='utf-8', timeout=timeout)
        out = work/'out.json'
        if not out.exists():raise RuntimeError(f'codex exit {run.returncode}: {run.stderr[-400:]}')
        items = json.loads(out.read_text(encoding='utf-8'))['items']
    if len(items) != len(sheets):raise RuntimeError(f'{len(items)} items for {len(sheets)} sheets')
    return [dict(item, sheet=s.name) for item, s in zip(items, sheets)]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--points', type=Path, required=True, help='vod_door_points.py output folder')
    p.add_argument('--sources', nargs='*');p.add_argument('--legend', type=Path, default=ROOT/'out/vod-review-legend')
    p.add_argument('--model', default='gpt-6-luna');p.add_argument('--effort', default='low')
    p.add_argument('--batch', type=int, default=6);p.add_argument('--workers', type=int, default=4)
    p.add_argument('--limit', type=int, help='at most N sheets per source (tests)')
    p.add_argument('--timeout', type=float, default=600)
    a = p.parse_args()
    sources = a.sources or sorted(f.stem for f in a.points.glob('*.json'))
    jobs = []
    for source in sources:
        points = json.loads((a.points/f'{source}.json').read_text())
        done_path = a.points/f'{source}.reviews.jsonl'
        done = {json.loads(l)['sheet'] for l in done_path.read_text(encoding='utf-8').splitlines() if l.strip()} if done_path.exists() else set()
        todo = [a.points/source/q['sheet'] for q in points if q.get('sheet') and 'repeat_of' not in q and q['sheet'] not in done][:a.limit]
        jobs += [(source, todo[i:i+a.batch]) for i in range(0, len(todo), a.batch)]
    print(json.dumps({'batches': len(jobs), 'sheets': sum(len(j[1]) for j in jobs)}), flush=True)
    with ThreadPoolExecutor(a.workers) as pool:
        futures = {pool.submit(review_batch, sheets, a.legend, a.model, a.effort, a.timeout): (source, sheets) for source, sheets in jobs}
        for f in as_completed(futures):
            source, sheets = futures[f]
            try:
                rows = f.result()
            except Exception as error:
                print(json.dumps({'source': source, 'first': sheets[0].name, 'error': f'{type(error).__name__}: {error}'[:500]}), flush=True);continue
            with open(a.points/f'{source}.reviews.jsonl', 'a', encoding='utf-8') as out:
                for r in rows:out.write(json.dumps(r)+'\n')
            print(json.dumps({'source': source, 'reviewed': len(rows), 'first': sheets[0].name}), flush=True)


if __name__ == '__main__':
    main()
