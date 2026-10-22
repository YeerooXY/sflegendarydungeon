"""Check review coverage and retain evidence for the reported room-16 error."""
import json
from pathlib import Path

from sfld_vod.media import sha256, write_json
from recover_vod_choice_gaps import missing_spans

ROOT = Path(__file__).resolve().parents[1]


def main():
    root = ROOT/'out/vod-older-2026-10-12'
    original = json.loads((root/'monster-mystery-candidates.json').read_text())
    expanded = json.loads((root/'quick-review-catalog.json').read_text())
    counter = json.loads((root/'room-sequence-v1.0.0/catalog.json').read_text())
    bundle = json.loads((root/'quick-predictions.json').read_text())
    wanted = {s['id'] for s in missing_spans(counter, original['candidates'])}
    actual = {r['recovery']['span_id'] for r in expanded['candidates'] if r.get('recovery')}
    assert wanted == actual, 'Missing counter sections in published review'
    original_ids = {r['id'] for r in original['candidates']}
    rows = expanded['candidates']; ids = [r['id'] for r in rows]
    assert len(ids) == len(set(ids)), 'Duplicate review IDs'
    assert original_ids <= set(ids), 'Original views were removed'
    assert set(ids) <= set(bundle['predictions']), 'Images lack predictions'
    case = []
    expected = {18:['locked','monster'], 19:['cursed','golden'], 20:['sacrifice','mystery']}
    for row in rows:
        if row['source_id'] != 'youtube-3Wj28RH0P8w' or not 85 < row['best_time'] < 106: continue
        room = row.get('frame_room_sequence', {}).get('room')
        if room not in expected or not row.get('recovery'): continue
        prediction = bundle['predictions'][row['id']]
        assert [d['kind'] for d in prediction['doors']] == expected[room], 'Reported gap has wrong door suggestions'
        case.append({'room':room,'id':row['id'],'image':prediction['image'],
                     'source_seconds':row['best_time'],'doors':expected[room],
                     'review':'Directly inspected in native source footage; room 20 is sacrificial, not wall.'})
    assert {r['room'] for r in case} == {18,19,20}
    ident = 'youtube-3Wj28RH0P8w:mu:6f4f10a1cbd81167'
    corrected = bundle['predictions'][ident]
    assert corrected['doors'][1]['kind'] == 'monster'
    assert corrected['doors'][1]['trap'] and corrected['doors'][1]['trap_visual'] == 'cleaver'
    report = {'schema':1, 'original_views':len(original_ids), 'published_views':len(ids),
              'added_counter_sections':len(actual), 'missing_readable_counter_sections':len(wanted-actual),
              'source_count':len(expanded['sources']), 'sources_with_counter_recovery':len({r['source_id'] for r in rows if r.get('recovery')}),
              'reported_monster':{'id':ident,'image':corrected['image'],'doors':corrected['doors']},
              'reported_gap':sorted(case,key=lambda r:r['room']),
              'inputs':{name:sha256(root/name) for name in ('monster-mystery-candidates.json','quick-review-catalog.json','quick-predictions.json')},
              'scope':'Image review coverage for ten older sources only. Counter sections are not unique choices. Obscured counters, transitions, retries and unreviewed scenes remain unresolved. No simulator odds or canonical probability records changed.'}
    write_json(ROOT/'data/vods/quick-review-coverage-fix-2026-10-12.json', report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('reported_monster','reported_gap','inputs')},indent=2))


if __name__ == '__main__': main()
