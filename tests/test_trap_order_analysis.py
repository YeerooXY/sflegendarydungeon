"""Regression checks for denominators and identifiability, not game rule assertions."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from analyze_trap_order_2026_10_12 import (
    any_trap, core_flags, deduplicate, exclusion, fit_presence, gem_label, probabilities, roles, stat,
)


def row(**kw):
    return dict({'id': 'r', 'phase': 'ordinary', 'room': 6, 'stage': 1,
                 'gems': [], 'kinds': ['mystery', 'locked'], 'sides': [False, False]}, **kw)


class TrapOrderAnalysisTests(unittest.TestCase):
    def test_opening_and_scripted_rooms_never_dilute_estimate(self):
        for room in (1, 2, 3, 4, 5, 25, 50, 75, 100):
            self.assertIsNotNone(exclusion(row(room=room)))
        for phase in ('shop', 'trial', 'gems', 'boss'):
            self.assertIsNotNone(exclusion(row(phase=phase)))
        self.assertIsNone(exclusion(row(room=6)))
        self.assertIsNone(exclusion(row(room=24)))
        self.assertIsNone(exclusion(row(room=26, stage=2)))

    def test_unknown_opening_boundary_excluded_not_assumed_ordinary(self):
        self.assertIsNotNone(exclusion(row(room=None, stage=1)))
        self.assertIsNotNone(exclusion(row(room=None, stage=None)))
        self.assertIsNone(exclusion(row(room=None, stage=3)))

    def test_physical_side_does_not_dilute_or_obscure_known_eligible_side(self):
        r = row(sides=[False, None])
        self.assertEqual(core_flags(r), [False])
        self.assertIs(any_trap(core_flags(r)), False)
        self.assertEqual(stat(core_flags(r))['n'], 1)

    def test_unreadable_eligible_side_is_not_absence(self):
        self.assertIsNone(any_trap([False, None]))
        self.assertIs(any_trap([True, None]), True)
        self.assertEqual(stat([False, None, True])['n'], 2)
        self.assertEqual(stat([False, None, True])['unknown'], 1)
        self.assertIsNone(any_trap([]))

    def test_stop_first_and_independent_have_same_any_not_same_joint(self):
        for p in (.01, .25, .5, .9):
            s = probabilities(p)
            self.assertAlmostEqual(s['p_any_two_eligible'], 1-(1-p)**2)
            self.assertAlmostEqual(s['p_first_role_trap']+s['p_second_role_trap']+s['p_no_trap_two'], 1)
            self.assertEqual(s['p_double_trap_stop_first'], 0)
            self.assertGreater(s['p_double_trap_independent'], 0)

    def test_conditional_risk_is_probabilistic_and_depends_on_role_priority(self):
        s = probabilities(.25, .9)
        self.assertAlmostEqual(s['first_role_given_trap'], 4/7)
        self.assertAlmostEqual(s['monster_if_choose_untrapped_in_trapped_pair'], .9*3/7)
        self.assertAlmostEqual(s['monster_if_choose_trapped'], .9*4/7)
        self.assertAlmostEqual(s['random_priority_monster_either_side'], .45)
        self.assertIsNone(probabilities(0)['first_role_given_trap'])

    def test_role_count_requires_both_sides_resolved_and_exactly_one_trap(self):
        rows = [row(id='a', kinds=['monster','mystery'], sides=[True,False]),
                row(id='b', kinds=['mystery','monster'], sides=[True,False]),
                row(id='c', kinds=['monster','mystery'], sides=[True,None]),
                row(id='d', kinds=['monster','mystery'], sides=[False,False])]
        s = roles(rows)
        self.assertEqual((s['k'],s['n'],s['unknown_sides']),(1,2,1))
        self.assertEqual((s['assuming_at_most_one_trap']['k'],s['assuming_at_most_one_trap']['n']),(2,3))

    def test_repeated_review_never_becomes_new_room(self):
        self.assertEqual(len(deduplicate([row(),row()])),1)
        with self.assertRaises(ValueError):
            deduplicate([row(),row(sides=[True,False])])

    def test_same_visit_at_different_timestamps_is_one_observation(self):
        rows = [row(id='first'),row(id='return')]
        result = deduplicate(rows,{'return':'first'})
        self.assertEqual([r['id'] for r in result],['first'])
        with self.assertRaises(ValueError):
            deduplicate([row(id='first'),row(id='return',sides=[True,False])],{'return':'first'})

    def test_same_id_with_disagreeing_room_type_is_not_silently_merged(self):
        with self.assertRaises(ValueError):
            deduplicate([row(),row(kinds=['monster','mystery'])])

    def test_pair_identity_correction_excludes_false_monster_role(self):
        s=roles([row(kinds=['mystery','mystery'],sides=[False,True])])
        self.assertEqual((s['all_offers'],s['n']),(0,0))

    def test_unverified_stones_are_not_pooled_with_verified_empty_slots(self):
        self.assertNotEqual(gem_label(row(stones_known=True)),gem_label(row(stones_known=False)))

    def test_presence_model_comparison_separates_known_generating_rates(self):
        single=[row(id=f's{i}',sides=[i<40,False]) for i in range(200)]
        for hits,sequential_better in [(40,False),(72,True)]:
            two=[row(id=f't{i}',kinds=['mystery','mystery'],sides=[i<hits,False]) for i in range(200)]
            seq=fit_presence(single+two,True)
            coin=fit_presence(single+two,False)
            self.assertEqual(seq['aic']<coin['aic'],sequential_better)


if __name__ == '__main__':
    unittest.main()
