"""Custom curriculum imports retain visible-state validity and honest counts."""
from pathlib import Path
import sys
import tempfile
import unittest
import json
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
AVAILABLE=all(importlib.util.find_spec(n) is not None for n in ('numpy','torch'))
LIB=library('build-v0611')
if AVAILABLE:
    import numpy as np
    import train_rl
    from sfld_rl.env import VectorEnv
    from sfld_rl.effect_lab import EffectCurriculum, EffectLab
    from sfld_rl.interaction_cases import cases


@unittest.skipUnless(AVAILABLE and LIB.is_file(),'Optional Torch/current native environment absent')
class InteractionCurriculumTests(unittest.TestCase):
    def test_all_declared_states_are_valid_on_current_engine(self):
        panel=cases()
        self.assertEqual(len({c['name'] for c in panel}),len(panel))
        with VectorEnv(LIB,[ROOT/'profiles/synthetic.profile'],1,budgets=[1000]) as env:
            env.reset();lab=EffectLab(env)
            for case in panel:
                with self.subTest(case=case['name']):
                    with lab.fixture(case['progress'],1000) as snap:lab.restore(0,snap,42)
                    obs,mask=lab.observe()
                    self.assertTrue(mask.any());self.assertTrue(np.isfinite(obs).all())

    def test_custom_bank_is_used_and_partial_episodes_are_counted(self):
        panel=[next(c for c in cases() if c['name']=='trial-guaranteed-d4-h50')]
        with VectorEnv(LIB,[ROOT/'profiles/synthetic.profile'],8,budgets=[1000]) as env:
            env.reset();curriculum=EffectCurriculum(env,15500,fraction=1,cases=panel)
            try:
                curriculum.restore_resets(np.arange(8))
                for slot in range(8):
                    visible=curriculum.lab.visible(slot)
                    self.assertEqual(visible['room'],55);self.assertEqual(visible['trial_depth'],4)
                curriculum.completed(np.array([0,1]))
                self.assertEqual(curriculum.counts(),dict(partial_scenarios_started=8,completed_partial_scenarios=2,completed_whole_events=0))
            finally:curriculum.close()

    def test_declared_json_requires_curriculum_and_is_in_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'cases.json';p.write_text(json.dumps(cases()[:1]))
            args=train_rl.parse(['--mode','train','--output',folder,'--library',str(LIB),
                                 '--profile',str(ROOT/'profiles/synthetic.profile'),'--effect-curriculum','--curriculum-cases',str(p)])
            with train_rl.make_env(args,n_envs=1,profiles=[ROOT/'profiles/synthetic.profile']) as env:
                metadata=train_rl.source_metadata(args,env)
            self.assertEqual(metadata['curriculum_cases']['sha256'],train_rl.sha(p))


if __name__=='__main__':unittest.main()
