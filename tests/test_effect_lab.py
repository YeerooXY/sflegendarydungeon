"""Replay independence, native API compatibility and challenge accounting."""
from pathlib import Path
import importlib.util
import os
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
AVAILABLE=importlib.util.find_spec('numpy') is not None
if AVAILABLE:
    import numpy as np
    from sfld_rl.env import VectorEnv
    from sfld_rl.effect_lab import EffectLab, EffectCurriculum, challenge_cases, position
LIB=next((p for p in (Path(os.environ.get('SFLD_RL_LIBRARY','__absent__')),
    ROOT/'build-effects/Release/sfld_rl.dll',ROOT/'build/Release/sfld_rl.dll',ROOT/'build/libsfld_rl.so') if p.is_file()),None)
PROFILE=ROOT/'profiles/synthetic.profile'

@unittest.skipUnless(AVAILABLE and LIB,'Optional NumPy/native replay library absent')
class EffectLabTests(unittest.TestCase):
    def env(self, n=1, budget=0, library=LIB):
        return VectorEnv(library,[PROFILE],n,budgets=[budget],seed=12200001,auto_reset=False)

    def test_challenge_bank_is_valid(self):
        with self.env() as env:
            env.reset();lab=EffectLab(env)
            for case in challenge_cases():
                with lab.fixture(case['progress'],0) as snapshot:
                    lab.restore(0,snapshot,41)
                    _,masks=lab.observe()
                    self.assertTrue(all(masks[0,env.action_names.index(a)] for a in case['actions']),case['name'])

    def test_restore_has_independent_future_and_does_not_mutate_source(self):
        with self.env(2) as env:
            env.reset();lab=EffectLab(env)
            source=lab.visible(0)
            with lab.fixture(position(encounter='barrel'),0) as fixture:
                lab.restore(1,fixture,1)
                self.assertEqual(lab.visible(0),source)
                outcomes=set()
                for seed in range(20):
                    lab.restore(1,fixture,seed)
                    before=lab.visible(1)
                    _,m=lab.observe();a=np.argmax(m,axis=1)
                    a[1]=env.action_names.index('interact')
                    env.step(a)
                    after=lab.visible(1)
                    import json
                    outcomes.add(json.dumps(after,sort_keys=True))
                    lab.restore(1,fixture,seed)
                    self.assertEqual(lab.visible(1),before)
                    _,m=lab.observe();a=np.argmax(m,axis=1);a[1]=env.action_names.index('interact')
                    env.step(a)
                    self.assertEqual(lab.visible(1),after)
                self.assertGreater(len(outcomes),1)

    def test_capture_retains_observation_and_episode_identity(self):
        with self.env() as env:
            o,m=env.reset();lab=EffectLab(env)
            indices=env.episode_indices.copy();next_index=env.next_episode_index
            with lab.capture(0) as snapshot:
                env.step(np.argmax(m,axis=1))
                lab.restore(0,snapshot,99);o2,m2=lab.observe()
                np.testing.assert_array_equal(o,o2);np.testing.assert_array_equal(m,m2)
                np.testing.assert_array_equal(indices,env.episode_indices)
                self.assertEqual(next_index,env.next_episode_index)

    def test_cross_budget_restore_rejected_without_mutation(self):
        with self.env() as source,self.env(budget=20) as dest:
            source.reset();dest.reset();a,b=EffectLab(source),EffectLab(dest)
            before=b.visible()
            with a.capture(0) as snapshot:
                with self.assertRaises(RuntimeError): b.restore(0,snapshot,20)
            self.assertEqual(before,b.visible())

    def test_new_library_matches_existing_simulation(self):
        baseline=Path(os.environ.get('SFLD_RL_BASELINE',str(ROOT/'build/Release/sfld_rl.dll')))
        if not baseline.is_file() or baseline.resolve()==LIB.resolve():self.skipTest('Separate baseline binary absent')
        with self.env(8) as new,self.env(8,library=baseline) as old:
            a,ma=new.reset();b,mb=old.reset()
            rng=np.random.default_rng(117)
            for _ in range(200):
                np.testing.assert_array_equal(a,b);np.testing.assert_array_equal(ma,mb)
                actions=np.array([rng.choice(np.flatnonzero(row)) for row in ma])
                aa=new.step(actions);bb=old.step(actions)
                for x,y in zip(aa[:5],bb[:5]):np.testing.assert_array_equal(x,y)
                for k in aa[5]:np.testing.assert_array_equal(aa[5][k],bb[5][k])
                a,ma=aa[:2];b,mb=bb[:2]
                ended=np.flatnonzero(aa[3]|aa[4])
                if len(ended):a,ma=new.reset_done(ended);b,mb=old.reset_done(ended)

    def test_curriculum_counter_reset(self):
        with self.env(16) as env:
            env.reset();curr=EffectCurriculum(env,12200001,fraction=1)
            try:
                curr.restore_resets(np.arange(16))
                self.assertTrue(curr.partial.all())
                np.testing.assert_array_equal(env._episode_info['completions'],np.zeros(16))
                curr.completed(np.arange(16))
                self.assertEqual(curr.counts()['completed_partial_scenarios'],16)
                self.assertEqual(curr.counts()['completed_whole_events'],0)
            finally:curr.close()

if __name__=='__main__':unittest.main()
