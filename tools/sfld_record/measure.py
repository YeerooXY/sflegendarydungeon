"""Ingest recorded dungeon packets and measure game rates with their sample sizes.

ingest: spool JSONL files -> private SQLite (duplicates dropped).
report: per-character transitions (state before -> command -> state after) ->
        door, room, key, damage, container, shop, stone, wheel and chance
        records, plus every unrecognized status, room or container code.

Field meanings follow tools/sfld_study/protocol.py and automatic.py for client
31.500.260929.1. Derived meanings are PROVISIONAL until checked against the
first real event packets; raw packets are kept so everything can be re-decoded.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sfld_record.spool import default_directory  # noqa: E402
from sfld_study.automatic import CONTAINERS, DOORS, EFFECTS, GEMS  # noqa: E402

TARGET = 385  # about +/-5 points at 95% for a rate near 50%
KNOWN_STATUS = {0, 1, 10, 11, 12, 100, 101, 102}
KNOWN_ENCOUNTERS = {1, 2, 3, 4, 5, 50, 100, 200, 250, 315, 323, 900, 901, 902, 903, 904, 905, 906, 907, 908, 909}
DOOR_CODES = {1, 2, 5, 6, 9, 10, 13, 14}
# Sensitivity-ranked priorities from the 2026-10-22 robustness sweep.
PRIORITY = {'damage': 1, 'keys': 2, 'mystery_contents': 3, 'traps': 4, 'barrels': 5}


def connect(path):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS packets(id INTEGER PRIMARY KEY, digest TEXT UNIQUE, source TEXT, ms INTEGER, '
               'host TEXT, player TEXT, client TEXT, req TEXT, params TEXT, fields TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS packets_character ON packets(host, player, ms)')
    return db


def ingest(directory, database):
    db = connect(database);added = 0;skipped = 0
    for path in sorted(Path(directory).glob('*.jsonl')):
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                line = line.strip()
                if not line:continue
                try:record = json.loads(line)
                except json.JSONDecodeError:skipped += 1;continue
                if record.get('schema') != 1:skipped += 1;continue
                digest = hashlib.sha256(line.encode()).hexdigest()
                cursor = db.execute('INSERT OR IGNORE INTO packets(digest,source,ms,host,player,client,req,params,fields) VALUES(?,?,?,?,?,?,?,?,?)',
                    (digest, record['source'], record['ms'], record['host'], record['player'], record['client'], record['req'],
                     record['params'], json.dumps(record['fields'], sort_keys=True)))
                added += cursor.rowcount
    db.commit()
    total = db.execute('SELECT COUNT(*) FROM packets').fetchone()[0];db.close()
    return dict(added=added, skipped_lines=skipped, total=total, database=str(database))


def numbers(text):
    return [int(x) for x in text.split('/')] if text else []


def wilson(k, n, z=1.96):
    if not n:return None
    p = k/n;d = 1+z*z/n;c = (p+z*z/(2*n))/d;h = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [round(c-h, 4), round(c+h, 4)]


class State:
    def __init__(self):
        self.save = None;self.mushrooms = None;self.stones = None


def effect_ids(save):
    return sorted(x for x in save[5:11] if x)


def measure(database):
    db = connect(database)
    rows = db.execute('SELECT id,source,ms,host,player,client,req,params,fields FROM packets ORDER BY host,player,ms,id').fetchall()
    db.close()
    tally = defaultdict(Counter)   # quantity -> outcome counts
    samples = defaultdict(list)    # quantity -> numeric samples
    unknown = defaultdict(lambda: {'count': 0, 'example_packet': None})
    chances = Counter();deltas = 0;transitions = 0;characters = set();sources = Counter();clients = Counter()
    last_offer = {}
    states = defaultdict(State)
    for pid, source, ms, host, player, client, req, params, text in rows:
        key = (host, player);characters.add(key);sources[source] += 1;clients[client] += 1
        fields = json.loads(text);state = states[key]
        deltas += sum(1 for k in fields if k.startswith('iadungeonsave('))
        for k, v in fields.items():
            if k.split('(')[0] == 'iadungeonchances':chances[v] += 1
        if 'wheelresult' in fields:tally['wheel_result'][fields['wheelresult']] += 1
        after = numbers(fields.get('iadungeonsave'))
        if len(after) != 50:after = None
        mushrooms = numbers(fields.get('resources'));mushrooms = mushrooms[1] if len(mushrooms) > 1 else None
        if after:
            status = after[15]
            if status not in KNOWN_STATUS:
                u = unknown[f'status {status}'];u['count'] += 1;u['example_packet'] = u['example_packet'] or pid
            stage = after[16]+1
            if status == 1:
                offer = (after[17], after[19], after[20], after[25], after[26])
                if last_offer.get(key) != offer:
                    last_offer[key] = offer
                    pair = tuple(sorted(DOORS.get(t, f'code {t}') for t in after[19:21]))
                    tally[f'door_pair stage {stage}'][' + '.join(pair)] += 1
                    for t, trap in ((after[19], after[25]), (after[20], after[26])):
                        if t not in DOORS:
                            u = unknown[f'door type {t}'];u['count'] += 1;u['example_packet'] = u['example_packet'] or pid
                        elif DOORS[t] not in ('wall', 'boss', 'shop'):
                            tally[f'traps stage {stage}']['trapped' if trap else 'clear'] += 1
                            # The model traps only monster and mystery doors; check every type.
                            tally[f'traps door {DOORS[t]}']['trapped' if trap else 'clear'] += 1
            if 10 <= status <= 12:
                enc, variant = after[19+status-10], after[22+status-10]
                if enc not in KNOWN_ENCOUNTERS:
                    u = unknown[f'encounter {enc}'];u['count'] += 1;u['example_packet'] = u['example_packet'] or pid
                if enc == 100 and variant not in CONTAINERS:
                    u = unknown[f'container {variant}'];u['count'] += 1;u['example_packet'] = u['example_packet'] or pid
            for e in effect_ids(after):
                if e not in EFFECTS:
                    u = unknown[f'effect {e}'];u['count'] += 1;u['example_packet'] = u['example_packet'] or pid
            if (enc := (after[19+status-10] if 10 <= status <= 12 else None)) in (315, 323) and 'iamerchant' in fields:
                offers = numbers(fields['iamerchant'])
                for i in range(0, len(offers)-2, 3):
                    tally[f'shop_offer stage {stage}'][f'{EFFECTS.get(offers[i], offers[i])} power {offers[i+1] % 10000}'] += 1
            if enc == 904 and 'iadungeonsoulstones' in fields:
                stones = numbers(fields['iadungeonsoulstones'])
                if len(stones) == 36:
                    for g in (stones[i] for i in (18, 24, 30) if stones[i]):
                        tally['stone_offer'][GEMS.get(g, f'code {g}')] += 1
        before = state.save
        if before and after and isinstance(req, str) and req.lower().startswith('iadungeon'):
            transitions += 1
            code = int(params) if req == 'IADungeonInteract' and params and params.lstrip('-').isdigit() else None
            b_status, b_stage = before[15], before[16]+1
            hp_max = max(1, before[4]);hp_change = (after[2]-before[2])/hp_max;key_change = after[39]-before[39]
            gained = [e for e in effect_ids(after) if e not in effect_ids(before)]
            if code in DOOR_CODES and b_status == 1:
                side = (code-1) % 4
                door = DOORS.get(before[19+side], f'code {before[19+side]}')
                if 10 <= after[15] <= 12:
                    enc, variant = after[19+after[15]-10], after[22+after[15]-10]
                    label = CONTAINERS[variant][1] if enc == 100 and variant in CONTAINERS else f'encounter {enc}'
                    tally[f'contents {door}'][label] += 1
                if before[25+side]:
                    samples[f'trap_damage stage {b_stage}'].append(round(-hp_change, 4))
            elif 10 <= b_status <= 12:
                enc, variant = before[19+b_status-10], before[22+b_status-10]
                if enc in (1, 2, 3) and code in (20, 21):
                    action = 'fight' if code == 20 else 'flee'
                    samples[f'{action}_damage stage {b_stage}'].append(round(-hp_change, 4))
                    if code == 21:tally[f'flee stage {b_stage}']['no damage' if hp_change >= 0 else 'hit'] += 1
                    if after[15] not in (101, 102):tally[f'key_after_{action} stage {b_stage}']['key' if key_change > 0 else 'none'] += 1
                if enc == 100 and code == 40:
                    kind = CONTAINERS.get(variant, (f'code {variant}',))[0]
                    outcome = []
                    if key_change > 0:outcome.append('key')
                    outcome += [('blessing ' if e < 100 else 'curse ')+EFFECTS.get(e, str(e)) for e in gained]
                    if any(k.split('(')[0] in ('ialootitem', 'iapendingitems', 'reward') for k in fields):outcome.append('item/reward record')
                    if mushrooms is not None and state.mushrooms is not None and mushrooms > state.mushrooms:outcome.append('mushroom')
                    if hp_change < 0:outcome.append('damage')
                    tally[f'container {kind}'][', '.join(outcome) or 'nothing visible'] += 1
                if enc in (4, 5) and code == 20:
                    samples[f'boss_damage stage {b_stage}'].append(round(-hp_change, 4))
        if after:state.save = after
        if mushrooms is not None:state.mushrooms = mushrooms
    quantities = []
    for name, counts in sorted(tally.items()):
        n = sum(counts.values())
        quantities.append({'quantity': name, 'n': n, 'coverage': round(min(1, n/TARGET), 3),
            'outcomes': {k: {'count': c, 'share': round(c/n, 4), 'ci95': wilson(c, n)} for k, c in counts.most_common()}})
    for name, values in sorted(samples.items()):
        values = sorted(values);n = len(values)
        quantities.append({'quantity': name, 'n': n, 'coverage': round(min(1, n/TARGET), 3),
            'mean': round(sum(values)/n, 4), 'min': values[0], 'max': values[-1],
            'quartiles': [values[n//4], values[n//2], values[(3*n)//4]]})
    return {'packets': len(rows), 'characters': len(characters), 'transitions': transitions, 'sources': dict(sources),
            'clients': {str(k): v for k, v in clients.items()}, 'delta_records': deltas,
            'chances_records': [{'value': v, 'count': c} for v, c in chances.most_common(20)],
            'unknown_codes': {k: v for k, v in sorted(unknown.items(), key=lambda kv: -kv[1]['count'])},
            'quantities': quantities, 'target_per_condition': TARGET,
            'note': 'Provisional decoding for client 31.500.260929.1; validate on the first event packets before fitting the profile.'}


def print_report(result):
    print(f"{result['packets']} packets, {result['characters']} characters, {result['transitions']} command transitions; "
          f"sources {result['sources']}; partial save records {result['delta_records']}")
    for q in result['quantities']:
        bar = '#'*int(q['coverage']*20)
        head = f"{q['quantity']:34} n={q['n']:6} [{bar:20}]"
        if 'outcomes' in q:
            top = '; '.join(f"{k} {v['share']:.1%}" for k, v in list(q['outcomes'].items())[:4])
            print(head, top)
        else:
            print(head, f"mean {q['mean']:.3f}, quartiles {q['quartiles']}")
    if result['unknown_codes']:
        print('Unrecognized codes (new rooms/containers/effects to decode):')
        for k, v in result['unknown_codes'].items():print(f"   {k}: {v['count']} times, first in packet {v['example_packet']}")
    if result['chances_records']:print('iadungeonchances values seen:', result['chances_records'][:5])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('ingest', 'report'));p.add_argument('--spool', type=Path, default=default_directory())
    p.add_argument('--database', type=Path);p.add_argument('--json', type=Path, help='also write the full report as JSON')
    a = p.parse_args();database = a.database or a.spool/'records.sqlite'
    if a.command == 'ingest':
        print(json.dumps(ingest(a.spool, database)))
    else:
        result = measure(database);print_report(result)
        if a.json:a.json.write_text(json.dumps(result, indent=1)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
