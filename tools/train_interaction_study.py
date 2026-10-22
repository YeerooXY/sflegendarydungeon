"""Current-engine PPO with ordinary and player-interaction curriculum branches.

Selection and final cohorts are ordinary fresh weeks. Engine/profile, all
sources and curriculum are frozen before training. This script does not deploy.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean, NormalDist
import subprocess
import sys
import time
import traceback
import torch
import train_rl
import train_current_study as evaluation
from train_continuation_study import audit_seeds, status, verify_inputs
from migrate_current_engine import migrate
from benchmark_player_ideas import write_once
from publish_effect_study import stats
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA
from sfld_rl.interaction_cases import cases
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab

ROOT=Path(__file__).resolve().parents[1]
BUDGETS=[0,20,50,100,500,1000]


def prepare(out, smoke):
    if out.exists(): raise FileExistsError(out)
    if train_rl.sha(CHECKPOINT)!=CHECKPOINT_SHA: raise RuntimeError('Parent changed')
    roots=[2026101521,2026101522,2026101531,2026101532] if not smoke else [2026101591,2026101592,2026101593,2026101594]
    audit=audit_seeds(roots)
    out.mkdir(parents=True)
    for source,name in ((CHECKPOINT,'original-raw.pt'),(ROOT/'build-v0611/Release/sfld_rl.dll','sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile')):
        evaluation.copy(source,out/name)
    panel=cases()
    with VectorEnv(out/'sfld_rl.dll',[out/'synthetic.profile'],1,budgets=[1000],auto_reset=False) as env:
        env.reset();lab=EffectLab(env)
        for case in panel:
            try:
                with lab.fixture(case['progress'],1000) as snap: lab.restore(0,snap,0)
                obs,masks=lab.observe()
                if not masks.any(): raise RuntimeError('No legal actions')
            except Exception as exc: raise RuntimeError(case['name']) from exc
        if env.metadata['schema']['engine']!='0.6.11': raise RuntimeError('Wrong engine')
    write_once(out/'challenges.json',panel)
    sources=[Path(__file__),ROOT/'CMakeLists.txt']
    sources += [ROOT/'tools'/p for p in ('train_rl.py','train_continuation_study.py','train_current_study.py',
                                       'migrate_current_engine.py','benchmark_player_ideas.py','publish_effect_study.py')]
    sources += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    sources += sorted(p for folder in ('src','include') for p in (ROOT/folder).rglob('*') if p.suffix in ('.cpp','.hpp','.h','.c'))
    plan=dict(schema='sfld-interaction-study-v1',smoke=smoke,engine='0.6.11',budgets=BUDGETS,
        created_utc=datetime.now(timezone.utc).isoformat(),parent=str(CHECKPOINT.relative_to(ROOT)),parent_sha256=CHECKPOINT_SHA,
        profile_sha256=train_rl.sha(out/'synthetic.profile'),library_sha256=train_rl.sha(out/'sfld_rl.dll'),
        challenges_sha256=train_rl.sha(out/'challenges.json'),challenge_positions=len(panel),
        sources={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        branches=[dict(name=name,seed=roots[i],curriculum=bool(i),steps=2048 if smoke else 16777216)
                  for i,name in enumerate(('ordinary','interactions'))],
        settings=dict(n_envs=8 if smoke else 512,rollout_steps=16 if smoke else 128,
                      minibatch_size=64 if smoke else 2048,validation_events=2 if smoke else 256,
                      validate_every=8 if smoke else 64,learning_rate=.00007,entropy_coefficient=.0075,
                      gae_lambda=.99,target_kl=.02),
        selection=dict(seed=roots[2],namespace=203,events=2 if smoke else 1024),
        final=dict(seed=roots[3],namespace=204,events=4 if smoke else 8192),
        curriculum='25% of reset training episodes in interactions branch start at declared visible interaction states. '
                   'No target action labels. These are partial remaining-week scenarios; never count them as fresh weeks.',
        objective='Completions in 168 hours, random eligible gems, unrestricted legal spending within cap.',
        evaluation_controller='Identical Practical(learned) guards for all candidates. UI-only visual_advice guards '
                              'are audited separately and are not represented in these core-policy scores.',
        selection_rule='Independent selection on equal mean over six budgets, parent wins ties. Freeze one candidate before final.',
        promotion_gate='Positive simultaneous 99% lower bound on equal-budget gain; no individual budget negative upper bound. '
                       'Seven Bonferroni contrasts. Also require runtime interaction acceptance before deploying.',
        limits='One seed per training method. Synthetic profile uncertainty excluded from Monte Carlo intervals. No global optimality proof.')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for p in sources: evaluation.copy(p,out/'source'/p.relative_to(ROOT))
    args=train_rl.parse(['--mode','train','--output',str(out/'unused'),'--library',str(out/'sfld_rl.dll'),
                        '--profile',str(out/'synthetic.profile'),'--budgets',','.join(map(str,BUDGETS)),
                        '--device','cuda','--shop-policy','learned'])
    migrate(out/'original-raw.pt',out/'parent-raw.pt',args)
    return plan


def train(out,plan,branch):
    settings=plan['settings']; dest=out/branch['name']
    command=[sys.executable,str(ROOT/'tools/train_rl.py'),'--mode','train','--finetune-from',str(out/'parent-raw.pt'),
        '--library',str(out/'sfld_rl.dll'),'--profile',str(out/'synthetic.profile'),'--output',str(dest),
        '--budgets',','.join(map(str,BUDGETS)),'--device','cuda','--torch-threads','2','--seed',str(branch['seed']),
        '--shop-policy','learned','--total-steps',str(branch['steps']),'--checkpoint-every',str(settings['validate_every'])]
    for k,v in settings.items():command+=['--'+k.replace('_','-'),str(v)]
    if branch['curriculum']: command+=['--effect-curriculum','--curriculum-cases',str(out/'challenges.json')]
    write_once(out/(branch['name']+'-invocation.json'),dict(command=command))
    start=time.monotonic()
    with (out/(branch['name']+'.log')).open('x',encoding='utf-8') as log:
        process=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    write_once(out/(branch['name']+'-status.json'),dict(exit_code=process.returncode,seconds=time.monotonic()-start))
    if process.returncode:raise RuntimeError('Training failed: '+branch['name'])
    summary=evaluation.read(dest/'training-summary.json');best=evaluation.read(dest/'best.json');checkpoint=dest/best['checkpoint']
    if summary['state']['steps']!=branch['steps'] or train_rl.sha(checkpoint)!=best['sha256']:raise RuntimeError('Incomplete training')
    return dict(name=branch['name'],checkpoint=checkpoint.relative_to(out).as_posix(),sha256=best['sha256'],steps=best['steps'],style='raw')


def run(out,smoke):
    torch.set_num_threads(2);plan=prepare(out,smoke)
    parent=dict(name='parent',checkpoint='parent-raw.pt',sha256=train_rl.sha(out/'parent-raw.pt'),steps=0,style='raw')
    candidates=[parent]
    for branch in plan['branches']:
        verify_inputs(out,plan)
        status(out,'training',branch=branch['name'],decisions=branch['steps'])
        candidates.append(train(out,plan,branch))
    write_once(out/'training-selection.json',candidates)
    status(out,'selection')
    cells={(c['name'],b):evaluation.evaluate(out,plan,c,'selection',b) for c in candidates for b in BUDGETS}
    scores={c['name']:mean(cells[c['name'],b]['summary']['completions']['mean'] for b in BUDGETS) for c in candidates}
    selection=dict(winner=max(scores,key=scores.get),scores=scores);write_once(out/'selection.json',selection)
    winner=next(c for c in candidates if c['name']==selection['winner'])
    status(out,'final',selection=selection)
    chosen=[parent] if winner==parent else [parent,winner]
    final={(c['name'],b):evaluation.evaluate(out,plan,c,'final',b) for c in chosen for b in BUDGETS}
    contrasts=[]
    if winner!=parent:
        z=NormalDist().inv_cdf(1-.01/(2*7));deltas=[]
        for b in BUDGETS:
            old=final['parent',b]['rows'];new=final[winner['name'],b]['rows']
            if [r['event'] for r in old]!=[r['event'] for r in new]:raise RuntimeError('Unmatched events')
            delta=[y['completions']-x['completions'] for x,y in zip(old,new)];deltas.append(delta)
            contrasts.append(dict(budget=b,**stats(delta,z)))
        contrasts.append(dict(budget='equal-budget mean',**stats([mean(x) for x in zip(*deltas)],z)))
    eligible=not smoke and bool(contrasts) and contrasts[-1]['family_99_ci'][0]>0 and all(c['family_99_ci'][1]>=0 for c in contrasts[:-1])
    verify_inputs(out,plan)
    if train_rl.sha(out/'challenges.json')!=plan['challenges_sha256']:raise RuntimeError('Curriculum changed')
    training={b['name']:evaluation.read(out/b['name']/'training-summary.json')['state'] for b in plan['branches']}
    result=dict(selection=selection,contrasts=contrasts,promotion_eligible=eligible,deployed_policy_changed=False,
                training=training,final=[{k:v for k,v in c.items() if k!='rows'} for c in final.values()])
    write_once(out/'summary.json',result)
    status(out,'complete',selection=selection,promotion_eligible=eligible)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--smoke',action='store_true')
    a=p.parse_args();out=a.output.resolve();existed=out.exists()
    try:run(out,a.smoke)
    except Exception as exc:
        if not existed and out.is_dir():status(out,'failed',error=str(exc),traceback=traceback.format_exc())
        raise
