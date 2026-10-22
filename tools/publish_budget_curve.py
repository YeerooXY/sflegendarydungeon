"""Validate, archive and draw the frozen-policy mushroom/Ultimate experiment."""
import argparse
import csv
import gzip
import hashlib
import html
import json
from pathlib import Path
import shutil
from statistics import mean

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from benchmark_budget_curve import project, summarize, interval, select


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(source):
    result=read(source/'summary.json');plan=result['plan'];selection=read(source/'selection.json')
    for name,expected in plan['inputs'].items():
        if digest(source/name)!=expected:raise ValueError('Input hash changed: '+name)
    for name,expected in plan['sources'].items():
        if digest(source/'source'/name)!=expected:raise ValueError('Source hash changed: '+name)
    cells={}
    for path in source.glob('*-supply-*.json.gz'):
        cohort=path.name.split('-supply-')[0]
        with gzip.open(path,'rt',encoding='utf-8') as f:cell=json.load(f)
        rows=cell['rows'];supply=cell['supply'];setup=plan[cohort]
        expected_checkpoint=plan.get('cell_checkpoints',{}).get(str(supply),plan['checkpoint_sha256'])
        if cell['cohort']!=setup or cell['checkpoint_sha256']!=expected_checkpoint:
            raise ValueError('Cell identity changed')
        if [r['event'] for r in rows]!=list(range(setup['events'])):raise ValueError('Missing or duplicate event')
        for row in rows:
            if len(row['prefixes'])!=row['completions']:raise ValueError('Missing completion prefix')
            old_spent=old_time=0
            for n,prefix in enumerate(row['prefixes'],1):
                if prefix['completions']!=n or prefix['runs_started']!=n:raise ValueError('Bad run prefix')
            for snap in row['prefixes']+[row]:
                if (snap['mushrooms']>supply or snap['mushrooms']<old_spent or
                    snap['mushrooms']!=snap['recovery_mushrooms']+snap['reroll_mushrooms'] or
                    snap['invalid_actions'] or snap['legendaries']<snap['completions'] or
                    snap['elapsed_hours']<old_time or snap['elapsed_hours']>168.000001):
                    raise ValueError('Trajectory accounting failed')
                old_spent=snap['mushrooms'];old_time=snap['elapsed_hours']
        cells[cohort,supply]=rows
    if 'cell_checkpoints' in plan:
        router=read(source/'router-selection.json')
        for row in router:
            s=row['supply'];scores={}
            for name in ('parent','specialist') if s>=1000 else ('parent',):
                path=source/'candidate-pilots'/name/f'selection-supply-{s}.json.gz'
                with gzip.open(path,'rt',encoding='utf-8') as f:cell=json.load(f)
                if (cell['checkpoint_sha256']!=plan['inputs'][name+'.pt'] or cell['cohort']!=plan['selection']
                    or [r['event'] for r in cell['rows']]!=list(range(plan['selection']['events']))):
                    raise ValueError('Router pilot identity failed')
                scores[name]=mean(r['completions'] for r in cell['rows'])
            choice='specialist' if scores.get('specialist',-1)>scores['parent'] else 'parent'
            if row['selected']!=choice or plan['cell_checkpoints'][str(s)]!=plan['inputs'][choice+'.pt']:
                raise ValueError('Policy router selection changed')
        training=read(source/'training/training-summary.json')
        if training['state']['steps']!=plan['training']['steps']:raise ValueError('Incomplete specialist training')
    if select(plan,{s:cells['selection',s] for s in plan['supplies']})!=selection:
        raise ValueError('Selection could not be reproduced')
    projections={}
    for cell in result['results']:
        rows=[project(r,cell['budget'],cell['supply'],cell['reserved_entries'],cell['mode'])
              for r in cells['final',cell['supply']]]
        if summarize(rows)!=cell['summary']:raise ValueError('Final summary changed')
        projections[cell['mode'],cell['budget']]=rows
    for row in result['marginal']:
        a=projections['normal',row['from']];b=projections['normal',row['to']]
        expected=interval((y['completions']-x['completions'])*100/(row['to']-row['from']) for x,y in zip(a,b))
        if expected!=row['extra_clears_per_100_mushrooms']:raise ValueError('Marginal comparison changed')
    return result,projections


