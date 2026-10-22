"""Publish explicit manual footage judgements; no predicted labels become data.

The 129 candidate transitions were fixed by prepare_key_extension_20261017.py
before reading their key outcomes. Dense native bursts resolve missed rooms.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '2878297081'
BASE = ROOT / 'out/key-drop-extension-2026-10-17' / SOURCE
DEST = ROOT / 'data/vods/key-drop-extension-2026-10-17/maexel.json'

# candidate, pre-hit keys, settled keys, One Hit Wonder at the hit, note
SIMPLE = [
    (1, 0, 1, False, 'Destiny then living goblin; crossed swords followed by death and key. Gold Rush is the bag icon, not Key Moment.'),
    (7, 8, 9, False, 'Living swordsman slides down and disappears; one key floats.'),
    (8, 9, 10, False, 'Living fish monster; attack, death and one key.'),
    (19, 10, 11, False, 'Trap on entry is distinct from subsequent ordinary kill; empty blessing slots.'),
    (30, 7, 8, False, 'Entry trap then torch-bearing cultist; death and one key. No active blessing.'),
    (42, 3, 4, False, 'Living fish monster; attack and one key. Recovery, Disarm and Lockpick are not key-drop modifiers.'),
    (47, 5, 6, False, 'Initially hovers Escape, then attacks; death and one key. Do not classify by first cursor.'),
    (57, 12, 13, False, 'Purple hooded monster; death and key. Moonstone only, empty blessing slots.'),
    (68, 9, 9, True, 'Trap damages on entry; One Hit Wonder kills fat monk, which slides down-left and disappears. No key; no escape zoom.'),
    (70, 9, 9, True, 'Monk killed; gold reward 1,480,715, no key change.'),
    (71, 9, 10, True, 'Whip-bearing monk killed; new key float, previous gold float remains visible.'),
    (72, 10, 11, True, 'Red-hatted cultist killed; one key.'),
    (73, 11, 12, True, 'Purple hooded monster killed; one key.'),
    (74, 12, 12, True, 'Last One Hit Wonder charge; monk dies, gold reward 740,357, no new key. Prior key float is not a new reward.'),
    (113, 10, 11, True, 'Stage-four living monster killed; One Hit Wonder and Lockpick only. Key Moment is no longer present.'),
    (114, 11, 12, True, 'Final One Hit Wonder charge kills living tentacle monster; one key.'),
    (125, 5, 6, False, 'Stage-one goblin killed; all blessing and stone slots empty at hit. Earlier Key Moment has expired.'),
]

# Dense range start, candidate(s), keys before/after, OHW, actual hit locator.
DENSE = [
    (7395.875, 2, 1, 2, False, 7396.9),
    (7399.875, 2, 2, 3, True, 7400.7),
    (7406.0, 3, 3, 4, True, 7406.5),
    (7408.0, 4, 4, 5, True, 7408.5),
    (7410.0, 4, 5, 6, True, 7410.5),
    (7416.75, 5, 6, 7, False, 7417.3),
    (7426.125, 5, 7, 8, False, 7426.95),
    (7457.25, 13, 9, 9, False, 7457.85),
    (7458.875, 13, 9, 10, False, 7459.4),
    (7976.125, 46, 4, 5, False, 7977.1),
]

DISPOSITIONS = '''
0 noncombat_hungry
1 included
2 split_included
3 dense_included
4 split_included
5 split_included
6 escape
7 included
8 included
9 escape
10 noncombat_container
11 noncombat_container
12 escape_then_container
13 split_included
14 two_escapes
15 escape_then_boss_death
16 noncombat_hungry
17 empty
18 escape
19 included
20 escape
21 escape
22 corpse_did_not_awaken
23 escape
24 shop
25 corpse_did_not_awaken
26 empty
27 escape
28 escape
29 escape
30 included
31 escape
32 escape
33 escape
34 escape
35 escape
36 cursed_chest
37 empty
38 golden_reward
39 noncombat_hungry
40 noncombat_container
41 awakened_corpse
42 included
43 noncombat_container
44 escape
45 noncombat_container
46 escape_then_verified_kill
47 included
48 merged_noncombat
49 escape
50 noncombat_container
51 escape
52 barrel
53 multiple_kills_with_key_moment
54 kill_with_key_moment
55 kill_with_key_moment
56 escape
57 included
58 empty
59 noncombat_container
60 escape
61 escape
62 escape
63 empty
64 noncombat_container
65 escape
66 shop
67 noncombat_hungry
68 included
69 golden_reward
70 included
71 included
72 included
73 included
74 included
75 barrel
76 empty
77 escape
78 kill_with_key_moment
79 barrel
80 escape
81 golden_heal
82 escape
83 corpse_did_not_awaken
84 escape
85 noncombat_hungry
86 noncombat_container
87 escape
88 barrel
89 shop
90 golden_item
91 kill_with_key_moment
92 kill_with_key_moment
93 kill_with_key_moment
94 kill_with_key_moment
95 escape
96 golden_heal
97 escape
98 escape
99 noncombat_container
100 merged_empty_and_escape
101 noncombat_container
102 noncombat_container
103 escape
104 noncombat_hungry
105 shop
106 noncombat_container
107 escape
108 escape
109 noncombat_container
110 golden_reward_and_inventory
111 inventory
112 noncombat_container
113 included
114 included
115 noncombat_hungry
116 corpse_did_not_awaken
117 noncombat_container
118 corpse_did_not_awaken
119 barrel
120 noncombat_hungry
121 merged_kills_with_key_moment
122 noncombat_container
123 escape
124 merged_kills_with_key_moment
125 included
126 two_escapes
127 chest_item
128 escape
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    candidates = json.loads((BASE / 'index.json').read_text())
    scope = json.loads((BASE / 'scope.json').read_text())
    dense = {r['start']: r for r in json.loads((BASE / 'dense/index.json').read_text())}
    dispositions = {int(a): b for a, b in (s.split() for s in DISPOSITIONS.splitlines() if s.strip())}
    assert set(dispositions) == set(range(len(candidates)))
    kills = []

    def add(number, before, after, ohw, evidence, sheet, note, hit=None):
        c = candidates[number]
        hit = hit if hit is not None else (c['end'] + c['next_start']) / 2
        record = {
            'id': f'{SOURCE}:kill:{hit:.3f}', 'source_id': SOURCE,
            'url': f'https://www.twitch.tv/videos/{SOURCE}?t={int(hit)}s',
            'offer_id': c['offer_id'], 'candidate_index': number,
            'source_seconds': hit, 'start': evidence[0]['source_seconds'],
            'end': evidence[-1]['source_seconds'], 'room': None,
            'stage': c['stage_locator'], 'run_class': 'unknown', 'mode': 'unknown',
            'run_id': f'{SOURCE}:window-' + ('A' if hit < 7800 else 'B' if hit < 8500 else 'C'),
            'gems': c['gems_locator'], 'stone_set_complete': True,
            'key_moment_active': False, 'key_modifiers': [],
            'action': 'fight', 'outcome': 'kill', 'encounter': 'ordinary_living_on_entry',
            'one_hit_wonder': ohw, 'keys_before': before, 'keys_after': after,
            'keys_gained': after-before, 'key_drop': after>before,
            'verification': 'manual_native_sequence_and_enlarged_hud',
            'notes': [note, 'Room counter is obscured; stage comes from environment and visible stone/boss sequence. First/later and Normal/Ultimate not established.'],
            'evidence': evidence, 'sheet': sheet,
        }
        assert not set(record['gems']) & {'spying', 'lodestone'}
        kills.append(record)

    for n, before, after, ohw, note in SIMPLE:
        c = candidates[n]
        add(n, before, after, ohw, c['evidence'], c['sheet'], note)
    for start, n, before, after, ohw, hit in DENSE:
        r = dense[start]
        note = 'Dense 8 fps review separates this ordinary kill from missed adjacent rooms. Attack and monster death followed through settled counter; no key-drop modifier at hit.'
        if after == before:
            note += ' Zero-drop example: swords at 7457.750, monster fades down-left at 7458.125, keys stay at 9. Earlier red key-spend float is not a reward.'
        add(n, before, after, ohw, r['evidence'], r['sheet'], note, hit)
    kills.sort(key=lambda r: r['source_seconds'])
    assert len(kills) == 27
    assert len({r['id'] for r in kills}) == len(kills)
    evidence = {e['image']: e for r in kills for e in r['evidence']}
    for rel, e in evidence.items():
        assert sha(ROOT / rel) == e['sha256'], rel
        assert abs(e['source_seconds'] - e['decoded_source_seconds']) < .11
    counts = Counter(r['keys_gained'] for r in kills)
    reviews = []
    for i, c in enumerate(candidates):
        reviews.append({**c, 'review': dispositions[i],
                        'included_kill_ids': [r['id'] for r in kills if r['candidate_index'] == i]})
    result = {
        'schema': 1, 'reviewed_on': '2026-10-18', 'source_id': SOURCE,
        'source_url': f'https://www.twitch.tv/videos/{SOURCE}',
        'source_metadata': f'out/vod-hunt-2026-10-12/{SOURCE}/source.json',
        'sampling': 'Fixed source-bounded convenience sample; 129 eligible decision intervals frozen before key-outcome review. Not a random sample or all combat in this VOD.',
        'scope_file': (BASE / 'scope.json').relative_to(ROOT).as_posix(),
        'scope_sha256': sha(BASE / 'scope.json'),
        'candidate_count': len(candidates), 'pre_review_exclusions': scope['pre_review_exclusions'],
        'candidate_reviews': reviews,
        'baseline_kills': kills,
        'summary': {'n': len(kills), 'drops_0': counts[0], 'drops_1': counts[1], 'drops_2': counts[2]},
        'validation': {'included_evidence_files_hash_checked': len(evidence), 'unique_kill_ids': True,
                       'all_candidates_dispositioned': True, 'no_spying_lodestone_key_moment_included': True},
        'limitations': [
            'Counter obscuration prevents exact room labels and independent first/later classification in these windows.',
            'Window C contains character switches before the included event; run_id is a review segment identifier, not a confirmed account identity.',
            'Non-key-modifying stones and One Hit Wonder are recorded; they are not assumed to produce statistically identical rates without comparison.',
            'Automatic decision parsing may miss entire transitions; bounded candidates and recovered merged intervals are not a complete VOD census.',
        ],
    }
    DEST.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'path': str(DEST), **result['summary'], **result['validation']}))


if __name__ == '__main__':
    main()
