"""Protect frozen-study receipts, paired samples and independent seed cohorts."""
import csv
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import benchmark_balanced_gems as study


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sfld-balanced-tests-')
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.workspace)

    def receipt(self, output, artifacts, command=None):
        study.write_json(output.with_suffix('.receipt.json'), {
            'command': command or [],
            'sha256': study.sha(output),
            'artifacts': [{'path': str(path), 'sha256': study.sha(path)}
                          for path in artifacts],
            'seconds': 0,
        })


class ReceiptTests(WorkspaceTest):
    def job(self, artifact_kind):
        exe = self.workspace / 'engine.exe'
        exe.write_bytes(b'fixture executable; never invoked')
        profile = self.workspace / 'working.profile'
        profile.write_text('schema=1\nid=fixture\n', encoding='utf-8')
        initial = self.workspace / 'initial.rules'
        initial.write_text('schema=1\nid=start\n', encoding='utf-8')
        output = self.workspace / 'job' / 'report.json'
        study.write_json(output, {'fixture': True})
        artifact = output.parent / ('winner.rules' if artifact_kind == 'winner' else 'events.csv')
        artifact.write_text('original artifact\n', encoding='utf-8')
        flag = '--save-rules' if artifact_kind == 'winner' else '--event-completions'
        command = [str(exe), '--rules', str(initial), '--output', str(output), flag, str(artifact)]
        plan = {'executable_sha256': study.sha(exe), 'profile': str(profile),
                'profile_sha256': study.sha(profile)}
        self.receipt(output, [artifact], command)
        return exe, initial, output, artifact, command, plan

    def test_unchanged_completed_job_reuses_receipt_without_running_engine(self):
        exe, _, output, _, command, plan = self.job('winner')
        with patch.object(study.subprocess, 'run', side_effect=AssertionError('Unexpected engine execution')):
            self.assertEqual(study.run_command(command, output, plan, exe), {'fixture': True})
            study.verify_receipt(output)

    def assert_modified_artifact_rejected(self, kind):
        exe, _, output, artifact, command, plan = self.job(kind)
        artifact.write_text('modified artifact\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Artifact changed'):
            study.verify_receipt(output)
        with patch.object(study.subprocess, 'run', side_effect=AssertionError('Unexpected engine execution')):
            with self.assertRaisesRegex(RuntimeError, 'changed artifact'):
                study.run_command(command, output, plan, exe)

    def test_receipt_rejects_modified_search_winner(self):
        self.assert_modified_artifact_rejected('winner')

    def test_receipt_rejects_modified_final_csv(self):
        self.assert_modified_artifact_rejected('csv')

    def test_receipt_rejects_modified_report(self):
        exe, _, output, _, command, plan = self.job('winner')
        study.write_json(output, {'fixture': 'changed'})
        with self.assertRaisesRegex(RuntimeError, 'Report changed'):
            study.verify_receipt(output)
        with self.assertRaisesRegex(RuntimeError, 'Cannot reuse changed job'):
            study.run_command(command, output, plan, exe)

    def test_rule_change_during_execution_cannot_receive_completion_receipt(self):
        exe, initial, output, artifact, command, plan = self.job('winner')
        output.unlink()
        output.with_suffix('.receipt.json').unlink()
        artifact.unlink()

        def execution(*args, **kwargs):
            study.write_json(output, {'fixture': True})
            artifact.write_text('new winner\n', encoding='utf-8')
            initial.write_text('schema=1\nid=changed-while-running\n', encoding='utf-8')
            return SimpleNamespace(returncode=0)

        with patch.object(study.subprocess, 'run', side_effect=execution):
            with self.assertRaisesRegex(RuntimeError, 'Input rules changed during execution'):
                study.run_command(command, output, plan, exe)
        self.assertFalse(output.with_suffix('.receipt.json').exists())


class PairedSamplesTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.out = self.workspace / 'study'
        self.plan = {'final_events': 4, 'final_root': 713100}
        study.write_json(self.out / 'plan.json', self.plan)
        self.candidate = {'id': 'candidate', 'budget': 50}
        self.reference = {'id': 'reference', 'budget': 50}
        self.candidate_rows = [(50, i, value) for i, value in enumerate([2, 4, 3, 5])]
        self.reference_rows = [(50, i, value) for i, value in enumerate([1, 4, 1, 4])]
        self.samples(self.candidate, self.candidate_rows)
        self.samples(self.reference, self.reference_rows)

    def samples(self, case, rows, *, root=None, runs=None, mean=None):
        output = self.out / 'final' / (case['id'] + '.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        samples = output.with_suffix('.csv')
        with samples.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['budget', 'event_index', 'completions'])
            writer.writerows(rows)
        report = {
            'settings': {'root_seed': str(self.plan['final_root'] if root is None else root),
                         'runs': self.plan['final_events'] if runs is None else runs},
            'evaluation': {'budgets': [{'budget': case['budget'], 'completions': {
                'mean': statistics.mean(row[2] for row in rows) if mean is None else mean}}]},
        }
        study.write_json(output, report)
        # Seal the fixture after introducing malformed contents. The following
        # tests exercise sample validation in addition to artifact hash checks.
        self.receipt(output, [samples])

    def test_pairs_by_event_index_not_csv_order_and_computes_interval(self):
        self.samples(self.reference, list(reversed(self.reference_rows)))
        result = study.paired(self.out, self.candidate, self.reference)
        self.assertEqual(result['events'], 4)
        self.assertEqual(result['mean'], 1)
        expected_se = math.sqrt(1 / 6)
        self.assertAlmostEqual(result['standard_error'], expected_se)
        self.assertAlmostEqual(result['normal_95_ci'][0], 1 - 1.96 * expected_se)
        self.assertAlmostEqual(result['normal_95_ci'][1], 1 + 1.96 * expected_se)

    def test_rejects_duplicate_missing_truncated_and_wrong_budget_samples(self):
        bad_rows = {
            'duplicate': self.candidate_rows + [self.candidate_rows[0]],
            'missing_middle': [self.candidate_rows[i] for i in [0, 2, 3]],
            'truncated_tail': self.candidate_rows[:2],
            'wrong_budget': [(100, i, value) for _, i, value in self.candidate_rows],
            'wrong_index': [(50, i + 1, value) for _, i, value in self.candidate_rows],
        }
        for name, rows in bad_rows.items():
            with self.subTest(corruption=name):
                self.samples(self.candidate, rows)
                with self.assertRaisesRegex(RuntimeError, 'Duplicate, missing or wrong-budget'):
                    study.paired(self.out, self.candidate, self.reference)

    def test_identically_truncated_csvs_cannot_pass_as_a_smaller_cohort(self):
        self.samples(self.candidate, self.candidate_rows[:2])
        self.samples(self.reference, self.reference_rows[:2])
        with self.assertRaisesRegex(RuntimeError, 'Duplicate, missing or wrong-budget'):
            study.paired(self.out, self.candidate, self.reference)

    def test_rejects_wrong_final_root_or_event_count(self):
        for metadata in [{'root': 713101}, {'runs': 3}]:
            with self.subTest(metadata=metadata):
                self.samples(self.candidate, self.candidate_rows, **metadata)
                with self.assertRaisesRegex(RuntimeError, 'Wrong final cohort metadata'):
                    study.paired(self.out, self.candidate, self.reference)

    def test_rejects_sample_mean_disagreeing_with_report(self):
        self.samples(self.candidate, self.candidate_rows, mean=3.75)
        with self.assertRaisesRegex(RuntimeError, 'Event samples disagree with aggregate'):
            study.paired(self.out, self.candidate, self.reference)


class CohortTests(WorkspaceTest):
    @staticmethod
    def plan():
        return {'search_roots': [12, 13], 'selection_root': 14, 'final_root': 15,
                'training_events': 4, 'validation_events': 3,
                'selection_events': 5, 'final_events': 6}

    def test_disjoint_blocks_produce_distinct_actual_event_seeds(self):
        audit = study.check_cohorts(self.plan())
        self.assertEqual(audit['new_unique_cohorts'], 6)
        self.assertEqual(audit['overlaps'], 0)
        seeds = [study.mix(int(cohort['master_seed']) ^ index)
                 for cohort in audit['cohorts'] for index in range(cohort['events'])]
        self.assertEqual(len(seeds), len(set(seeds)))

    def test_rejects_selection_final_reuse_and_truncated_hash_collision(self):
        plan = self.plan()
        plan['final_root'] = plan['selection_root']
        with self.assertRaisesRegex(ValueError, 'Cohort overlaps'):
            study.check_cohorts(plan)
        # Distinct 64-bit roots can map to the same 32-bit namespace identity.
        with patch.object(study, 'mix', return_value=17):
            with self.assertRaisesRegex(ValueError, 'Cohort overlaps'):
                study.check_cohorts(self.plan())

    def test_rejects_overlap_with_published_previous_final_cohort(self):
        plan = self.plan()
        previous = Path('docs/rule-strategy-2026-10-09/final/manifest.json')
        study.write_json(previous, {'event_master_seed': str(study.master(plan['final_root'], 0x73)),
                                   'runs': 2})
        with self.assertRaisesRegex(ValueError, 'Cohort overlaps historical'):
            study.check_cohorts(plan)

    def test_rejects_overlap_with_previous_search_training_cohort(self):
        plan = self.plan()
        previous = Path('out/human-rules-2026-10-09/previous/search.json')
        study.write_json(previous, {'seed_plan': {'cohorts': [{
            'name': 'training', 'master_seed': str(study.master(plan['search_roots'][0], 0x31)),
            'events': 2}]}})
        with self.assertRaisesRegex(ValueError, 'Cohort overlaps historical'):
            study.check_cohorts(plan)

    def test_rejects_event_counts_that_escape_the_reserved_index_block(self):
        plan = self.plan()
        plan['final_events'] = 1 << 24
        with self.assertRaisesRegex(ValueError, 'valid nonoverlapping seed block'):
            study.check_cohorts(plan)


if __name__ == '__main__':
    unittest.main()