def plots(target,result,baseline=None):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
                         'axes.spines.right':False,'axes.titleweight':'bold','axes.grid':True,
                         'grid.alpha':.18,'savefig.facecolor':'#ffffff'})
    fig,axs=plt.subplots(2,2,figsize=(14,9),layout='constrained')
    colors={'normal':'#1768ac','ultimate':'#bd6c08','mixed':'#7544a8'}
    adapted='cell_checkpoints' in result['plan']
    labels={'normal':'Normal: budget-trained policy' if adapted else 'Normal: current policy','ultimate':'Ultimate only: reserved entries',
            'mixed':'Pilot-selected mix (may use normal only)'}
    def series(ax,mode,metric,label=None,maximum=None,style='-'):
        rows=[r for r in result['results'] if r['mode']==mode and (maximum is None or r['budget']<=maximum)]
        x=[r['budget'] for r in rows];y=[r['summary'][metric]['mean'] for r in rows]
        lo=[r['summary'][metric]['ci95'][0] for r in rows];hi=[r['summary'][metric]['ci95'][1] for r in rows]
        ax.plot(x,y,style,marker='o',markersize=3.5,lw=2,color=colors[mode],label=label or labels[mode])
        ax.fill_between(x,lo,hi,color=colors[mode],alpha=.16)
    series(axs[0,0],'normal','completions')
    if baseline:
        earlier=[r for r in baseline['results'] if r['mode']=='normal']
        baseline_label=('Same policies: engine '+baseline['plan']['engine'] if 'cell_checkpoints' in baseline['plan']
                        else 'Previous policy: trained only to 1,000')
        axs[0,0].plot([r['budget'] for r in earlier],[r['summary']['completions']['mean'] for r in earlier],
                      ':',color='#7b8795',lw=2,label=baseline_label)
        axs[0,0].legend(fontsize=8,loc='upper left')
    axs[0,0].set(title='Normal dungeon: completions vs budget',ylabel='Mean completions in 7 days',xlabel='Weekly mushroom cap')
    axs[0,0].axvline(1000,color='#64748b',ls=':',lw=1.4)
    note='Parent / specialist chosen on a separate pilot\nSpecialist trained through 20,000' if adapted else 'Training covered caps up to 1,000\nHigher caps test extrapolation'
    axs[0,0].text(.98,.06,note,
                  transform=axs[0,0].transAxes,ha='right',color='#596679',fontsize=9)
    series(axs[0,1],'normal','completions',maximum=1000)
    axs[0,1].set(title='Closer look: 0–1,000 mushrooms',ylabel='Mean completions in 7 days',xlabel='Weekly mushroom cap')
    for mode in ('normal','ultimate','mixed'):
        series(axs[1,0],mode,'legendaries',style='--' if mode=='mixed' else '-')
    axs[1,0].set(title='Legendary items: entrance fees included',ylabel='Mean legendary items in 7 days',xlabel='Total weekly mushroom cap')
    axs[1,0].legend(fontsize=8,loc='upper left')
    m=result['marginal'];x=[(r['from']+r['to'])/2 for r in m]
    y=[r['extra_clears_per_100_mushrooms']['mean'] for r in m]
    e=[1.959964*r['extra_clears_per_100_mushrooms']['standard_error'] for r in m]
    axs[1,1].errorbar(x,y,yerr=e,color=colors['normal'],marker='o',capsize=3,lw=1.5)
    axs[1,1].axhline(0,color='#64748b',lw=1)
    axs[1,1].axhline(200/650,color=colors['ultimate'],ls='--',lw=1.2,label='Ultimate reference: 2 extra boss items / 650')
    axs[1,1].legend(fontsize=8,loc='best')
    axs[1,1].set_xscale('symlog',linthresh=500)
    axs[1,1].set(title='Marginal return from the next 100 mushrooms',ylabel='Additional clears per 100 mushrooms',
                  xlabel='Midpoint of tested budget interval (compressed above 500)')
    axs[1,1].set_xlim(0,20000)
    axs[1,1].set_xticks([0,100,500,1000,5000,15000], ['0','100','500','1,000','5,000','15,000'])
    fig.suptitle('Mushroom returns · engine '+result['plan']['engine']+' · 168-hour events',fontsize=18,fontweight='bold')
    fig.get_layout_engine().set(rect=(0,.045,1,.95))
    fig.text(.5,.018,f"{result['plan']['final']['events']:,} fresh weeks per operating cap · pointwise 95% confidence intervals · "
             'Ultimate costs 650 per run; ×3 bonus-item rewards are a scenario assumption',ha='center',fontsize=9)
    for ext in ('png','svg','pdf'):fig.savefig(target/f'mushroom_returns.{ext}',dpi=180)
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,5),layout='constrained')
    for mode in ('normal','ultimate','mixed'):
        rows=[r for r in result['results'] if r['mode']==mode]
        x=[r['budget'] for r in rows]
        for ax,metric,scale in ((axes[0],'gold_units',.01),(axes[1],'epics',1)):
            y=[r['summary'][metric]['mean']*scale for r in rows]
            lo=[r['summary'][metric]['ci95'][0]*scale for r in rows]
            hi=[r['summary'][metric]['ci95'][1]*scale for r in rows]
            ax.plot(x,y,'--' if mode=='mixed' else '-',color=colors[mode],lw=2,label=labels[mode])
            ax.fill_between(x,lo,hi,color=colors[mode],alpha=.15)
    axes[0].set(title='Gold at 10 million per relative reward unit',xlabel='Total weekly mushroom cap',ylabel='Mean gold in billions / 7 days')
    axes[1].set(title='Epic items under the requested ×3 scenario',xlabel='Total weekly mushroom cap',ylabel='Mean epic items / 7 days')
    axes[0].legend(fontsize=8)
    fig.suptitle('Other loot from the same plans · selected for legendary items',fontsize=15,fontweight='bold')
    fig.get_layout_engine().set(rect=(0,.045,1,.94))
    fig.text(.5,.018,'Pointwise 95% intervals · gold scaling is illustrative; all-epic tripling is a scenario assumption',ha='center',fontsize=9)
    for ext in ('png','svg','pdf'):fig.savefig(target/f'loot_returns.{ext}',dpi=180)
    plt.close(fig)


