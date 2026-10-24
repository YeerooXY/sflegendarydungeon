"""The PyTorch-free adviser copy must choose exactly what the checkpoint chooses."""
from pathlib import Path
import importlib.util
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from engine_build import MISSING, library  # noqa: E402
from sfld_rl.numpy_policy import export, load, sha  # noqa: E402
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA  # noqa: E402

TORCH = importlib.util.find_spec('torch') is not None


class NumpyPolicyTests(unittest.TestCase):
    def test_deployed_copy_matches_its_checkpoint_hash(self):
        info, policy = load(CHECKPOINT, CHECKPOINT_SHA)
        self.assertEqual(len(info['observation_names']), policy.layers[0][0].shape[1])
        self.assertEqual(len(info['action_names']), policy.actor[0].shape[0])
        with self.assertRaisesRegex(ValueError, 'different checkpoint'):
            load(CHECKPOINT, '0'*64)

    @unittest.skipUnless(TORCH, 'PyTorch absent')
    def test_export_is_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = export(CHECKPOINT, Path(folder)/'copy.npz')
            with np.load(path) as fresh, np.load(CHECKPOINT.with_suffix('.npz')) as deployed:
                for key in deployed.files:
                    np.testing.assert_array_equal(fresh[key], deployed[key])

    @unittest.skipUnless(TORCH, 'PyTorch absent')
    @unittest.skipUnless(library().is_file(), MISSING)
    def test_greedy_actions_equal_the_torch_actor_on_simulated_states(self):
        import torch
        import train_rl
        from sfld_rl.env import VectorEnv
        self.assertEqual(sha(CHECKPOINT), CHECKPOINT_SHA)
        _, model = train_rl.load_checkpoint(CHECKPOINT, torch.device('cpu'))
        _, policy = load(CHECKPOINT, CHECKPOINT_SHA)
        rng = np.random.default_rng(20261024);checked = 0
        with VectorEnv(library(), [ROOT/'profiles/mushroom-school-2024.profile'], 64, budgets=[0, 100, 1000], seed=77) as env:
            obs, masks = env.reset()
            for _ in range(400):
                with torch.inference_mode():
                    expected = model.actions(torch.as_tensor(obs), torch.as_tensor(masks), greedy=True)[0].numpy()
                np.testing.assert_array_equal(policy.greedy(obs, masks), expected)
                checked += len(obs)
                # Mix the adviser's choice with random legal moves to reach varied states.
                chosen = np.array([rng.choice(np.flatnonzero(m)) for m in masks])
                chosen = np.where(rng.random(len(chosen)) < .7, expected, chosen)
                obs, masks, *_ = env.step(chosen)
        self.assertGreaterEqual(checked, 25000)


if __name__ == '__main__':
    unittest.main()
