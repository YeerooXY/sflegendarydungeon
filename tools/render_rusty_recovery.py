"""Verify and publish the frozen conditional Rusty recovery experiment."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from statistics import NormalDist, mean, stdev

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
NAMES={
    'rusty_s2':'Rusty (room 35)',
    'rusty_greed_s3':'Rusty + Greed (room 60)',
    'rusty_moon_s3':'Rusty + Moonstone (room 60)',
    'rusty_greasy_s3':'Rusty + Greasy (room 60)',
    'rusty_greasy_time_s4':'Rusty + Greasy + Time Traveler (room 85)',
    'rusty_greasy_moon_s4':'Rusty + Greasy + Moonstone (room 85)',
    'rusty_rabbit_moon_s4':'Rusty + Rabbit + Moonstone (room 85)',
}
LABELS={'control':'Current policy','hp20':'20%','hp30_1':'30.1%','hp40':'40%',
        'hp50':'50%','hp60':'60%','hp80':'80%','hp100':'100%','one_hit':'One maximum hit'}


def render(out):
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    plan=read(out/'plan.json');summary=read(out/'summary.json');selection=read(out/'selection.json')
    if read(out/'status.json')['phase']!='complete':raise RuntimeError('Study incomplete')
    checks={}
    for name,digest in plan['sources'].items():
        checks['frozen:'+name]=sha(out/'source'/name)==digest
        checks['current:'+name]=sha(ROOT/name)==digest
    for name,key in [('checkpoint.pt','checkpoint_sha256'),('sfld_rl.dll','library_sha256'),('synthetic.profile','profile_sha256')]:
        checks[name]=sha(out/name)==plan[key]
    cells={};runs=0;decisions=0
    for phase in ('screening','confirmation'):
        count=plan[phase]['events'];paths=list((out/phase).glob('*.json'))
        expected=len(plan['cases'])*2*(len(plan['targets']) if phase=='screening' else 2)
        checks[phase+':cells']=len(paths)==expected
        for path in paths:
            c=read(path);rows=c['rows'];runs+=len(rows);decisions+=sum(r['actions'] for r in rows)
            checks[path.stem+':'+phase]=(
                [r['event'] for r in rows]==list(range(count)) and
                all(r['initial_keys']==6*(r['event']%2) and 0<=r['hours']<=168 and
                    (r['completed'] or r['hours']==168) for r in rows) and
                abs(mean(r['hours'] for r in rows)-c['summary']['hours']['mean'])<1e-9)
            if phase=='confirmation':cells[c['case']['name'],c['variant'],c['wake']]=c
    z=NormalDist().inv_cdf(1-.05/(2*len(summary['contrasts'])))
    for contrast in summary['contrasts']:
        name=contrast['case'];v=contrast['variant'];w=contrast['wake']
        d=[b['hours']-a['hours'] for a,b in zip(cells[name,'control',w]['rows'],cells[name,v,w]['rows'])]
        m=mean(d);se=stdev(d)/len(d)**.5
        checks[f'interval:{name}:{w}']=np.allclose([m-z*se,m+z*se],contrast['simultaneous_95']['ci'],atol=1e-10,rtol=0)
    if not all(checks.values()):raise RuntimeError([k for k,v in checks.items() if not v])
    (out/'verification.json').write_text(json.dumps(dict(checks=checks,passed=True,conditional_runs=runs,
        simulated_actions=int(decisions)),indent=2)+'\n',encoding='utf-8')
    results=[]
    for c in summary['contrasts']:
        name=c['case'];v=c['variant'];w=c['wake'];a=cells[name,'control',w]['summary'];b=cells[name,v,w]['summary']
        results.append(dict(case=name,target=LABELS[v],sleep_until_hour=w,control_hours=a['hours']['mean'],
            target_hours=b['hours']['mean'],difference_hours=c['simultaneous_95']['mean'],
            simultaneous_95_low=c['simultaneous_95']['ci'][0],simultaneous_95_high=c['simultaneous_95']['ci'][1],
            control_deaths=a['deaths']['mean'],target_deaths=b['deaths']['mean'],
            control_noncompletion=1-a['completed']['mean'],target_noncompletion=1-b['completed']['mean'],
            control_mean_ordinary_entry_hp=100*a['ordinary_hp_sum']['mean']/a['ordinary_reentries']['mean'],
            target_mean_ordinary_entry_hp=100*b['ordinary_hp_sum']['mean']/b['ordinary_reentries']['mean']))
    with (out/'results.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    fig,axes=plt.subplots(1,2,figsize=(12.5,5.4),sharey=True,sharex=True)
    names=list(NAMES);y=np.arange(len(names))
    for ax,w,title in zip(axes,(0,8),('Continuous availability','No actions 00:00-08:00')):
        rs=[next(r for r in results if r['case']==name and r['sleep_until_hour']==w) for name in names]
        for i,r in enumerate(rs):
            lo=r['simultaneous_95_low'];hi=r['simultaneous_95_high'];m=r['difference_hours']
            color='#147a54' if hi<0 else '#52677d'
            ax.errorbar(m,i,xerr=[[m-lo],[hi-m]],fmt='o',color=color,capsize=4)
        ax.axvline(0,color='#555555',linewidth=1);ax.grid(axis='x',alpha=.2)
        ax.set_title(title);ax.set_xlabel('Change in remaining completion time (hours)\nNegative means faster')
    axes[0].set_yticks(y,[NAMES[n]+' | '+LABELS[selection['selected'][n]] for n in names]);axes[0].invert_yaxis()
    fig.suptitle('Conditional Rusty recovery: independent confirmation',fontweight='bold')
    fig.text(.5,.02,f'{plan["confirmation"]["events"]:,} paired trajectories per comparison; simultaneous 95% sampling intervals.\n'
             'Synthetic death states, zero mushrooms; unfinished runs count as 168 hours. Not weekly forecasts.',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.09,1,.95]);fig.savefig(out/'recovery-comparison.png',dpi=170)
    fig.savefig(out/'recovery-comparison.svg');plt.close(fig)
    lines=['# Rusty recovery and stone history - 15 October 2026','',
        'Rusty amplifies Greasy healing, but its poison costs more than that amplification adds. '
        'Higher entry HP can reduce repeated poison exposure; it must be tested separately from builds with Greasy.',
        '', '## Verified healing totals','',
        '| Stones | Healing | Poison | Net HP |','|---|---:|---:|---:|',
        '| Greasy | 3 x 10% = 30% | 0% | +30% |',
        '| Greasy + Time Traveler | 4 x 10% = 40% | 0% | +40% |',
        '| Rusty + Greasy | 3 x 14% = 42% | 5 x 5% = 25% | +17% |',
        '| Rusty + Greasy + Time Traveler | 4 x 14% = 56% | 5 x 5% = 25% | +31% |',
        '| Rusty + Greasy + Moonstone | 3 x 14% = 42% | 6 x 5% = 30% | +12% |','',
        'These are percentage points of maximum HP, over all active ordinary-room ticks. '
        'They exclude incoming hits, overhealing, effect replacement, premature death and boss exits that consume duration without these ticks. '
        'The incoming hit must be survived first; capped healing then happens before poison. '
        'Rusty does not multiply natural recovery or paid refills. Three stone slots prevent holding all four stones together.',
        '', '## Rejected stones','',
        'The current profile excludes every previously offered stone from later menus in that run. '
        'The native engine and browser encoder expose the same 23 offered-stone flags to the current 460-input actor. '
        'The rating helper also removes rejected stones from hypothetical future menus. '
        'History survives death and resets after room 100. Imported progress must contain the previous offers; missing history cannot be reconstructed.',
        '', 'This gives the policy the relevant information. It does not prove that the trained network always values unavailable combinations correctly. '
        'Focused tests check native/browser input equality and that rejected Greasy never returns in 64 generated menus.',
        '', '## Experiment','',
        f'{runs:,} conditional trajectories: 128 screening trajectories per target/case/schedule, then '
        f'{plan["confirmation"]["events"]:,} fresh trajectories for the current policy and one preselected alternative per case/schedule. '
        'Targets screened: 20%, 30.1%, 40%, 50%, 60%, 80%, 100%, and enough for one modeled maximum hit, plus the current policy. '
        'One alternative per stone case was chosen on the equally weighted two-schedule screening average before confirmation.',
        '', plan['fixtures'], '',
        'Each trajectory starts at zero HP at 17:00, in run two, with zero or six keys in equal proportions. '
        'Current-stage shops are pending; at room 85 that means the shop is due immediately. '
        'There is no mushroom spending. The target changes natural ordinary-room reentry only. '
        'Boss, combat, doors, shops and stone choices keep the existing actor. '
        'If a case without Greasy subsequently gains it, its higher-target rule stops applying. '
        'When the event deadline prevents reaching the target, an already legal reentry can occur earlier.',
        '', 'The measured outcome is hours from the initial death to completing the current run, capped at 168 hours. '
        'Unfinished trajectories remain in the average at 168; they are not discarded. '
        'Daily sleep can overshoot a target. The actor is frozen, not retrained.',
        '', '## What the comparison found','',
        'Selected targets: '+ '; '.join(NAMES[n]+' -> '+LABELS[selection['selected'][n]] for n in names)+'. '
        'These are selection results, not proof of optimal thresholds.',
        '', f'{sum(r["simultaneous_95_high"]<0 for r in results)} of the 14 fresh confirmation comparisons '
        'support faster completion at the simultaneous 95% level. The tables below distinguish supported '
        'improvements from estimates whose intervals still include no improvement.',
        '', '### Screening means, equally weighting the two schedules','',
        'These smaller-sample values selected the candidate. They are not the independent confirmation results.', '',
        '| Starting stones and room | Current | 20% | 30.1% | 40% | 50% | 60% | 80% | 100% | One hit |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name in names:
        lines += ['| '+NAMES[name]+' | '+' | '.join(f'{selection["scores"][name][v]:.2f}' for v in plan['targets'])+' |']
    lines += ['', '## Independent confirmation','',
        'Negative time differences are improvements. Intervals use a Bonferroni adjustment across all 14 comparisons '
        'for simultaneous 95% sampling coverage under the simulator. They do not include uncertainty in the real game mechanics.', '']
    for w,title in ((0,'Continuous availability'),(8,'Daily sleep 00:00-08:00')):
        lines += [f'### {title}','',
            '| Starting stones and room | Target | Current hours | Target hours | Difference, simultaneous 95% CI | Deaths, current -> target | Unfinished, current -> target |',
            '|---|---:|---:|---:|---|---:|---:|']
        for r in results:
            if r['sleep_until_hour']!=w:continue
            lines += [f'| {NAMES[r["case"]]} | {r["target"]} | {r["control_hours"]:.2f} | {r["target_hours"]:.2f} | '
                      f'{r["difference_hours"]:+.2f} [{r["simultaneous_95_low"]:+.2f}, {r["simultaneous_95_high"]:+.2f}] | '
                      f'{r["control_deaths"]:.2f} -> {r["target_deaths"]:.2f} | '
                      f'{100*r["control_noncompletion"]:.2f}% -> {100*r["target_noncompletion"]:.2f}% |']
        lines += ['']
    lines += ['![Independent recovery comparison](recovery-comparison.png)','', '## Scope and validation','',
        'This tests recovery after already acquiring Rusty. It does not show that choosing Rusty beats choosing another stone, '
        'nor that the selected HP target is globally optimal. Different starting rooms, shops, keys and rejected histories can change the result. '
        'The deployed policy and mechanics are unchanged; use these results as evidence for a future state-dependent rule, not a new weekly score.',
        '', 'Five focused Python/native tests and the existing CTest gem-history check pass. '
        'The experiment rejects invalid actions, truncated episodes, duplicate event IDs and mushroom spending. '
        'Artifact verification independently checks all trajectory IDs, paired intervals and frozen source/checkpoint/profile/library hashes. '
        'Raw screening and confirmation data, `selection.json`, `results.csv`, `seed-audit.json` and `verification.json` are preserved alongside this report.',
        '', 'Relevant code: `tools/sfld_rl/rusty_recovery.py`, `tools/benchmark_rusty_recovery.py`, '
        '`tests/test_rusty_recovery.py`; existing history paths are `src/engine.cpp`, `src/rl_env.cpp`, '
        '`tools/sfld_rl/visual_policy.py` and `tools/sfld_rl/visual_ratings.py`.', '']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(verified=True,conditional_runs=runs,simulated_actions=int(decisions),results=results),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    render(p.parse_args().output.resolve())
