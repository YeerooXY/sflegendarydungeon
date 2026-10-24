"""Runtime changes preserve masks, sampled policy outputs and episode identities."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
AVAILABLE = importlib.util.find_spec('torch') is not None
LIBRARY = library('build-perf') if library('build-perf').is_file() else None
if AVAILABLE:
    import numpy as np
    import torch
    import train_rl
    from sfld_rl.env import VectorEnv
    from sfld_rl.shop_policy import ShopPolicyActor


@unittest.skipUnless(AVAILABLE and LIBRARY, 'Optional RL environment absent')
class RuntimeTests(unittest.TestCase):
    @unittest.skipUnless((ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt').exists(), 'Local research archive absent (not published)')
    def test_host_shop_masks_preserve_sampled_and_greedy_outputs(self):
        checkpoint = ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt'
        for device in ['cpu'] + (['cuda'] if torch.cuda.is_available() else []):
            _, model = train_rl.load_checkpoint(checkpoint, torch.device(device)); model.eval()
            with VectorEnv(LIBRARY, [ROOT/'profiles/synthetic.profile'], 64, budgets=[0,100,1000], seed=43219) as env, torch.inference_mode():
                obs, masks = env.reset()
                for step in range(70):
                    for greedy in (True, False):
                        torch.manual_seed(step+31)
                        expected = model.actions(train_rl.tensor(obs, device), train_rl.tensor(masks, device, torch.bool), greedy=greedy)
                        torch.manual_seed(step+31)
                        actual = train_rl.policy_actions(model, obs, masks, device, greedy=greedy)
                        for a,b in zip(expected, actual): torch.testing.assert_close(a,b,rtol=0,atol=0)
                    obs,masks,*_ = env.step(actual[0].cpu().numpy())

    @unittest.skipUnless((ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt').exists(), 'Local research archive absent (not published)')
    def test_specialized_action_wrapper_is_not_bypassed(self):
        class Specialized(ShopPolicyActor):
            calls = 0
            def actions(self, *args, **kwargs):
                self.calls += 1
                return super().actions(*args, **kwargs)
        for device in ['cpu'] + (['cuda'] if torch.cuda.is_available() else []):
            _, base = train_rl.load_checkpoint(ROOT/'docs/rl-current-2026-10-11/shop-aware/checkpoint-000004194304.pt', torch.device(device))
            wrapper = Specialized(base, {'observation_names': list(base.oi), 'action_names': list(base.ai)}, base.shop_config)
            with VectorEnv(LIBRARY,[ROOT/'profiles/synthetic.profile'],2) as env, torch.inference_mode():
                obs,masks=env.reset()
                train_rl.policy_actions(wrapper,obs,masks,device,greedy=True)
                self.assertEqual(wrapper.calls,1)

    def test_metrics_are_private_and_auto_reset_identity_is_current(self):
        for automatic in (False, True):
            with VectorEnv(LIBRARY,[ROOT/'profiles/synthetic.profile'],4,auto_reset=automatic,deadline_hours=.001) as env:
                obs,masks=env.reset(); observed_terminal=False
                for _ in range(12):
                    before=env.episode_indices.copy()
                    obs,masks,reward,done,trunc,info=env.step(masks.argmax(1))
                    np.testing.assert_array_equal(info['episode_index'],before)
                    identities=env.episode_indices.copy()
                    if automatic:
                        self.assertTrue(np.all(identities[done]>before[done]))
                    else: np.testing.assert_array_equal(identities,before)
                    info['episode_index'][:]=-1
                    np.testing.assert_array_equal(env.episode_indices,identities)
                    old_info=copy.deepcopy(info)
                    if done.any():
                        observed_terminal=True
                        if not automatic: obs,masks=env.reset_done(np.flatnonzero(done))
                    for key in old_info: np.testing.assert_array_equal(info[key],old_info[key])
                self.assertTrue(observed_terminal)


if __name__=='__main__': unittest.main()
