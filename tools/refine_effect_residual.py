"""Focused counterfactual follow-up at effect expiry/replacement boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import mean, NormalDist
import numpy as np
import torch
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.tree import DecisionTreeRegressor
import train_rl
import train_current_study as study
import train_effect_residual as first
from benchmark_player_ideas import write_once
from publish_effect_study import stats
from sfld_rl.effect_lab import EffectLab, probe_cases
from sfld_rl.effect_refinement import EffectSummaries, RefinedEffectResidual
from sfld_rl.effect_residual import export_tree, predict_tree
from sfld_rl.env import VectorEnv
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values

ROOT = Path(__file__).resolve().parents[1]


def boundary_cases():
    cases = []
    for case in probe_cases():
        progress = case['progress']
        family = 3 if 'encounter=cursed_chest' in progress else 4 if 'doors=cursed,' in progress else 0 if 'encounter=crate' in progress else 2
        cases.append({'name': case['name'], 'source': 'boundary', 'family': family, 'holdout': False, 'progress': progress})
        altered = progress.replace('room=60\n', 'room=66\n').replace('hp_percent=50\n', 'hp_percent=57\n').replace('hp_percent=21\n', 'hp_percent=27\n')
        cases.append({'name': case['name']+'-unseen', 'source': 'boundary', 'family': family, 'holdout': True, 'progress': altered})
        if family in (0, 3):
            barrel = progress.replace('encounter=crate', 'encounter=barrel').replace('encounter=cursed_chest', 'encounter=barrel')
            cases.append({'name': case['name']+'-barrel', 'source': 'boundary', 'family': 1, 'holdout': False, 'progress': barrel})
    return cases


def prepare(out, base):
    out.mkdir(parents=True, exist_ok=True)
    for name in ('sfld_rl.dll','synthetic.profile','parent-checkpoint.pt','original-checkpoint.pt','parent-checkpoint.transfer.json'):
        study.copy(base/name, out/name)
    source_paths = [Path(__file__), ROOT/'tools/train_effect_residual.py', ROOT/'tools/train_current_study.py',
                    ROOT/'tools/train_rl.py', ROOT/'tools/benchmark_player_ideas.py', ROOT/'tools/probe_effect_choices.py']
    source_paths += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    base_names = ['plan.json','fit.json','selection.json','summary.json']
    base_names += [f'{prefix}-{b}.{suffix}' for b in first.TRAIN_BUDGETS
                   for prefix,suffix in (('labels','npz'),('labels','json'),('cases','json'),('collection','json'))]
    for name in base_names: study.copy(base/name, out/'base'/name)
    base_plan = json.loads((base/'plan.json').read_text())
    plan = {'schema': 'sfld-effect-boundary-study-v1', 'smoke': False, 'budgets': first.BUDGETS,
        'training_budgets': [0,100], 'parent_sha256': base_plan['parent_sha256'],
        'profile_sha256': base_plan['profile_sha256'], 'library_sha256': base_plan['library_sha256'],
        'base_files': {n:train_rl.sha(out/'base'/n) for n in base_names},
        'sources': {p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in source_paths},
        'rollouts': {'seed':12262001,'namespace':249,'pairs_per_state':256},
        'selection': {'seed':12263001,'namespace':250,'events':1024},
        'final': {'seed':12264001,'namespace':251,'events':8192},
        'model_seed':12266001,
        'candidates': [{'name':'joint-tree','kind':'tree','depth':6,'leaf':8,'margin':0},
                       {'name':'joint-forest','kind':'forest','depth':8,'leaf':6,'margin':0},
                       {'name':'joint-cautious','kind':'forest','depth':8,'leaf':6,'margin':.005},
                       {'name':'joint-conservative','kind':'forest','depth':8,'leaf':6,'margin':.01}],
        'target': 'Primary prediction is paired remaining weekly completions. Tree splits are jointly fitted to that target and negative next-clear-time difference / 24 as an auxiliary outcome; only the primary prediction chooses actions. No game reward changes.',
        'training': 'Reuse the first study data, add 256 paired futures at 90 boundary positions per budget (0,100), and weight each state by its number of future pairs. All altered room-66 positions are held out. Original data keep their previous grouped 20% holdout.',
        'selection_rule': 'Highest equal-budget fresh-week mean; ties keep parent then earlier candidate. Final tests only the selected winner.',
        'promotion_gate': 'Positive lower family-adjusted 99% CI over equal-budget matched differences and no budget with a negative upper bound; seven contrasts. Browser acceptance required.',
        'reason': 'The first fitted models did not beat the parent in selection and failed the short-curse diagnostic. The follow-up is declared before new rollouts or selection/final outcomes.',
        'limitations': 'The auxiliary time target guides splits, not deployment scores. First-study data are reused training data, not independent confirmation. Live effect acquisition timing remains uncertain.'}
    write_once(out/'plan.json',plan);write_once(out/'boundary-cases.json',boundary_cases())
    for p in source_paths:study.copy(p,out/'source'/p.relative_to(ROOT))
    roots=[plan[k]['seed'] for k in ('rollouts','selection','final')]
    old=set();pattern=re.compile(r'"(?:root_seed|seed)"\s*:\s*(\d+)')
    for path in (ROOT/'docs').rglob('*.json'):old.update(map(int,pattern.findall(path.read_text(encoding='utf-8-sig'))))
    old.update(base_plan[k]['seed'] for k in ('collection','rollouts','selection','final'))
    fps=[train_rl.seed_fingerprint(x) for x in roots]
    if len(set(fps))!=len(fps) or set(fps)&{train_rl.seed_fingerprint(x) for x in old}:raise RuntimeError('Seed reuse')
    write_once(out/'seed-audit.json',{'roots':roots,'fingerprints':fps,'overlap':False})
    return plan


def fit(out, plan):
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],1,budgets=[0]) as env:
        encoder=EffectSummaries(env.observation_names,env.action_names)
    xs=[];ys=[];weights=[];holdouts=[];total=0
    for folder,budgets in ((out/'base',first.TRAIN_BUDGETS),(out,plan['training_budgets'])):
        for budget in budgets:
            data=np.load(folder/f'labels-{budget}.npz');cases=json.loads((folder/f'cases-{budget}.json').read_text())
            obs=data['observations'];family=np.array([c['family'] for c in cases])
            xs.append(encoder.encode(obs,family,data['actions'][:,0]))
            paired=data['outcomes'][:,:,0]-data['outcomes'][:,:,1]
            ys.append(np.column_stack([paired[:,:,0].mean(1),-paired[:,:,3].mean(1)/24]))
            weights.extend([data['outcomes'].shape[1]/64]*len(cases));total+=len(cases)*data['outcomes'].shape[1]*2
            for c in cases:
                if folder==out:holdouts.append(c['holdout'])
                else:
                    key=('engineered',c['name']) if c['source']=='engineered' else ('natural',budget,c['episode'])
                    holdouts.append(int.from_bytes(hashlib.sha256(json.dumps(key).encode()).digest()[:4],'little')%5==0)
    x=np.concatenate(xs);y=np.concatenate(ys);w=np.array(weights);hold=np.array(holdouts);diagnostics=[]
    for c in plan['candidates']:
        if c['kind']=='tree':model=DecisionTreeRegressor(max_depth=c['depth'],min_samples_leaf=c['leaf'],random_state=plan['model_seed'])
        else:model=ExtraTreesRegressor(n_estimators=32,max_depth=c['depth'],min_samples_leaf=c['leaf'],max_features=.8,n_jobs=2,random_state=plan['model_seed'])
        model.fit(x[~hold],y[~hold],sample_weight=w[~hold])
        trees=model.estimators_ if hasattr(model,'estimators_') else [model]
        artifact={'schema':'sfld-effect-residual-v2','name':c['name'],'parent_sha256':plan['parent_sha256'],
                  'profile_sha256':plan['profile_sha256'],'features':encoder.feature_names,'margin':c['margin'],
                  'target':plan['target'],'trees':[export_tree(t) for t in trees]}
        write_once(out/(c['name']+'.json'),artifact)
        predicted=model.predict(x)[:,0]
        exported=sum(predict_tree(tuple(np.asarray(t[k]) for k in ('left','right','feature','threshold','value')),x) for t in artifact['trees'])/len(trees)
        np.testing.assert_allclose(exported,predicted,rtol=0,atol=1e-12)
        diagnostics.append({'candidate':c['name'],'train_states':int((~hold).sum()),'held_out_states':int(hold.sum()),
            'held_out_primary_mse':float(np.mean((predicted[hold]-y[hold,0])**2)),
            'held_out_null_mse':float(np.mean(y[hold,0]**2)),
            'importance':sorted(zip(encoder.feature_names,model.feature_importances_.tolist()),key=lambda x:-x[1])[:12]})
    reused=json.loads((out/'base/fit.json').read_text())['partial_weeks']
    write_once(out/'fit.json',{'states':len(x),'partial_weeks':total,'reused_partial_weeks':reused,
        'new_partial_weeks':total-reused,'models':diagnostics})
    print(json.dumps({'fit':diagnostics}),flush=True)


class RefinedPractical(Practical):
    artifact=None
    def __init__(self,names,actions,*args):
        super().__init__(names,actions,*args);self.residual=RefinedEffectResidual(names,actions,type(self).artifact)
    def apply(self,obs,masks,proposed):
        actual,targets,reasons=super().apply(obs,masks,proposed)
        actual,_=self.residual.apply(obs,masks,actual)
        return actual,targets,reasons


def evaluate(out,plan,candidate,cohort,budget):
    original=study.Practical
    try:
        if candidate['name']!='parent':
            RefinedPractical.artifact=out/(candidate['name']+'.json');study.Practical=RefinedPractical
        return study.evaluate(out,plan,candidate,cohort,budget)
    finally:study.Practical=original


def run(out,base):
    torch.set_num_threads(2);plan=prepare(out,base)
    saved,model=train_rl.load_checkpoint(out/'parent-checkpoint.pt',torch.device('cuda'));model.eval()
    profile=profile_values(out/'synthetic.profile')
    with torch.inference_mode():
        for budget in plan['training_budgets']:
            if (out/f'labels-{budget}.json').exists():continue
            cases=json.loads((out/'boundary-cases.json').read_text())
            with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],1,budgets=[budget],auto_reset=False) as env:
                env.reset();lab=EffectLab(env)
                for c in cases:c['snapshot']=lab.fixture(c['progress'],budget)
            write_once(out/f'cases-{budget}.json',[{k:v for k,v in c.items() if k!='snapshot'} for c in cases])
            first.label(out,plan,budget,cases,model,profile)
    fit(out,plan)
    base_candidate={'checkpoint':'parent-checkpoint.pt','sha256':train_rl.sha(out/'parent-checkpoint.pt'),'steps':0,'style':'practical'}
    candidates=[{'name':'parent',**base_candidate}]+[{'name':c['name'],**base_candidate,'effect_artifact_sha256':train_rl.sha(out/(c['name']+'.json'))} for c in plan['candidates']]
    validation={(c['name'],b):evaluate(out,plan,c,'selection',b) for c in candidates for b in plan['budgets']}
    scores={c['name']:mean(validation[c['name'],b]['summary']['completions']['mean'] for b in plan['budgets']) for c in candidates}
    selection={'winner':max(scores,key=scores.get),'scores':scores};write_once(out/'selection.json',selection)
    print(json.dumps({'frozen_selection':selection}),flush=True)
    if selection['winner']=='parent':
        write_once(out/'summary.json',{'selection':selection,'promotion':False,'reason':'Parent won; final unused.'});return
    winner=next(c for c in candidates if c['name']==selection['winner'])
    cells={(c['name'],b):evaluate(out,plan,c,'final',b) for c in (candidates[0],winner) for b in plan['budgets']}
    deltas=[];contrasts=[];z=NormalDist().inv_cdf(1-.01/(2*7))
    for b in plan['budgets']:
        delta=[n['completions']-o['completions'] for o,n in zip(cells['parent',b]['rows'],cells[winner['name'],b]['rows'])]
        deltas.append(delta);contrasts.append({'budget':b,**stats(delta,z)})
    contrasts.append({'budget':'equal-budget mean',**stats([mean(x) for x in zip(*deltas)],z)})
    promote=contrasts[-1]['family_99_ci'][0]>0 and all(c['family_99_ci'][1]>=0 for c in contrasts[:-1])
    for p,h in plan['sources'].items():
        if train_rl.sha(ROOT/p)!=h:raise RuntimeError('Frozen source changed: '+p)
    write_once(out/'summary.json',{'selection':selection,'promotion':promote,'contrasts':contrasts,
        'cells':[{k:v for k,v in c.items() if k!='rows'} for c in cells.values()]})
    print(json.dumps({'promotion':promote,'difference':contrasts[-1]}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--base',type=Path,required=True)
    a=p.parse_args();run(a.output.resolve(),a.base.resolve())
