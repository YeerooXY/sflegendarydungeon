import copy
import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.full_reanalysis import pair_report, trap_report
from sfld_vod.full_traps import can_confirm_with_second, timing_problem
from sfld_vod.trap_detector import consensus, DEFAULT_CONFIG


def offer(ident='v1', gems=None):
    return {'observation_id': ident, 'source_id': 'source', 'pair_family': 'door/mystery',
            'offered_kinds': ['locked', 'mystery'], 'context': {'gems': gems or [], 'stage': 2}}


def scan(ident='v1', positive=False, unresolved=False):
    return {'observation_id': ident, 'source_id': 'source', 'context': {'gems': [], 'stage': 2},
            'machine_sides': [True if positive else None, None], 'verified_traps': [None, None],
            'status': 'unresolved' if unresolved else 'machine_suggestions_only',
            'source_interval': [10, 12],
            'evidence': [] if unresolved else [
                {'sha256': 'first', 'source_seconds': 10.5, 'decoded_source_seconds': 10.52},
                {'sha256': 'second', 'source_seconds': 11, 'decoded_source_seconds': 11.02}]}


class FullReanalysisTests(unittest.TestCase):
    def test_unknown_scan_does_not_become_verified_clear(self):
        report = trap_report([[scan()]], [offer()])['ordinary_offers']
        self.assertEqual(report['no_machine_positive'], 1)
        self.assertEqual(report['verified_clear_pairs_in_existing_ledger'], 0)

    def test_recovery_replaces_without_double_counting(self):
        report = trap_report([[scan(unresolved=True)], [scan(positive=True)]], [offer()])
        self.assertEqual(report['all_decisions']['offers'], 1)
        self.assertEqual(report['ordinary_offers']['machine_positive_pairs'], 1)
        with self.assertRaisesRegex(ValueError, 'already scanned'):
            trap_report([[scan()], [scan(positive=True)]], [offer()])

    def test_compact_export_recounts_the_same_predictions_and_quarantine(self):
        records = [scan(positive=True), scan('v2')]
        records[1]['evidence'][0]['decoded_source_seconds'] += 8
        offers = [offer(), offer('v2')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'scan.jsonl'
            expected = trap_report([records], offers, export_scan=path)
            compact = [json.loads(line) for line in path.read_text().splitlines()]
            actual = trap_report([compact], offers)
        self.assertEqual(expected, actual)

    def test_blind_sample_keeps_unreadable_in_missingness_bounds(self):
        sample = {'sample_size': 2, 'observation_ids': ['v1', 'v2'], 'design': 'fixed'}
        reviews = [{'observation_id': 'v1', 'sides': [True, None], 'evidence': [{'sha256': 'first'}]}]
        report = trap_report([[scan(), scan('v2', unresolved=True)]], [offer(), offer('v2')], sample, reviews)
        measured = report['prespecified_blind_review']
        self.assertEqual(measured['pair_presence_resolved'], 1)
        self.assertEqual(measured['sample_missingness_bounds'], [0.5, 1.0])
        self.assertEqual(measured['detector_confusion_on_resolved_pairs'], {'unflagged_trap': 1})
        reviews[0]['evidence'][0]['sha256'] = 'wrong-visit'
        with self.assertRaisesRegex(ValueError, 'provenance|match'):
            trap_report([[scan(), scan('v2')]], [offer(), offer('v2')], sample, reviews)

    def test_recovery_cannot_add_visits_or_mask_missing_primary_coverage(self):
        with self.assertRaisesRegex(ValueError, 'adds visits'):
            trap_report([[scan()], [scan('new')]], [offer()])
        with self.assertRaisesRegex(ValueError, 'every eligible'):
            trap_report([[scan()]], [offer(), offer('missing')])

    def test_wrong_visit_or_seek_drift_is_unknown_without_removing_denominator(self):
        drifted = scan(positive=True)
        drifted['evidence'][0]['decoded_source_seconds'] += 9
        report = trap_report([[drifted]], [offer()])
        self.assertEqual(report['ordinary_offers']['offers'], 1)
        self.assertEqual(report['ordinary_offers']['machine_positive_pairs'], 0)
        self.assertEqual(report['ordinary_offers']['native_two_frame_records'], 0)
        self.assertTrue(report['quarantine'][0]['previous_machine_positive'])
        good = scan(positive=True)
        report = trap_report([[good]], [offer()], door_reviews=[
            {'observation_id': 'v1', 'verdict': 'temporal_mismatch'}])
        self.assertEqual(report['ordinary_offers']['quarantined_records'], 1)
        self.assertEqual(good['machine_sides'], [True, None])

    def test_timing_guard_accepts_frame_rounding_but_not_other_offer_or_unknown_time(self):
        record = scan()
        self.assertIsNone(timing_problem(record))
        record['source_interval'] = [10, 11.01]
        self.assertIn('outside', timing_problem(record))
        record = scan()
        record['evidence'][0]['decoded_source_seconds'] = float('nan')
        self.assertIn('invalid decoded', timing_problem(record))

    def test_independent_identity_audit_cannot_vouch_for_different_image(self):
        sample = {'sample_size': 1, 'observation_ids': ['v1'], 'design': 'fixed'}
        review = [{'observation_id': 'v1', 'sides': [True, None], 'evidence': [{'sha256': 'first'}]}]
        identity = [{'observation_id': 'v1', 'status': 'same_visit_verified', 'evidence': [
            {'sha256': 'other', 'audit_role': 'resampled_native_offer'}]}]
        with self.assertRaisesRegex(ValueError, 'independent same-visit'):
            trap_report([[scan()]], [offer()], sample, review, identity_reviews=identity)
        identity[0]['evidence'] = [dict(e, audit_role='resampled_native_offer') for e in scan()['evidence']]
        result = trap_report([[scan()]], [offer()], sample, review, identity_reviews=identity)
        self.assertEqual(result['prespecified_blind_review']['trap_pairs'], 1)

    def test_gated_cohorts_do_not_pollute_no_stone_style_denominator(self):
        locked = offer()
        unlocked = offer('v2', ['spying', 'lodestone'])
        unlocked['offered_kinds'] = ['unlocked', 'mystery']
        report = pair_report([locked, unlocked])
        self.assertEqual(report['no_active_stones']['physical_slots'], 1)
        self.assertNotIn('unlocked', report['no_active_stones']['physical_style_frequencies'])
        self.assertEqual(report['stone_enabled_cohorts']['spying']['offers'], 1)
        self.assertEqual(report['stone_enabled_cohorts']['lodestone']['offers'], 1)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            pair_report([locked, locked])

    def test_second_match_shortcut_cannot_discard_two_frame_positive(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        for score in (0.0, 0.79, 0.80, 0.95):
            match = {'score': score, 'visual': 'rope', 'bbox': [10, 10, 50, 10]}
            first = {'usable': True, 'sides': [{'candidates': [match]}, {}]}
            later = {'usable': True, 'sides': [{'candidates': [{**match, 'score': 0.99}]}, {}]}
            full = consensus([first, later], config)
            shortcut = can_confirm_with_second(first, config['positive_threshold'])
            if any(r['present'] is True for r in full):
                self.assertTrue(shortcut)
            if not shortcut:
                self.assertTrue(all(r['present'] is None for r in full))


if __name__ == '__main__':
    unittest.main()
