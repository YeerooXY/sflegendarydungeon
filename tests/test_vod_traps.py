"""Guard trap review against silent absence and stale or damaged evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_vod.media import sha256
from sfld_vod.traps import ABSENCE_CHECKS, fingerprint, make_patch, select_sample, systematic


class TrapReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.row = {'id': 'source:room', 'source_id': 'source', 'source_seconds': 10,
                    'status': 'decision', 'candidate_id': 'candidate',
                    'doors': [{'kind': 'mystery', 'trap': None}, {'kind': 'monster', 'trap': None}],
                    'context': {'run_id': 'run1', 'room': 30}}
        evidence = []
        for i in range(2):
            path = self.root / f'{i}.jpg'; path.write_bytes(f'frame{i}'.encode())
            evidence.append({'image': path.name, 'sha256': sha256(path), 'source_seconds': 10+i*.25, 'native_height': 720})
        self.item = {'observation_id': self.row['id'], 'source_id': 'source', 'source_seconds': 10,
                     'before': [None, None], 'expected_fingerprint': fingerprint(self.row), 'evidence': evidence}
        self.queue = {'items': [self.item]}
        self.annotation = {'observation_id': self.row['id'], 'doors': [
            {'trap': True, 'visual': 'cleaver', 'reason': 'Hanging bloody blade is visible.'},
            {'trap': False, 'reason': 'Both native frames show the full opening and floor.',
             'checks': dict.fromkeys(ABSENCE_CHECKS, True)}]}
        self.annotation.update(expected_fingerprint=self.item['expected_fingerprint'],
                               evidence_sha256=[e['sha256'] for e in evidence])

    def patch(self):
        return make_patch(self.queue, [self.annotation], {self.row['id']: self.row}, self.root)

    def test_review_returns_patch_without_mutating_canonical(self):
        before = deepcopy(self.row)
        patch = self.patch()[0]
        self.assertEqual(patch['set']['door_traps'], [True, False])
        self.assertEqual(self.row, before)

    def test_unknown_is_retained_not_zero_filled(self):
        self.annotation['doors'][1] = {'trap': None, 'reason': 'Tooltip covers the floor.'}
        self.assertEqual(self.patch()[0]['set']['door_traps'], [True, None])

    def test_absence_requires_two_distinct_native_frames(self):
        for change in ('one_frame', 'same_time', 'low_resolution', 'unchecked_floor'):
            with self.subTest(change=change):
                queue, annotation = deepcopy(self.queue), deepcopy(self.annotation)
                if change == 'one_frame': queue['items'][0]['evidence'].pop()
                if change == 'same_time': queue['items'][0]['evidence'][1]['source_seconds'] = 10
                if change == 'low_resolution': queue['items'][0]['evidence'][0]['native_height'] = 360
                if change == 'unchecked_floor': annotation['doors'][1]['checks']['floor_area_visible'] = False
                with self.assertRaises(ValueError):
                    make_patch(queue, [annotation], {self.row['id']: self.row}, self.root)

    def test_presence_cannot_be_asserted_without_evidence(self):
        self.item['evidence'] = []
        self.annotation['evidence_sha256'] = []
        with self.assertRaisesRegex(ValueError, 'native image evidence'): self.patch()

    def test_annotation_cannot_silently_follow_a_regenerated_queue(self):
        self.annotation['evidence_sha256'] = ['old-frame', 'old-frame-2']
        with self.assertRaisesRegex(ValueError, 'queued target and evidence'): self.patch()

    def test_stale_door_or_identity_fails_but_new_outcome_does_not(self):
        self.row['outcome'] = {'initial_content': 'chest'}
        self.patch()
        self.row['doors'][0]['kind'] = 'locked'
        with self.assertRaisesRegex(ValueError, 'target changed'): self.patch()

    def test_changed_image_hash_fails(self):
        (self.root/'0.jpg').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'evidence changed'): self.patch()

    def test_path_escape_and_non_boolean_label_fail(self):
        self.item['evidence'][0]['image'] = '../outside.jpg'
        with self.assertRaisesRegex(ValueError, 'under its declared root'): self.patch()
        self.item['evidence'][0]['image'] = '0.jpg'
        self.annotation['doors'][0]['trap'] = 1
        with self.assertRaisesRegex(ValueError, 'true, false, or unknown'): self.patch()

    def test_duplicate_reviews_fail(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate trap review'):
            make_patch(self.queue, [self.annotation, self.annotation], {self.row['id']: self.row}, self.root)

    def test_positive_stratum_and_midpoint_sample_are_distinct(self):
        rows = []
        for i in range(20):
            row = deepcopy(self.row); row.update(id=str(i), source_seconds=i)
            row['doors'][0]['trap'] = True if i % 2 else None; rows.append(row)
        sample = select_sample(rows, 8, 3)
        self.assertEqual(len({r['id'] for r, _ in sample}), 8)
        self.assertEqual(sum(s == 'existing_present' for _, s in sample), 3)
        self.assertEqual([r['id'] for r in systematic(rows, 4)], ['2','7','12','17'])
        self.assertEqual(systematic(rows, 0), [])


if __name__ == '__main__':
    unittest.main()
