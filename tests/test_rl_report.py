"""Report integrity checks use tiny fixtures and no optional ML dependencies."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import render_rl_study as report


def fixture(values=(1, 2, 4), *, policy='greedy', budget=20):
    rows = [{'profile': 'profiles/synthetic.profile', 'budget': budget, 'event_index': i,
             'terminated': True, 'truncated': False, 'completions': n, 'mushrooms': 10,
             'recovery_mushrooms': 8, 'reroll_mushrooms': 2, 'elapsed_hours': 167.9999,
             'invalid_actions': 0, 'actions': 250} for i, n in enumerate(values)]
    env = {'budgets': [budget], 'profiles': [{'path': 'profiles/synthetic.profile', 'sha256': 'a' * 64}],
           'library_sha256': 'b' * 64, 'schema_sha256': 'c' * 64, 'seed_namespace': 195,
           'deadline_hours': 168., 'max_actions': 100000, 'initial_episode_index': 0}
    stats = report.estimate(values)
    summary = {'profile': rows[0]['profile'], 'budget': budget, 'events': len(rows), 'failed_events': 0,
               'completions': stats, 'mean_mushrooms': 10., 'mean_recovery_mushrooms': 8.,
               'mean_reroll_mushrooms': 2., 'invalid_actions': 0, 'truncations': 0}
    return {'root_seed': '9100002', 'namespace': 'final', 'namespace_byte': 195,
            'action_policy': policy, 'summaries': [summary], 'environment_metadata': [env],
            'outcomes': rows, 'selection_score': stats['mean'], 'checkpoint_sha256': 'd' * 64}


class RLReportTests(unittest.TestCase):
    def test_pairing_uses_exact_indices_even_when_rows_arrive_out_of_order(self):
        learner, teacher = fixture(), fixture((2, 2, 2), policy='teacher')
        learner['outcomes'].reverse()
        result = report.paired_results(learner, teacher)[0]
        self.assertEqual(result['paired_difference'], report.estimate([-1, 0, 2]))
        self.assertEqual(result['events'], 3)
        self.assertEqual(result['learner_spending']['mushrooms'], 10)

    def test_seed_namespace_profile_binary_and_horizon_must_match(self):
        learner = fixture()
        mutations = [('seed', lambda r: r.update(root_seed='9100003')),
                     ('namespace', lambda r: (r.update(namespace_byte=178), r['environment_metadata'][0].update(seed_namespace=178))),
                     ('namespace_name', lambda r: r.update(namespace='validation')),
                     ('profile', lambda r: r['environment_metadata'][0]['profiles'][0].update(sha256='e' * 64)),
                     ('library', lambda r: r['environment_metadata'][0].update(library_sha256='e' * 64)),
                     ('schema', lambda r: r['environment_metadata'][0].update(schema_sha256='e' * 64)),
                     ('horizon', lambda r: r['environment_metadata'][0].update(deadline_hours=169.)),
                     ('action_cap', lambda r: r['environment_metadata'][0].update(max_actions=200000))]
        for label, change in mutations:
            teacher = fixture(policy='teacher'); change(teacher)
            with self.subTest(label=label), self.assertRaises(ValueError):
                report.paired_results(learner, teacher)

    def test_missing_duplicate_extra_and_wrong_group_rows_are_rejected(self):
        mutations = [('missing', lambda r: r['outcomes'].pop()),
                     ('duplicate', lambda r: r['outcomes'].append(copy.deepcopy(r['outcomes'][0]))),
                     ('index', lambda r: r['outcomes'][0].update(event_index=3)),
                     ('profile', lambda r: r['outcomes'][0].update(profile='other.profile')),
                     ('budget', lambda r: r['outcomes'][0].update(budget=50))]
        for label, change in mutations:
            value = fixture(); change(value)
            with self.subTest(label=label), self.assertRaises(ValueError):
                report.validate_evaluation(value)

    def test_invalid_truncated_unfinished_events_and_overspending_are_rejected(self):
        changes = [{'invalid_actions': 1}, {'truncated': True}, {'terminated': False},
                   {'mushrooms': 21}, {'recovery_mushrooms': 0}, {'elapsed_hours': 169.},
                   {'elapsed_hours': 0}, {'completions': True}, {'completions': 2.5}]
        for change in changes:
            value = fixture(); value['outcomes'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                report.validate_evaluation(value)

    def test_summary_and_selection_claims_are_recomputed(self):
        mutations = [lambda r: r['summaries'][0]['completions'].update(mean=7),
                     lambda r: r['summaries'][0]['completions'].update(standard_error=0),
                     lambda r: r['summaries'][0]['completions'].update(normal_95_ci=[0, 0]),
                     lambda r: r['summaries'][0].update(mean_reroll_mushrooms=0),
                     lambda r: r['summaries'][0].update(failed_events=1),
                     lambda r: r.update(selection_score=9)]
        for mutate in mutations:
            value = fixture(); mutate(value)
            with self.assertRaises(ValueError):
                report.validate_evaluation(value)

    def test_final_event_counts_must_match(self):
        with self.assertRaisesRegex(ValueError, 'event counts'):
            report.paired_results(fixture(), fixture((2, 2), policy='teacher'))

    def test_report_json_cannot_break_out_of_script(self):
        rendered = report.render({'hostile': '</script><script>alert(1)</script>'})
        self.assertNotIn('</script><script>alert(1)', rendered)
        self.assertIn('\\u003c/script>', rendered)

    def test_common_selection_rejects_tampering_or_wrong_winner(self):
        with tempfile.TemporaryDirectory(prefix='sfld-rl-report-') as directory:
            base = Path(directory)
            candidates = []
            for name, values, fingerprint in [('a', (1, 1, 2), 'd'), ('b', (2, 2, 3), 'e')]:
                evaluation = fixture(values)
                evaluation['checkpoint_sha256'] = fingerprint * 64
                path = base / (name + '.json'); path.write_text(json.dumps(evaluation), encoding='utf-8')
                candidates.append({'name': name, 'validation_file': path.name,
                                   'validation_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                   'checkpoint_sha256': evaluation['checkpoint_sha256'],
                                   'selection_score': evaluation['selection_score']})
            manifest = {'schema': 'sfld-ppo-common-validation-v1', 'seed': '9100002', 'namespace': 'final',
                        'namespace_byte': 195, 'events_per_budget': 3, 'budgets': [20],
                        'candidates': candidates, 'selected': candidates[1]}
            common_path = base / 'common.json'; common_path.write_text(json.dumps(manifest), encoding='utf-8')
            artifacts = report.Artifacts(base / 'report.html')
            self.assertEqual(report.read_common(common_path, artifacts)['selected']['name'], 'b')
            manifest['selected'] = candidates[0]
            common_path.write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'not the common-validation winner'):
                report.read_common(common_path, artifacts)
            manifest['selected'] = candidates[1]
            common_path.write_text(json.dumps(manifest), encoding='utf-8')
            (base / 'a.json').write_text(json.dumps(fixture((9, 9, 9))), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
                report.read_common(common_path, artifacts)

    def test_jsonl_ignores_only_unfinished_last_line(self):
        with tempfile.TemporaryDirectory(prefix='sfld-rl-report-') as directory:
            path = Path(directory) / 'curve.jsonl'
            artifacts = report.Artifacts(Path(directory) / 'report.html')
            path.write_text('{"steps":1}\n{"steps":', encoding='utf-8')
            self.assertEqual(artifacts.read(path, jsonl=True), [{'steps': 1}])
            path.write_text('{"steps":1}\n{"steps":\n', encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):
                artifacts.read(path, jsonl=True)

    def test_complete_study_connects_frozen_candidate_to_final_pair(self):
        with tempfile.TemporaryDirectory(prefix='sfld-rl-report-') as directory:
            base = Path(directory); run = base / 'warm'; run.mkdir()
            def save(path, value):
                path.write_text(json.dumps(value), encoding='utf-8')
            checkpoint = run / 'checkpoint.pt'; checkpoint.write_bytes(b'fixture checkpoint')
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            (base / 'selected-checkpoint.pt').write_bytes(checkpoint.read_bytes())
            learner, teacher = fixture(), fixture((2, 2, 2), policy='teacher')
            learner['checkpoint_sha256'] = digest
            validation = copy.deepcopy(learner)
            validation.update(root_seed='9100001', namespace='validation', namespace_byte=178)
            validation['environment_metadata'][0]['seed_namespace'] = 178
            validation_path = base / 'common-warm.json'; save(validation_path, validation)
            candidate = {'name': 'warm', 'checkpoint_sha256': digest, 'selection_score': validation['selection_score'],
                         'validation_file': validation_path.name,
                         'validation_sha256': hashlib.sha256(validation_path.read_bytes()).hexdigest()}
            common = {'schema': 'sfld-ppo-common-validation-v1', 'seed': '9100001', 'namespace': 'validation',
                      'namespace_byte': 178, 'events_per_budget': 3, 'budgets': [20], 'candidates': [candidate],
                      'selected': {**candidate, 'checkpoint': 'selected-checkpoint.pt'}}
            save(base / 'common-validation.json', common)
            save(base / 'study-plan.json', {'jobs': [{'name': 'warm', 'seed': 12, 'cloning_steps': 0, 'ppo_steps': 10}],
                                          'common_validation_seed': 9100001, 'final_seed': 9100002,
                                          'profile_sha256': 'a' * 64, 'library_sha256': 'b' * 64})
            save(run / 'run.json', {'metadata': {'seed': '12', 'sources': {'src/engine.cpp': 'f' * 64},
                                                'environment': learner['environment_metadata'][0]}})
            curve = {'phase': 'ppo', 'steps': 10, 'completed_events': 3, 'validation_summaries': validation['summaries']}
            (run / 'curve.jsonl').write_text(json.dumps(curve) + '\n', encoding='utf-8')
            save(run / 'training-summary.json', {'state': {'bc_steps': 0, 'steps': 10, 'bc_completed_events': 0,
                                                         'completed_training_events': 3}})
            save(run / 'best.json', {'checkpoint': checkpoint.name, 'sha256': digest, 'steps': 10})
            save(base / 'final-learner.json', learner); save(base / 'final-teacher.json', teacher)
            result = report.build_report(base, base / 'report.html')
            self.assertEqual(result['runs'][0]['status'], 'completed')
            self.assertEqual(result['comparison'][0]['events'], 3)
            self.assertGreater(len(result['artifacts']), 7)
            learner['checkpoint_sha256'] = 'e' * 64; save(base / 'final-learner.json', learner)
            with self.assertRaisesRegex(ValueError, 'differs from the candidate frozen'):
                report.build_report(base, base / 'report.html')

    def test_behavior_diagnostics_reject_wrong_checkpoint_or_action_totals(self):
        env = fixture()['environment_metadata'][0]; env['seed_namespace'] = 213
        row = {'budget': 20, 'decisions': 10, 'disagreements': 2, 'disagreement_rate': .2,
               'actions': {'fight': 7, 'flee': 3}, 'teacher_actions': {'fight': 5, 'flee': 5}}
        audit = {'schema': 'sfld-ppo-behavior-audit-v1', 'cohort': {'namespace_byte': 213},
                 'checkpoint': {'sha256': 'd' * 64}, 'environment_metadata': [env],
                 'summaries': [row], 'tables': {'combat': [row]}}
        plan = {'library_sha256': 'b' * 64, 'profile_sha256': 'a' * 64}
        report.validate_behavior(audit, plan, 'd' * 64)
        with self.assertRaisesRegex(ValueError, 'different checkpoint'):
            report.validate_behavior(audit, plan, 'e' * 64)
        row['actions']['fight'] = 6
        with self.assertRaisesRegex(ValueError, 'do not sum'):
            report.validate_behavior(audit, plan, 'd' * 64)

    def test_distillation_checks_artifacts_identity_and_recomputes_actual_loss(self):
        with tempfile.TemporaryDirectory(prefix='sfld-rl-distill-report-') as directory:
            base = Path(directory)
            def save(name, value, plain=False):
                path = base / name
                path.write_text(value if plain else json.dumps(value), encoding='utf-8')
                return {'file': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            environment = fixture()['environment_metadata'][0]
            provenance = {'checkpoint_sha256': 'd' * 64, 'library_sha256': 'b' * 64, 'profile_sha256': 'a' * 64}
            trees = {'schema': 'sfld-phase-trees-v1', 'provenance': provenance,
                     'trees': {'encounter': {'nodes': [{'samples': 10}], 'samples': 10}},
                     'fit': {'max_depth': 1, 'max_leaves': 2}, 'fallback': 'Choose a legal action.'}
            tree_link = save('trees.json', trees)
            neural, tree = fixture(), fixture((1, 1, 2))
            for value, kind in ((neural, 'neural'), (tree, 'phase_trees')):
                value.update(root_seed='9200002', namespace='distill_gameplay', namespace_byte=215, policy_type=kind)
                value['environment_metadata'][0]['seed_namespace'] = 215
            tree['tree_sha256'] = tree_link['sha256']
            held = {'samples': 3, 'agreement': 2 / 3,
                    'by_phase': {'encounter': {'samples': 3, 'agreement': 2 / 3}},
                    'by_budget': {'20': {'samples': 3, 'agreement': 2 / 3}}}
            fit_env = copy.deepcopy(environment); fit_env['seed_namespace'] = 214
            held_env = copy.deepcopy(environment); held_env['seed_namespace'] = 216
            agreement = {'schema': 'sfld-distillation-agreement-v1', 'held_out': held,
                         'fit_collection': {'namespace_byte': 214, 'environment_metadata': [fit_env]},
                         'held_out_collection': {'namespace_byte': 216, 'environment_metadata': [held_env],
                                                 'requested_labels': 3, 'sampling': 'Held-out event reservoir.'}}
            summary = {'schema': 'sfld-distillation-summary-v1', **provenance, 'deadline_hours': 168., 'budgets': [20],
                       'trees': tree_link, 'readable_rules': save('rules.txt', 'IF encounter THEN fight', plain=True),
                       'agreement': {**save('agreement.json', agreement), 'held_out': held},
                       'neural_evaluation': save('neural.json', neural), 'tree_evaluation': save('tree.json', tree),
                       'gameplay_cohort': {'root_seed': '9200002', 'namespace_byte': 215, 'events_per_budget': 3},
                       'paired_gameplay': [{'profile': neural['summaries'][0]['profile'], 'budget': 20, 'events': 3,
                                            'neural_completions_mean': 7 / 3, 'tree_completions_mean': 4 / 3,
                                            'paired_completion_loss': report.estimate([0, 1, 2])}]}
            path = base / 'distillation-summary.json'; save(path.name, summary)
            artifacts = report.Artifacts(base / 'report.html')
            result = report.read_distillation(path, artifacts, provenance, 'd' * 64, environment)
            self.assertEqual(result['rule_count'], 1)
            self.assertTrue(result['losses'][0]['large_observed_loss'])
            self.assertEqual(result['losses'][0]['paired_completion_loss']['mean'], 1)
            self.assertEqual(result['readable_rules_href'], 'rules.txt')
            with self.assertRaisesRegex(ValueError, 'different checkpoint'):
                report.read_distillation(path, artifacts, provenance, 'e' * 64, environment)
            summary['paired_gameplay'][0]['paired_completion_loss']['mean'] = 0
            save(path.name, summary)
            with self.assertRaisesRegex(ValueError, 'Paired tree loss mean'):
                report.read_distillation(path, artifacts, provenance, 'd' * 64, environment)
            summary['paired_gameplay'][0]['paired_completion_loss']['mean'] = 1
            save(path.name, summary)
            save('rules.txt', 'Changed rule without updating its hash', plain=True)
            with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
                report.read_distillation(path, artifacts, provenance, 'd' * 64, environment)


if __name__ == '__main__':
    unittest.main()
