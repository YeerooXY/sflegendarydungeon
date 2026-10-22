"""Re-run the key comparisons under each uncertain-value variant profile.

For every profile: the deployed adviser versus adviser plus deadline_heal
(paired weeks), and the adviser versus the handwritten strategic policy
(independent native runs). The adviser and its rules keep assuming the
shipped profile, as they would if the live game differed from it.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def job(command):
    r=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if r.returncode:raise RuntimeError(' '.join(map(str,command))+'\n'+r.stderr[-1500:])


def main(args):
    out=args.output.resolve();profiles=[ROOT/'profiles/synthetic.profile']+sorted((out/'profiles').glob('*.profile'))
    jobs=[]
    for profile in profiles:
        name='shipped' if profile.parent.name=='profiles' and profile.parent.parent==ROOT else profile.stem
        for budget in args.budgets:
            target=out/'weeks'/f'{name}-budget-{budget}.json'
            if not target.exists():
                jobs.append([sys.executable,'tools/benchmark_candidate_week.py','--output',str(out/'weeks'),'--budget',str(budget),
                             '--rules','deadline_heal','--label',name,'--weeks',str(args.weeks),'--profile',str(profile)])
            native=out/'strategic'/f'{name}-budget-{budget}.json'
            if not native.exists():
                native.parent.mkdir(parents=True,exist_ok=True)
                jobs.append([str(ROOT/'build-ui-decisions/Release/sfld.exe'),'event','--allow-assumptions','--strategy','strategic',
                             '--profile',str(profile),'--budget',str(budget),'--deadline-hours','168','--event-start-hour','17',
                             '--runs',str(args.native_runs),'--threads','2','--seed','2026102208','--output',str(native)])
    with ThreadPoolExecutor(args.workers) as pool:
        for i,_ in enumerate(pool.map(job,jobs)):print(json.dumps(dict(done=i+1,total=len(jobs))),flush=True)
    rows=[]
    for profile in profiles:
        name='shipped' if profile.parent.parent==ROOT else profile.stem
        for budget in args.budgets:
            w=json.loads((out/'weeks'/f'{name}-budget-{budget}.json').read_text())['summary']['metrics']['completions']
            s=json.loads((out/'strategic'/f'{name}-budget-{budget}.json').read_text())
            se_base=json.loads((out/'weeks'/f'{name}-budget-{budget}.json').read_text())
            import numpy as np
            base=np.array(se_base['base'])[:,0];adviser_se=float(base.std(ddof=1)/len(base)**.5)
            gap=w['base']-s['mean_completions'];gap_se=math.hypot(adviser_se,s['mean_completions_mc_se'])
            rows.append(dict(profile=name,budget=budget,adviser=w['base'],deadline_heal_gain=w['difference'],deadline_heal_ci=w['ci'],
                             strategic=s['mean_completions'],adviser_minus_strategic=gap,gap_ci=[gap-1.96*gap_se,gap+1.96*gap_se]))
    (out/'summary.json').write_text(json.dumps(rows,indent=1)+'\n',encoding='utf-8')
    print(f"{'profile':20} {'budget':>6} {'adviser':>8} {'strategic':>9} {'gap':>8} {'gap 95% CI':>18} {'deadline_heal':>14}")
    for r in rows:
        print(f"{r['profile']:20} {r['budget']:>6} {r['adviser']:8.3f} {r['strategic']:9.3f} {r['adviser_minus_strategic']:+8.3f} "
              f"[{r['gap_ci'][0]:+.3f},{r['gap_ci'][1]:+.3f}] {r['deadline_heal_gain']:+8.4f}{'*' if r['deadline_heal_ci'][0]>0 or r['deadline_heal_ci'][1]<0 else ' '}")


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'out/robustness-2026-10-22')
    p.add_argument('--budgets',type=int,nargs='+',default=[0,100,1000]);p.add_argument('--weeks',type=int,default=2048)
    p.add_argument('--native-runs',type=int,default=20000);p.add_argument('--workers',type=int,default=4)
    main(p.parse_args())
