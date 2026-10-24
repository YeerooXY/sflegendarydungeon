"""Kept loot must be native rewards, survive replay and undo without duplication."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
from visual_lab import Lab


@unittest.skipUnless(Path(os.environ.get('SFLD_EXE', engine('build-v0611'))).is_file(), MISSING)
class VisualLootTests(unittest.TestCase):
    def setUp(self):
        self.lab = Lab(Path(os.environ.get('SFLD_EXE', engine('build-v0611'))),
                       ROOT / 'profiles/synthetic.profile')

    def tearDown(self):
        self.lab.close()

    def session(self, encounter, room=60, seed=1):
        gems = 'rabbit,moonstone,greed' if room>75 else 'rabbit,moonstone'
        state = f'schema=1\nroom={room}\nrun_number=2\nphase=encounter\nencounter={encounter}\nhp_percent=100\nkeys=8\ngems={gems}\n'
        created = self.lab.create({'initial_state': state, 'run_number':2, 'seed':seed, 'neural_adviser':False})
        return self.lab.session(created['session'])

    def test_epic_and_boss_items_replay_undo_and_restart(self):
        for encounter, room, action, rarity in [('epic',60,'interact','epic'),('boss',100,'fight','legendary')]:
            with self.subTest(rarity=rarity):
                s = self.session(encounter, room)
                empty = s.payload()['loot']
                after = s.step({'kind':action})
                self.assertEqual(after['loot']['mode'],'keep_all')
                self.assertEqual(after['loot']['counts'][rarity],1)
                self.assertEqual(sum(after['loot']['counts'].values()),1)
                entry={'step':1,'run':2,'room':room,'rarity':rarity,'count':1}
                self.assertEqual(after['loot']['recent'],[entry])
                record = s.export()
                self.assertEqual(record['loot']['items'],[entry])
                # Imported UI metadata cannot mint items: replay derives rewards.
                record['loot']['counts'][rarity] = 999
                replay = self.lab.create(record['settings'], record)
                self.assertEqual(replay['loot'],after['loot'])
                s.undo();self.assertEqual(s.payload()['loot'],empty)
                self.assertEqual(s.step({'kind':action})['loot'],after['loot'])
                if room==100:
                    self.assertEqual(s.step({'kind':'restart'})['loot'],after['loot'])
                    self.assertEqual(s.undo()['loot'],after['loot'])

    def test_ordinary_item_is_one_even_when_native_amount_is_zero(self):
        for seed in range(1,80):
            s = self.session('crate', seed=seed)
            result = s.step({'kind':'interact'})
            found = any(e['kind']=='item' for e in result['journal'][-1]['outcomes'])
            if not found:
                s.close()
                # Remove closed search sessions to stay below the viewer cap.
                self.lab.sessions = {key:value for key,value in self.lab.sessions.items() if value is not s}
                continue
            self.assertEqual(result['loot']['counts']['ordinary'],1)
            replay = self.lab.create(s.config,s.export())
            self.assertEqual(replay['loot'],result['loot'])
            self.assertEqual(s.undo()['loot']['counts']['ordinary'],0)
            break
        else:self.fail('No ordinary item fixture found')

    def test_skip_never_keeps_unopened_chest_contents(self):
        s = self.session('epic')
        result = s.step({'kind':'skip'})
        self.assertEqual(sum(result['loot']['counts'].values()),0)
        self.assertEqual(result['loot']['recent'],[])


if __name__=='__main__':unittest.main()
