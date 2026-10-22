"""Guard evidence denominators against recounting and uncertain outcome labels."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from summarize_vod_cumulative_2026_10_12 import (
    build, content_summary, normalize_content, ordinary_new, stat, unique,
)


class CumulativeEvidenceTests(unittest.TestCase):
    def test_unknown_content_is_not_an_observed_category_or_negative(self):
        rows = [{'source_id': 'one', 'content': normalize_content(v)}
                for v in ['monster', 'empty', 'unknown', None]]
        result = content_summary(rows, clustered=False)
        self.assertEqual(result['known_contents'], 2)
        self.assertEqual(result['monster']['n'], 2)
        self.assertEqual(result['monster']['unknown'], 2)
        self.assertEqual(result['monster']['missingness_bounds'], [.25, .75])
        self.assertNotIn('unknown', result['counts'])

    def test_overlapping_publications_cannot_recount_the_same_observation(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            unique([{'id': 'source:1'}, {'id': 'source:1'}], 'id')

    def test_expanded_offer_denominator_still_excludes_ambiguous_opening_and_duplicates(self):
        base = {'status': 'decision', 'phase': 'ordinary', 'room': None, 'stage': 3,
                'doors': [{'kind': 'locked'}, {'kind': 'mystery'}]}
        self.assertTrue(ordinary_new(base))
        self.assertFalse(ordinary_new(dict(base, stage=1)))
        self.assertFalse(ordinary_new(dict(base, room=5)))
        self.assertFalse(ordinary_new(dict(base, room=75)))
        self.assertFalse(ordinary_new(dict(base, sequence_dedup_pending=True)))

    def test_zero_observations_do_not_imply_zero_possible_probability(self):
        result = stat([{'blessing': False}] * 3, 'blessing', clustered=False)
        self.assertGreater(result['wilson_95'][1], .5)

    def test_canonical_cumulative_counts_and_cohort_separation(self):
        result = build()
        self.assertEqual(result['coverage']['current_ordinary_offers'], 6538)
        self.assertEqual(result['historical_separate']['offers']['n'], 728)
        self.assertEqual(result['walls']['appearances'], 354)
        self.assertEqual(result['walls']['other_choice']['monster']['k'], 61)
        m = result['mystery']['wall']['route_verified_without_outcome_exclusion']['monster']
        self.assertEqual((m['k'], m['n'], m['unknown']), (142, 163, 2))
        m = result['mystery']['monster']
        self.assertEqual(m['all_attributed']['known_contents'], 89)
        self.assertEqual(m['route_verified_without_outcome_exclusion']['known_contents'], 3)
        self.assertEqual(result['spinner']['contents']['known_contents'], 72)
        self.assertEqual(result['spinner']['selected_spins'], 73)
        self.assertEqual(result['containers']['without_hero']['without_greed']['rewards']['blessing']['n'], 569)
        self.assertEqual(result['barrels']['unmodified']['blessing']['n'], 54)
        trap = result['traps']['per_individual_monster_or_mystery_option']
        self.assertEqual((trap['k'], trap['n'], trap['unknown']), (54, 242, 8))
        self.assertEqual(result['traps']['per_individual_offered_position_including_walls']['n'], 427)


if __name__ == '__main__':
    unittest.main()
