"""Actual-menu rollouts retain state, preserve the source, and count failures."""
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
from sfld_rl.effect_lab import EffectLab
from sfld_rl.env import VectorEnv
from sfld_rl.stone_advisor import StoneAdvisor
from sfld_rl.stone_grid import position
from render_final_stone_guide import build_data

LIB = library('build-v0612')
PROFILE = ROOT/'profiles/synthetic.profile'
MENU = dict(run=2,held=['moonstone','thirsty'],offered=['deceit','rusty','pendant'],rejected=['hero','pearl','explorer','blood'])


@unittest.skipUnless(LIB.is_file(), MISSING)
class StoneAdvisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adviser = StoneAdvisor(device='cpu')

    def forecast(self, progress=None, **options):
        return self.adviser.from_progress(progress or position(MENU,3,1),LIB,PROFILE,
            samples=32,planning_seed=516991,**options)

    def test_preserves_full_state_and_does_not_touch_source(self):
        with VectorEnv(LIB,[PROFILE],1,budgets=[5],deadline_hours=.1,auto_reset=False,event_start_hour=22,sleep_until_hour=8) as env:
            env.reset(); lab=EffectLab(env)
            state=position(MENU,3,41,blessings='recovery:strong:4,one_hit:weak:3:1',curses='poison:weak:2')
            state+='paid_heals=2\nresources=4,3,2,1,5,6\nreroll_shop_visits=2\nreroll_bank_used=3\n'
            with lab.fixture(state,5) as snapshot:lab.restore(0,snapshot,123)
            obs,mask=lab.observe();before=lab.visible()
            with lab.capture(0) as untouched:
                result,data=self.adviser.compare(env,samples=32,planning_seed=71292)
                self.assertEqual(lab.visible(),before)
                self.assertEqual(result['source_observation'],before)
                self.assertEqual(result['source_observation']['paid_heals'],2)
                self.assertEqual(result['source_observation']['blessings'][1]['starts_at'],76)
                after_obs,after_mask=lab.observe()
                np.testing.assert_array_equal(obs,after_obs);np.testing.assert_array_equal(mask,after_mask)
                # The source RNG was not consumed either. Its next transition
                # matches the independent-seed copy of its original snapshot.
                env.step([env.action_names.index('gem_0')]);after=lab.visible()
                lab.restore(0,untouched,123);env.step([env.action_names.index('gem_0')])
                self.assertEqual(lab.visible(),after)
            self.assertEqual(data.shape,(3,32,6))

    def test_all_offers_and_fixed_seed_reproducibility(self):
        a,data_a=self.forecast(batch_size=96)
        b,data_b=self.forecast(batch_size=12)
        self.assertEqual({r['stone'] for r in a['rankings']},set(MENU['offered']))
        self.assertNotIn(a['estimated_best'],('rabbit','greasy','time_traveler'))
        self.assertEqual(a['action']['index'],MENU['offered'].index(a['estimated_best']))
        self.assertEqual(a['trajectories'],96)
        self.assertTrue(np.all(data_a[...,3]<=5))
        np.testing.assert_array_equal(data_a,data_b)

    def test_timeout_is_not_dropped_or_reported_as_completion(self):
        result,data=self.forecast(hours=.001)
        np.testing.assert_allclose(data[...,0],.001)
        np.testing.assert_array_equal(data[...,1],0)
        self.assertEqual(len(result['contenders']),3)
        self.assertIsNone(result['supported_best'])

    def test_later_event_run_counts_only_the_next_completion(self):
        with VectorEnv(LIB,[PROFILE],1,budgets=[5],seed=1271,auto_reset=False,deadline_hours=168) as env:
            obs,masks=env.reset();lab=EffectLab(env)
            for _ in range(3000):
                before=lab.visible()
                if before['phase']=='gems' and before['room']==76 and before['completed_runs']>0:break
                self.assertFalse(before['terminal'],'Teacher ran out of time before reaching the test state')
                obs,masks,*_=env.step(env.teacher_actions('survival'))
            else:self.fail('Could not reach a later-run final stone choice')
            self.assertGreater(before['elapsed_hours'],0)
            self.assertIsNotNone(before['first_completion_hours'])
            result,data=self.adviser.compare(env,samples=32,planning_seed=128771)
            self.assertEqual(result['source_observation'],before)
            self.assertEqual(lab.visible(),before)
            self.assertTrue(np.all(data[...,0]>0))
            self.assertTrue(np.all(data[...,0]<=before['hours_left']+1e-9))
            self.assertTrue(np.all(data[...,3]<=before['mushrooms_left']))
            self.assertTrue(np.all(data[...,4]>1),'An old completion must not end the new continuation')

    def test_requires_actual_offers_and_final_choice(self):
        with self.assertRaisesRegex(ValueError,'actual gem_offers'):
            self.forecast(position(MENU,3,1).replace('gem_offers=deceit,rusty,pendant','gem_offers=unknown'))
        with self.assertRaisesRegex(ValueError,'final stone choice'):
            self.forecast('schema=1\nroom=1\nrun_number=2\nphase=gems\nhp_percent=100\nkeys=0\ngem_offers=deceit,rusty,pendant\n')


@unittest.skipUnless((ROOT/'docs/final-stones-five-2026-10-15').exists(), 'Local research archive absent (not published)')
class FullRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=build_data(ROOT/'docs/final-stones-five-2026-10-15')

    def test_complete_rank_coverage_and_global_confidence(self):
        rows=self.data['rows']
        self.assertEqual(len(rows),1175)
        self.assertEqual(sum(len(r['ranks']) for r in rows),18900)
        for row in rows:
            self.assertEqual(len(row['ranks']),8 if row['run']==1 else 18)
            self.assertEqual({r['stone'] for r in row['ranks']},set(self.data['pools'][str(row['run'])])-set(row['held']))
            self.assertEqual([r['hours'] for r in row['ranks']],sorted(r['hours'] for r in row['ranks']))

    def test_unavailable_global_winner_does_not_hide_offered_winner(self):
        for keys in (0,1,3,5,10):
            row=next(r for r in self.data['rows'] if r['run']==2 and set(r['held'])=={'moonstone','thirsty'} and r['keys']==keys)
            self.assertNotIn(row['ranks'][0]['stone'],MENU['offered'])
            actual=[r['stone'] for r in row['ranks'] if r['stone'] in MENU['offered']]
            self.assertEqual(actual,['deceit','pendant','rusty'])
            beaten={b for a,b in row['beats'] if a in actual and b in actual}
            self.assertEqual(set(actual)-beaten,{'deceit'})


if __name__=='__main__':unittest.main()
