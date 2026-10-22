"""Publish the completed recovery study without selecting additional variants."""
import argparse
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
LABELS=dict(control='Current policy',entry_mean='Next mean-hit threshold',entry_max='Next maximum-hit threshold',
            wait_mean='Mean-hit recovery target',wait_max='Maximum-hit recovery target',
            wait_one='Survive one maximum hit',hybrid_mean='Cheap partial + mean target',
            hybrid_max='Cheap partial + maximum target',hybrid_nearby='Cheap partial + nearby target')


def render(out):
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    plan=read(out/'plan.json');summary=read(out/'summary.json');selection=read(out/'selection.json')
    variants=['control',*summary['selection'].values()];budgets=plan['confirmation']['budgets']
    cells={(c['variant'],c['budget'],c['sleep_until_hour']):c for c in summary['cells']}
    mean=lambda c,k:c['summary'][k]['mean']
    rows=[]
    for (v,b,w),c in cells.items():
        s=c['summary'];den=mean(c,'reentries');rusty=mean(c,'rusty_reentries');boss=mean(c,'boss25_count')
        rows.append(dict(variant=v,budget=b,sleep_until_hour=w,completions=mean(c,'completions'),
            ci_low=s['completions']['ci'][0],ci_high=s['completions']['ci'][1],mushrooms=mean(c,'mushrooms'),
            deaths=mean(c,'deaths'),reentry_hp_percent=100*mean(c,'reentry_hp_sum')/den if den else 0,
            rusty_reentry_hp_percent=100*mean(c,'rusty_reentry_hp_sum')/rusty if rusty else 0,
            keys_after_room25=mean(c,'boss25_keys_sum')/boss if boss else 0,
            partial_refills=mean(c,'partial_refills'),full_refills=mean(c,'full_refills'),
            selected_wait_hours=mean(c,'selected_wait_hours')))
    with (out/'results.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    colors=['#52677d','#147a54','#c06428'];fig,axes=plt.subplots(1,2,figsize=(12,4.5),sharey=True)
    for ax,w,title in zip(axes,[0,8],['Continuous availability','No actions 00:00–08:00']):
        for v,color in zip(variants,colors):
            y=[mean(cells[v,b,w],'completions') for b in budgets]
            error=[(cells[v,b,w]['summary']['completions']['ci'][1]-cells[v,b,w]['summary']['completions']['ci'][0])/2 for b in budgets]
            ax.errorbar(budgets,y,yerr=error,marker='o',capsize=3,color=color,label=LABELS[v],linewidth=1.8)
        ax.set_title(title);ax.set_xlabel('Weekly mushroom budget');ax.grid(alpha=.2)
    axes[0].set_ylabel('Average completed runs in 168 hours')
    fig.suptitle('SMOKE TEST — layout and execution check only' if plan['smoke'] else
                 'Recovery thresholds: independent confirmation',fontweight='bold')
    handles,labels=axes[0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False)
    fig.text(.5,.075,f'{plan["confirmation"]["events"]:,} simulated weeks per point · 95% sampling CIs · same frozen actor · start 17:00',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.13,1,.96]);fig.savefig(out/'weekly-completions.png',dpi=170);fig.savefig(out/'weekly-completions.svg');plt.close(fig)
    lines=['# '+('SMOKE TEST ONLY — ' if plan['smoke'] else '')+'Recovery thresholds — 15 October 2026','',
        'This compares changes to recovery decisions over full seven-day events. It does not retrain the actor or prove optimal play.',
        '',f'Screening: {plan["screening"]["events"]:,} weeks × ten strategies × six budget/schedule cells. '
        f'Confirmation: {plan["confirmation"]["events"]:,} fresh weeks × three strategies × twelve cells. '
        'Budgets are gross spending caps. Eligible stone offers remain random. Both schedules start at 17:00.',
        '',f'The screening rule selected **{summary["selection"]["threshold"]}** and **{summary["selection"]["hybrid"]}**. '
        'Their definitions were frozen before confirmation. Screening outcomes are excluded from the confidence intervals below.',
        '', '## Verified revival mechanics','',
        '- Greasy grants three 10% recovery charges; Time Traveler extends that to four.',
        '- Rusty increases those healing ticks to 14%, then applies its 5% poison tick. The heal caps at 100% before poison.',
        '- Rusty poison lasts five room exits, or six with Cursed Moonstone.',
        '- The character must survive the incoming hit before room-exit healing can help. Effects are cleared on death.',
        '', 'These existing native mechanics now have focused regression coverage. No damage distribution or gem balance was changed.',
        '', '## Model examples','',
        '| Situation | Entry HP target |', '|---|---:|',
        '| Stage 1, no modifiers, two mean hits | 26.28% |',
        '| Stage 1, no modifiers, two maximum hits | 28.90% |',
        '| Stage 2, Rusty, one maximum hit followed by poison | 25.67% |',
        '| Stage 3, Greasy + Time Traveler, survive first maximum hit | 23.48% |',
        '| Stage 4, Greasy + Rusty + Time Traveler, survive first maximum hit | 25.81% |',
        '', 'Each includes a 0.1 percentage-point cushion. These are hypothetical normal hits under the current profile, '
        'without an additional trap or newly acquired curse. A mean-damage target is not a survival guarantee. '
        'Waiting from 20% to 28.90% takes 2.136 hours; buying 20% HP from empty and then waiting uses the same remaining delay.',
        '', 'The calculation follows successive damage → capped healing → poison transitions, with effect expiry. '
        'It does not simply add the entire poison duration to the entry target. Each recovery gets one fixed target; '
        'reaching it or overshooting during sleep does not trigger another round of waiting.',
        '', '## Recovery rules tested','',
        '- `entry_mean` / `entry_max`: extend a planned ordinary-room reentry to the next survivable-hit boundary, '
        'using mean or maximum modeled damage. Preserve other natural waits and paid recovery choices.',
        '- `wait_mean` / `wait_max`: use that boundary when a natural wait begins as well. '
        'This can shorten a learned 40% or higher wait.',
        '- `wait_one`: use only enough HP for one maximum modeled normal hit, with a minimum of 20%. '
        'Keep the actor’s paid recovery and boss decisions.',
        '- `hybrid_mean` / `hybrid_max`: also replace paid recovery with one cheap 20%-HP purchase followed by natural waiting. '
        'At bosses, target one maximum boss hit.',
        '- `hybrid_nearby`: the same cheap hybrid, but skip ordinary-room top-ups requiring more than ten additional HP percentage points.',
        '- `cap20`: one cheap 20%-HP purchase per recovery, retaining the learned legal wait/reentry choices; '
        'if another paid heal is proposed, use the actor’s highest-ranked non-paid action.',
        '', 'For the cheap variants, “cheap” means a partial-refill price of 10, 15 or 20 mushrooms. '
        'A full-refill purchase is never made, even if it would cost less than 20. '
        'The experiment therefore tests the specified partial-refill approach, not every possible cost-capped policy.',
        '', '## Confirmation results','']
    for w,title in ((0,'Continuous availability'),(8,'Daily sleep 00:00–08:00')):
        lines += [f'### {title}','', '| Budget | Current policy | '+LABELS[variants[1]]+' | '+LABELS[variants[2]]+' |',
                  '|---:|---:|---:|---:|']
        for b in budgets:lines += ['| '+str(b)+' | '+' | '.join(f'{mean(cells[v,b,w],"completions"):.4f}' for v in variants)+' |']
        lines += ['']
    lines += ['### Paired changes from current policy','',
              'Normal-approximation intervals below use a Bonferroni adjustment for simultaneous 95% coverage across '
              'the 24 cell comparisons and two overall comparisons. '
              'The overall result weights all twelve cells equally and preserves shared-seed correlation. '
              'Full pointwise intervals and diagnostics are in `summary.json` and `results.csv`.','',
              '| Variant | Budget | Availability | Mean change | Simultaneous 95% interval |',
              '|---|---:|---|---:|---|']
    for c in summary['contrasts']:
        s=c['simultaneous_95'];lo,hi=s['ci']
        lines += [f'| {c["variant"]} | {c["budget"]} | '+('continuous' if not c['sleep_until_hour'] else 'sleep 00–08')+
                  f' | {s["mean"]:+.4f} | [{lo:+.4f}, {hi:+.4f}] |']
    lines += ['', '### Overall assessment','']
    for c in summary['overall']:
        s=c['simultaneous_95'];lo,hi=s['ci']
        lines += [f'- **{c["variant"]}:** {s["mean"]:+.4f} runs; simultaneous 95% CI [{lo:+.4f}, {hi:+.4f}]. '
                  f'Satisfies the declared general-improvement criterion: **{c["supported_general_improvement"]}**.']
    positive=[c for c in summary['contrasts'] if c['simultaneous_95']['ci'][0]>0]
    lines += ['', '### Supported budget-specific gains','']
    if not positive:lines += ['No individual cell had a positive simultaneous lower confidence bound.']
    for c in positive:
        old=mean(cells['control',c['budget'],c['sleep_until_hour']],'completions')
        new=mean(cells[c['variant'],c['budget'],c['sleep_until_hour']],'completions')
        lo,hi=c['simultaneous_95']['ci']
        lines += [f'- {c["variant"]}, {c["budget"]} mushrooms, '+
                  ('continuous availability' if not c['sleep_until_hour'] else 'sleep 00–08')+
                  f': **{old:.4f} → {new:.4f}** ({100*(new/old-1):.2f}%); difference CI [{lo:+.4f}, {hi:+.4f}].']
    lines += ['', 'A cell-specific gain does not justify applying the same rule to other budgets. '
              'The hybrid changes partial-refill purchases, waiting and boss targets together; '
              'this comparison does not isolate which component caused any gain.']
    lines += ['', '## Interpretation and limits','',
        'Surviving an extra fight can produce keys and keep beneficial effects alive, but waiting consumes the same event clock. '
        'An ordinary-room death still advances a room, and low HP can still produce progress through safe rooms, successful escapes '
        'and beneficial interactions. These effects are included in the complete-event comparison.',
        '', 'The hybrid variants allow one 20%-HP purchase per recovery, only at prices 10, 15 or 20 mushrooms, then wait naturally. '
        'They never buy a full refill and use a single maximum-hit target at bosses. This is a restriction at high budgets, '
        'not an equal-spending comparison. See actual average spending in `results.csv`. '
        'The cap20 screening arm isolates the refill restriction without custom HP targets.',
        '', 'Non-recovery actions still come from the same trained policy and existing runtime guards. '
        'The projected survival thresholds are heuristics, not a lookahead into hidden room outcomes. '
        'Confidence intervals describe simulation sampling error, not uncertainty in real-game mechanics. '
        'The deployed application has not been switched by this experiment.',
        '', '## Reproduction','',
        'The study folder contains the frozen plan, independent seed audit, checkpoint, profile, native library, source snapshot, '
        'individual event rows, selection record, summary and chart. The smoke run is separate under `out/recovery-thresholds-smoke-2026-10-15`.',
        '', '```powershell',
        'out/rl-env/Scripts/python.exe -m unittest discover -s tests -p test_recovery_thresholds.py',
        'out/rl-env/Scripts/python.exe tools/benchmark_recovery_thresholds.py --output <new-study-directory>',
        'out/rl-env/Scripts/python.exe tools/render_recovery_thresholds.py --output <completed-study-directory>',
        '```','',
        'The benchmark rejects reused root seeds. Exact replay can use the frozen source and plan in an isolated copy; '
        'a new statistical experiment needs new, predeclared seed roots.','']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(overall=summary['overall'],report=str(out/'report.md'),chart=str(out/'weekly-completions.png'))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    render(p.parse_args().output.resolve())
