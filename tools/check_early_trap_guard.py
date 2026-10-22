"""Fresh, declared comparison before deploying the UI-compatible effect policy."""
import argparse
import gzip
import itertools
import json
from pathlib import Path
import re
import shutil
from statistics import mean, NormalDist

import torch
import train_rl
import train_current_study as study
from benchmark_player_ideas import write_once
from publish_effect_study import stats
from sfld_rl.door_safety import EarlyTrapSafety

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = [0,20,50,100,500,1000]


class GuardedPractical(study.Practical):
    def __init__(self, names, actions, *args):
        super().__init__(names, actions, *args)
        self.early_traps = EarlyTrapSafety(names, actions)

    def apply(self, obs, masks, proposed):
        actual, targets, reasons = super().apply(obs, masks, proposed)
        return self.early_traps.apply(obs, masks, actual), targets, reasons


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(2)
    archive=ROOT/'docs/effect-exploration-2026-10-11'
    files={'parent.pt':archive/'parent-checkpoint.pt',
           'curriculum.pt':archive/'effect-curriculum/checkpoint-000016777216.pt',
           'sfld_rl.dll':archive/'sfld_rl.dll', 'synthetic.profile':archive/'synthetic.profile'}
    for name,path in files.items():study.copy(path,out/name)
    paths=['tools/check_early_trap_guard.py','tools/sfld_rl/door_safety.py',
           'tools/train_current_study.py','tools/sfld_rl/shop_policy.py','tools/sfld_rl/practical.py']
    plan={'budgets':BUDGETS,'final':{'seed':12241001,'namespace':247,'events':2048},
          'profile_sha256':train_rl.sha(out/'synthetic.profile'),'library_sha256':train_rl.sha(out/'sfld_rl.dll'),
          'sources':{x:train_rl.sha(ROOT/x) for x in paths},
          'purpose':'UI acceptance caught a stage-1 trap preference regression. Keep a safe visible monster when the proposed other door has a trap, only before room 25. Other choices are unchanged.',
          'candidate_selected_before_final':'guarded',
          'gate':'Guarded curriculum minus parent: positive lower family-adjusted 99% interval for the equally weighted budget mean. All 3 pairs x (6 budgets + pooled) = 21 contrasts.',
          'no_training':True,'no_probability_changes':True}
    write_once(out/'plan.json',plan)
    if not (out/'seed-audit.json').exists():
        roots=set();pattern=re.compile(r'"(?:root_seed|seed)"\s*:\s*(\d+)')
        for path in (ROOT/'docs').rglob('*.json'):roots.update(map(int,pattern.findall(path.read_text(encoding='utf-8-sig'))))
        fp=train_rl.seed_fingerprint(plan['final']['seed'])
        if fp in {train_rl.seed_fingerprint(x) for x in roots}:raise ValueError('Overlapping root fingerprint')
        write_once(out/'seed-audit.json',{'seed':plan['final']['seed'],'fingerprint':fp,'overlap':False})
    for name in paths:study.copy(ROOT/name,out/'source'/name)
    candidates=[{'name':n,'checkpoint':c,'sha256':train_rl.sha(out/c),'steps':0,'style':'practical'}
                for n,c in [('parent','parent.pt'),('curriculum','curriculum.pt'),('guarded','curriculum.pt')]]
    write_once(out/'candidates.json',candidates)
    original=study.Practical;cells={}
    try:
        for candidate in candidates:
            study.Practical=GuardedPractical if candidate['name']=='guarded' else original
            for budget in BUDGETS:cells[candidate['name'],budget]=study.evaluate(out,plan,candidate,'final',budget)
    finally:study.Practical=original
    for name,digest in plan['sources'].items():
        if train_rl.sha(ROOT/name)!=digest:raise ValueError('Frozen guard study source changed')
    contrasts=[];z=NormalDist().inv_cdf(1-.01/(2*21))
    for old,new in itertools.combinations([x['name'] for x in candidates],2):
        by_budget=[]
        for b in BUDGETS:
            values=[n['completions']-o['completions'] for n,o in zip(cells[new,b]['rows'],cells[old,b]['rows'])]
            by_budget.append(values);contrasts.append({'old':old,'new':new,'budget':b,**stats(values,z)})
        contrasts.append({'old':old,'new':new,'budget':'equal-budget mean',**stats([mean(x) for x in zip(*by_budget)],z)})
    tested=next(x for x in contrasts if x['old']=='parent' and x['new']=='guarded' and x['budget']=='equal-budget mean')
    summary={'plan':plan,'candidates':candidates,'contrasts':contrasts,'promotion':tested['family_99_ci'][0]>0,
             'cells':[{k:v for k,v in c.items() if k!='rows'} for c in cells.values()]}
    write_once(out/'summary.json',summary)
    with gzip.open(out/'final-rows.jsonl.gz','wt',encoding='utf-8') as f:
        for c in cells.values():
            for row in c['rows']:f.write(json.dumps(row)+'\n')
    print(json.dumps({'promotion':summary['promotion'],'guarded_vs_parent':tested}),flush=True)


if __name__=='__main__':main()
