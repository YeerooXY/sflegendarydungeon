"""Native sequence extraction and seekable local video regression checks."""
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from sfld_vod.media import write_json
from sfld_vod.serve import EvidenceHandler


class RangeServerTests(unittest.TestCase):
    def test_video_seek_returns_exact_bytes_and_rejects_outside_range(self):
        class Quiet(EvidenceHandler):
            def log_message(self, *args): pass
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'video.mp4').write_bytes(bytes(range(256)))
            with ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=temp)) as server:
                worker=threading.Thread(target=server.serve_forever, daemon=True);worker.start()
                try:
                    connection=HTTPConnection(*server.server_address)
                    for value,expected in [('bytes=10-19',bytes(range(10,20))),('bytes=-4',bytes(range(252,256)))]:
                        connection.request('GET','/video.mp4',headers={'Range':value})
                        response=connection.getresponse();self.assertEqual(response.status,206)
                        self.assertEqual(response.read(),expected)
                    connection.request('GET','/video.mp4',headers={'Range':'bytes=999-1000'})
                    response=connection.getresponse();self.assertEqual(response.status,416);response.read()
                    connection.close()
                finally:
                    server.shutdown();worker.join(timeout=5)


NATIVE=all(importlib.util.find_spec(name) for name in ('cv2','numpy','imageio_ffmpeg'))


@unittest.skipUnless(NATIVE,'Optional native VOD environment required.')
class SceneTests(unittest.TestCase):
    def test_full_clip_saves_stable_choices_and_following_evidence_without_hud_gate(self):
        import cv2
        import numpy as np
        from sfld_vod.scenes import scan_scenes
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);video=root/'fixture.avi'
            positive=np.random.default_rng(52).integers(0,255,(224,320,3),dtype=np.uint8)
            cv2.imwrite(str(root/'stage.png'),positive)
            writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'FFV1'),24,(320,224))
            self.assertTrue(writer.isOpened())
            for index in range(96):writer.write(positive if index//24%2==0 else np.zeros_like(positive))
            writer.release()
            write_json(root/'layout.json',{'source_id':'fixture','crop':[0,0,1,1]})
            write_json(root/'config.json',{'schema':1,'threshold':.75,'references':[{'image':'stage.png','stage':1}]})
            result=scan_scenes(video,root/'layout.json',root/'config.json',root/'scan',source_start=100)
            self.assertEqual(result['decoded_frames'],32)
            self.assertEqual(len(result['epochs']),2)
            self.assertFalse(result['decode_warnings'])
            first,second=result['epochs']
            self.assertEqual(first['start'],100)
            self.assertEqual(second['start'],102)
            self.assertTrue(first['evidence'])
            self.assertLess(first['evidence'][-1]['source_seconds'],second['start'])
            for epoch in result['epochs']:
                self.assertEqual(epoch['review_status'],'unreviewed_scene_candidate')
                self.assertTrue((root/'scan'/epoch['last_image']).is_file())
            self.assertEqual(scan_scenes(video,root/'layout.json',root/'config.json',root/'scan',source_start=100),result)
            with self.assertRaisesRegex(ValueError,'inputs changed'):
                scan_scenes(video,root/'layout.json',root/'config.json',root/'scan',source_start=101)


if __name__ == '__main__':unittest.main()
