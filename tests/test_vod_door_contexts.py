"""Door-family denominators and attribution must preserve uncertainty."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.door_contexts import analyze, separated_cohorts


def row(ident='one', kinds=('mystery', 'wall'), room=10, content='monster', side=None, gems=()):
    fields = ['identity', 'doors', 'stones']
    value = {'id': ident, 'source_id': 'source', 'status': 'decision', 'generation': 'ordinary',
             'context': {'run_id': 'run', 'room': room, 'stage': 1, 'phase': 'ordinary',
                         'run_class': 'later', 'gems': list(gems), 'stone_status': 'verified',
                         'version': 'v', 'theme': 'theme', 'mode': 'normal'},
             'doors': [{'kind': name, 'trap': None} for name in kinds],
             'chosen_side': side, 'verified_fields': fields}
    if content is not None:
        value['outcome'] = {'initial_content': content, 'evidence': [{'sha256': 'proof'}]}
        fields += ['initial_content', 'entered_room']
    if side is not None:
        fields.append('chosen_side')
    return value


class DoorContextCounts(unittest.TestCase):
    def test_whole_ledger_identity_dedup_is_enforced(self):
        a = row()
        with self.assertRaises(ValueError):
            analyze([a, deepcopy(a)])
        b = row('different_id')
        with self.assertRaises(ValueError):
            analyze([a, b])
        b['status'] = 'duplicate'
        self.assertEqual(analyze([a, b])['overall_descriptive']['eligible_offers'], 1)

    def test_two_physical_slots_are_two_slots_but_one_offer(self):
        r = analyze([row(kinds=('locked', 'locked'), content=None)])['baseline_no_stones_descriptive']
        self.assertEqual(r['physical_door_slots'], 2)
        self.assertEqual(r['offers_with_two_physical_doors'], 1)
        counts = r['physical_style_counts']['locked']
        self.assertEqual(counts['style_slots'], 2)
        self.assertEqual(counts['offers_containing_style'], 1)
        self.assertEqual(counts['share_of_physical_slots'], 1)
        self.assertEqual(counts['share_of_all_offers'], 1)

    def test_hover_and_later_content_do_not_establish_mystery_selection(self):
        a = row(kinds=('mystery', 'monster'), side=0)
        a['verified_fields'].remove('chosen_side')
        a['chosen_side_status'] = 'inferred_from_pointer_and_adjacent_scene'
        stats = analyze([a])['overall_descriptive']['mystery_by_exact_alternative']['monster']
        self.assertEqual(stats['verified_selected_mystery_entries'], 0)
        self.assertEqual(stats['unknown_selection'], 1)
        self.assertEqual(stats['unlinked_following_contents'], {'monster': 1})

    def test_double_mystery_requires_content_but_not_side(self):
        a = row(kinds=('mystery', 'mystery'), content='chest')
        b = row('two', kinds=('mystery', 'mystery'), room=11, content=None)
        stats = analyze([a, b])['overall_descriptive']['mystery_by_exact_alternative']['mystery']
        self.assertEqual(stats['verified_selected_mystery_entries'], 1)
        self.assertEqual(stats['initial_contents'], {'chest': 1})
        self.assertEqual(stats['unknown_selection'], 1)

    def test_resource_verification_is_distinct_from_timing_hypotheses(self):
        verified = row(kinds=('mystery', 'locked'), side=0)
        verified['chosen_side_status'] = 'verified_by_resource_constraint'
        conditional = row('conditional', kinds=('mystery', 'locked'), room=11, side=0)
        conditional['verified_fields'].remove('chosen_side')
        conditional['chosen_side_status'] = 'timing_conditional_resource_hypothesis'
        result = analyze([verified, conditional])
        stats = result['overall_descriptive']['mystery_by_exact_alternative']['locked']
        self.assertEqual(stats['verified_selected_mystery_entries'], 1)
        self.assertEqual(stats['selection_bases'], {'verified_resource_constraint': 1})
        self.assertEqual(stats['unknown_selection'], 1)
        self.assertEqual(result['coverage']['decision_choice_verified_resource_constraint'], 1)

    def test_literal_wall_is_separate_from_physical_family(self):
        a = row(side=0)
        b = row('two', kinds=('mystery', 'locked'), room=11, content='barrel')
        c = row('three', kinds=('mystery', 'cursed'), room=12, content=None, side=0)
        d = row('four', kinds=('mystery', 'locked'), room=13, content='chest', side=1)
        report = analyze([a, b, c, d])['overall_descriptive']
        wall = report['mystery_by_exact_alternative']['wall']
        physical = report['mystery_by_alternative_family']['door']
        self.assertEqual(wall['offered_pairs'], 1)
        self.assertEqual(physical['offered_pairs'], 4)
        self.assertEqual(physical['verified_selected_mystery_entries'], 1)
        self.assertEqual(physical['unknown_selection'], 1)
        self.assertEqual(physical['selected_mystery_without_known_content'], 1)
        self.assertEqual(physical['selected_other'], 1)
        self.assertEqual(physical['unlinked_following_entries'], 1)
        self.assertEqual(report['family_pair_counts'], {'door/mystery': 4})

    def test_stone_gate_anomalies_are_retained_with_denominators(self):
        a = row(kinds=('mystery', 'unlocked'), gems=('spying',))
        b = row('anomaly', kinds=('mystery', 'unlocked'), room=11)
        c = row('ordinary', kinds=('mystery', 'locked'), room=12, gems=('spying',))
        result = analyze([a, b, c])
        gate = result['overall_descriptive']['stone_gate_diagnostics']['unlocked']
        self.assertEqual(gate['gate_present']['eligible_offers'], 2)
        self.assertEqual(gate['gate_present']['physical_slots'], 2)
        self.assertEqual(gate['gate_present']['style_slots'], 1)
        self.assertEqual(gate['gate_absent_anomaly_ids'], ['anomaly'])
        self.assertEqual(result['overall_descriptive']['physical_style_inventory']['unlocked']['style_slots'], 2)
        self.assertEqual(result['baseline_no_stones_descriptive']['eligible_offers'], 1)

    def test_gated_door_exposures_never_enter_normal_baseline(self):
        a = row(kinds=('mystery', 'locked'))
        b = row('spying', room=11, kinds=('mystery', 'unlocked'), gems=('spying',))
        c = row('both', room=12, kinds=('mystery', 'locked'), gems=('spying', 'lodestone'))
        result = analyze([a, b, c])
        self.assertNotIn('physical_style_counts', result['overall_descriptive'])
        baseline = result['baseline_no_stones_descriptive']
        self.assertEqual(baseline['physical_door_slots'], 1)
        self.assertEqual(baseline['physical_style_counts']['locked']['share_of_physical_slots'], 1)
        cohorts = separated_cohorts([a, b, c])
        self.assertEqual([r['id'] for r in cohorts['no_stones']], ['one'])
        self.assertEqual([r['id'] for r in cohorts['spying']], ['spying', 'both'])
        self.assertEqual([r['id'] for r in cohorts['lodestone']], ['both'])
        self.assertEqual(cohorts['spying'][1]['context']['gems'], ['spying', 'lodestone'])

    def test_scripted_and_unknown_stones_do_not_enter_random_room_counts(self):
        a = row(room=4)
        b = row('unknown', room=11)
        b['verified_fields'].remove('stones')
        result = analyze([a, b])
        self.assertEqual(result['coverage']['nonordinary_or_unclassified'], 1)
        self.assertEqual(result['coverage']['stone_context_unknown'], 1)
        self.assertEqual(result['overall_descriptive']['eligible_offers'], 0)

    def test_exact_contexts_preserve_full_stones_source_and_mode(self):
        a = row(side=0, gems=('spying', 'greed'))
        b = row('two', room=11, side=0, gems=('spying',))
        b['context']['mode'] = None
        c = row('three', room=12, side=0, gems=('spying', 'greed'))
        c['source_id'] = 'another'
        result = analyze([a, b, c])
        self.assertEqual(len(result['fully_conditioned_strata']), 3)
        groups = [value['context'] for value in result['fully_conditioned_strata']]
        self.assertTrue(any(g['gems'] == ['greed', 'spying'] for g in groups))
        self.assertTrue(any(g['mode'] is None for g in groups))


if __name__ == '__main__':
    unittest.main()
