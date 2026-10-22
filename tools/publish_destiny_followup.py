"""Publish manually verified wheel rewards separately from entered-room contents."""
import collections
import hashlib
import json
from pathlib import Path
from publish_barrel_followup import wilson
from prepare_destiny_followup import REFINED

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vods/destiny-followup-2026-10-12'
# Original sample index -> reward, readable reward frame, room contents, content
# frame. Frame references below are from the original 4fps sequences. Every
# provisional selection was also followed at 12fps, including the three rejected
# candidates (9,14: hover only; 28: preceding chest's blessing).
OPENED = {
    5: ('curse', 6, 'monster', 11), 7: ('curse', 4, 'monster', 9),
    8: ('curse', 4, 'crate', 9), 10: ('lucky_coins', 4, 'empty', 9),
    12: ('curse', 31, 'empty', 37), 15: ('curse', 5, 'empty', 11),
    17: ('curse', 5, 'chest', 12), 18: ('curse', 5, 'monster', 11),
    20: ('blessing', 5, 'monster', 11), 23: ('blessing', 5, 'empty', 11),
    24: ('blessing', 5, 'monster', 11), 29: ('lucky_coins', 5, 'monster', 10),
}
SKIP_FRAMES = {0:4, 1:4, 2:5, 3:5, 4:4, 6:5, 9:9, 11:4, 13:5,
               14:4, 16:4, 19:4, 21:5, 22:5, 25:25, 26:4, 27:4,
               28:10, 30:3, 31:4, 32:3, 33:3, 34:3}


