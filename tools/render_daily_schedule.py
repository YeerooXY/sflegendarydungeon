"""Render the frozen daily-availability comparison; never changes its samples."""
import argparse
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def render(folder):
    result=json.loads((folder/'summary.json').read_text())
    plan=json.loads((folder/'plan.json').read_text())
    budgets=plan['budgets'];cells={(c['budget'],c['arm']):c for c in result['cells']}
    columns=['budget','availability','events','mean_completions','ci95_low','ci95_high',
             'mean_mushrooms_spent','mean_first_completion_hours','fraction_with_first_completion']
    with (folder/'comparison.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for budget in budgets:
            for arm in ('continuous','sleep_00_08'):
                c=cells[budget,arm];v=c['summary']['completions']
                writer.writerow(dict(budget=budget,availability=arm,events=c['cohort']['events'],
                    mean_completions=v['mean'],ci95_low=v['ci'][0],ci95_high=v['ci'][1],
                    mean_mushrooms_spent=c['summary']['mushrooms']['mean'],
                    mean_first_completion_hours=c['conditional_first_hours']['mean'],
                    fraction_with_first_completion=c['first_completion_fraction']))
    fig,ax=plt.subplots(figsize=(10,5.6),layout='constrained');x=np.arange(len(budgets));width=.36
    for arm,label,color,offset in [('continuous','Continuous availability','#326dab',-.5),
                                   ('sleep_00_08','Sleep 00:00–08:00','#dc9233',.5)]:
        means=[cells[b,arm]['summary']['completions']['mean'] for b in budgets]
        errors=[cells[b,arm]['summary']['completions']['ci'][1]-m for b,m in zip(budgets,means)]
        bars=ax.bar(x+offset*width,means,width,color=color,label=label,yerr=errors,capsize=3)
        ax.bar_label(bars,fmt='%.2f',padding=5,fontsize=10)
    ax.set_xticks(x,[str(b) for b in budgets]);ax.set_xlabel('Mushroom budget for the seven days')
    ax.set_ylabel('Mean completed runs');ax.set_ylim(0,11.7)
    ax.set_title('Seven days starting at 17:00 local time',loc='left',fontweight='bold',pad=16)
    ax.legend(frameon=False,loc='upper left');ax.set_axisbelow(True);ax.grid(axis='y',alpha=.2)
    ax.spines[['top','right']].set_visible(False)
    fig.supxlabel(f"{plan['cohort']['events']:,} paired events per budget and schedule · 95% Monte Carlo intervals\n"
                  'Same frozen policy; simulator assumptions remain uncertain. Not trained for sleep.',fontsize=9)
    fig.savefig(folder/'completions.png',dpi=180);fig.savefig(folder/'completions.svg');plt.close(fig)
    print(f'Wrote CSV, PNG and SVG to {folder}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('folder',type=Path)
    render(parser.parse_args().folder)
