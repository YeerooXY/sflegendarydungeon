"""Training-time shop constraints preserve legality and PPO likelihoods."""
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
AVAILABLE=importlib.util.find_spec('torch') is not None
if AVAILABLE:
    import numpy as np
    import torch
    import train_rl
    from sfld_rl.shop_policy import with_shop, profile_config
    from sfld_rl.ppo import ActorCritic

@unittest.skipUnless((ROOT/'docs/pendant-adaptation-2026-10-11/control/selected-checkpoint.pt').exists(), 'Local research archive absent (not published)')
@unittest.skipUnless(AVAILABLE,'Optional Torch absent')
class ShopPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(2)
        cls.saved,cls.base=train_rl.load_checkpoint(ROOT/'docs/pendant-adaptation-2026-10-11/control/selected-checkpoint.pt',torch.device('cpu'))
        cls.schema=cls.saved['metadata']['environment_signature']['schema']
        cls.model=with_shop(cls.base,cls.schema,profile_config([ROOT/'profiles/synthetic.profile'],'shop5'))
        cls.oi=cls.model.oi;cls.ai=cls.model.ai

    def fixture(self, keys=4, used=0, visits=1):
        o=torch.zeros((1,len(self.oi)));m=torch.zeros((1,len(self.ai)),dtype=torch.bool)
        for name,value in {'phase.shop':1,'room_div100':.35,'hp':.7,'keys_div20':keys/20,
                           'reroll_shop_visits_div20':visits/20,'reroll_bank_used_div100':used/100}.items():o[0,self.oi[name]]=value
        for name in ('skip','buy_0','buy_1','reroll','heal_full'):m[0,self.ai[name]]=True
        return o,m

    def test_bank_carries_and_four_key_gate_is_exact(self):
        for keys,used,visits,forced in [(4,4,1,True),(4,5,1,False),(3,0,1,False),(4,5,2,True)]:
            o,m=self.fixture(keys,used,visits);out=self.model.constrained_masks(o,m)
            self.assertEqual(bool(out[0,self.ai['reroll']]),forced)
            self.assertEqual(int(out.sum()),1 if forced else 4)
            self.assertFalse(bool((out&~m).any()))

    def test_good_offer_stops_before_five_and_must_be_affordable(self):
        o,m=self.fixture()
        for name,value in {'offers.0.effect.kind.one_hit':1,'offers.0.effect.remaining_div10':.8}.items():o[0,self.oi[name]]=value
        out=self.model.constrained_masks(o,m)
        self.assertTrue(out[0,self.ai['buy_0']]);self.assertEqual(int(out.sum()),1)
        m[0,self.ai['buy_0']]=False
        out=self.model.constrained_masks(o,m)
        self.assertTrue(out[0,self.ai['reroll']]);self.assertEqual(int(out.sum()),1)

    def test_curse_shop_other_phases_and_healing_are_preserved(self):
        o,m=self.fixture(keys=3);out=self.model.constrained_masks(o,m)
        self.assertTrue(out[0,self.ai['heal_full']])
        o[0,self.oi['phase.shop']]=0;o[0,self.oi['phase.curse_shop']]=1
        torch.testing.assert_close(self.model.constrained_masks(o,m),m)

    def test_sample_and_update_use_identical_likelihood(self):
        o,m=self.fixture();o=o.repeat(32,1);m=m.repeat(32,1)
        actions,logs,_=self.model.actions(o,m)
        self.assertTrue(bool((actions==self.ai['reroll']).all()))
        dist,_=self.model.distribution(o,m)
        torch.testing.assert_close(logs,dist.log_prob(actions))
        loss=-(dist.log_prob(actions)).mean();loss.backward()
        self.assertTrue(all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in self.model.parameters()))

    def test_parameters_and_nonshop_predictions_are_unchanged(self):
        o,m=self.fixture();o[0,self.oi['phase.shop']]=0
        a,va=self.base.distribution(o,m);b,vb=self.model.distribution(o,m)
        torch.testing.assert_close(a.logits,b.logits,rtol=0,atol=0)
        torch.testing.assert_close(va,vb,rtol=0,atol=0)
        for a,b in zip(self.base.parameters(),self.model.parameters()):self.assertIs(a,b)
        unwrapped=with_shop(self.model,self.schema,None)
        for key,value in self.base.state_dict().items():torch.testing.assert_close(value,unwrapped.state_dict()[key],rtol=0,atol=0)

    def test_cached_ppo_masks_preserve_logits_values_and_gradients(self):
        o,m=self.fixture(keys=3)
        o=o.repeat(8,1);m=m.repeat(8,1)
        o[0,self.oi['keys_div20']]=.2
        o[1,self.oi['phase.shop']]=0
        a,va=self.model.distribution(o,m)
        b,vb=self.model.ppo_distribution(o,self.model.ppo_masks(o,m))
        torch.testing.assert_close(a.logits,b.logits,rtol=0,atol=0)
        torch.testing.assert_close(va,vb,rtol=0,atol=0)
        params=list(self.model.parameters())
        ga=torch.autograd.grad(a.entropy().sum()+va.sum(),params)
        gb=torch.autograd.grad(b.entropy().sum()+vb.sum(),params)
        for x,y in zip(ga,gb):torch.testing.assert_close(x,y,rtol=0,atol=0)

    @unittest.skipUnless((ROOT/'docs/effect-slots-2026-10-11/source/tools/sfld_rl/shop_policy.py').exists(), 'Local research archive absent (not published)')
    def test_fast_masks_match_frozen_torch_rules(self):
        # Independent pre-optimization implementation, preserved with the study.
        path=ROOT/'docs/effect-slots-2026-10-11/source/tools/sfld_rl/shop_policy.py'
        spec=importlib.util.spec_from_file_location('sfld_rl._reference_shop_policy',path)
        reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
        previous=reference.ShopPolicyActor(self.base,self.schema,self.model.shop_config)
        generator=torch.Generator().manual_seed(718331)
        obs=torch.rand((8192,len(self.oi)),generator=generator)
        masks=torch.rand((8192,len(self.ai)),generator=generator)>.4
        # Include exact budget/key/duration boundaries and offer ties.
        for name in ('keys_div20','reroll_shop_visits_div20','reroll_bank_used_div100'):
            obs[:,self.oi[name]]=torch.round(obs[:,self.oi[name]]*10)/10
        original_obs=obs.clone();original_masks=masks.clone()
        for device in ('cpu','cuda') if torch.cuda.is_available() else ('cpu',):
            self.model.to(device);previous.to(device)
            o,m=obs.to(device),masks.to(device)
            actual=self.model.constrained_masks(o,m)
            expected=previous.constrained_masks(o,m)
            torch.testing.assert_close(actual,expected,rtol=0,atol=0)
            torch.testing.assert_close(o,original_obs.to(device),rtol=0,atol=0)
            torch.testing.assert_close(m,original_masks.to(device),rtol=0,atol=0)
        self.model.cpu();previous.cpu()

if __name__=='__main__':unittest.main()
