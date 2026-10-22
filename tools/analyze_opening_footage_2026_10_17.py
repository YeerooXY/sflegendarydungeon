"""Assemble manually reviewed native evidence; predictions never become observations.

The compact labels below were entered after direct visual review of all 16
contact sheets and the continuous N3 opening sequences. This is an audit
artifact, not a classifier, and it does not change simulator parameters.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vod-hunt-2026-10-12'
OUT = ROOT / 'data/vods/opening-audit-2026-10-17'
KINDS = {'e': 'monster', 'm': 'mystery', 'd': 'destiny',
         'b': 'blessing', 'h': 'hungry', 'o': 'shop', 'w': 'wall'}
LABELS = ("eb em me me me em ?? em de de me ow me ed he he wo he em de ed wo "
          "be me me eh me em me me em me ed me me eh em em he eb me ed de em "
          "em eb eh eb me he me de he eb me be eh me eb de de he me de eh he wo "
          "ed ed de me me ed em em me eh me be eb me eh ed em he be me me de me me be").split()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def kind(value):
    if value.startswith('hungry') or value in {'wood', 'stone', 'souls', 'metal', 'arcane', 'hourglasses'}:
        return 'hungry'
    return value


def wilson(k, n):
    if not n:
        return None
    z = 1.959963984540054
    p = k / n
    center = (p + z*z/(2*n)) / (1+z*z/n)
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
    return [max(0.0, center-half), min(1.0, center+half)]


def evidence(e, base=ROOT):
    if 'crop' in e:
        path = ROOT / e['crop']
        expected = e['crop_sha256']
    else:
        path = base / e['image']
        expected = e['sha256']
    assert path.exists(), path
    assert digest(path) == expected, path
    return {'image': relative(path), 'sha256': expected,
            'source_seconds': e['source_seconds'],
            'decoded_source_seconds': e.get('decoded_source_seconds'),
            'video_sha256': e['video_sha256']}


def original_rows():
    candidates = read(ROOT / 'out/opening-audit-2026-10-17/opening-candidates.json')
    assert len(candidates) == len(LABELS) == 92
    contexts = read(OUT / 'run-context.json')['runs']
    rows = []
    for index, (r, label) in enumerate(zip(candidates, LABELS)):
        # These two old rows are replaced by the complete four-opening review.
        if r['source_id'] == '2877408140':
            continue
        observed = [KINDS[x] for x in label]
        assert observed == [kind(x['kind']) for x in r['doors']], (index, observed, r['doors'])
        eligible = [c for c in contexts if c['source_id'] == r['source_id']
                    and c['opening_start_seconds'] <= r['start'] + 3]
        context = max(eligible, key=lambda x: x['opening_start_seconds'])
        if context.get('offer_ids'):
            assert r['id'] in context['offer_ids'], (r['id'], context['run_id'])
        else:
            assert r['start'] - context['opening_start_seconds'] < 200, (r['id'], context['run_id'])
        doors = [{'kind': k, 'style': r['doors'][i]['kind'], 'trap': False}
                 for i, k in enumerate(observed)]
        notes = []
        if r['source_id'] in {'2881887759', '2885567293'}:
            doors[1]['trap'] = None
            notes.append('Right lower trap area partly obscured by streamer overlay; unknown, not negative.')
        rows.append({'id': r['id'], 'source_id': r['source_id'], 'view_id': r['source_id'],
                     'opening_id': context['run_id'], 'room': r['room'],
                     'source_seconds': r['start'], 'classification': context['classification'],
                     'mode': 'unknown', 'doors': doors,
                     'evidence': [evidence(e, WORK) for e in r['evidence']],
                     'reviewer': 'root', 'method': 'manual_native_offer_and_counter_recheck',
                     'original_audit_index': index, 'notes': notes})
    return rows, contexts


def n3_rows():
    source = '2877408140'
    directory = WORK / source / 'review-preparation/bursts'
    data = {
        'a': ('opening-audit-20261017-2258', 2273, [(15,'me'),(19,'me'),(23,'em'),(31,'em'),(35,'ow')]),
        'b': ('opening-audit-20261017-2394', 2398, [(8,'me'),(14,'he'),(24,'me'),(32,'em'),(37,'ow')]),
        'c': ('opening-audit-20261017-2515', 2521, [(6,'em'),(29,'be'),(33,'eb'),(37,'he'),(None,'wo')]),
        'd': ('opening-audit-20261017-2515', 2604, [(89,'be'),(94,'em'),(96,'me'),(123,'he'),(None,'wo')]),
    }
    rows, runs = [], []
    contexts = {r['opening_start_seconds']: r for r in read(OUT/'additional-n3-context.json')['rows']}
    for name, (burst, start, labels) in data.items():
        frames = read(directory / burst / 'index.json')['frames']
        context = contexts[start]
        run_id = f'{source}:opening-{start}'
        runs.append({'run_id': run_id, 'source_id': source, 'view_id': source,
                     'opening_start_seconds': start, 'classification': 'first_confirmed',
                     'mode': context['mode'], 'basis': context['run_context_note'],
                     'context_reference': 'additional-n3-context.json'})
        for room, (index, label) in enumerate(labels, 1):
            if index is None:
                dense = read(directory / f'opening-audit-20261017-shop-{name}' / 'index.json')
                e = dense['frames'][7 if name == 'c' else 11]
            else:
                e = frames[index]
            notes = []
            if name == 'd' and room == 3:
                notes.append('Replaces original audit row6: its saved still showed room2 combat, not the claimed room3 offer. Continuous native sequence verifies room3 here.')
            rows.append({'id': f'{run_id}:room-{room}', 'source_id': source, 'view_id': source,
                         'opening_id': run_id, 'room': room, 'source_seconds': e['source_seconds'],
                         'classification': 'first_confirmed', 'mode': context['mode'],
                         'doors': [{'kind': KINDS[x], 'trap': False} for x in label],
                         'evidence': [evidence(e, WORK)], 'reviewer': 'root',
                         'method': 'manual_continuous_native_sequence_and_counter', 'notes': notes})
    return rows, runs


def additional_rows():
    data = read(OUT / 'additional-openings.json')
    modes = {r['opening_id']: r for r in read(OUT/'additional-mode-context.json')['rows']}
    mapping = {'first_run_supported': 'first_supported', 'first_or_later_unknown': 'unknown',
               'later_run_observed': 'later_confirmed'}
    rows, runs = [], []
    for r in data['rows']:
        mode = modes[r['opening_id']]['mode']
        rows.append({'id': r['id'], 'source_id': r['source_id'], 'view_id': r['view_id'],
                     'opening_id': r['opening_id'], 'room': r['room'],
                     'source_seconds': r['source_seconds'], 'mode': mode,
                     'classification': mapping[r['run_context']],
                     'doors': [{'kind': kind(r[s]['kind']), 'style': r[s]['kind'],
                                'trap': r[s]['trap']} for s in ['left', 'right']],
                     'evidence': [evidence(r['evidence'])], 'reviewer': 'extra_opening_footage',
                     'method': r['review'], 'notes': r['notes']})
    for r in data['runs']:
        runs.append({'run_id': r['id'], 'source_id': r['view'].split('-')[0], 'view_id': r['view'],
                     'classification': mapping[r['context']], 'mode': modes[r['id']]['mode'],
                     'basis': r['context_note'], 'context_reference': 'additional-openings.json',
                     'mode_reference': 'additional-mode-context.json'})
    return rows, runs


def distribution(rows):
    counts = Counter()
    for r in rows:
        doors = [d['kind'] for d in r['doors']]
        if 'monster' in doors:
            assert doors.count('monster') == 1
            counts[next(x for x in doors if x != 'monster')] += 1
        else:
            assert set(doors) == {'shop', 'wall'}, doors
            counts['shop_wall'] += 1
    return {'n': len(rows), 'counts': dict(counts),
            'categories': {k: {'count': counts[k], 'fraction': counts[k]/len(rows),
                                'nominal_wilson_95': wilson(counts[k], len(rows))}
                           for k in ['mystery','destiny','blessing','hungry','shop_wall']}}


def cluster_bootstrap(rows, iterations=20000):
    import numpy as np
    groups = defaultdict(list)
    for r in rows:
        groups[r['source_id']].append(r)
    types = ['mystery', 'destiny', 'blessing', 'hungry']
    matrix = np.array([[sum(any(d['kind']==t for d in r['doors']) for r in rs)
                        for t in types] + [len(rs)] for rs in groups.values()])
    rng = np.random.default_rng(20261017)
    draws = rng.integers(0, len(matrix), size=(iterations, len(matrix)))
    sums = matrix[draws].sum(axis=1)
    fractions = sums[:, :4]/sums[:, 4, None]
    limits = np.quantile(fractions, [.025, .975], axis=0)
    return {'cluster_unit': 'source_vod_id', 'clusters': len(matrix), 'iterations': iterations,
            'seed': 20261017, 'percentile_95': {t: limits[:,i].tolist() for i,t in enumerate(types)},
            'limitation': 'Sensitivity to VOD clustering only; convenience selection, character clustering across VODs and recognition omissions are not corrected.'}


def report(summary):
    def pct(value):
        return f'{100*value:.1f}%'

    def interval(values):
        return f'{100*values[0]:.1f}–{100*values[1]:.1f}%'

    lines = [
        '# Opening rooms and monster-key audit — 17 October 2026', '',
        'Direct footage review challenges the simulator’s 50% baseline key chance and its opening-door weights. '
        'It confirms special doors in rooms 1–5 and supports a first-run room-five shop. '
        'No engine, profile, policy, or training output was changed by this audit.', '',
        '## Monster keys', '',
        'Counted confirmed kills of ordinary living monsters, using the key counter before and after the fight. '
        'Escapes, bosses, deaths before a kill, awakened corpses, uncertain actions and key-modifying effects were excluded. '
        'All 60 baseline kills are from stage 1, rooms 1–24; this does not establish a common rate for later stages.', '',
        '| Conditions | At least one key / kills | Observed rate | Nominal 95% Wilson interval |',
        '|---|---:|---:|---:|',
    ]
    keys = summary['monster_keys']
    for label, result in [('No key modifiers', keys['baseline'])] + [
            (name.replace('_',' ').capitalize()+' run', value) for name,value in keys['by_run_class'].items()] + [
            (name.capitalize()+' mode', value) for name,value in keys['by_confirmed_mode'].items()]:
        lines.append(f"| {label} | {result['drops_1']+result['drops_2']}/{result['n']} | "
                     f"{pct(result['any_key_fraction'])} | {interval(result['any_key_wilson_95'])} |")
    lines += ['',
        'The 60 kills gave **50 single keys, ten zero-key results and no two-key drops**. '
        'Normal attacks gave keys in 38/47 kills; One Hit Wonder kills did so in 12/13. '
        'Those subsets are too small to establish a difference between attack types.', '',
        'A concrete first-run failure is Sensei’s room 3 at 1851.5–1857.8 seconds: the monster dies, '
        '7,092,707 gold appears, and the counter stays at two keys. '
        'The preceding first-entry achievement independently establishes the run classification. '
        '[Before/fight/after evidence](../out/opening-audit-2026-10-17/keys/2877531246-1837-2000/1851.500-r03.jpg).', '',
        '**Working interpretation:** around 80% is a substantially better candidate than 50% for these stage-one kills. '
        '83.3% is the sample rate, not a recovered game constant. First-versus-later intervals overlap; '
        'the sample does not establish different probabilities. A separate fixed held-out sample, including stages 2–4, '
        'is needed before claiming a universal value.', '',
        '## Lodestone', '',
        'The native tooltip says “Increases the chance of keys from fights by 30%.” '
        '[Native tooltip](../out/opening-audit-2026-10-17/lodestone/2881859569-07627.000.png). '
        'Eight ordinary kills with Lodestone and no other key modifier gave eight single keys. '
        'Five further kills with Key Moment at entry were kept separate.', '',
        'A 100% cap is **consistent with 8/8, not verified by it**. The nominal Wilson interval is '
        f"{interval(summary['lodestone']['nominal_wilson_95'])}; even an 80% chance produces eight successive successes about 16.8% of the time. "
        'All eight observations come from two runs of one streamer.', '',
        'Even if the cap were confirmed, it would not identify the base chance. With a +30 percentage-point modifier, '
        'any base of at least 70% caps at 100%; with a relative ×1.30 modifier, any base of at least 76.9% does. '
        'At an 80% base both interpretations cap, so Lodestone alone cannot distinguish them. '
        'The current simulator uses +30 percentage points, producing only 80% with its existing 50% base.', '',
        'The [official Legendary Dungeon help page](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/192-legendary-dungeon-1664468203/?p=ios) '
        'describes keys from defeated monsters without publishing a percentage. It also explicitly excludes keys '
        'from Ultimate’s threefold resource multiplier. Checked 17 October 2026.', '',
        '## Rooms 1–5', '',
        f"Reviewed **{summary['offers']} unique offers across {summary['openings']} complete openings in {summary['sources']} VODs**. "
        'Every opening has separately verified counters for all five rooms. Repeated frames count once. '
        'This is the audited subset of readable downloaded footage, not a claim that every opening in the full corpus was recovered.', '',
        'For the comparable rooms **1–4**, every pair contained a visible monster plus the following other choice:', '',
        '| Other choice | Observed / 112 | Observed rate | Nominal 95% Wilson interval | Simulator |',
        '|---|---:|---:|---:|---:|',
    ]
    for name, simulated in [('mystery',.8),('destiny',.08),('blessing',.08),('hungry',.04)]:
        r = summary['rooms_1_to_4_all']['categories'][name]
        lines.append(f"| {name.capitalize()} | {r['count']}/112 | {pct(r['fraction'])} | "
                     f"{interval(r['nominal_wilson_95'])} | {pct(simulated)} |")
    lines += ['',
        'Special doors are substantially more frequent here than in the 80/8/8/4 placeholder. '
        'These counts do not establish exact replacement weights or prove identical generation in Normal and Ultimate.', '',
        '| Run classification | Openings | Rooms 1–4: mystery / Destiny / blessing / hungry | Room-five shop |',
        '|---|---:|---|---:|',
    ]
    for name, s in summary['classification'].items():
        c = s['rooms_1_to_4']['counts']
        counts = ' / '.join(str(c.get(k,0)) for k in ['mystery','destiny','blessing','hungry'])
        lines.append(f"| {name.replace('_',' ')} | {s['openings']} | {counts} | "
                     f"{s['room_5']['counts'].get('shop_wall',0)}/{s['openings']} |")
    lines += ['',
        'First-confirmed means an independent first-entry achievement. Later-confirmed requires a preceding completion '
        'by the same character. First-supported relies on the introductory shop and therefore cannot independently prove '
        'the shop rule. Unknowns stay unknown: switching characters is not evidence of a later run.', '',
        'The clear supported distinction is **room 5: first-run shop versus later-run ordinary pair**. '
        'Destiny, blessing and hungry doors occur in the first four rooms of both confirmed first and confirmed later runs. '
        'There is not enough evidence to claim different underlying probabilities between those groups.', '',
        'Mode evidence is retained separately:', '',
        '| Mode | Openings | Rooms 1–4: mystery / Destiny / blessing / hungry |',
        '|---|---:|---|',
    ]
    for name, s in summary['mode'].items():
        c = s['rooms_1_to_4']['counts']
        counts = ' / '.join(str(c.get(k,0)) for k in ['mystery','destiny','blessing','hungry'])
        lines.append(f"| {name} | {s['openings']} | {counts} |")
    t = summary['traps']
    lines += ['',
        f"No traps were visible in {t['individual_readable_offers']} readable individual non-shop offers; "
        f"{t['obscured_offers']} offered sides remain obscured. There were {t['fully_visible_nonshop_pairs']} fully readable non-shop pairs. "
        'This supports the no-traps opening rule but cannot prove zero probability. '
        'No ordinary locked, cursed, sacrificial or golden opening choice was found in this audit.', '',
        '## Reliability and implications', '',
        'The displayed confidence intervals are nominal binomial intervals, not guarantees about hidden game constants. '
        'The footage is a convenience sample with repeated characters and streamers. For opening choices, a separate '
        '20,000-draw VOD-cluster bootstrap is saved in summary.json; its 95% ranges are:', '',
    ]
    for name, limits in summary['rooms_1_to_4_cluster_sensitivity']['percentile_95'].items():
        lines.append(f'- {name.capitalize()}: {interval(limits)}.')
    lines += ['',
        'That sensitivity check addresses within-VOD dependence, not nonrandom selection or missed footage. '
        'It is particularly important not to pool room 5 into a first/later door comparison because the first-run shop '
        'changes that denominator.', '',
        'The earlier forced-opening-fight experiment used 50% keys and the old opening weights. Its rankings remain '
        'valid only for that simulated scenario; this footage audit is a reason to recalibrate and rerun that comparison, '
        'not evidence that avoiding early fights is best in the real game.', '',
        '## Saved evidence and verification', '',
        '- [Merged offers, run identities and per-frame hashes](../data/vods/opening-audit-2026-10-17/offers.json).',
        '- [Per-kill baseline key ledger](../data/vods/opening-audit-2026-10-17/key-drops.json).',
        '- [Lodestone kills and exclusions](../data/vods/opening-audit-2026-10-17/lodestone-key-drops.json).',
        '- [Counts, intervals and verification results](../data/vods/opening-audit-2026-10-17/summary.json).',
        '- [First/later evidence](../data/vods/opening-audit-2026-10-17/run-context.json) and '
        '[mode and first-entry amendments](../data/vods/opening-audit-2026-10-17/run-modes.json).', '',
        'Reproduction: `out/vod-env/Scripts/python.exe tools/analyze_opening_footage_2026_10_17.py`. '
        'The script assembles manual labels, validates native-image hashes, rejects duplicate run/room identities and '
        'requires all five counters per opening. It does not turn classifier predictions into observations. '
        'One stale supporting image was replaced: the old N3 room-three record showed room-two combat; '
        'the continuous sequence now supplies the actual room-three offer.', '',
    ]
    (ROOT/'docs/OPENING_FOOTAGE_2026_10_17.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    rows, contexts = original_rows()
    runs = [{**c, 'mode': 'unknown', 'view_id': c['source_id'],
             'context_reference': 'run-context.json'} for c in contexts]
    nr, nc = n3_rows(); rows.extend(nr); runs.extend(nc)
    ar, ac = additional_rows(); rows.extend(ar); runs.extend(ac)
    mode_path = OUT / 'run-modes.json'
    if mode_path.exists():
        mode_rows = read(mode_path)
        mode_rows = mode_rows.get('runs', mode_rows.get('rows', []))
        modes = {r['run_id']: r for r in mode_rows}
        for r in rows:
            if r['opening_id'] in modes:
                amendment = modes[r['opening_id']]
                r['mode'] = amendment['mode']
                r['classification'] = amendment.get('classification_override', r['classification'])
        for r in runs:
            if r['run_id'] in modes:
                amendment = modes[r['run_id']]
                r['mode'] = amendment['mode']
                r['classification'] = amendment.get('classification_override', r['classification'])
                if amendment.get('classification_override'):
                    r['classification_amendment_reference'] = 'run-modes.json'
                r['mode_reference'] = 'run-modes.json'
    rows.sort(key=lambda r: (r['source_id'], r['view_id'], r['source_seconds']))
    identifiers = [(r['view_id'], r['opening_id'], r['room']) for r in rows]
    assert len(identifiers) == len(set(identifiers)) == 140
    for run in runs:
        actual = sorted(r['room'] for r in rows if r['opening_id'] == run['run_id'])
        assert actual == [1,2,3,4,5], (run['run_id'], actual)
    save(OUT/'offers.json', {'schema': 1, 'reviewed_on': '2026-10-17', 'rows': rows,
        'runs': runs, 'original_ledger_sha256': digest(ROOT/'data/vods/reviewed-offers-2026-10-12.jsonl'),
        'scope': '140 unique offers from28 complete openings; selected readable newer footage, not the entire corpus.',
        'changes_from_92_candidates': ['Rechecked all16 contact sheets.',
            'Replaced2 partial N3 records with20 native offers from4 complete openings; corrected stale room3 evidence.',
            'Added30 supplementary offers from6 openings.',
            'Recovered fourth N3 first-entry achievement with dense sampling; applied explicit Ultimate mode proofs.',
            'Unknown trap visibility retained; character switches do not establish a later run.']})
    first_four = [r for r in rows if r['room']<=4]
    strata = {}
    for classification in ['first_confirmed','first_supported','later_confirmed','unknown']:
        group = [r for r in rows if r['classification']==classification]
        strata[classification] = {'openings': len(group)//5,
                                  'rooms_1_to_4': distribution([r for r in group if r['room']<=4]),
                                  'room_5': distribution([r for r in group if r['room']==5])}
    modes = {}
    for mode in sorted({r['mode'] for r in rows}):
        group = [r for r in rows if r['mode']==mode]
        modes[mode] = {'openings': len(group)//5,
                       'rooms_1_to_4': distribution([r for r in group if r['room']<=4]),
                       'room_5': distribution([r for r in group if r['room']==5])}
    trap_values = [d['trap'] for r in rows for d in r['doors']
                   if d['kind'] not in {'shop','wall'}]
    trap_rooms = [r for r in rows if all(d['trap'] is not None for d in r['doors'])
                  and not any(d['kind']=='shop' for d in r['doors'])]
    result = {'schema': 1, 'date': '2026-10-17', 'offers': len(rows), 'openings': len(runs),
              'sources': len({r['source_id'] for r in rows}),
              'views': len({r['view_id'] for r in rows}), 'classification': strata,
              'mode': modes, 'rooms_1_to_4_all': distribution(first_four),
              'room_5_all': distribution([r for r in rows if r['room']==5]),
              'rooms_1_to_4_cluster_sensitivity': cluster_bootstrap(first_four),
              'traps': {'observed': sum(t is True for t in trap_values),
                        'individual_readable_offers': sum(t is not None for t in trap_values),
                        'obscured_offers': sum(t is None for t in trap_values),
                        'fully_visible_nonshop_pairs': len(trap_rooms),
                        'nominal_pair_wilson_95': wilson(0, len(trap_rooms)),
                        'note': 'Absence in this sample supports but cannot prove the no-opening-traps rule.'},
              'simulator_comparison': {'opening_mystery': .80, 'opening_destiny': .08,
                                      'opening_blessing': .08, 'opening_hungry': .04,
                                      'key_drop_chance': .50, 'parameters_changed': False},
              'uncertainty': ['Wilson intervals assume common independent Bernoulli trials; these are descriptive nominal intervals for a clustered convenience sample.',
                              'First-supported openings were partly identified by their free shop: they cannot independently prove the free-shop rule.',
                              'Mode equality and first/later probability equality remain unestablished.',
                              'No exact engine constants are inferred by rounding observed percentages.']}
    key_path = OUT/'key-drops.json'
    if key_path.exists():
        keys = read(key_path)
        result['monster_keys'] = {k: keys[k] for k in ['baseline','by_run_class','by_source','by_one_hit_wonder','opening_rooms_1_to_5','by_confirmed_mode']}
        result['monster_keys']['reference'] = 'key-drops.json'
    lodestone = read(OUT/'lodestone-key-drops.json')['summary']
    result['lodestone'] = {**lodestone,
        'nominal_wilson_95': wilson(lodestone['with_key'],lodestone['ordinary_kills_no_other_key_modifier']),
        'reference': 'lodestone-key-drops.json'}
    result['verification'] = {
        'unique_view_opening_room_ids': len(set(identifiers)),
        'complete_openings': len(runs), 'rooms_per_opening': 5,
        'native_offer_frame_hashes_checked': sum(len(r['evidence']) for r in rows),
        'engine_profile_or_policy_changes': False,
    }
    save(OUT/'summary.json', result)
    report(result)
    verification = verify_evidence()
    save(OUT/'verification.json', verification)
    print(json.dumps(result, indent=2))


def verify_evidence():
    checked = {}
    files = ['offers.json','run-context.json','run-modes.json','additional-openings.json',
             'additional-mode-context.json','additional-n3-context.json',
             'key-drops.json','lodestone-key-drops.json']

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for field, hash_field in [('image','sha256'),('crop','crop_sha256'),('frame','frame_sha256')]:
                if field in value and hash_field in value:
                    path = ROOT / value[field]
                    if not path.exists():
                        path = WORK / value[field]
                    assert path.exists(), path
                    name = relative(path)
                    actual = checked.get(name)
                    if actual is None:
                        actual = digest(path)
                        checked[name] = actual
                    assert actual == value[hash_field], name
            for item in value.values():
                if isinstance(item,(list,dict)):
                    visit(item)

    for filename in files:
        visit(read(OUT/filename))
    keys = read(OUT/'key-drops.json')['baseline_kills']
    assert len(keys) == len({(r['source_id'],r['run_id'],r['room']) for r in keys}) == 60
    assert all(1<=r['room']<=24 and not r['gems'] and not r['key_moment_active'] for r in keys)
    assert all(r['keys_after']-r['keys_before']==r['key_drop'] for r in keys)
    assert sum(r['key_drop'] for r in keys)==50
    return {'status': 'passed', 'date': '2026-10-17',
            'unique_native_images_hash_checked': len(checked), 'ledgers_checked': files,
            'baseline_unique_kills': 60, 'baseline_total_keys': 50,
            'checks': ['All cited native image files exist and match stored SHA256.',
                       '140 unique view/opening/room records;28 openings each contain rooms1–5 exactly once.',
                       '60 distinct baseline kills, no stones or Key Moment, rooms1–24.',
                       'Every baseline key delta equals its recorded drop;50 total keys.',
                       'Wilson intervals and seeded VOD-cluster bootstrap regenerated from manual counts.'],
            'limits': ['Hash checks establish file integrity, not visual-label correctness.',
                       'Visual labels are manual judgments; uncertain fields remain unknown.',
                       'No website or simulator changes; browser and gameplay regression tests are not applicable.']}


if __name__ == '__main__':
    main()
