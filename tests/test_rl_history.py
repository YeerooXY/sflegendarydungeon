"""Checkpoint-transfer and pre-final selection regressions (optional Torch)."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
AVAILABLE = all(importlib.util.find_spec(n) for n in ('torch', 'numpy'))
if AVAILABLE:
    import torch
    from sfld_rl.ppo import ActorCritic
    from migrate_rl_history import check_schema, expand_checkpoint
    from train_rl_history_study import select_candidates, BUDGETS
    from publish_rl_history import paired_comparison, check_csv, CSV_FIELDS


@unittest.skipUnless(AVAILABLE, 'Optional training dependencies are absent')
class HistoryTransfer(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1); torch.manual_seed(717)
        self.old = {'engine': 'old', 'observation_names':
                    [f'x{i}' for i in range(414)] + [f'owned_gem.g{i}' for i in range(23)],
                    'action_names': ['fight', 'skip'], 'reward': 'completion_increment'}
        self.new = {**self.old, 'engine': 'new', 'observation_names':
                    self.old['observation_names'] + [f'offered_gem.g{i}' for i in range(23)]}
        self.model = ActorCritic(437, 2, [8, 8])
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=1e-4)
        obs = torch.randn(4, 437)
        logits, values = self.model(obs)
        (logits.square().sum() + values.square().sum()).backward()
        self.optimizer.step()
        self.saved = {'architecture': self.model.architecture, 'model': self.model.state_dict(),
                      'optimizer': self.optimizer.state_dict(), 'state': {'steps': 1234},
                      'metadata': {'environment_signature': {'schema': self.old}}}

    def test_preserves_policy_value_and_old_optimizer_moments(self):
        result = expand_checkpoint(self.saved, self.new)
        new = ActorCritic(**result['architecture']); new.load_state_dict(result['model'])
        observations = torch.randn(100, 437)
        history = torch.randint(0, 2, (100, 23)).float()
        for a, b in zip(self.model(observations), new(torch.cat((observations, history), dim=1))):
            torch.testing.assert_close(a, b, rtol=1e-6, atol=1e-6)
        old_state = self.saved['optimizer']['state'][0]
        new_state = result['optimizer']['state'][0]
        for key in ('exp_avg', 'exp_avg_sq'):
            torch.testing.assert_close(old_state[key], new_state[key][:, :437], rtol=0, atol=0)
            self.assertEqual(torch.count_nonzero(new_state[key][:, 437:]).item(), 0)
        self.assertEqual(old_state['step'], new_state['step'])
        self.assertEqual(result['state']['steps'], 1234)
        # It must be a usable Adam transfer, not just a loadable network.
        optimizer = torch.optim.Adam(new.parameters()); optimizer.load_state_dict(result['optimizer'])
        optimizer.zero_grad(); new.values(torch.randn(3, 460)).sum().backward(); optimizer.step()
        self.assertGreater(torch.count_nonzero(new.trunk[0].weight[:, 437:]).item(), 0)
        self.assertEqual(self.saved['model']['trunk.0.weight'].shape[1], 437)

    def test_refuses_reordered_prefix_unrelated_changes_and_wrong_history(self):
        for field in ('prefix', 'history', 'reward', 'action_names'):
            bad = copy.deepcopy(self.new)
            if field == 'prefix': bad['observation_names'][0:2] = ['x1', 'x0']
            elif field == 'history': bad['observation_names'][-1] = 'hidden_future_gem'
            elif field == 'reward': bad['reward'] = 'shaped'
            else: bad['action_names'].reverse()
            with self.assertRaises(ValueError): check_schema(self.old, bad)

    def test_rejects_corrupt_optimizer_shape(self):
        bad = copy.deepcopy(self.saved)
        bad['optimizer']['state'][0]['exp_avg'] = torch.zeros(8, 436)
        with self.assertRaisesRegex(ValueError, 'Adam'): expand_checkpoint(bad, self.new)

    def test_selection_keeps_parent_on_ties_and_allows_budget_specialists(self):
        parent = dict(name='parent', selection_score=3., budget_means={str(b): 3. for b in BUDGETS})
        tied = dict(name='tied', selection_score=3., budget_means={str(b): 3. for b in BUDGETS})
        special = dict(name='special', selection_score=3., budget_means={str(b): 2. if b == 0 else 3.25 for b in BUDGETS})
        chosen = select_candidates([parent, tied, special])
        self.assertEqual(chosen['global']['name'], 'parent')
        self.assertEqual(chosen['per_budget']['0']['name'], 'parent')
        self.assertEqual(chosen['per_budget']['500']['name'], 'special')

    def test_comparison_pairs_indices_not_finish_order(self):
        def row(i, n):
            return dict(budget=0, event_index=i, completions=n, mushrooms=0,
                        recovery_mushrooms=0, reroll_mushrooms=0)
        old = {'outcomes': [row(0, 2), row(1, 5), row(2, 3)]}
        new = {'outcomes': [row(2, 4), row(0, 3), row(1, 6)]}
        result = paired_comparison(old, new, 0, 'new')
        self.assertEqual(result['paired_improvement']['mean'], 1)
        self.assertEqual(result['paired_improvement']['standard_error'], 0)
        self.assertEqual(result['paired_improvement']['normal_99_ci'], [1, 1])
        with self.assertRaises(ValueError): paired_comparison(old, {'outcomes': [row(0, 3)]}, 0, 'bad')

    def test_csv_checks_missing_duplicate_and_changed_outcomes(self):
        rows = {'parent': {'outcomes': [dict(budget=0, event_index=0, completions=2,
                mushrooms=0, recovery_mushrooms=0, reroll_mushrooms=0,
                elapsed_hours=168., actions=800, invalid_actions=0, terminated=True, truncated=False)]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outcomes.csv'
            header = ','.join(CSV_FIELDS) + '\n'
            row = 'parent,0,0,2,0,0,0,168,800,0,True,False\n'
            path.write_text(header + row)
            check_csv(path, rows)
            for content in (header, header + row.replace(',2,', ',3,'), header + row + row,
                            'policy,budget,event_index\nparent,0,0\n'):
                path.write_text(content)
                with self.assertRaises(ValueError): check_csv(path, rows)


if __name__ == '__main__':
    unittest.main()
