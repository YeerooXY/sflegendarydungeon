"""Occlusion, color ambiguity and neighboring-frame safety regressions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from sfld_vod.spatial_doors import SpatialDoorRecognizer
from prepare_quick_vod_review import temporal_neighbor, temporal_guard


class SpatialDoorTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest('Optional native OpenCV environment required.')
        self.cv, self.np = cv2, np
        cv2.setNumThreads(1)

    def make_reader(self, root, names=('locked', 'double_locked'), same_shape_colors=False):
        cv, np = self.cv, self.np
        rng = np.random.default_rng(51)
        sprites = []
        pattern = rng.integers(25, 255, (130, 60), dtype=np.uint8)
        for index, name in enumerate(names):
            raw = np.full((130, 60, 4), 255, dtype=np.uint8)
            raw[:, :, :3] = rng.integers(25, 230, (130, 60, 3), dtype=np.uint8)
            if same_shape_colors:
                raw[:, :, :3] = (pattern[:, :, None] * np.array(
                    [0., 0., 1.] if index == 0 else [0., .8, 1.])).astype(np.uint8)
            raw[30:90, :10, 3] = 0
            cv.imwrite(str(root/(name+'.png')), raw)
            sprites.append(raw)
        (root/'manifest.json').write_text(json.dumps({'assets': {'door.'+n: n+'.png' for n in names}}))
        (root/'layout.json').write_text(json.dumps({'schema': 1, 'template_heights': [130],
            'door_search': [[49, 29, 88, 158], [194, 29, 88, 158]]}))
        frame = np.full((224, 320, 3), 210, dtype=np.uint8)
        for side, raw in enumerate((sprites[0], cv.flip(sprites[1], 1))):
            target = frame[40:170, 55+145*side:115+145*side]
            use = raw[:, :, 3] > 230
            target[use] = raw[:, :, :3][use]
        return SpatialDoorRecognizer(root/'layout.json', root), frame

    def test_upper_occluder_leaves_independent_lower_regions_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            reader, frame = self.make_reader(Path(temp))
            frame[40:82, 55:115] = (110, 110, 110)
            result = reader.doors(frame)
            self.assertEqual([d['kind'] for d in result], ['locked', 'double_locked'])
            self.assertGreaterEqual(result[0]['support_regions'], 4)
            self.assertTrue(all(r['pixels'] >= 16 for r in result[0]['regions']))

    def test_identical_silhouettes_in_red_and_gold_are_not_interchangeable(self):
        with tempfile.TemporaryDirectory() as temp:
            reader, frame = self.make_reader(Path(temp), ('monster', 'golden'), True)
            result = reader.doors(frame)
            self.assertEqual([d['kind'] for d in result], ['monster', 'golden'])
            self.assertLess(result[0]['golden_color_support'], .1)
            self.assertGreater(result[1]['golden_color_support'], .75)

    def test_blank_frame_and_random_texture_are_not_confident_doors(self):
        with tempfile.TemporaryDirectory() as temp:
            reader, frame = self.make_reader(Path(temp))
            for noise in (self.np.zeros_like(frame), self.np.random.default_rng(76).integers(
                    0, 255, frame.shape, dtype=self.np.uint8)):
                self.assertEqual([d['kind'] for d in reader.doors(noise)], [None, None])

    def test_too_little_visible_evidence_remains_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            reader, frame = self.make_reader(Path(temp))
            frame[40:132, 55:115] = (110, 110, 110)
            self.assertIsNone(reader.doors(frame)[0]['kind'])


class TemporalDoorTests(unittest.TestCase):
    @staticmethod
    def proof(digest, time, video='v'):
        return {'sha256': digest, 'source_seconds': time, 'decoded_source_seconds': time,
                'video_sha256': video}

    def test_same_image_decode_video_and_other_room_cannot_supply_second_vote(self):
        first=self.proof('a',10.2)
        invalid=[self.proof('a',10.3), self.proof('b',10.2),
                 self.proof('c',10.3,'other-video'), self.proof('d',11.1)]
        row={'start':10.,'end':10.8}
        self.assertIsNone(temporal_neighbor(row,first,invalid))
        good=self.proof('e',10.4)
        self.assertEqual(temporal_neighbor(row,first,invalid+[good]),good)

    def test_neighbor_never_fills_an_unknown_displayed_image(self):
        primary=[{'kind':None,'status':'unknown'}]
        result=temporal_guard(primary,[{'kind':'monster'}])
        self.assertIsNone(result[0]['kind'])
        self.assertEqual(result[0]['temporal_status'],'primary_image_unknown')

    def test_conflict_abstains_and_agreement_requires_position_consistency(self):
        item={'kind':'locked','status':'unreviewed_suggestion','bbox':[10,10,40,130]}
        self.assertIsNone(temporal_guard([item],[{'kind':'golden'}])[0]['kind'])
        self.assertEqual(temporal_guard([item],[dict(item)])[0]['temporal_status'],'two_frames_agree')
        moved={**item,'bbox':[35,10,40,130]}
        self.assertEqual(temporal_guard([item],[moved])[0]['temporal_status'],'same_label_position_changed')
        self.assertEqual(item['kind'],'locked')


if __name__ == '__main__':
    unittest.main()