def run():
    manifest = json.loads((WORK / 'manifest.json').read_text())
    frames = json.loads((WORK / 'frames/index.json').read_text())
    refined = json.loads((WORK / 'refined/index.json').read_text())
    assert len(manifest['rows']) == len(frames) == 35
    assert set(OPENED) | set(SKIP_FRAMES) == set(range(35))
    assert not set(OPENED) & set(SKIP_FRAMES)
    rows = []
    for index, (r, f) in enumerate(zip(manifest['rows'], frames)):
        assert r['observation_id'] == f['observation_id'] and not f['missing_timestamps']
        opening = OPENED.get(index)
        reward, peak, contents, after = opening or (None, SKIP_FRAMES[index], None, SKIP_FRAMES[index])
        evidence = [dict(f['frames'][n], role=role) for n, role in
                    [(1, 'offered_pair'), (peak, 'wheel_reward' if opening else 'other_door_selected'),
                     (after, 'initial_contents' if opening else 'other_room_entry')]]
        notes = []
        if index in (9, 14): notes.append('Dense sequence shows hovering the wheel, then choosing the other door. Hover animation is not a spin outcome.')
        if index == 28: notes.append('Blue Key Moment card comes from the preceding cursed chest. Player chooses the mystery door; not a wheel reward.')
        if index in (8, 10, 24, 29): notes.append('Later container/monster rewards and later door spins are excluded.')
        dense = None
        if index in REFINED:
            dense = refined[REFINED.index(index)]
            assert dense['observation_id'] == r['observation_id'] and not dense['missing_timestamps']
        row = {'schema': 1, 'observation_id': r['observation_id'], 'source_id': r['source_id'],
               'source_url': f'https://www.twitch.tv/videos/{r["source_id"]}?t={int(f["source_start"])}s',
               'context': r['context'], 'offered_doors': r['offered_doors'],
               'interaction': 'opened' if opening else 'other_door_chosen',
               'wheel_reward': reward, 'wheel_reward_complete': bool(opening),
               'reward_amount': (10 if index == 10 else 30) if reward == 'lucky_coins' else None,
               'initial_contents': contents, 'effect_identity_and_strength_verified': False,
               'evidence': evidence, 'refinement_sequence': dense,
               'review_status': 'verified_native_sequence', 'sample_index': index,
               'cohort': 'outcome_blind_destiny_followup_2026_10_12', 'notes': notes}
        rows.append(row)
    assert len({r['observation_id'] for r in rows}) == len(rows)
    dest = ROOT / 'data/vods/destiny-followup-2026-10-12.jsonl'
    dest.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf8')
    opened = [r for r in rows if r['interaction'] == 'opened']
    counts = collections.Counter(r['wheel_reward'] for r in opened)
    source_counts = {}
    for source in sorted({r['source_id'] for r in rows}):
        rs = [r for r in rows if r['source_id'] == source]
        source_counts[source] = {'offers': len(rs), 'opened': sum(r['interaction'] == 'opened' for r in rs),
                                 'rewards': dict(collections.Counter(r['wheel_reward'] for r in rs if r['wheel_reward']))}
    report = {'schema': 1, 'offered': len(rows), 'opened': len(opened), 'other_door_chosen': len(rows)-len(opened),
              'wheel_rewards': {k: {'count': counts[k], 'fraction': counts[k]/len(opened),
                                    'descriptive_wilson95': wilson(counts[k], len(opened))}
                                for k in ('curse','blessing','lucky_coins','gold','hourglasses','mushrooms')},
              'initial_contents': dict(collections.Counter(r['initial_contents'] for r in opened)),
              'per_source': source_counts,
              'dataset_sha256': hashlib.sha256(dest.read_bytes()).hexdigest(),
              'manifest_sha256': hashlib.sha256((WORK/'manifest.json').read_bytes()).hexdigest(),
              'candidates_in_population': sum(manifest['pool_sizes'].values()),
              'model_changed': False,
              'limitations': ['Only 12 actual spins; no exact odds or absent-outcome claims.',
                              'Equal numbers of offers per source, followed by player-selected openings; not a random live-game sample.',
                              'Stone contexts, opening/ordinary rooms and unresolved event-run classes are preserved, not claimed interchangeable.',
                              'Intervals describe binomial uncertainty only; source/run clustering and recognition selection are omitted.',
                              'Gorgoss targeted opening spin is kept separately from this prespecified sample.']}
    (ROOT/'docs/destiny-followup-2026-10-12.json').write_text(json.dumps(report, indent=2)+'\n')
    lines = ['# Destiny Door follow-up, 2026-10-12', '',
             'Manually reviewed 35 offers selected before seeing their outcomes: five per source across seven Twitch VODs, from 303 recognized offers. Twelve doors were entered; the other door was chosen in 23 cases. Every provisional spin was rechecked at native resolution at 12 frames/second. Each visit is counted once.', '',
             '## Wheel rewards', '', '| Outcome | Count / 12 spins | Observed fraction | Descriptive 95% interval |', '|---|---:|---:|---:|']
    for kind, v in report['wheel_rewards'].items():
        lo,hi=v['descriptive_wilson95']
        lines.append(f'| {kind} | {v["count"]} | {v["fraction"]:.1%} | {lo:.1%}-{hi:.1%} |')
    lines += ['', 'This small sample suggests testing a higher curse probability than the existing one-sixth placeholder. It does not establish a 58.3% live rate. No observed gold/hourglasses/mushrooms is not evidence that those outcomes are impossible. The current default weights remain explicitly provisional.', '',
              '## Inside the entered doors', '',
              'Six monsters, four empty rooms, one wooden crate and one chest. This is a separate outcome from the wheel reward. Container rewards and combat drops after entry are excluded from the wheel counts.', '',
              '## Attribution checks', '',
              '- A wheel can animate while hovered; two initially ambiguous cases visibly select the other door afterward.',
              '- A blessing from the previous cursed chest floats over a subsequent Destiny/mystery pair. That case is a mystery entry, not a wheel blessing.',
              '- The 10- and 30-coin rewards are readable. Effect identities/strengths have not been counted from tiny icons.',
              '- The new Gorgoss opening spin is an additional targeted example, archived separately in the opening correction evidence. It is excluded from this sampling denominator.', '',
              '## Source breakdown', '', '| Source | Offers | Openings | Wheel outcomes |', '|---|---:|---:|---|']
    for source,v in source_counts.items():
        lines.append(f'| [Twitch {source}](https://www.twitch.tv/videos/{source}) | {v["offers"]} | {v["opened"]} | {v["rewards"]} |')
    lines += ['', 'The raw records retain room/stage, visible stones, unresolved run labels, timestamps and frame/video hashes. These are records of streamer choices; neither the opening fraction nor the contents mix can be generalized without controlling for those choices and contexts. Wilson intervals omit clustering.', '',
              'Data: `data/vods/destiny-followup-2026-10-12.jsonl`. Summary: `docs/destiny-followup-2026-10-12.json`.',
              'Reproduce with `python tools/prepare_destiny_followup.py`, repeat with `--refine`, then `python tools/publish_destiny_followup.py`. Native video caches and layout files are required; footage is not redistributed.']
    (ROOT/'docs/DESTINY_FOLLOWUP_2026_10_12.md').write_text('\n'.join(lines)+'\n', encoding='utf8')
    print(json.dumps({k:report[k] for k in ('offered','opened','wheel_rewards','initial_contents')}, indent=2))


if __name__ == '__main__': run()
