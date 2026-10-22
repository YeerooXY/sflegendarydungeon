"""Census coverage, independent loot flags and stone strata remain auditable."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.container_census_report import (COHORT, blessing, combine, eligible,
                                             matched_comparison, progress_stratum, summarize, wilson)
from sfld_vod.container_reanalysis import FLAGS, public_evidence


def fixture():
    row = {'observation_id': 'visit', 'source_id': 'source', 'initial_style': 'wooden_box',
           'reward_review_status': 'unreviewed', 'reward_complete': False, 'interaction': 'unknown',
           'reward_flags': {k: None for k in FLAGS}, 'rewards': [], 'evidence': [], 'notes': [],
           'context': {'gems': [], 'stone_status': 'verified', 'stage': 2, 'run_id': 'one'}}
    manifest = {'rows': [{'observation_id': 'visit', 'source_id': 'source', 'index': 0}]}
    review = {'observation_id': 'visit', 'source_id': 'source', 'initial_style': 'wooden_box',
              'complete': True, 'interaction': 'opened', 'rewards': [{'kind': 'gold'}, {'kind': 'persistent_blessing'}],
              'reward_flags': {k: k in {'gold', 'persistent_blessing'} for k in FLAGS},
              'native_cohort': {'name': COHORT, 'index': 0},
              'evidence': [{'sha256': 'a' * 64, 'video_sha256': 'b' * 64, 'native_height': 720,
                            'source_seconds': 10, 'decoded_source_seconds': 9.98}]}
    return row, manifest, review


class CensusTests(unittest.TestCase):
    def test_incomplete_census_cannot_silently_look_finished(self):
        row, manifest, _ = fixture()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            combine([row], manifest, [])
        self.assertEqual(combine([row], manifest, [], True), [row])

    def test_duplicate_or_already_reviewed_visit_is_rejected(self):
        row, manifest, review = fixture()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            combine([row], manifest, [review, review])
        row['reward_review_status'] = 'verified_native_sequence'
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            combine([row], manifest, [review])

    def test_rewards_overlap_and_baseline_is_immutable(self):
        row, manifest, review = fixture()
        result = combine([row], manifest, [review])
        self.assertEqual(row['interaction'], 'unknown')
        summary = summarize(result)
        self.assertEqual(summary['complete_ordinary_openings'], 1)
        self.assertEqual(summary['rewards']['gold']['positive'], 1)
        self.assertEqual(summary['rewards']['any_blessing']['positive'], 1)
        self.assertEqual(summary['joint_blessing_item_gold_mask']['5'], 1)
        self.assertEqual(result[0]['evidence'][-1]['decoded_source_seconds'], 9.98)

    def test_unopened_visit_is_not_a_reward_failure(self):
        row, manifest, review = fixture()
        review.update(interaction='skipped', rewards=[], reward_flags={k: None for k in FLAGS})
        s = summarize(combine([row], manifest, [review]))
        self.assertEqual(s['visits'], 1)
        self.assertEqual(s['rewards']['any_blessing']['denominator'], 0)

    def test_unknown_healing_is_not_a_negative_blessing(self):
        row, _, _ = fixture()
        row['reward_flags']['persistent_blessing'] = False
        self.assertIsNone(blessing(row))
        row['reward_flags']['instant_healing'] = True
        self.assertIs(blessing(row), True)

    def test_partial_review_cannot_assert_absences(self):
        row, manifest, review = fixture()
        review['complete'] = False
        with self.assertRaisesRegex(ValueError, 'negative'):
            combine([row], manifest, [review])

    def test_native_video_provenance_is_required(self):
        row, manifest, review = fixture()
        review['evidence'][0]['native_height'] = 360
        with self.assertRaisesRegex(ValueError, 'native'):
            combine([row], manifest, [review])

    def test_canonical_reference_does_not_substitute_for_native_sequence(self):
        row, manifest, review = fixture()
        reference = {'role': 'canonical_offer_identity_reference', 'sha256': 'c' * 64}
        review['evidence'].append(reference)
        combine([row], manifest, [review])
        review['evidence'] = [reference]
        with self.assertRaisesRegex(ValueError, 'native'):
            combine([row], manifest, [review])

    def test_stone_correction_preserves_original_and_uncertainty_excludes(self):
        row, manifest, review = fixture()
        review['stone_context_review'] = {'status': 'corrected', 'observed_gems': ['greed']}
        result = combine([row], manifest, [review])[0]
        self.assertEqual(result['context']['gems'], ['greed'])
        self.assertEqual(result['canonical_context_before_correction']['gems'], [])
        result['stone_context_review']['status'] = 'unknown'
        self.assertFalse(eligible(result))

    def test_differing_confirmed_gems_require_explicit_correction(self):
        row, manifest, review = fixture()
        review['stone_context_review'] = {'status': 'confirmed', 'observed_gems': ['greed']}
        with self.assertRaisesRegex(ValueError, 'correction'):
            combine([row], manifest, [review])

    def test_matching_requires_source_stage_and_style_overlap(self):
        row, manifest, review = fixture()
        base = combine([row], manifest, [review])[0]
        greed = copy.deepcopy(base)
        greed['context']['gems'] = ['greed']
        self.assertEqual(matched_comparison([base, greed])['matched_greed_openings'], 1)
        greed['context']['stage'] = 3
        self.assertEqual(matched_comparison([base, greed])['matched_greed_openings'], 0)

    def test_wilson_extremes_do_not_claim_certainty(self):
        self.assertGreater(wilson(0, 10)['wilson95'][1], 0)
        self.assertLess(wilson(10, 10)['wilson95'][0], 1)
        self.assertIsNone(wilson(0, 0)['rate'])

    def test_unknown_item_rarity_does_not_estimate_overall_epic_rate(self):
        row, manifest, review = fixture()
        result = combine([row], manifest, [review])[0]
        unknown = copy.deepcopy(result)
        unknown['reward_flags']['item'] = True
        unknown['reward_flags']['epic_item'] = None
        rarity = summarize([result, unknown])['rewards']['epic_item']
        self.assertIsNone(rarity['rate'])
        self.assertIsNone(rarity['wilson95'])
        self.assertEqual(rarity['identification_bounds'], [0, .5])

    def test_first_five_and_unknown_progress_stay_separate(self):
        row, _, _ = fixture()
        row['context'].update(stage=1, room=3, phase='ordinary')
        self.assertEqual(progress_stratum(row), 'known_rooms_1_to_5')
        row['context']['room'] = None
        self.assertEqual(progress_stratum(row), 'opening_status_unknown')
        row['context']['stage'] = 2
        self.assertEqual(progress_stratum(row), 'known_beyond_room_5')


if __name__ == '__main__':
    unittest.main()
