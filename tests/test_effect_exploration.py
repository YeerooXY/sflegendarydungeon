from pathlib import Path
import importlib.util
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
AVAILABLE=importlib.util.find_spec('torch') is not None
LIB=library('build-effects') if library('build-effects').is_file() else None
PARENT=ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt'
if AVAILABLE:
    import numpy as np
    import torch
    import train_rl
    from train_effect_exploration import ExploringEffects
    from sfld_rl.env import VectorEnv
    from sfld_rl.effect_lab import EffectLab,probe_cases

@unittest.skipUnless(AVAILABLE and LIB and PARENT.is_file(),'Optional Torch/native replay/checkpoint absent')
class EffectExplorationTests(unittest.TestCase):
    def test_exploration_preserves_greedy_and_matches_ppo_likelihood(self):
        torch.set_num_threads(2)
        saved,base=train_rl.load_checkpoint(PARENT,torch.device('cpu'))
        model=ExploringEffects(base,saved['metadata']['environment_signature']['schema'])
        with VectorEnv(LIB,[ROOT/'profiles/synthetic.profile'],36,budgets=[0],auto_reset=False) as env:
            env.reset();lab=EffectLab(env)
            for slot,case in enumerate(probe_cases()):
                with lab.fixture(case['progress'],0) as snapshot:lab.restore(slot,snapshot,2)
            o,k=lab.observe();o=torch.from_numpy(o);k=torch.from_numpy(k)
            with torch.no_grad():
                normal,_=base.distribution(o,k);dist,value=model.distribution(o,k)
                np.testing.assert_array_equal(normal.logits.argmax(1).numpy(),dist.logits.argmax(1).numpy())
                torch.testing.assert_close(value,base.values(o),rtol=0,atol=0)
                sampled,logs,_=model.actions(o,k)
            cached,_=model.ppo_distribution(o,model.ppo_masks(o,k))
            torch.testing.assert_close(cached.log_prob(sampled),logs,rtol=0,atol=0)
            index=next(i for i,c in enumerate(probe_cases()) if c['name']=='poison_oldest-cursed_chest-h50')
            interact=env.action_names.index('interact')
            self.assertGreater(float(dist.probs[index,interact]),.05)
            self.assertLess(float(normal.probs[index,interact]),1e-8)
            loss=-cached.log_prob(torch.full((36,),interact)).mean()+value.mean()
            loss.backward()
            self.assertTrue(all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in model.parameters()))

if __name__=='__main__':unittest.main()
