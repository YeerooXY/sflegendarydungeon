"""Train a high-budget specialist, then freeze a budget router on fresh pilots.

The desktop default is never changed. The second curve uses independent seeds
from the first frozen-policy diagnostic and retains the parent below 1000.
"""
import argparse
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from statistics import mean

import torch
import train_rl
import benchmark_budget_curve as curve
from benchmark_player_ideas import write_once
from train_continuation_study import audit_seeds
from migrate_current_engine import migrate

ROOT=Path(__file__).resolve().parents[1]


def run(out):
    if out.exists():raise FileExistsError('Use a fresh experiment directory')
    roots=[2026101471,2026101472,2026101473]
    audit=audit_seeds(roots)
    out.mkdir(parents=True)
    for source,name in [(curve.CHECKPOINT,'parent.pt'),(ROOT/'build-v0610/Release/sfld_rl.dll','sfld_rl.dll'),
                        (ROOT/'profiles/synthetic.profile','synthetic.profile')]:shutil.copyfile(source,out/name)
    training={'schema':'sfld-high-budget-specialist-v1','root_seed':roots[0],
              'selection_seed':roots[1],'final_seed':roots[2],
              'budgets':[1000,2000,5000,10000,20000],'steps':16777216,
              'parent_sha256':train_rl.sha(out/'parent.pt'),
              'rule':'Retain parent below 1000; at higher operating caps compare parent and specialist '
                     'on fresh selection means. Freeze per-cap winner before final evaluation. '
                     'No change to the desktop default and no global optimality claim.'}
    write_once(out/'plan.json',training);write_once(out/'seed-audit.json',audit)
    command=[sys.executable,str(ROOT/'tools/train_rl.py'),'--mode','train','--finetune-from',str(out/'parent.pt'),
             '--output',str(out/'training'),'--library',str(out/'sfld_rl.dll'),'--profile',str(out/'synthetic.profile'),
             '--budgets',','.join(map(str,training['budgets'])),'--seed',str(roots[0]),'--device','cuda',
             '--torch-threads','2','--shop-policy','learned','--n-envs','512','--rollout-steps','128',
             '--total-steps',str(training['steps']),'--minibatch-size','2048','--validation-events','128',
             '--validate-every','64','--checkpoint-every','64','--learning-rate','0.0001',
             '--entropy-coefficient','0.01','--gae-lambda','0.99','--target-kl','0.02']
    # Re-attest current source metadata against the identical native DLL and
    # profile. Migration explicitly verifies unchanged weights and Adam state.
    migrate(out/'parent.pt',out/'parent-training.pt',train_rl.parse(command[2:]))
    command[command.index('--finetune-from')+1]=str(out/'parent-training.pt')
    write_once(out/'invocation.json',{'command':command})
    curve.status(out,'training',target_steps=training['steps']);start=time.monotonic()
    with (out/'training.log').open('x',encoding='utf-8') as log:
        child=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    if child.returncode:raise RuntimeError('Training failed; see training.log')
    best=curve.read(out/'training/best.json');chosen=out/'training'/best['checkpoint']
    if train_rl.sha(chosen)!=best['sha256']:raise RuntimeError('Training checkpoint changed')
    shutil.copyfile(chosen,out/'specialist.pt')
    curve.status(out,'trained',seconds=time.monotonic()-start,best=best['checkpoint'])
    # Reuse the declared accounting strategy, but independent new pilot/final seeds.
    original=curve.read(ROOT/'out/budget-ultimate-2026-10-14/plan.json')
    plan={**original,'schema':'sfld-budget-specialist-router-v1','checkpoint_sha256':best['sha256'],
          'selection':{'events':512,'seed':roots[1],'namespace':221},
          'final':{'events':4096,'seed':roots[2],'namespace':222},
          'trained_budget_range':[0,20000],
          'policy_description':'Parent below 1000; at >=1000 choose parent or new high-budget specialist '
             'on independent pilot mean completions, ties retain parent. Fixed before final.',
          'training':training,'specialist_selected_checkpoint':best,
          'inputs':{n:train_rl.sha(out/n) for n in ['parent.pt','specialist.pt','sfld_rl.dll','synthetic.profile']}}
    paths=[ROOT/name for name in original['sources']]+[Path(__file__),ROOT/'tools/migrate_current_engine.py']
    plan['sources']={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in paths}
    target=out/'curve';target.mkdir()
    for name in plan['inputs']:shutil.copyfile(out/name,target/name)
    for name in ('best.json','run.json','curve.jsonl','training-summary.json'):
        dest=target/'training'/name;dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(out/'training'/name,dest)
    shutil.copyfile(out/'seed-audit.json',target/'seed-audit.json')
    for p in paths:
        dest=target/'source'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    models={};candidates={};torch.set_num_threads(2)
    for name in ('parent','specialist'):
        saved,model=train_rl.load_checkpoint(out/f'{name}.pt',torch.device('cuda'));model.eval();models[name]=(saved,model)
        dest=out/('pilot-'+name);dest.mkdir()
        for resource in ('sfld_rl.dll','synthetic.profile'):shutil.copyfile(out/resource,dest/resource)
        candidate_plan={**plan,'checkpoint_sha256':train_rl.sha(out/f'{name}.pt')}
        supplies=plan['supplies'] if name=='parent' else [s for s in plan['supplies'] if s>=1000]
        candidates[name]={s:curve.collect(dest,candidate_plan,'selection',s,saved,model) for s in supplies}
        for s in supplies:
            retained=target/'candidate-pilots'/name/f'selection-supply-{s}.json.gz'
            retained.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(dest/f'selection-supply-{s}.json.gz',retained)
    router={};selection_cells={};comparisons=[]
    for s in plan['supplies']:
        parent=mean(r['completions'] for r in candidates['parent'][s]);special=None;choice='parent'
        if s>=1000:
            special=mean(r['completions'] for r in candidates['specialist'][s])
            if special>parent:choice='specialist'
        router[s]=choice;selection_cells[s]=candidates[choice][s]
        comparisons.append({'supply':s,'parent_clears':parent,'specialist_clears':special,'selected':choice})
        shutil.copyfile(out/('pilot-'+choice)/f'selection-supply-{s}.json.gz',target/f'selection-supply-{s}.json.gz')
    plan['cell_checkpoints']={str(s):train_rl.sha(out/f'{name}.pt') for s,name in router.items()}
    write_once(target/'plan.json',plan);write_once(target/'router-selection.json',comparisons)
    chosen=curve.select(plan,selection_cells);write_once(target/'selection.json',chosen)
    curve.status(out,'router_frozen',choices=comparisons)
    supplies=sorted({p['supply'] for p in chosen});cells={}
    for s in supplies:
        name=router[s];setup={**plan,'checkpoint_sha256':plan['cell_checkpoints'][str(s)]}
        cells[s]=curve.collect(target,setup,'final',s,*models[name])
    final=[];all_rows=[]
    for p in chosen:
        rows=[curve.project(r,p['budget'],p['supply'],p['reserved_entries'],p['mode']) for r in cells[p['supply']]]
        final.append({**p,'summary':curve.summarize(rows)});all_rows.extend(rows)
    with gzip.open(target/'final-plans.json.gz','wt',encoding='utf-8') as f:json.dump(all_rows,f,separators=(',',':'))
    normal=[p for p in chosen if p['mode']=='normal'];marginal=[]
    for a,b in zip(normal,normal[1:]):
        difference=curve.interval((y['completions']-x['completions'])*100/(b['budget']-a['budget'])
                                  for x,y in zip(cells[a['supply']],cells[b['supply']]))
        marginal.append({'from':a['budget'],'to':b['budget'],'extra_clears_per_100_mushrooms':difference})
    write_once(target/'summary.json',{'plan':plan,'results':final,'marginal':marginal,
        'selection_policy_weeks':sum(len(v) for c in candidates.values() for v in c.values()),
        'final_policy_weeks':len(supplies)*plan['final']['events']})
    curve.status(out,'complete',training_decisions=training['steps'],curve=str(target))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.output.resolve())
