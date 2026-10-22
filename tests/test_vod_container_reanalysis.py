"""Full-source loot reporting preserves missingness, selection, and item rarity."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.container_reanalysis import (FLAGS, apply_review, container_label, describe,
                                          inventory, merge_reviews, validate, augment_entry_links,
                                          select_uniform_candidates)


def row(ident='one'):
    return {'observation_id': ident, 'source_id': 'source', 'initial_style': 'wooden_box',
            'appearance_basis': 'verified_initial_content', 'interaction': 'unknown',
            'reward_review_status': 'unreviewed', 'ordinary_loot_eligible': None,
            'reward_flags': {k: None for k in FLAGS}, 'reward_complete': False,
            'rewards': [], 'evidence': [], 'notes': [], 'item_rarity': None,
            'context': {'gems': [], 'stone_status': 'verified', 'run_class': None}}


class FullContainerAnalysis(unittest.TestCase):
    def test_initial_content_is_not_replaced_by_unrelated_later_scene(self):
        obs = {'verified_fields': ['initial_content'], 'outcome': {'initial_content': 'chest', 'following_scene_observation': 'corpse'}}
        self.assertEqual(container_label(obs), ('chest_unknown', 'verified_initial_content'))

    def test_following_object_is_candidate_not_verified_initial(self):
        self.assertEqual(container_label({'outcome': {'initial_content': None, 'following_scene_observation': 'epic'}}),
                         ('epic_chest', 'following_scene_candidate'))

    def test_item_sale_does_not_become_direct_gold(self):
        r = row()
        apply_review(r, {'interaction': 'opened', 'rewards': [{'kind': 'item'}, {'kind': 'item_sale_gold', 'amount': 42}]}, 'prior_complete')
        self.assertFalse(r['reward_flags']['gold'])
        self.assertTrue(r['reward_flags']['item'])
        self.assertIsNone(r['reward_flags']['legendary_item'])

    def test_skipped_mimic_and_unknown_are_not_reward_failures(self):
        for kind in ('skipped', 'awakened_mimic', 'unknown'):
            r = row()
            apply_review(r, {'interaction': kind, 'rewards': []}, 'prior_complete')
            validate([r])
            self.assertTrue(all(v is None for v in r['reward_flags'].values()))

    def test_partial_new_blessing_does_not_deny_other_rewards(self):
        r = row()
        supplemental = {'observation_id': 'one', 'reward': {'container': 'wooden_box', 'persistent_blessing': 'escape', 'item_reward': None}}
        merge_reviews([r], [], [supplemental])
        self.assertTrue(r['reward_flags']['persistent_blessing'])
        self.assertIsNone(r['reward_flags']['gold'])
        self.assertIsNone(r['reward_flags']['item'])
        self.assertFalse(r['reward_complete'])

    def test_epic_chest_does_not_identify_item_rarity(self):
        r = row()
        apply_review(r, {'initial_style': 'epic', 'interaction': 'opened', 'rewards': [{'kind': 'item'}]}, 'prior_complete')
        self.assertEqual(r['initial_style'], 'epic_chest')
        self.assertIsNone(r['reward_flags']['epic_item'])
        self.assertIsNone(r['reward_flags']['legendary_item'])
        self.assertFalse(r['ordinary_loot_eligible'])

    def test_nothing_requires_complete_interaction_and_no_positive(self):
        r = row()
        apply_review(r, {'interaction': 'opened', 'rewards': [{'kind': 'nothing'}]}, 'prior_complete')
        validate([r])
        r['reward_complete'] = False
        with self.assertRaises(ValueError):
            validate([r])
        r['reward_complete'] = True
        r['reward_flags']['gold'] = True
        with self.assertRaises(ValueError):
            validate([r])

    def test_overlapping_supplemental_visit_is_rejected(self):
        complete = {'observation_id': 'one', 'interaction': 'opened', 'rewards': []}
        supplemental = {'observation_id': 'one', 'reward': {'container': 'wooden_box'}}
        with self.assertRaises(ValueError):
            merge_reviews([row()], [complete], [supplemental])

    def test_every_planned_source_is_scanned_and_unknowns_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            sources = []
            for source in ('a', 'b'):
                rows = [{'id': source+'-one', 'source_id': source, 'source_seconds': 1, 'status': 'decision',
                         'verified_fields': ['initial_content'], 'outcome': {'initial_content': 'wooden_box'}},
                        {'id': source+'-two', 'source_id': source, 'source_seconds': 2, 'status': 'decision', 'outcome': {}},
                        {'id': source+'-dup', 'source_id': source, 'source_seconds': 3, 'status': 'duplicate', 'outcome': {'initial_content': 'wooden_box'}}]
                (p/f'{source}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                sources.append({'source_id': source, 'observations': f'{source}.jsonl'})
            plan = p/'plan.json'
            plan.write_text(json.dumps({'sources': sources}))
            rows, coverage = inventory([plan])
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(coverage['sources']), 2)
            self.assertEqual(coverage['excluded_decisions']['entry_unclassified'], 2)
            self.assertEqual([s['records'] for s in coverage['sources']], [3, 3])

    def test_rewards_can_overlap_but_visit_count_stays_one(self):
        r = row()
        apply_review(r, {'interaction': 'opened', 'rewards': [{'kind': 'gold'}, {'kind': 'gold'}, {'kind': 'persistent_blessing'}], 'persistent_blessing': True}, 'prior_complete')
        s = describe([r])
        self.assertEqual(s['ordinary_opened'], 1)
        self.assertEqual(s['ordinary_opened_reward_flags']['gold']['positive'], 1)
        self.assertEqual(s['ordinary_opened_reward_flags']['persistent_blessing']['positive'], 1)

    def test_defeated_mimic_reward_is_not_an_ordinary_opening(self):
        r = row()
        review = {'observation_id': 'one', 'interaction': 'mimic_defeated', 'complete': True,
                  'rewards': [{'kind': 'item'}], 'item_rarity': 'epic',
                  'reward_flags': {'item': True, 'epic_item': True}}
        merge_reviews([r], [], [], [review])
        validate([r])
        self.assertTrue(r['reward_flags']['epic_item'])
        self.assertEqual(describe([r])['ordinary_opened'], 0)

    def test_confirmed_false_candidate_is_retained_without_reward_negatives(self):
        r = row()
        merge_reviews([r], [], [], [{'observation_id': 'one', 'interaction': 'not_container',
                                    'initial_style': 'not_container', 'complete': True}])
        validate([r])
        self.assertEqual(r['appearance_basis'], 'native_review_rejects_legacy_container_label')
        self.assertTrue(all(v is None for v in r['reward_flags'].values()))

    def test_native_entry_correction_replaces_legacy_chest_label(self):
        r = row()
        r['initial_style'] = 'silver'
        augment_entry_links([r], [{'observation_id': 'one', 'initial_content': 'barrel',
                                  'new_review': {'status': 'confirmed', 'evidence': []}}])
        self.assertEqual(r['initial_style'], 'barrel')
        self.assertEqual(r['appearance_basis'], 'verified_native_floor_object')

    def test_uniform_sample_is_order_independent_and_excludes_reviewed(self):
        rows = [row(str(i)) for i in range(8)]
        rows[0]['reward_review_status'] = 'verified_native_sequence'
        a = select_uniform_candidates(rows, 4)
        b = select_uniform_candidates(list(reversed(rows)), 4)
        self.assertEqual(a, b)
        self.assertEqual(a['sampling_population'], 7)
        self.assertNotIn('0', [r['observation_id'] for r in a['rows']])
        with self.assertRaises(ValueError):
            select_uniform_candidates(rows, 8)

    def test_partial_manual_review_cannot_insert_reward_absences(self):
        with self.assertRaises(ValueError):
            merge_reviews([row()], [], [], [{'observation_id': 'one', 'complete': False,
                                             'interaction': 'opened', 'reward_flags': {'gold': False}}])


if __name__ == '__main__':
    unittest.main()
