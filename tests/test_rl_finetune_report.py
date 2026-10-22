"""Fine-tuning report checks use small complete cohorts, without ML libraries."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import render_rl_finetune as report
from render_rl_study import estimate


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def evaluation(values, checkpoint, seed, *, final=False):
    namespace, namespace_byte = ('final', 195) if final else ('validation', 178)
    summaries, environments, rows = [], [], []
    for budget in (20, 50):
        environments.append({'budgets': [budget], 'profiles': [{'path': 'fixture.profile', 'sha256': 'a'*64}],
                             'library_sha256': 'b'*64, 'schema_sha256': 'c'*64, 'seed_namespace': namespace_byte,
                             'deadline_hours': 168., 'max_actions': 100000, 'initial_episode_index': 0})
        summaries.append({'profile': 'fixture.profile', 'budget': budget, 'events': len(values), 'failed_events': 0,
                          'completions': estimate(values), 'mean_mushrooms': 10., 'mean_recovery_mushrooms': 8.,
                          'mean_reroll_mushrooms': 2., 'invalid_actions': 0, 'truncations': 0})
        rows += [{'profile': 'fixture.profile', 'budget': budget, 'event_index': i, 'terminated': True,
                  'truncated': False, 'completions': value, 'mushrooms': 10, 'recovery_mushrooms': 8,
                  'reroll_mushrooms': 2, 'elapsed_hours': 167.9999, 'invalid_actions': 0, 'actions': 250}
                 for i, value in enumerate(values)]
    return {'schema': 'sfld-ppo-evaluation-v1', 'root_seed': str(seed), 'namespace': namespace,
            'namespace_byte': namespace_byte, 'action_policy': 'greedy', 'summaries': summaries,
            'environment_metadata': environments, 'outcomes': rows, 'selection_score': sum(values)/len(values),
            'checkpoint_sha256': checkpoint, 'library_sha256': 'b'*64, 'profile_sha256': 'a'*64}


def fixture(study):
    (study / 'parent-checkpoint.pt').write_bytes(b'parent')
    (study / 'new-checkpoint.pt').write_bytes(b'new')
    (study / 'selected-checkpoint.pt').write_bytes(b'new')
    parent = hashlib.sha256(b'parent').hexdigest()
    new = hashlib.sha256(b'new').hexdigest()
    plan = {'schema': 'sfld-ppo-finetune-plan-v1', 'budgets': [20,50], 'parent_checkpoint_sha256': parent,
            'library_sha256': 'b'*64, 'profile_sha256': 'a'*64,
            'variants': [{'name':'steady','seed':10,'steps':64,'learning_rate':.0001,'entropy_coefficient':.01,'gae_lambda':.95,'rollout_steps':128},
                         {'name':'explore','seed':20,'steps':96,'learning_rate':.0003,'entropy_coefficient':.02,'gae_lambda':.95,'rollout_steps':128}],
            'extension': {'seed':30,'steps':128}, 'common_validation': {'seed':111,'events':3},
            'selection_validation': {'seed':222,'events':3}, 'final': {'seed':333,'events':3}}
    write(study / 'study-plan.json', plan)
    write(study / 'selection-parent.json', evaluation([2,2,2], parent, 222))
    write(study / 'selection-new.json', evaluation([2,3,4], new, 222))
    write(study / 'common-parent.json', evaluation([2,2,2], parent, 111))
    write(study / 'common-new.json', evaluation([2,3,4], new, 111))
    write(study / 'common-validation.json', {'schema':'sfld-ppo-finetune-selection-v1',
        'candidates':[{'name':'parent','checkpoint':'parent-checkpoint.pt','checkpoint_sha256':parent,'selection_score':2.,'evaluation_file':'common-parent.json'},
                      {'name':'new','checkpoint':'new-checkpoint.pt','checkpoint_sha256':new,'selection_score':3.,'evaluation_file':'common-new.json'}],
        'selected':{'name':'new','checkpoint':'new-checkpoint.pt','checkpoint_sha256':new,'selection_score':3.}})
    write(study / 'final-parent.json', evaluation([2,2,2], parent, 333, final=True))
    write(study / 'final-learner.json', evaluation([3,4,2], new, 333, final=True))
    write(study / 'selection.json', {'schema':'sfld-ppo-finetune-selection-v1', 'selection_rule':'Mean, first-listed ties',
        'candidates':[{'name':'parent','checkpoint':'parent-checkpoint.pt','checkpoint_sha256':parent,'selection_score':2.,'evaluation_file':'selection-parent.json'},
                      {'name':'new','checkpoint':'new-checkpoint.pt','checkpoint_sha256':new,'selection_score':3.,'evaluation_file':'selection-new.json'}],
        'selected':{'name':'new','checkpoint':'selected-checkpoint.pt','checkpoint_sha256':new,'selection_score':3.},
        'final_protocol':{'seed':333,'events':3}})
    jobs = []
    for name, seed, steps in (('steady',10,64),('explore',20,96),('extension',30,128)):
        folder = study / name; folder.mkdir()
        rows = []
        for iteration, n in enumerate((steps//2, steps)):
            rows.append({'phase':'ppo','steps':n,'completed_events':iteration+1,'validation_score':2.,
                         'validation_summaries':evaluation([2,2,2],new,222)['summaries']})
        (folder / 'curve.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
        write(folder / 'training-summary.json', {'state':{'steps':steps,'completed_training_events':3,'bc_steps':0}})
        write(folder / 'run.json', {'metadata':{'seed':str(seed),'budgets':[20,50],
            'finetune':{'parent_sha256':new if name=='extension' else parent},
            'environment':{'library_sha256':'b'*64,'deadline_hours':168.,'profiles':[{'sha256':'a'*64}]}}})
        jobs.append({'name':name,'steps':steps,'completed_training_events':3,'wall_seconds':1.})
    write(study / 'training-totals.json', {'new_ppo_steps':288,'completed_training_events':9,'wall_seconds':3.,'jobs':jobs})


class FinetuneReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sfld-finetune-report-')
        self.addCleanup(self.temporary.cleanup)
        self.study = Path(self.temporary.name)
        fixture(self.study)

    def change(self, filename, operation):
        path = self.study / filename
        value = json.loads(path.read_text(encoding='utf-8'))
        operation(value); write(path,value)

    def collect(self):
        return report.collect(self.study, self.study / 'report.html')

    def test_complete_report_reconstructs_pairs_curves_totals_and_early_terminal(self):
        self.change('final-learner.json', lambda value: value['outcomes'].reverse())
        result = self.collect()
        self.assertEqual(len(result['pairs']), 2)
        self.assertEqual(result['pairs'][0]['paired_difference'], estimate([1,2,0]))
        self.assertEqual(result['training_totals']['new_ppo_steps'],288)
        self.assertEqual(sum(job['completed_training_events'] for job in result['jobs']),9)
        self.assertEqual(len(result['jobs'][0]['points']),4)

    def test_failed_truncated_illegal_and_missing_outcomes_are_rejected(self):
        original = json.loads((self.study / 'final-learner.json').read_text())
        mutations = (lambda v:v['outcomes'][0].update(truncated=True),
                     lambda v:v['outcomes'][0].update(invalid_actions=1),
                     lambda v:v['outcomes'].pop(),
                     lambda v:v['outcomes'].append(copy.deepcopy(v['outcomes'][0])),
                     lambda v:v['outcomes'][0].update(mushrooms=21))
        for mutate in mutations:
            value = copy.deepcopy(original); mutate(value); write(self.study/'final-learner.json',value)
            with self.assertRaises(ValueError): self.collect()

    def test_final_seed_schema_and_checkpoint_must_match_frozen_selection(self):
        original = json.loads((self.study/'final-learner.json').read_text())
        for mutate in (lambda v:v.update(root_seed='334'),
                       lambda v:v.update(checkpoint_sha256='f'*64),
                       lambda v:v['environment_metadata'][0].update(schema_sha256='d'*64),
                       lambda v:v.update(action_policy='sample')):
            value = copy.deepcopy(original); mutate(value); write(self.study/'final-learner.json',value)
            with self.assertRaises(ValueError): self.collect()

    def test_selection_must_use_shared_declared_cohort_and_correct_winner(self):
        self.change('selection-new.json',lambda v:v.update(root_seed='111'))
        with self.assertRaisesRegex(ValueError,'seed'):self.collect()
        write(self.study/'selection-new.json',evaluation([2,3,4],hashlib.sha256(b'new').hexdigest(),222))
        self.change('selection.json',lambda v:v['selected'].update(name='parent'))
        with self.assertRaisesRegex(ValueError,'Selected policy'):self.collect()

    def test_exact_selection_ties_keep_parent(self):
        new = hashlib.sha256(b'new').hexdigest()
        write(self.study/'selection-new.json',evaluation([2,2,2],new,222))
        def tie(value):
            value['candidates'][1]['selection_score']=2.
            value['selected']['selection_score']=2.
        self.change('selection.json',tie)
        with self.assertRaisesRegex(ValueError,'Selected policy'):self.collect()

    def test_artifact_hash_and_path_escape_are_rejected(self):
        (self.study/'selected-checkpoint.pt').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.collect()
        (self.study/'selected-checkpoint.pt').write_bytes(b'new')
        self.change('selection.json',lambda v:v['candidates'][1].update(evaluation_file='../other.json'))
        with self.assertRaisesRegex(ValueError,'escapes'):self.collect()

    def test_training_totals_require_every_full_job_and_matching_events(self):
        self.change('training-totals.json',lambda v:v.update(new_ppo_steps=287))
        with self.assertRaisesRegex(ValueError,'Total PPO'):self.collect()
        self.change('training-totals.json',lambda v:v.update(new_ppo_steps=288))
        self.change('steady/training-summary.json',lambda v:v['state'].update(completed_training_events=4))
        with self.assertRaisesRegex(ValueError,'event counts'):self.collect()

    def test_standalone_html_escapes_data_and_uses_no_external_assets(self):
        data=self.collect();data['selection']['selection_rule']='</script><script>alert(1)</script>'
        html=report.render(data)
        self.assertNotIn('</script><script>alert(1)',html)
        self.assertIn('\\u003c/script>',html)
        self.assertIn('id="budget"',html)
        self.assertNotIn('<script src=',html)
        self.assertNotIn('<link',html)

    def test_first_stage_failures_and_wrong_extension_parent_are_rejected(self):
        self.change('common-new.json',lambda value:value['outcomes'][0].update(truncated=True))
        with self.assertRaisesRegex(ValueError,'truncated'):self.collect()
        write(self.study/'common-new.json',evaluation([2,3,4],hashlib.sha256(b'new').hexdigest(),111))
        self.change('extension/run.json',lambda value:value['metadata']['finetune'].update(parent_sha256='f'*64))
        with self.assertRaisesRegex(ValueError,'inherited'):self.collect()

    def test_archived_source_manifest_hashes_are_verified(self):
        source=self.study/'source'/'tools'/'fixture.py';source.parent.mkdir(parents=True);source.write_bytes(b'pass\n')
        write(self.study/'source-manifest.json',{'tools\\fixture.py':hashlib.sha256(b'pass\n').hexdigest()})
        self.assertTrue(report.build_report(self.study,self.study/'report.html'))
        source.write_bytes(b'changed\n')
        with self.assertRaisesRegex(ValueError,'Artifact hash mismatch'):self.collect()


if __name__=='__main__':
    unittest.main()