def outputs(target,result,projections):
    normal={r['budget']:r for r in result['results'] if r['mode']=='normal'}
    ultimate={r['budget']:r for r in result['results'] if r['mode']=='ultimate'}
    mixed={r['budget']:r for r in result['results'] if r['mode']=='mixed'}
    base=normal[0]['summary']['legendaries']['mean']
    table=[]
    for r in result['results']:
        s=r['summary'];gain=s['legendaries']['mean']-base
        table.append({'budget':r['budget'],'mode':r['mode'],'operating_cap':r['supply'],
          'reserved_ultimate_entries':r['reserved_entries'],
          'clears':s['completions']['mean'],'clears_ci95_low':s['completions']['ci95'][0],
          'clears_ci95_high':s['completions']['ci95'][1],
          'legendaries':s['legendaries']['mean'],'legendaries_ci95_low':s['legendaries']['ci95'][0],
          'legendaries_ci95_high':s['legendaries']['ci95'][1],
          'legendaries_only_final_boss_tripled':s['legendary_final_boss_only']['mean'],
          'actual_mushrooms':s['mushrooms']['mean'],'entry_mushrooms':s['entry_mushrooms']['mean'],
          'healing_mushrooms':s['recovery_mushrooms']['mean'],'reroll_mushrooms':s['reroll_mushrooms']['mean'],
          'arena_legendaries':s['arena_legendaries']['mean'],'epics_scenario':s['epics']['mean'],
          'gold_billions_at_10m_per_unit':s['gold_units']['mean']*.01,
          'mushrooms_per_extra_legendary_vs_free':s['mushrooms']['mean']/gain if gain>0 else None})
    with (target/'results.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=table[0].keys());w.writeheader();w.writerows(table)
    contrasts=[]
    for budget in sorted(ultimate):
        if budget not in normal:continue
        for mode in ('ultimate','mixed'):
            left=projections['normal',budget];right=projections[mode,budget]
            contrasts.append({'budget':budget,'mode':mode,
                'extra_legendaries_vs_normal':interval(b['legendaries']-a['legendaries'] for a,b in zip(left,right))})
    (target/'paired-comparisons.json').write_text(json.dumps(contrasts,indent=2)+'\n',encoding='utf-8')
    adapted='cell_checkpoints' in result['plan']
    transferred=result['plan'].get('selection_engine')
    intro=('A separate high-budget specialist added 16,777,216 training decisions at caps of '
           '1,000, 2,000, 5,000, 10,000 and 20,000 mushrooms. A fresh pilot chooses between it and '
           'the parent for each operating cap of at least 1,000; the parent is retained below that. '
           'The policy choices and Ultimate allocations are frozen before the final test. '
           'The desktop default is unchanged. These results do not prove an optimal strategy.' if adapted else
           'These are new tests of the October 14 frozen adviser, not additional training. '
           'They measure what this policy does with larger budgets; they do not establish a gameplay ceiling.')
    engine_note=('The final test uses **engine 0.6.11**, including the Recovery timing, combat-curse and '
                 'refill corrections now in the desktop. Training and the selection pilot used **0.6.10**. '
                 'Policies and entry reservations were transferred unchanged, with compatible inputs/actions '
                 'verified before inference. No plan was selected using these newer-engine outcomes.' if transferred else
                 'The final test uses frozen engine **0.6.10**, the same mechanics as the earlier training '
                 'benchmark. The desktop now uses engine 0.6.11, whose Recovery timing, combat-curse and '
                 'refill corrections can change these results. These graphs are not a benchmark of that newer version.')
    text=['# Mushroom budgets and Ultimate Dungeon', '',intro, '',engine_note, '',
          '![Budget and reward curves](mushroom_returns.png)', '',
          f"The experiment uses {result['selection_policy_weeks']:,} pilot policy-weeks and "
          f"{result['final_policy_weeks']:,} fresh final policy-weeks. Each final operating cap has "
          f"{result['plan']['final']['events']:,} independent 168-hour events. The same event seeds pair comparisons. "
          'Random eligible stones and the frozen profile are retained. The profile allows two seconds per '
          'action and continuous availability for recovery; it does not model a human sleep/work schedule.', '',
          '| Total cap | Normal clears | Normal legendary items | Ultimate-only legendary items | Pilot-selected mix: legendary items |',
          '|---:|---:|---:|---:|---:|']
    for b,r in normal.items():
        f=lambda cell,key:f"{cell['summary'][key]['mean']:.3f}"
        text.append(f"| {b:,} | {f(r,'completions')} | {f(r,'legendaries')} | "
                    f"{f(ultimate[b],'legendaries') if b in ultimate else '—'} | {f(mixed[b],'legendaries')} |")
    text+=['','## Additional mushrooms are not a fixed conversion rate','',
           '| Budget interval | Additional clears per 100 mushrooms | Paired 95% interval |',
           '|---:|---:|---:|']
    for r in result['marginal']:
        s=r['extra_clears_per_100_mushrooms'];lo,hi=s['ci95']
        text.append(f"| {r['from']:,}–{r['to']:,} | {s['mean']:.3f} | [{lo:.3f}, {hi:.3f}] |")
    budget_note=('The parent was trained through 1,000 mushrooms and the specialist through 20,000. '
                 'Per-cap policy selection is based on pilot mean completions, not the final results. '
                 'Remaining gains from further training or a different policy remain possible.' if adapted else
                 'The actor was trained only through 1,000 mushrooms; poor returns above that point are not evidence '
                 'that a policy trained for larger budgets could not improve them.')
    text+=['','A flat line in the marginal-return panel would indicate constant returns over the sampled range. '
           'The measured changes combine game mechanics, the weekly deadline and the frozen policy. '+budget_note, '',
           'A useful marginal benchmark is **650 / 2 = 325 mushrooms per extra final-boss legendary**: '
           'upgrading a run gives two additional guaranteed boss items compared with completing that same run '
           'normally. If another normal completion costs less than 325 mushrooms, normal play can be the '
           'better use of extra funds. This simple reference excludes trial rewards, unfinished entries, '
           'time limits and discrete entry counts; the full event comparison accounts for them.', '',
           '## What Ultimate means in this test','',
           'The player confirmed **650 mushrooms per new run**, charged on entry, including an unfinished final run. '
           'Deaths do not repay the entry fee. Final-boss legendary items and gold are multiplied by three. '
           '[The official Ultimate help page](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/201-legendary-dungeon-ultimate-1670924829/) '
           'also lists triple resources, excluding keys, blessings and curses. It does not explicitly verify triple '
           'arena rewards or all other items. The main graph includes those item multipliers as the requested scenario; '
           '`results.csv` also reports legendary counts when only final-boss drops are tripled.', '',
           'Triple rewards mean three items from a successful reward roll, not three independent trials or a tripled '
           'chance of the roll succeeding. The existing arena reward probabilities are unchanged: '
           'the current profile uses **6.25%** for the fifth-tier legendary roll, not the earlier 10% placeholder. '
           'These policies were trained for completions, not separately for arena loot. Ordinary-item '
           'counts are not available from the batch API, so they are not invented here. Epic counts are reported '
           'under the triple-item scenario. Gold uses the requested reporting scale of 10 million per relative unit.', '',
           'For each total budget, the pilot chooses an operating cap and a number of Ultimate entries to reserve '
           'before the event. Operating spending cannot use the reserved fees. The pure-Ultimate plan stops after '
           'its reserved runs; the mixed plan continues with normal runs afterward. Mixed selection may choose no '
           'Ultimate runs at all. Unused reservations remain unspent and are not released to healing midweek. '
           'This is a concrete affordable strategy family, not an optimization over every possible adaptive plan.', '',
           'Because the supplied scenario changes loot counts only, ordinary-policy trajectories can be reused '
           'at their recorded run boundaries. The policy sees base-equivalent loot counters. Each entry plan is '
           'fixed using the pilot, before the independent final week begins. No final event chooses its plan '
           'after seeing future successes. The exported costs include entry, recovery and shop rerolls, and '
           'the validation checks the total cap on every event.', '',
           '## Confidence and reproducibility','',
           'Bands are pointwise 95% normal-approximation Monte Carlo confidence intervals for means, conditional on the selected plan '
           'and current simulator assumptions. They are not ranges covering 95% of individual weeks, nor confidence '
           'bounds on real-game mechanics. Selection uncertainty and imperfect room/drop calibration remain.', '',
           'The archive retains the plan, selection, per-event trajectories with completion prefixes, final results, '
           'source snapshots and a checksum manifest. `publish_budget_curve.py` independently reconstructs '
           'entry spending, reward multipliers, selection, summaries and marginal comparisons. No model was '
           'promoted to the desktop default by this experiment.', '',
           '[PNG](mushroom_returns.png) · [SVG](mushroom_returns.svg) · [PDF](mushroom_returns.pdf) · [CSV](results.csv)', '',
           '![Gold and epic-item scenario](loot_returns.png)', '',
           'These additional loot curves use the same plans selected for legendary-item count. '
           'They are not separately optimized gold or epic-item strategies.', '']
    (target/'RESULTS.md').write_text('\n'.join(text),encoding='utf-8')
    header='<tr>'+''.join('<th>'+html.escape(k)+'</th>' for k in table[0])+'</tr>'
    body=''.join('<tr>'+''.join('<td>'+html.escape(f'{v:.3f}' if isinstance(v,float) else str(v) if v is not None else '—')+'</td>'
                             for v in row.values())+'</tr>' for row in table)
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mushroom returns and Ultimate Dungeon</title><style>body{font:16px system-ui;max-width:1500px;margin:30px auto;padding:0 24px;color:#172b42}img{width:100%;height:auto}p{max-width:1000px;line-height:1.6}.table{overflow:auto}table{border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid #d6dfe9;padding:9px;text-align:right}th{background:#edf2f8;position:sticky;top:0}a{color:#1768ac}</style>
<h1>Mushroom returns and Ultimate Dungeon</h1><p>Seven-day events on engine 0.6.10, policies frozen before final testing, pointwise 95% confidence intervals. Ultimate costs 650 per new run. Its triple arena/other-item rewards are a scenario assumption. The desktop now uses 0.6.11; see the report for engine differences and policy-selection details.</p>
<img src="mushroom_returns.svg" alt="Four panels show weekly completions, a low-budget zoom, legendary rewards including Ultimate entry fees, and marginal clears per extra 100 mushrooms.">
<img src="loot_returns.svg" alt="Gold and epic-item scenario from the same selected plans.">
<p><a href="RESULTS.md">Method and full results</a> · <a href="results.csv">Download CSV</a> · <a href="mushroom_returns.pdf">Download PDF</a></p><h2>All tested plans</h2><div class="table"><table>'''+header+body+'</table></div></html>'
    if transferred:
        page=page.replace('Seven-day events on engine 0.6.10', 'Seven-day events on engine 0.6.11')
        page=page.replace('The desktop now uses 0.6.11; see the report for engine differences and policy-selection details.',
                          'Policies and entry plans were selected on engine 0.6.10 and tested unchanged on 0.6.11. See the report for details.')
    (target/'index.html').write_text(page,encoding='utf-8')


def main(source,target,baseline_path=None):
    result,projections=verify(source)
    target.mkdir(parents=True,exist_ok=True)
    # Native DLL and checkpoint are identified by hash; the checkpoint already
    # exists in the preceding published study. Raw trajectories are preserved.
    for p in source.rglob('*'):
        if p.is_file() and p.suffix!='.dll' and (p.suffix!='.pt' or p.name=='specialist.pt'):
            dest=target/p.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists() and digest(dest)!=digest(p):raise ValueError('Archive collision: '+str(dest))
            shutil.copyfile(p,dest)
    shutil.copyfile(__file__,target/'source'/'tools'/'publish_budget_curve.py')
    baseline=read(baseline_path) if baseline_path else None
    if baseline_path:shutil.copyfile(baseline_path,target/'diagnostic-summary.json')
    plots(target,result,baseline);outputs(target,result,projections)
    validation={'accounting_verified':True,'selection_reproduced':True,'summaries_reproduced':True,
                'pointwise_confidence':.95,'selection_policy_weeks':result['selection_policy_weeks'],
                'final_policy_weeks':result['final_policy_weeks'],'checkpoint_sha256':result['plan']['checkpoint_sha256']}
    if baseline_path:validation['diagnostic_summary_sha256']=digest(baseline_path)
    (target/'validation.json').write_text(json.dumps(validation,indent=2)+'\n',encoding='utf-8')
    manifest={p.relative_to(target).as_posix():digest(p) for p in sorted(target.rglob('*')) if p.is_file() and p.name!='manifest.json'}
    (target/'manifest.json').write_text(json.dumps({'files':manifest},indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'archive':str(target),'validated':validation}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--target',type=Path,required=True);p.add_argument('--baseline',type=Path)
    a=p.parse_args();main(a.source.resolve(),a.target.resolve(),a.baseline)
