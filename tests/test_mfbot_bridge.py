"""MFBot bridge: synthetic packets only, no network or game commands."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'));sys.path.insert(0, str(ROOT/'tests'))
from sfld_live.mfbot_bridge import Bridge  # noqa: E402
from sfld_study.schema import StudyError  # noqa: E402
from test_study_automatic import packet  # noqa: E402


def mfbot_fields(**extra):
    """MFBot's view: main save under "iadungeon", level from its own parser."""
    p = packet(**extra);p['iadungeon'] = p.pop('iadungeonsave');p.pop('ownplayersavecharacter');return p


class FakePolicy:
    def __init__(self):
        self.calls = []

    def __call__(self, visible, memory):
        self.calls.append((visible, memory))
        if visible['status'] == 101:raise StudyError('recovery')
        return {'action': visible['legal_actions'][0], 'memory': {'room': visible['room'], 'reason': 'learned'}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory();self.policy = FakePolicy()
        self.bridge = Bridge(self.dir.name, choose=self.policy)

    def tearDown(self):
        self.dir.cleanup()

    def decide(self, **extra):
        return self.bridge.decide({'character': 'Hero@s1', 'level': 120, 'fields': mfbot_fields(**extra)})

    def test_alias_budget_and_confirm_commits_memory(self):
        reply = self.decide()
        self.assertEqual(reply['decision'], 'command');self.assertEqual(reply['req'], 'IADungeonInteract')
        visible, memory = self.policy.calls[0]
        self.assertEqual(visible['budget_remaining'], 0);self.assertEqual(visible['level'], 120)
        self.assertEqual(visible['run_number_event'], 1);self.assertIsNone(memory)
        self.assertGreaterEqual(visible['elapsed_seconds'], 100)
        self.assertTrue(self.bridge.confirm({'character': 'Hero@s1', 'id': reply['id'], 'ok': True})['committed'])
        self.decide(status=10, room=1, types=(100, 0, 0), payloads=(0, 0, 0))
        self.assertEqual(self.policy.calls[1][1], {'room': 1, 'reason': 'learned'})

    def test_refused_screens_fall_back_without_touching_memory(self):
        self.assertEqual(self.decide(status=101)['decision'], 'fallback')
        self.assertEqual(self.decide(status=10, room=1, types=(319, 0, 0))['decision'], 'fallback')

    def test_unconfirmed_or_rejected_commands_block_the_run(self):
        first = self.decide()
        second = self.decide()
        self.assertEqual(second['decision'], 'fallback');self.assertIn('unconfirmed', second['reason'])
        self.assertFalse(self.bridge.confirm({'character': 'Hero@s1', 'id': first['id'], 'ok': True})['committed'])
        other = Bridge(Path(self.dir.name)/'b', choose=FakePolicy())
        reply = other.decide({'character': 'X@s1', 'level': 120, 'fields': mfbot_fields()})
        other.confirm({'character': 'X@s1', 'id': reply['id'], 'ok': False, 'error': 'ERR'})
        self.assertIn('rejected', other.decide({'character': 'X@s1', 'level': 120, 'fields': mfbot_fields()})['reason'])

    def test_low_level_entry_falls_back(self):
        reply = self.bridge.decide({'character': 'Low@s1', 'level': 49, 'fields': mfbot_fields(status=0)})
        self.assertEqual(reply['decision'], 'fallback')


if __name__ == '__main__':
    unittest.main()
