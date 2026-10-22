"""Recognition geometry checks independent of copyrighted client artwork."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from vod_monster_mystery import PairFinder,source_link

try:
    import cv2
    import numpy as np
except ImportError:
    cv2=np=None


@unittest.skipIf(cv2 is None,'Optional OpenCV environment required')
class PartialDoorGeometryTests(unittest.TestCase):
    def setUp(self):
        self.finder=object.__new__(PairFinder)
        self.finder.cv=cv2
        self.rng=np.random.default_rng(20261012)

    def test_patch_at_last_legal_full_sprite_position_is_retained(self):
        area=self.rng.integers(0,256,(173,102,3),dtype=np.uint8)
        patch=self.rng.integers(0,256,(30,24,3),dtype=np.uint8)
        px,py,w,h=9,40,64,140
        x,y=102-w+px,173-h+py
        area[y:y+30,x:x+24]=patch
        self.assertGreater(self.finder.partial_score(area,('monster',patch,px,py,w,h)),.999)

    def test_spurious_match_where_the_whole_door_would_not_fit_is_rejected(self):
        area=self.rng.integers(0,256,(173,102,3),dtype=np.uint8)
        patch=self.rng.integers(0,256,(30,24,3),dtype=np.uint8)
        area[140:170,75:99]=patch
        self.assertGreater(cv2.minMaxLoc(cv2.matchTemplate(area,patch,cv2.TM_CCOEFF_NORMED))[1],.999)
        self.assertLess(self.finder.partial_score(area,('monster',patch,9,40,64,140)),.5)

    def test_oversized_door_abstains(self):
        area=np.zeros((10,10,3),np.uint8);patch=np.ones((2,2,3),np.uint8)
        self.assertEqual(self.finder.partial_score(area,('monster',patch,1,1,12,12)),-1)


class SourceLinksTests(unittest.TestCase):
    def test_timestamp_keeps_video_id(self):
        self.assertEqual(source_link('https://www.youtube.com/watch?v=abc',12.5),'https://www.youtube.com/watch?v=abc&t=12s')


if __name__=='__main__':unittest.main()
