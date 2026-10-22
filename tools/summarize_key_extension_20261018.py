"""Combine reviewed combat observations, with explicit provenance and validation.

This script calculates statistics from manual audit ledgers. It does not infer
combat outcomes from frames and never changes simulator parameters.
"""
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/vods/key-drop-extension-2026-10-17'
OLD = ROOT / 'data/vods/opening-audit-2026-10-17/key-drops.json'
NEW = [(OUT/'maexel.json', 'baseline_kills'), (OUT/'n3.json', 'baseline_kills'),
       (OUT/'lootenant.json', 'clean_kills'), (OUT/'official.json', 'baseline_kills')]
CREATORS = {'2877355041': 'Dorzer', '2877531246': 'Sensei', '2878439002': 'Lootenant',
            '2878297081': 'Maexel', '2885348704': 'N3utr4LSF', '2883910762': 'Official channel'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(rows):
    n = len(rows)
    k = sum(r['keys_gained'] > 0 for r in rows)
    if not n:
        return {'n': 0, 'with_key': 0, 'without_key': 0, 'proportion': None, 'wilson95': None}
    p = k/n
    z = 1.959963984540054
    den = 1 + z*z/n
    center = (p + z*z/(2*n))/den
    radius = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return {'n': n, 'with_key': k, 'without_key': n-k, 'proportion': p,
            'wilson95': [max(0., center-radius), min(1., center+radius)],
            'drops': dict(sorted(Counter(r['keys_gained'] for r in rows).items())),
            'mean_keys_per_kill': sum(r['keys_gained'] for r in rows)/n}


def groups(rows, field):
    buckets = defaultdict(list)
    for r in rows:
        value = r[field]
        key = ','.join(value) or 'none' if isinstance(value, list) else str(value)
        buckets[key].append(r)
    return {k: stats(v) for k,v in sorted(buckets.items())}


def normalize(r, path, phase):
    assert r['action'] == 'fight', (path, r)
    assert r['outcome'] in {'kill', 'monster_killed'}, (path, r)
    assert r['stone_set_complete'] is True
    assert r['key_moment_active'] is False
    assert not set(r['gems']) & {'spying', 'lodestone'}
    if 'encounter_origin' in r:
        assert r['encounter_origin'] == 'living_monster_on_entry'
    else:
        assert r['encounter'] == 'ordinary_living_on_entry'
    gained = r.get('keys_gained', r['key_drop'])
    gained = int(gained)
    assert gained == r['keys_after'] - r['keys_before'], (path, r['offer_id'])
    assert gained in {0, 1}, (path, r['offer_id'])
    room = r.get('room')
    assert room is None or room % 25 != 0
    stage = r.get('stage') or (room-1)//25+1
    assert stage in {1,2,3,4}
    rc = r.get('run_class', r.get('run_context', 'unknown'))
    rc = 'first' if rc in {'first', 'first_confirmed'} else 'later' if rc in {'later', 'later_confirmed'} else 'unknown'
    if 'one_hit_wonder_active' in r:
        ohw = r['one_hit_wonder_active']
    elif 'one_hit_wonder' in r:
        ohw = r['one_hit_wonder']
    else:
        assert 'blessings_observed' in r
        ohw = 'one_hit' in r['blessings_observed']
    assert isinstance(ohw, bool)
    evidence = r['evidence']
    bi = r.get('before_evidence_index', 0)
    ai = r.get('after_evidence_index', len(evidence)-1)
    assert 0 <= bi < ai < len(evidence)
    source = str(r['source_id'])
    return {
        'id': r.get('id', r.get('audit_id', r['offer_id'])),
        'phase': phase, 'audit_file': path.relative_to(ROOT).as_posix(),
        'source_id': source, 'creator': CREATORS[source], 'offer_id': r['offer_id'],
        'source_seconds': r.get('source_seconds', (evidence[bi]['source_seconds'] + evidence[ai]['source_seconds'])/2),
        'before_seconds': evidence[bi]['source_seconds'], 'after_seconds': evidence[ai]['source_seconds'],
        'room': room, 'stage': stage, 'run_id': r['run_id'], 'run_class': rc,
        'mode': r.get('game_mode', r.get('mode', 'unknown')),
        'gems': sorted(r['gems']), 'stone_stratum': 'none' if not r['gems'] else 'other_stones',
        'one_hit_wonder': ohw, 'keys_before': r['keys_before'], 'keys_after': r['keys_after'],
        'keys_gained': gained, 'evidence_count': len(evidence),
        'sheet': r['sheet'],
    }


def find_images(node, out):
    if isinstance(node, dict):
        for pk, hk in [('image','sha256'), ('frame','frame_sha256'), ('crop','crop_sha256')]:
            if isinstance(node.get(pk), str) and hk in node:
                rel = Path(node[pk]).as_posix()
                if rel in out:
                    assert out[rel] == node[hk], rel
                out[rel] = node[hk]
        for value in node.values():
            find_images(value, out)
    elif isinstance(node, list):
        for value in node:
            find_images(value, out)


def table_line(label, s):
    a,b = s['wilson95']
    return f"| {label} | {s['with_key']}/{s['n']} | {100*s['proportion']:.1f}% | {100*a:.1f}–{100*b:.1f}% |"


def main():
    files = [(OLD,'baseline_kills','original')] + [(p,k,'extension') for p,k in NEW]
    documents, rows, manifest = [], [], []
    for path, key, phase in files:
        data = json.loads(path.read_text(encoding='utf-8'))
        documents.append(data)
        manifest.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path), 'rows': len(data[key]), 'phase': phase})
        rows.extend(normalize(r,path,phase) for r in data[key])
    old = [r for r in rows if r['phase']=='original']
    new = [r for r in rows if r['phase']=='extension']
    assert len(old)==60 and sum(r['keys_gained'] for r in old)==50
    assert len({r['id'] for r in rows}) == len(rows)
    # A shared parser offer can hold multiple distinct kills only after an
    # explicit dense split (Maexel). Never deduplicate those by offer alone.
    repeats = defaultdict(list)
    for r in rows:
        repeats[r['offer_id']].append(r)
    repeated_offers = {k:v for k,v in repeats.items() if len(v)>1}
    for key, batch in repeated_offers.items():
        assert all(r['source_id']=='2878297081' and r['phase']=='extension' for r in batch), key
        ordered = sorted(batch,key=lambda r:r['before_seconds'])
        for left,right in zip(ordered, ordered[1:]):
            assert left['after_seconds'] < right['before_seconds'], key
    # New Lootenant windows share a VOD with the old sample, not its kills.
    overlaps = []
    for a in old:
        for b in new:
            if a['source_id']==b['source_id'] and max(a['before_seconds'],b['before_seconds']) < min(a['after_seconds'],b['after_seconds']):
                overlaps.append((a['id'],b['id']))
    assert not overlaps, overlaps

    images = {}
    for d in documents:
        find_images(d,images)
    failures = []
    for rel,digest in images.items():
        p = ROOT/rel
        if not p.exists() or sha(p)!=digest:
            failures.append(rel)
    assert not failures, failures
    result = {
        'schema': 1, 'date': '2026-10-18', 'definition': 'Ordinary living-on-entry monster kills; Spying Gem, Lodestone and Key Moment absent at hit.',
        'inputs': manifest, 'original': stats(old), 'extension': stats(new), 'combined': stats(rows),
        'extension_by_source': groups(new,'source_id'), 'combined_by_source': groups(rows,'source_id'),
        'extension_by_stage': groups(new,'stage'), 'combined_by_stage': groups(rows,'stage'),
        'combined_by_one_hit_wonder': groups(rows,'one_hit_wonder'),
        'extension_by_one_hit_wonder': groups(new,'one_hit_wonder'),
        'combined_by_stone_stratum': groups(rows,'stone_stratum'),
        'combined_by_gems': groups(rows,'gems'), 'combined_by_run_class': groups(rows,'run_class'),
        'combined_by_mode': groups(rows,'mode'),
        'confidence_method': 'Two-sided Wilson score interval, z=1.959963984540054. Nominal binomial sampling interval; convenience VOD selection, shared runs, missed footage and classification uncertainty are not included.',
        'simulator_modified': False,
    }
    verification = {
        'date': '2026-10-18', 'status': 'passed', 'input_files': manifest,
        'original_60_preserved': True, 'unique_combat_ids': len(rows),
        'cross_audit_overlapping_combat_windows': overlaps,
        'explicitly_split_shared_offers': {k:[r['id'] for r in v] for k,v in repeated_offers.items()},
        'excluded_key_modifiers_absent': True, 'all_deltas_match_before_after': True,
        'only_ordinary_verified_kills_in_denominator': True,
        'evidence_image_hashes_checked': len(images), 'evidence_failures': failures,
        'limitations': ['File integrity and schema checks do not independently prove visual judgements.',
                        'All source scopes are convenience samples, not a census of all kills in the VOD corpus.'],
    }
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    (OUT/'verification.json').write_text(json.dumps(verification,indent=2)+'\n',encoding='utf-8')
    with (OUT/'observations.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({**r,'gems':','.join(r['gems'])} for r in rows)

    combined = result['combined']; fresh = result['extension']
    lines = [
        '# Monster key-drop footage audit — 18 October 2026', '',
        f"The additional review found **{fresh['with_key']} key drops in {fresh['n']} verified kills ({100*fresh['proportion']:.1f}%)**. Combined with the frozen original sample, there are **{combined['with_key']}/{combined['n']} ({100*combined['proportion']:.1f}%)**. Every recorded base-rate reward was either zero or one key.", '',
        '| Sample | Key drops / kills | Observed rate | Nominal 95% Wilson interval |',
        '|---|---:|---:|---:|',
        table_line('Original, unchanged',result['original']),
        table_line('New extension',fresh),table_line('Combined',combined),'',
        'These are observed kill outcomes, not an exact game constant. The interval describes binomial sampling uncertainty under a common independent rate; it does not cover VOD selection, repeated runs from the same players, missing transitions or visual classification error. No finite sample proves a guaranteed drop rate.', '',
        '## Inclusion and review', '',
        'The denominator contains verified ordinary living monsters defeated by Fight or One Hit Wonder. A pre-hit key counter and settled post-hit counter were read, with any entrance key cost kept outside the delta. Spying Gem, Lodestone and Key Moment at the actual hit are excluded. Successful/failed escapes, bosses, trial fights, awakened corpses, deaths without a verified kill and unresolved transitions are excluded. Other stones are retained and recorded separately.', '',
        'Four VOD scopes were selected before key outcomes were reviewed: 129 Maexel candidate intervals, 140 N3 adjacent ordinary transitions, 154 Lootenant transitions in two fixed stage-2–4 runs, and 155 eligible official-channel transitions (187 grouped intervals retained including exclusions). Scope is bounded by retained offer parsing and local footage availability, not every kill in the original videos. Dense native checks recovered multiple distinct kills inside merged intervals. Cursor shape or unchanged keys alone was never enough to label a kill.', '',
        '| New source | Key drops / kills | Observed rate | Nominal 95% Wilson interval |',
        '|---|---:|---:|---:|',
    ]
    for source,s in result['extension_by_source'].items():
        lines.append(table_line(f'[{CREATORS[source]}](https://www.twitch.tv/videos/{source})',s))
    lines += ['', '## Stage and combat context', '',
              '| Stage, combined | Key drops / kills | Observed rate | Nominal 95% Wilson interval |',
              '|---|---:|---:|---:|']
    for stage,s in result['combined_by_stage'].items():
        lines.append(table_line(stage,s))
    lines += ['', '| Attack, combined | Key drops / kills | Observed rate | Nominal 95% Wilson interval |',
              '|---|---:|---:|---:|']
    for ohw,s in result['combined_by_one_hit_wonder'].items():
        lines.append(table_line('One Hit Wonder' if ohw=='True' else 'Ordinary attack',s))
    lines += ['', 'Lootenant contributes 25 One Hit Wonder kills and only one ordinary attack. Later-stage samples therefore do not replicate the earlier sample’s exact combat mix. Differences between these small subgroups do not establish stage or blessing modifiers.', '',
              '| Stone state, combined | Key drops / kills | Observed rate | Nominal 95% Wilson interval |',
              '|---|---:|---:|---:|']
    for state,s in result['combined_by_stone_stratum'].items():
        lines.append(table_line('No stones' if state=='none' else 'Other stones; no Spying/Lodestone',s))
    lines += ['', '| Run context, combined | Key drops / kills | Observed rate | Nominal 95% Wilson interval |',
              '|---|---:|---:|---:|']
    for state,s in result['combined_by_run_class'].items():
        lines.append(table_line(state,s))
    lines += ['', '| Confirmed mode, combined | Key drops / kills | Observed rate | Nominal 95% Wilson interval |',
              '|---|---:|---:|---:|']
    for state,s in result['combined_by_mode'].items():
        lines.append(table_line(state,s))
    lines += ['', 'Unknown first/later and mode labels remain unknown; no inference from room-five shops or the HUD orb is used to fill them. Maexel’s obscured room counter permits stage labels but not exact room numbers in this extension. Combat timestamps and dense split evidence distinguish those kills.', '',
              '## Implications and limits', '',
              'The new sample reinforces a substantially higher kill key-drop rate than the simulator’s old 50% placeholder. Approximately 85% is a reasonable provisional calibration candidate for the audited context; test neighboring rates rather than treating 85% or the empirical fraction as proven. The source ledgers and strata remain available to detect a mode, stage or combat-method difference as more data arrives.', '',
              'The separate Lodestone observations are not pooled here. A bonus that caps the final probability at 100% cannot identify the underlying base rate by subtraction. This audit does not prove that Lodestone itself guarantees a key.', '',
              'No simulator parameter, policy or training result was changed by this audit. Existing completion estimates still use their recorded configuration.', '',
              '## Saved evidence and verification', '',
              '- [Combined summary](../data/vods/key-drop-extension-2026-10-17/summary.json)',
              '- [All included observations, CSV](../data/vods/key-drop-extension-2026-10-17/observations.csv)',
              '- [Verification record](../data/vods/key-drop-extension-2026-10-17/verification.json)',
              '- [Maexel ledger](../data/vods/key-drop-extension-2026-10-17/maexel.json)',
              '- [N3 ledger](../data/vods/key-drop-extension-2026-10-17/n3.json)',
              '- [Lootenant ledger](../data/vods/key-drop-extension-2026-10-17/lootenant.json)',
              '- [Official-channel ledger](../data/vods/key-drop-extension-2026-10-17/official.json)', '',
              f"Validation checked {len(images):,} referenced evidence-image hashes, unique combat identities, separate dense splits, cross-audit time overlap, modifier exclusions and before/after key arithmetic. Source ledgers retain timestamps, evidence paths and video hashes. Uncertain outcomes are not counted as zeroes.", '']
    report = ROOT/'docs/KEY_DROP_EXTENSION_2026_10_18.md'
    report.write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'report':str(report),'original':result['original'],'extension':fresh,'combined':combined,
                      'by_stage':result['combined_by_stage'],'by_attack':result['combined_by_one_hit_wonder'],
                      'evidence_images':len(images)},indent=2))


if __name__=='__main__':
    main()
