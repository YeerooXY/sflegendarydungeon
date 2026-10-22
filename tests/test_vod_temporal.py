"""Temporal observation contracts: repeats, stages, stones and selected outcomes."""
from copy import deepcopy
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from sfld_vod.temporal import observation_counts, build_timeline
from sfld_vod.media import write_json, sha256
from sfld_vod.hud import parse_counter


def observation(ident='one', room=30):
    return {'id': ident, 'source_id': 'source', 'status': 'decision', 'generation': 'ordinary',
            'doors': [{'kind': 'mystery', 'trap': False}, {'kind': 'monster', 'trap': False}],
            'context': {'run_id': 'account-a-run-1', 'room': room, 'stage': 2, 'phase': 'ordinary',
                        'gems': [], 'stone_status': 'verified', 'run_class': 'first'},
            'verified_fields': ['identity', 'doors', 'traps', 'stones', 'chosen_side', 'initial_content'],
            'chosen_side': 0, 'outcome': {'initial_content': 'corpse', 'awakened': True,
                                         'evidence': [{'image': 'before-awakening.jpg', 'source_seconds': 10}]}}


class ObservationTests(unittest.TestCase):
    def test_review_coverage_does_not_promote_suggested_choices_or_traps(self):
        known = observation()
        unknown = observation('unknown', room=31)
        unknown['verified_fields'].remove('chosen_side')
        unknown['verified_fields'].remove('traps')
        partial = observation('partial', room=32)
        partial['doors'][1]['trap'] = None
        partial['doors'][0]['trap'] = True
        result = observation_counts([known, unknown, partial])
        self.assertEqual(result['coverage']['decision_choice_verified'], 2)
        self.assertEqual(result['coverage']['decision_choice_unverified'], 1)
        self.assertEqual(result['coverage']['decision_traps_complete'], 1)
        self.assertEqual(result['coverage']['decision_traps_partial'], 1)
        self.assertEqual(result['coverage']['decision_traps_unverified'], 1)
        self.assertEqual(result['door_groups'][0]['trap_status_unknown'], 1)
        self.assertEqual(result['door_groups'][0]['pair_has_trap']['successes'], 1)

    def test_held_screen_is_one_opportunity(self):
        first = observation()
        repeat = observation('repeat')
        with self.assertRaisesRegex(ValueError, 'Repeated'):
            observation_counts([first, repeat])
        repeat['status'] = 'duplicate'; repeat['duplicate_of'] = first['id']
        result = observation_counts([first, repeat])
        self.assertEqual(result['coverage']['eligible_decisions'], 1)
        self.assertEqual(result['coverage']['duplicate'], 1)

    def test_arena_exit_and_ordinary_same_counter_are_distinct(self):
        ordinary = observation(room=55)
        arena = observation('arena', room=55)
        arena['context']['phase'] = 'arena-exit'; arena['generation'] = 'scripted'
        result = observation_counts([ordinary, arena])
        self.assertEqual(result['coverage']['eligible_decisions'], 1)
        self.assertEqual(result['coverage']['nonordinary_or_unclassified'], 1)

    def test_same_pair_next_room_is_a_new_opportunity(self):
        result = observation_counts([observation(), observation('two', room=31)])
        self.assertEqual(result['door_groups'][0]['decisions'], 2)

    def test_new_pair_after_death_can_share_counter_with_separate_attempt(self):
        row = observation('retry'); row['context']['offer_attempt'] = 2
        row['doors'][1]['kind'] = 'mystery'
        result = observation_counts([observation(), row])
        self.assertEqual(result['coverage']['eligible_decisions'], 2)

    def test_both_mysteries_have_identifiable_content_without_identifiable_side(self):
        row = observation(); row['doors'][1]['kind'] = 'mystery'; row['chosen_side'] = None
        row['verified_fields'].remove('chosen_side'); row['verified_fields'].append('entered_room')
        result = observation_counts([row])
        self.assertEqual(result['coverage']['eligible_mystery_outcomes'], 1)

    def test_unreadable_counter_uses_explicit_visit_without_guessing(self):
        first = observation(room=None)
        result = observation_counts([first])
        self.assertEqual(result['coverage']['decision_identity_unverified'], 1)
        first['context']['visit_id'] = 'reviewed-visit-25'
        self.assertEqual(observation_counts([first])['coverage']['eligible_decisions'], 1)

    def test_unknown_incomplete_or_unreviewed_stones_never_become_baseline(self):
        for modification in ('unknown', 'missing', 'suggestion'):
            row = observation()
            if modification == 'unknown': row['context']['gems'] = None
            if modification == 'missing': row['verified_fields'].remove('stones')
            if modification == 'suggestion': row['context']['stone_status'] = 'suggestion'
            result = observation_counts([row])
            self.assertEqual(result['coverage']['stone_context_unknown'], 1)
            self.assertEqual(result['door_groups'], [])

    def test_sources_stones_stage_and_first_later_stay_separate(self):
        rows = [observation(str(i), 30+i) for i in range(5)]
        rows[1]['source_id'] = 'historical-2024'
        rows[2]['context']['gems'] = ['greed']
        rows[3]['context']['stage'] = 3
        rows[4]['context']['run_class'] = 'later'
        self.assertEqual(len(observation_counts(rows)['mystery_groups']), 5)

    def test_hover_unknown_side_or_unseen_interior_is_not_mystery_outcome(self):
        for field in ('chosen_side', 'initial_content'):
            row = observation(); row['verified_fields'].remove(field)
            self.assertEqual(observation_counts([row])['mystery_groups'], [])
        row = observation(); row['outcome']['evidence'] = []
        self.assertEqual(observation_counts([row])['mystery_groups'], [])
        row = observation(); row['chosen_side'] = 1
        result = observation_counts([row])
        self.assertEqual(result['coverage']['mystery_not_selected'], 1)

    def test_corpse_awakened_is_not_a_monster_on_entry(self):
        group = observation_counts([observation()])['mystery_groups'][0]
        self.assertEqual(group['contents']['corpse']['successes'], 1)
        self.assertNotIn('monster', group['contents'])
        self.assertEqual(group['context']['other_choice'], 'monster')

    def test_no_assumed_legal_pair_filter(self):
        row = observation(); row['doors'][1]['kind'] = 'mystery'
        result = observation_counts([row])
        self.assertEqual(result['door_groups'][0]['pairs'][0]['choices'], ['mystery', 'mystery'])

    def test_curse_trader_content_does_not_turn_golden_offer_into_compulsory_shop(self):
        row = observation()
        row['doors'] = [{'kind': 'golden', 'trap': False}, {'kind': 'wall', 'trap': False}]
        row['outcome']['initial_content'] = 'curse_shop'
        result = observation_counts([row])
        self.assertEqual(result['coverage']['eligible_decisions'], 1)
        self.assertEqual(result['door_groups'][0]['pairs'][0]['choices'], ['golden', 'wall'])
        self.assertEqual(result['mystery_groups'], [])

    def test_scripted_or_unknown_opening_is_not_random_room_odds(self):
        shop=observation(); shop['doors'][0]['kind']='shop'
        opening=observation('opening',room=3); opening['context']['stage']=1
        unreadable=observation('unreadable',room=None)
        unreadable['context'].update(stage=1,visit_id='reviewed-visit')
        result=observation_counts([shop,opening,unreadable])
        self.assertEqual(result['coverage']['nonordinary_or_unclassified'],3)
        self.assertEqual(result['door_groups'],[])

    def test_counter_requires_denominator_and_does_not_infer_missing_digit(self):
        self.assertEqual(parse_counter(' 37 / 100 '), 37)
        self.assertEqual(parse_counter('1/100'), 1)
        for value in ('37100', '7/10', '101/100', 'room 7', '?7/100'):
            self.assertIsNone(parse_counter(value))

    def test_timeline_evidence_is_bound_to_local_hash_and_safe_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'source').mkdir(); (root/'scan').mkdir()
            write_json(root/'source/source.json', {'id': 'source', 'url': 'https://www.twitch.tv/videos/1',
                                                 'uploader': '</script><script>bad</script>'})
            image = root/'scan/frame.jpg'; image.write_bytes(b'fixture')
            record = {'id': 'candidate', 'source_id': 'source', 'source_seconds': 10, 'image': 'frame.jpg',
                      'image_sha256': sha256(image)}
            path = root/'scan/candidates.jsonl'; path.write_text(json.dumps(record)+'\n')
            result = build_timeline(root, 'source', candidate_files=[path])
            self.assertEqual(result['coverage']['candidates'], 1)
            self.assertNotIn('</script><script>bad', (root/'source-review.html').read_text(encoding='utf-8'))
            image.write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError, 'changed'):
                build_timeline(root, 'source', candidate_files=[path])

    def test_manual_recovery_and_annotation_outcome_evidence_reach_gallery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'source').mkdir()
            write_json(root/'source/source.json', {'id':'source','url':'https://www.twitch.tv/videos/1'})
            for name in ('offer.jpg','entry.jpg'):
                (root/name).write_bytes(name.encode())
            row=observation(); row.update(candidate_id=None, source_seconds=20,
                evidence=[{'image':'offer.jpg','source_seconds':20}])
            row['outcome']['evidence']=[{'image':'entry.jpg','source_seconds':21,
                                        'sha256':sha256(root/'entry.jpg')}]
            path=root/'observations.jsonl'; path.write_text(json.dumps(row)+'\n')
            result=build_timeline(root,'source',observations=path)
            candidate=result['candidates'][0]
            self.assertEqual(candidate['kind'],'manually_recovered')
            self.assertEqual(result['observations'][0]['candidate_id'],candidate['id'])
            self.assertEqual({x['image'] for x in candidate['evidence']},{'offer.jpg','entry.jpg'})
            same=build_timeline(root,'source',observations=path)
            self.assertEqual(result['revision'],same['revision'])
            row['note']='Corrected after native review'; path.write_text(json.dumps(row)+'\n')
            changed=build_timeline(root,'source',observations=path)
            self.assertNotEqual(result['revision'],changed['revision'])
            # The same proof must be checked when an annotation matches a detected candidate.
            detected=root/'detected.jsonl'
            detected.write_text(json.dumps({'id':'detected','source_seconds':20,'image':'offer.jpg',
                                            'image_sha256':sha256(root/'offer.jpg')})+'\n')
            row['candidate_id']='detected'; path.write_text(json.dumps(row)+'\n')
            result=build_timeline(root,'source',candidate_files=[detected],observations=path)
            self.assertEqual(len(result['candidates']),1)
            self.assertEqual(len(result['candidates'][0]['evidence']),2)
            (root/'entry.jpg').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'Reviewed evidence changed'):
                build_timeline(root,'source',candidate_files=[detected],observations=path)


if __name__ == '__main__':
    unittest.main()
