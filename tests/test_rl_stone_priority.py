"""A stone constraint must be identical in sampling, PPO updates and reloads."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
AVAILABLE = all(importlib.util.find_spec(n) for n in ('torch', 'numpy'))
if AVAILABLE:
    import torch
    import train_rl
    from sfld_rl.ppo import ActorCritic, PPOConfig
    from sfld_rl.stone_priority import with_priority


@unittest.skipUnless((ROOT/'docs/shop-arena-2026-10-11/schema.json').exists(), 'Local research archive absent (not published)')
@unittest.skipUnless(AVAILABLE, 'Optional Torch dependencies missing')
class StonePriorityTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        self.schema = json.loads((ROOT / 'docs/shop-arena-2026-10-11/schema.json').read_text())
        self.oi = {n: i for i, n in enumerate(self.schema['observation_names'])}
        self.ai = {n: i for i, n in enumerate(self.schema['action_names'])}
        self.base = ActorCritic(len(self.oi), len(self.ai), (8,))
        self.obs = torch.zeros(1, len(self.oi))
        self.masks = torch.zeros(1, len(self.ai), dtype=torch.bool)
        self.obs[0, self.oi['phase.gems']] = 1
        for i, gem in enumerate(('pendant', 'greasy', 'hero')):
            self.obs[0, self.oi[f'gem_offers.{i}.{gem}']] = 1
            self.masks[0, self.ai[f'gem_{i}']] = True

    def test_priority_changes_distribution_and_keeps_legal_healing(self):
        model = with_priority(self.base, 'pendant_priority', self.schema)
        obs, masks = self.obs.clone(), self.masks.clone()
        actions, logs, _ = model.actions(self.obs, self.masks)
        self.assertEqual(actions.item(), self.ai['gem_0'])
        self.assertEqual(logs.item(), 0)
        dist, _ = model.distribution(self.obs, self.masks)
        torch.testing.assert_close(logs, dist.log_prob(actions))
        self.assertEqual(dist.probs[0, self.ai['gem_1']].item(), 0)
        self.masks[0, self.ai['heal_full']] = True
        self.assertTrue(model.constrained_masks(self.obs, self.masks)[0, self.ai['heal_full']])
        torch.testing.assert_close(self.obs, obs)
        torch.testing.assert_close(self.masks[:, :self.ai['heal_full']], masks[:, :self.ai['heal_full']])
        self.obs[0, self.oi['gem_offers.2.hero']] = 0
        self.obs[0, self.oi['gem_offers.2.moonstone']] = 1
        constrained = model.constrained_masks(self.obs, self.masks)
        self.assertTrue(constrained[0, self.ai['gem_2']])
        self.assertFalse(constrained[0, self.ai['gem_0']])
        self.obs[0, self.oi['gem_offers.1.greasy']] = 0
        self.obs[0, self.oi['gem_offers.1.rabbit']] = 1
        self.assertTrue(model.constrained_masks(self.obs, self.masks)[0, self.ai['gem_1']])

    def test_conditional_rule_other_phases_and_unavailable_pendant(self):
        model = with_priority(self.base, 'pendant_escape', self.schema)
        torch.testing.assert_close(model.constrained_masks(self.obs, self.masks), self.masks)
        self.obs[0, self.oi['owned_gem.rabbit']] = 1
        self.assertFalse(model.constrained_masks(self.obs, self.masks)[0, self.ai['gem_1']])
        self.masks[0, self.ai['gem_0']] = False
        torch.testing.assert_close(model.constrained_masks(self.obs, self.masks), self.masks)
        self.masks[0, self.ai['gem_0']] = True
        self.obs[0, self.oi['phase.gems']] = 0
        torch.testing.assert_close(model.constrained_masks(self.obs, self.masks), self.masks)

    def test_checkpoint_retains_constraint_and_shared_parameters(self):
        model = with_priority(self.base, 'pendant_priority', self.schema)
        self.assertIs(next(model.parameters()), next(self.base.parameters()))
        optimizer = torch.optim.Adam(model.parameters())
        metadata = {'stone_priority': 'pendant_priority', 'environment_signature': {'schema': self.schema}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'priority.pt'
            train_rl.checkpoint(path, model, optimizer, {}, metadata, PPOConfig())
            _, restored = train_rl.load_checkpoint(path, 'cpu')
            self.assertEqual(restored.priority_mode, 'pendant_priority')
            torch.testing.assert_close(model.distribution(self.obs, self.masks)[0].probs,
                                       restored.distribution(self.obs, self.masks)[0].probs)
            ordinary = with_priority(restored, 'learned', self.schema)
            self.assertGreater(ordinary.distribution(self.obs, self.masks)[0].probs[0, self.ai['gem_1']], 0)

    def test_new_greed_priority_preserves_historical_mode(self):
        self.obs[0, self.oi['gem_offers.2.hero']] = 0
        self.obs[0, self.oi['gem_offers.2.greed']] = 1
        legacy = with_priority(self.base, 'pendant_priority', self.schema)
        revised = with_priority(self.base, 'pendant_greed_priority', self.schema)
        self.assertEqual(legacy.actions(self.obs, self.masks)[0].item(), self.ai['gem_0'])
        self.assertEqual(revised.actions(self.obs, self.masks)[0].item(), self.ai['gem_2'])
        self.obs[0, self.oi['gem_offers.1.greasy']] = 0
        self.obs[0, self.oi['gem_offers.1.moonstone']] = 1
        self.assertEqual(revised.actions(self.obs, self.masks)[0].item(), self.ai['gem_1'])
        self.obs[0, self.oi['gem_offers.0.pendant']] = 0
        self.obs[0, self.oi['gem_offers.0.rabbit']] = 1
        # A menu without Pendant remains unconstrained.
        torch.testing.assert_close(revised.constrained_masks(self.obs, self.masks), self.masks)


if __name__ == '__main__':
    unittest.main()
