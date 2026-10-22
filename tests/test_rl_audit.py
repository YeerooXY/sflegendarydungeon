"""Accounting tests for descriptive RL decision audits, without GPU/native work."""
import importlib.util
from pathlib import Path
import sys
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'torch'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
if AVAILABLE:
    import numpy as np
    import audit_rl_policy as audit


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch dependencies are not installed')
class BehaviorAuditTests(unittest.TestCase):
    def state(self, **changes):
        return {'budget': 50, 'episode_index': 4, 'phase': 'gems', 'room': 51,
                'stage': 3, 'hp': .4, 'keys': 6, 'run_number': 2, 'run_type': 'later',
                'owned_gems': ['moonstone'], 'gem_offers': ['rabbit', 'greasy', 'hero'], **changes}

    def test_gem_exposure_counts_actual_picks_not_healing_revisits(self):
        counts = audit.BehaviorAudit()
        state = self.state()
        counts.record('fixture', state, 'heal_step', 'gem_0')
        counts.record('fixture', state, 'gem_1', 'gem_0')
        result = counts.export()
        self.assertEqual(result['summaries'][0]['decisions'], 2)
        row = result['tables']['gems'][0]
        self.assertEqual(row['decisions'], 1)
        self.assertEqual(row['offered'], {'greasy': 1, 'hero': 1, 'rabbit': 1})
        self.assertEqual(row['picked'], {'greasy': 1})
        self.assertEqual(row['teacher_picked'], {'rabbit': 1})
        self.assertEqual(row['disagreement_rate'], 1)

    def test_all_action_denominators_and_context_strata_are_preserved(self):
        counts = audit.BehaviorAudit()
        offers = [{'kind': 'one_hit', 'magnitude': 1., 'remaining': 8, 'keys': 4},
                  {'kind': 'elixir', 'magnitude': .5, 'remaining': 1, 'keys': 3}]
        shop = self.state(phase='shop', offers=offers)
        counts.record('fixture', shop, 'reroll', 'buy_0')
        counts.record('fixture', shop, 'buy_1', 'buy_0')
        counts.record('fixture', {**shop, 'keys': 3}, 'skip', 'skip')
        monster = self.state(phase='encounter', encounter='monster', one_hit=False)
        counts.record('fixture', monster, 'heal_step', 'flee')
        counts.record('fixture', monster, 'flee', 'flee')
        barrel = self.state(phase='encounter', encounter='barrel', one_hit=False)
        counts.record('fixture', barrel, 'interact', 'skip')
        counts.record('fixture', {**barrel, 'owned_gems': ['time_traveler']}, 'skip', 'skip')
        result = counts.export()
        summary = result['summaries'][0]
        self.assertEqual(summary['decisions'], 7)
        self.assertEqual(summary['disagreements'], 4)
        self.assertEqual(len(result['tables']['shops']), 2)
        self.assertEqual(sum(r['decisions'] for r in result['tables']['combat']), 1)
        self.assertEqual(len(result['tables']['barrels']), 2)
        for row in [summary] + [r for rows in result['tables'].values() for r in rows]:
            self.assertEqual(sum(row['actions'].values()), row['decisions'])
            self.assertEqual(sum(row['teacher_actions'].values()), row['decisions'])
            self.assertLessEqual(row['disagreements'], row['decisions'])

    def test_float32_health_boundaries_and_sparse_teacher_gem_counts(self):
        self.assertEqual(audit.hp_bin(np.float32(.2)), '20-40%')
        self.assertEqual(audit.hp_bin(np.float32(.4)), '40-60%')
        self.assertEqual(audit.hp_bin(np.float32(.799)), '60-80%')
        self.assertEqual(audit.hp_bin(1.), '80-100%')
        counts = audit.BehaviorAudit()
        counts.record('fixture', self.state(), 'gem_2', 'heal_full')
        row = counts.export()['tables']['gems'][0]
        self.assertEqual(row['teacher_actions'], {'heal_full': 1})
        self.assertEqual(row['teacher_picked'], {})

    def test_absent_teacher_is_not_reported_as_disagreement(self):
        counts = audit.BehaviorAudit()
        counts.record('fixture', self.state(), 'gem_0', None)
        result = counts.export()['summaries'][0]
        self.assertEqual(result['decisions'], 1)
        self.assertEqual(result['teacher_decisions'], 0)
        self.assertEqual(result['teacher_actions'], {})
        self.assertIsNone(result['disagreement_rate'])

    def test_decoder_uses_names_and_preserves_actual_state(self):
        fields = {'hp': .62, 'keys_div20': .35, 'room_div100': .76,
                  'run_number_div10': .2, 'first_event_run': 0., 'budget_div500': .1,
                  'phase.doors': 1., 'phase.encounter': 0.,
                  'doors.0.kind.mystery': 1., 'doors.0.trap': 1.,
                  'doors.1.kind.monster': 1., 'doors.1.trap': 0.}
        names = list(reversed(fields))
        decoder = audit.Decoder(names, ['door_0', 'door_1'])
        row = np.array([fields[name] for name in names], np.float32)
        state = decoder.state(row, 50, 9)
        self.assertEqual((state['room'], state['stage'], state['keys']), (76, 4, 7))
        self.assertEqual(state['run_type'], 'later')
        self.assertEqual(state['doors'], [{'kind': 'mystery', 'trap': True}, {'kind': 'monster', 'trap': False}])
        self.assertAlmostEqual(state['hp'], .62, places=6)

    def test_invalid_cli_cohorts_are_rejected(self):
        base = ['--checkpoint', 'fixture.pt', '--output', 'audit.json']
        for extra in (['--budgets', '0,0'], ['--events', '0'], ['--n-envs', '0'], ['--sample-limit', '-1']):
            with self.assertRaises(SystemExit):
                audit.parse(base + extra)
        args = audit.parse(base)
        self.assertEqual((args.events, args.n_envs, args.seed), (256, 128, 9200001))
        self.assertEqual(audit.NAMESPACE, 0xD5)


if __name__ == '__main__':
    unittest.main()
