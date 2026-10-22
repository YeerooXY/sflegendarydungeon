"""Validate and combine four fixed-scope Spying Gem footage audits."""
from collections import Counter, defaultdict
from pathlib import Path
import csv
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/vods/spying-key-audit-2026-10-18'
NAMES = ['maexel', 'dorzer', 'lootenant', 'n3']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def stats(rows):
    n = len(rows)
    k = sum(int(r['keys_gained']) > 0 for r in rows)
    if not n:
        return {'kills': 0, 'with_key': 0, 'rate': None, 'wilson_95': None}
    p, z = k / n, 1.959963984540054
    den = 1 + z*z/n
    centre = (p + z*z/(2*n)) / den
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / den
    return {'kills': n, 'with_key': k, 'without_key': n-k,
            'rate': p, 'wilson_95': [max(0, centre-half), min(1, centre+half)],
            'key_counts': dict(sorted(Counter(int(r['keys_gained']) for r in rows).items()))}


def groups(rows, field):
    grouped = defaultdict(list)
    for row in rows:
        value = row[field]
        if isinstance(value, list):
            value = '+'.join(sorted(value))
        grouped[str(value)].append(row)
    return {k: stats(v) for k, v in sorted(grouped.items())}


def evidence_objects(obj):
    if isinstance(obj, dict):
        for field, hashed in [('image', 'sha256'), ('frame', 'frame_sha256'), ('crop', 'crop_sha256')]:
            if field in obj and hashed in obj:
                yield obj[field], obj[hashed]
        for value in obj.values():
            yield from evidence_objects(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from evidence_objects(value)


def normalize(name, row):
    r = row
    if name == 'maexel':
        evidence = r['evidence']
        before = min(evidence, key=lambda x: abs(x['source_seconds']-r['before_seconds']))
        after = min(evidence, key=lambda x: abs(x['source_seconds']-r['after_seconds']))
        keys_before, keys_after = r['keys_before'], r['keys_after']
        one_hit, key_moment = r['one_hit_active'], r['key_moment_active']
        run, context, mode = r['run_id'], r['run_class'], r['mode']
    elif name == 'dorzer':
        before, after = r['before_evidence'], r['after_evidence']
        keys_before, keys_after = r['keys_before_hit'], r['keys_after_resolution']
        one_hit, key_moment = r['one_hit_wonder_at_hit'], r['key_moment_at_hit']
        run = r['source_id'] + ':segment-' + str(r['segment'])
        context, mode = r['first_or_later_run'], r['game_mode']
        assert r['ordinary_living_monster'] and not any(r[k] for k in ['boss','trial','awakened_corpse','key_payment_between'])
    elif name == 'lootenant':
        before, after = r['evidence'][r['keys_before_frame']], r['evidence'][r['keys_after_frame']]
        keys_before, keys_after = r['keys_before'], r['keys_after']
        one_hit, key_moment = r['one_hit_wonder'], r['key_moment_active']
        run, context, mode = r['run_id'], r['run_class'], r['mode']
        assert r['encounter_origin'] == 'living_monster_on_entry' and r['outcome'] == 'monster_killed'
    else:
        before, after = r['before_evidence'], r['after_evidence']
        keys_before, keys_after = r['keys_before'], r['keys_after']
        one_hit = r['attack_type'] == 'one_hit_wonder'
        key_moment = r['key_moment_active_at_hit']
        run, context, mode = r['run_id'], r['run_context'], r['mode']
        assert r['eligible_for_spying_probability'] and r['encounter_origin'] == 'living_monster_on_entry'
    gems = r.get('stones_at_hit', r['gems'])
    assert r['stone_set_complete'] and 'spying' in gems and 'lodestone' not in gems
    assert key_moment is False and r['action'] in ['fight', 'one_hit']
    delta = keys_after - keys_before
    assert delta in (0,1) and before['source_seconds'] < after['source_seconds']
    assert delta == r.get('key_drop', r.get('keys_gained'))
    context = {'later_confirmed': 'later', 'first_confirmed': 'first'}.get(context, context)
    seconds = before['source_seconds']
    sheet = r.get('sheet')
    if not sheet:
        ordinal = r['candidate_ordinal'] if name == 'maexel' else r['scope_index']
        sheet = Path(before['image']).with_name(f'{ordinal:03d}.jpg').as_posix()
    assert (ROOT/sheet).is_file(), sheet
    return {'id': r.get('id', r['offer_id']), 'creator': name, 'source_id': r['source_id'],
            'source_url': f"https://www.twitch.tv/videos/{r['source_id']}?t={int(seconds)//3600}h{int(seconds)%3600//60}m{int(seconds)%60}s",
            'audit_file': (DATA / (name+'.json')).relative_to(ROOT).as_posix(),
            'offer_id': r['offer_id'], 'source_seconds': seconds,
            'before_seconds': before['source_seconds'], 'after_seconds': after['source_seconds'],
            'room': r.get('room'), 'stage': r['stage'], 'run_id': run, 'run_class': context,
            'mode': mode, 'gems': gems, 'one_hit_wonder': one_hit,
            'key_moment_active': False, 'keys_before': keys_before, 'keys_after': keys_after,
            'keys_gained': delta, 'before_image': before['image'], 'after_image': after['image'],
            'sheet': sheet}


def main():
    documents = {n: read(DATA / (n+'.json')) for n in NAMES}
    selected = {'maexel': documents['maexel']['rows'],
                'dorzer': documents['dorzer']['clean_kills'],
                'lootenant': documents['lootenant']['clean_kills'],
                'n3': documents['n3']['spying_kills']}
    rows = [normalize(n,r) for n in NAMES for r in selected[n]]
    rows.sort(key=lambda r: (r['source_id'], r['before_seconds']))
    assert len({(r['source_id'],r['id']) for r in rows}) == len(rows)
    assert len({(r['source_id'],r['offer_id']) for r in rows}) == len(rows)
    for a,b in zip(rows,rows[1:]):
        if a['source_id'] == b['source_id']:
            assert a['after_seconds'] <= b['before_seconds'], (a['id'],b['id'])
    baseline_path = ROOT / 'data/vods/key-drop-extension-2026-10-17/observations.csv'
    with baseline_path.open(encoding='utf-8-sig',newline='') as f:
        baseline = list(csv.DictReader(f))
    for r in rows:
        for b in baseline:
            if r['source_id'] == b['source_id']:
                assert not (r['before_seconds'] < float(b['after_seconds']) and
                            float(b['before_seconds']) < r['after_seconds']), (r['id'],b['id'])
    stage24 = [b for b in baseline if int(b['stage']) >= 2]

    hashes = {}
    for doc in documents.values():
        for image, expected in evidence_objects(doc):
            image = Path(image).as_posix()
            assert image not in hashes or hashes[image] == expected
            hashes[image] = expected
    for image, expected in hashes.items():
        assert sha(ROOT/image) == expected, image

    m,d,l,n = (documents[x] for x in NAMES)
    assert len(m['rows'])+len(m['excluded'])+len(m['pending']) == m['scope']['reviewed_candidates']
    assert len(d['candidates_and_exclusions']) == d['scope_n']
    assert len(l['candidates']) == 153 and l['validation']['pending_candidates'] == 0
    assert sum(len(n[k]) for k in ['spying_kills','modified_kills','excluded_transitions','pending_transitions']) == 86
    assert len(n['scope_exclusions']) == 14
    pending = [{'source_id':d['source_id'], **x} for x in d['unresolved_manual_review']]
    for x in m['scope']['pre_review_spying_exclusions']:
        if 'gap' in x['excluded_before_review'] or x['excluded_before_review'] == 'transition_too_short_or_long':
            pending.append({'source_id':m['source_id'], 'from_seconds':x['start'],
                            'to_seconds':x['next_start'],'reason':x['excluded_before_review']})
    summary = {'schema':1, 'date':'2026-10-18',
        'definition':'Ordinary living-on-entry monster defeated with Spying active, no Lodestone or Key Moment at the hit. Entrances and their key costs precede the measured delta.',
        'inputs':[{'path':(DATA/(name+'.json')).relative_to(ROOT).as_posix(),
                   'sha256':sha(DATA/(name+'.json')), 'kills':len(selected[name])} for name in NAMES],
        'scope_offer_locators':404, 'combined':stats(rows), 'by_source':groups(rows,'creator'),
        'by_stage':groups(rows,'stage'), 'by_one_hit_wonder':groups(rows,'one_hit_wonder'),
        'by_run':groups(rows,'run_id'), 'by_mode':groups(rows,'mode'),
        'by_run_class':groups(rows,'run_class'), 'by_gems':groups(rows,'gems'),
        'prior_comparison':{'path':baseline_path.relative_to(ROOT).as_posix(),
                            'sha256':sha(baseline_path), 'all_stages':stats(baseline),
                            'stages_2_to_4':stats(stage24)},
        'pending_gap_reviews':len(pending),
        'confidence_method':'Nominal 95% Wilson binomial intervals. Assumes a common independent rate; does not include selection, clustering, missing footage, or visual classification errors.',
        'simulator_modified':False}
    verification = {'passed':True, 'unique_kills':len(rows), 'unique_offer_ids':len(rows),
        'referenced_image_hashes_rechecked':len(hashes), 'duplicate_or_overlapping_kills':0,
        'overlap_with_baseline':0, 'key_arithmetic_and_modifier_exclusions_passed':True,
        'all_frozen_scopes_have_dispositions':True, 'source_sha256':summary['inputs'],
        'manual_gap_reviews':pending,
        'note':'Prior source reviewers also validated additional extraction frames; this count rechecks images actually referenced by the final four ledgers.'}
    for name,value in [('summary.json',summary),('verification.json',verification),('observations.json',rows)]:
        (DATA/name).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    with (DATA/'observations.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
        for row in rows:
            writer.writerow({**row,'gems':';'.join(row['gems'])})
    print(json.dumps({'combined':summary['combined'],'by_stage':summary['by_stage'],
                      'by_one_hit_wonder':summary['by_one_hit_wonder'],
                      'images_rechecked':len(hashes),'pending_gap_reviews':pending}))


if __name__ == '__main__':
    main()
