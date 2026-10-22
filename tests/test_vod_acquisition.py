"""HLS boundaries and uncertainty checks used by the public-footage audit."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from fetch_twitch_segments import playlist
from summarize_vod_review_2026_10_12 import interval, wilson

class PublicPlaylistTests(unittest.TestCase):
    def test_legacy_transport_stream_has_no_required_initialization(self):
        init,segments=playlist('#EXTM3U\n#EXTINF:9.75,\n0.ts\n#EXTINF:10.25,\n1.ts\n','https://example.test/vod/index.m3u8')
        self.assertIsNone(init)
        self.assertEqual([(s['start'],s['end']) for s in segments],[(0,9.75),(9.75,20)])
        self.assertEqual(segments[1]['url'],'https://example.test/vod/1.ts')

    def test_fragmented_mp4_retains_init_and_fractional_coverage(self):
        init,segments=playlist('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2.002,\n1.m4s\n','https://example.test/vod/index.m3u8')
        self.assertEqual(init,'https://example.test/vod/init.mp4')
        self.assertAlmostEqual(segments[0]['end'],2.002)

    def test_unhandled_encryption_or_discontinuity_is_not_silently_joined(self):
        for tag in ['#EXT-X-KEY:METHOD=AES-128,URI="secret"','#EXT-X-DISCONTINUITY','#EXT-X-BYTERANGE:4096@0']:
            with self.subTest(tag=tag),self.assertRaises(ValueError):
                playlist(f'#EXTM3U\n{tag}\n#EXTINF:10,\n1.ts\n','https://example.test/vod/')

    def test_invalid_durations_cannot_create_fictitious_coverage(self):
        for value in ['0','-1','nan','inf']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                playlist(f'#EXTM3U\n#EXTINF:{value},\n1.ts\n','https://example.test/vod/')

class ConfidenceTests(unittest.TestCase):
    def test_zero_sightings_still_have_nonzero_upper_bound(self):
        lo,hi=wilson(0,61)
        self.assertAlmostEqual(lo,0)
        self.assertAlmostEqual(hi,.059243867898,places=10)

    def test_all_sightings_do_not_prove_certainty(self):
        lo,hi=wilson(9,9)
        self.assertAlmostEqual(lo,.70085495158,places=10)
        self.assertAlmostEqual(hi,1)
        self.assertIsNone(wilson(0,0))

    def test_zero_events_do_not_get_a_zero_width_cluster_interval(self):
        result=interval([{'source_id':str(i)} for i in range(3)],lambda _:False)
        self.assertIsNone(result['source_cluster_bootstrap_95'])
        self.assertTrue(result['bootstrap_boundary_not_estimable'])
        self.assertGreater(result['wilson_95'][1],0)

if __name__=='__main__': unittest.main()
