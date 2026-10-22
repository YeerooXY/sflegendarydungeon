"""Evidence integrity and uncertainty boundaries for reusable recognition."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from sfld_vod.media import sha256
from sfld_vod.recognize import validate_items, render_review


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = []
        for i in range(2):
            path = self.root/f'{i}.png'; path.write_bytes(f'native-frame-{i}'.encode())
            evidence.append({'image': path.name, 'sha256': sha256(path),
                             'source_seconds': 100+i/4, 'native_height': 720})
        self.queue = {'items': [{'observation_id': 'source:offer:1', 'source_id': 'source', 'evidence': evidence}]}

    def test_distinct_native_proofs_retained_without_labels(self):
        before = deepcopy(self.queue)
        self.assertEqual(validate_items(self.queue, self.root), self.queue['items'])
        self.assertEqual(self.queue, before)
        self.assertNotIn('verified_fields', self.queue['items'][0])

    def test_changed_image_cannot_be_reused_as_prior_evidence(self):
        (self.root/'1.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            validate_items(self.queue, self.root)

    def test_one_frame_cannot_supply_two_temporal_votes(self):
        proof = self.queue['items'][0]['evidence']
        proof[1] = {**proof[0], 'source_seconds': 101}
        with self.assertRaisesRegex(ValueError, 'own evidence file'):
            validate_items(self.queue, self.root)

    def test_copied_images_under_distinct_names_do_not_supply_two_votes(self):
        (self.root/'1.png').write_bytes((self.root/'0.png').read_bytes())
        self.queue['items'][0]['evidence'][1]['sha256'] = sha256(self.root/'1.png')
        with self.assertRaisesRegex(ValueError, 'Copied image evidence'):
            validate_items(self.queue, self.root)

    def test_same_timestamp_is_not_two_frame_support(self):
        self.queue['items'][0]['evidence'][1]['source_seconds'] = 100
        with self.assertRaisesRegex(ValueError, 'one timestamp'):
            validate_items(self.queue, self.root)

    def test_third_distinct_time_does_not_legitimize_duplicate_votes(self):
        path = self.root/'2.png'; path.write_bytes(b'third frame')
        proof = self.queue['items'][0]['evidence']
        proof.append({**proof[0], 'image': path.name, 'sha256': sha256(path)})
        with self.assertRaisesRegex(ValueError, 'one timestamp'):
            validate_items(self.queue, self.root)

    def test_distinct_seeks_that_decode_one_frame_are_not_temporal_support(self):
        for ev in self.queue['items'][0]['evidence']:
            ev['decoded_source_seconds'] = 100
        with self.assertRaisesRegex(ValueError, 'Repeated decoded frames'):
            validate_items(self.queue, self.root)

    def test_low_resolution_and_bad_times_are_rejected(self):
        for key, value in [('native_height', 360), ('source_seconds', float('nan'))]:
            queue = deepcopy(self.queue); queue['items'][0]['evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_items(queue, self.root)

    def test_duplicate_offers_and_evidence_escape_are_rejected(self):
        duplicate = deepcopy(self.queue); duplicate['items'].append(deepcopy(duplicate['items'][0]))
        with self.assertRaisesRegex(ValueError, 'unique'):
            validate_items(duplicate, self.root)
        self.queue['items'][0]['evidence'][0]['image'] = '../outside.png'
        with self.assertRaisesRegex(ValueError, 'declared root'):
            validate_items(self.queue, self.root)

    def test_gallery_escapes_metadata_and_retains_unknown(self):
        record = {**self.queue['items'][0], 'observation_id': '<script>bad()</script>',
            'doors': [{'kind': None}, {'kind': 'mystery'}],
            'traps': {'sides': [{'present': None}, {'present': True}]}, 'stone_frame_predictions': []}
        target = self.root/'review.html'
        render_review({'rows': [record]}, self.root, target)
        page = target.read_text(encoding='utf-8')
        self.assertNotIn('<script>bad()', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('Unknown trap presence does not mean absent', page)
        self.assertNotIn('"present": false', page)
        self.assertEqual(page.count('loading="lazy"'), 2)


if __name__ == '__main__':
    unittest.main()
