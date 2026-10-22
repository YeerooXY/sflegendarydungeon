"""Verify final-stone grid artifacts and report independent crossover tests."""
import argparse
import csv
import gzip
import json
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
import train_rl
from sfld_rl.stone_grid import METRICS


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def interval(values,z):
    mean=float(np.mean(values));se=float(np.std(values,ddof=1)/len(values)**.5)
    return dict(mean=mean,se=se,ci=[mean-z*se,mean+z*se])


def verify(out):
    plan=read(out/'plan.json')
    if read(out/'status.json')['phase']!='complete':raise RuntimeError('Incomplete study')
    for name,digest in plan['sources'].items():
        if train_rl.sha(out/'source'/name)!=digest:raise RuntimeError('Frozen source mismatch: '+name)
    for name,key in [('checkpoint.pt','checkpoint_sha256'),('sfld_rl.dll','library_sha256'),('synthetic.profile','profile_sha256')]:
        if train_rl.sha(out/name)!=plan[key]:raise RuntimeError('Artifact mismatch: '+name)
    return plan


def load_shard(path):
    meta=read(path.with_suffix('.json'))
    if train_rl.sha(path)!=meta['data_sha256']:raise RuntimeError('Shard hash mismatch')
    with np.load(path) as archive:data=archive['data'];baseline=archive['baseline']
    if data.shape!=(len(meta['states']),meta['trials'],3,len(METRICS)):raise RuntimeError('Shard coverage mismatch')
    if not np.isfinite(data).all() or not np.all((data[...,0]>=0)&(data[...,0]<=168)):raise RuntimeError('Invalid results')
    if not np.all((data[...,1]==1)|(data[...,0]==168)):raise RuntimeError('Discarded censoring')
    if not np.all((data[...,3]>=0)&(data[...,3]<=meta['budget'])):raise RuntimeError('Budget violation')
    return meta,data,baseline


def dense_report(out):
    plan=verify(out);summary=read(out/'summary.json');lookup={m['id']:m for m in plan['menus']}
    total_contrasts=len(summary['menus'])*len(plan['health'])*len(plan['keys'])*3
    z=NormalDist().inv_cdf(1-.05/(2*total_contrasts));results=[];trajectories=0
    fig,axes=plt.subplots(1,len(summary['menus']),figsize=(7*len(summary['menus']),6),squeeze=False)
    for ax,entry in zip(axes[0],summary['menus']):
        b=entry['budget'];m=entry['menu_id'];menu=lookup[m]
        values={};bases={}
        for path in (out/'dense').glob(f'b{b}-menu-{m}-part-*.npz'):
            meta,data,baseline=load_shard(path);trajectories+=data.shape[0]*data.shape[1]*3
            for state,rows,base in zip(meta['states'],data,baseline):
                key=(state['hp'],state['keys'])
                if key in values:raise RuntimeError('Duplicate dense cell')
                values[key]=rows;bases[key]=int(base)
        expected={(hp,k) for hp in plan['health'] for k in plan['keys']}
        if set(values)!=expected:raise RuntimeError('Missing dense cells')
        picture=np.full((len(plan['health']),len(plan['keys'])),-1,int)
        for i,hp in enumerate(plan['health']):
            for j,k in enumerate(plan['keys']):
                rows=values[hp,k];means=rows[...,0].mean(axis=0);winner=int(means.argmin())
                margins=[interval(rows[:,other,0]-rows[:,winner,0],z) for other in range(3) if other!=winner]
                supported=all(x['ci'][0]>0 for x in margins)
                if supported:picture[i,j]=winner
                results.append(dict(menu_id=m,budget=b,hp=hp,keys=k,samples=len(rows),
                    estimated_best=menu['offered'][winner],supported_best=menu['offered'][winner] if supported else '',
                    baseline=menu['offered'][bases[hp,k]],means=means.tolist(),margins=margins))
        cmap=ListedColormap(['#dddddd','#146c94','#bd5c00','#6a5acd'])
        ax.imshow(picture+1,origin='lower',aspect='auto',extent=(-.5,10.5,.5,77.5),vmin=0,vmax=3,cmap=cmap)
        ax.set_xticks(range(11));ax.set_xlabel('Keys at final stone choice');ax.set_ylabel('HP after room-75 boss (%)')
        ax.set_title(f'Run {menu["run"]}: '+ ' + '.join(menu['held'])+f'\n{b} mushrooms remaining')
        ax.legend(handles=[Patch(color=cmap.colors[i+1],label=g) for i,g in enumerate(menu['offered'])]+
                  [Patch(color=cmap.colors[0],label='Unresolved')],loc='upper center',bbox_to_anchor=(.5,-.13),ncol=2,frameon=False)
    fig.suptitle('Final-stone choices supported by fresh simulations',fontweight='bold')
    fig.text(.5,.015,'256 continuations per stone/cell; simultaneous 95% comparisons across both grids.\n'
             'Grey means insufficient separation. Fixed menus/histories, no starting effects, current downstream policy.',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.13,1,.93]);fig.savefig(out/'decision-grid.png',dpi=165);fig.savefig(out/'decision-grid.svg');plt.close(fig)
    (out/'comparisons.json').write_text(json.dumps(dict(simultaneous_contrasts=total_contrasts,results=results),indent=2)+'\n',encoding='utf-8')
    with (out/'decision-table.csv').open('w',encoding='utf-8',newline='') as f:
        columns=['menu_id','budget','hp','keys','samples','estimated_best','supported_best','baseline']
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        writer.writerows({k:r[k] for k in columns} for r in results)
    supported=sum(bool(r['supported_best']) for r in results)
    verification=dict(passed=True,trajectories=trajectories,cells=len(results),supported_cells=supported,
        simultaneous_contrasts=total_contrasts,z=z)
    (out/'verification.json').write_text(json.dumps(verification,indent=2)+'\n',encoding='utf-8')
    return verification


def render(out,family_comparisons=None):
    plan=verify(out);summary=read(out/'summary.json');selection=read(out/'selection.json');lookup={m['id']:m for m in plan['menus']}
    screening_count=0;seen=set()
    with gzip.open(out/'screening.csv.gz','wt',encoding='utf-8',newline='') as f:
        writer=csv.writer(f);writer.writerow(['run','menu_id','held','offered','rejected','budget','hp','keys','samples',
            'stress_case','learned_choice','screening_winner','hours_0','hours_1','hours_2','se_0','se_1','se_2'])
        for path in sorted((out/'screening').glob('*.npz')):
            meta,data,baseline=load_shard(path);menu=lookup[meta['menu_id']];b=meta['budget']
            for state,rows,base in zip(meta['states'],data,baseline):
                key=(b,menu['id'],state['hp'],state['keys'])
                if key in seen:raise RuntimeError('Duplicate screening state')
                seen.add(key);means=rows[...,0].mean(axis=0);se=rows[...,0].std(axis=0,ddof=1)/len(rows)**.5
                writer.writerow([menu['run'],menu['id'],','.join(menu['held']),','.join(menu['offered']),','.join(menu['rejected']),
                    b,state['hp'],state['keys'],len(rows),state['hp']>77.05,menu['offered'][int(base)],
                    menu['offered'][int(means.argmin())],*means,*se])
            screening_count+=data.shape[0]*data.shape[1]*3
    expected={(b,m['id'],h,k) for b in plan['budgets'] for m in plan['menus'] for h in plan['health'] for k in plan['keys']}
    if seen!=expected or screening_count!=summary['screening_trajectories']:raise RuntimeError('Screening coverage mismatch')
    comparisons=[];confirmation_count=0;confirmation_cells={}
    family=family_comparisons or 7*len(selection)
    if family<7*len(selection):raise ValueError('Confidence family cannot omit this study comparisons')
    z=NormalDist().inv_cdf(1-.05/(2*max(1,family)))
    for choice in selection:
        b=choice['budget'];identity=choice['id'];menu=lookup[choice['menu_id']]
        meta,data,baseline=load_shard(out/'confirmation'/f'b{b}-case-{identity}.npz')
        if meta['states']!=choice['states'] or meta['menu_id']!=menu['id']:raise RuntimeError('Confirmation intervention changed')
        confirmation_count+=data.shape[0]*data.shape[1]*3
        for state,rows in zip(meta['states'],data):
            identity=(b,menu['id'],state['hp'],state['keys'])
            if identity in confirmation_cells and not np.array_equal(rows,confirmation_cells[identity]):
                raise RuntimeError('Repeated confirmation state differs on the same seed')
            confirmation_cells[identity]=rows
        a,c=choice['arms'];margins=[interval(data[0,:,c,0]-data[0,:,a,0],z),interval(data[1,:,a,0]-data[1,:,c,0],z)]
        interaction=interval((data[0,:,c,0]-data[0,:,a,0])-(data[1,:,c,0]-data[1,:,a,0]),z)
        endpoints=[]
        for s,winner in enumerate((a,c)):
            against={menu['offered'][other]:interval(data[s,:,other,0]-data[s,:,winner,0],z) for other in range(3) if other!=winner}
            endpoints.append(dict(state=choice['states'][s],selected=menu['offered'][winner],baseline=menu['offered'][int(baseline[s])],
                mean_hours=data[s,...,0].mean(axis=0).tolist(),mean_mushrooms=data[s,...,3].mean(axis=0).tolist(),
                mean_deaths=data[s,...,2].mean(axis=0).tolist(),deathless=data[s,...,5].mean(axis=0).tolist(),
                noncompletion=(1-data[s,...,1].mean(axis=0)).tolist(),advantages=against,
                supported_best=all(v['ci'][0]>0 for v in against.values())))
        comparisons.append(dict(**choice,menu=menu,endpoints=endpoints,margins=margins,interaction=interaction,
            supported_pairwise_reversal=all(r['ci'][0]>0 for r in margins),
            supported_best_offer_reversal=all(e['supported_best'] for e in endpoints)))
    if confirmation_count!=summary['confirmation_trajectories']:raise RuntimeError('Confirmation count mismatch')
    unique_confirmation=sum(len(rows)*3 for rows in confirmation_cells.values())
    (out/'comparisons.json').write_text(json.dumps(dict(comparisons=comparisons,simultaneous_contrasts=family,z=z),indent=2)+'\n',encoding='utf-8')
    with (out/'confirmation.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.writer(f);writer.writerow(['case','family','run','held','offers','budget','hp','keys','stone','mean_hours',
            'mean_mushrooms','baseline','screening_choice','confirmed_best','no_death_probability','noncompletion_probability'])
        for r in comparisons:
            for e in r['endpoints']:
                for i,g in enumerate(r['menu']['offered']):
                    writer.writerow([r['id'],r['family'],r['menu']['run'],','.join(r['menu']['held']),','.join(r['menu']['offered']),
                        r['budget'],e['state']['hp'],e['state']['keys'],g,e['mean_hours'][i],e['mean_mushrooms'][i],e['baseline'],e['selected'],
                        e['selected'] if e['supported_best'] else '',e['deathless'][i],e['noncompletion'][i]])
    dense=out/'dense-hp';dense_info=dense_report(dense) if (dense/'summary.json').exists() else None
    support={f:sum(r['family']==f and r['supported_pairwise_reversal'] for r in comparisons) for f in ('keys','health')}
    best={f:sum(r['family']==f and r['supported_best_offer_reversal'] for r in comparisons) for f in ('keys','health')}
    lines=['# Final-stone decisions conditioned on keys and health','',
        'This study forces each of the three legal final-stone choices from the same living room-76 state, '
        'then follows the deployed policy through room 100. The first two stones, HP, keys, prior rejected stones and offers stay fixed.',
        '', f'Completed **{screening_count:,} screening continuations** and **{confirmation_count:,} confirmation executions** '
        f'covering **{unique_confirmation:,} distinct state/action/seed continuations** on a fresh confirmation root. '
        f'Confirmation supports {support["keys"]} key-dependent and {support["health"]} HP-dependent pairwise preference reversals. '
        f'Of those, {best["keys"]} key cases and {best["health"]} HP cases establish opposite best offers among all three choices '
        'at both endpoints, using simultaneous 95% sampling intervals.',
        '', '## Coverage','',
        '- All 190 held pairs in the 20-stone later-run pool and all 45 in the 10-stone first-run pool. Every unowned candidate appears for each pair.',
        '- 1,275 legal menus; balanced groups cover candidates without enumerating every three-offer permutation. Each menu has four distinct previously rejected stones.',
        '- Keys 0 through 10. Screening HP: '+', '.join(map(str,plan['health']))+'%. Eight continuations per stone/cell, so screening labels are provisional.',
        '- Remaining mushroom budgets: '+', '.join(map(str,plan['budgets']))+'. Continuous availability, clock starting 17:00; zero initial blessings/curses.',
        '- The stone choice follows room 75, before room 76. Living HP may be below 20%. The 100% row is a stress case; '
        'the current room-75 boss damage permits at most 77.05% immediately after starting its fight at full health.',
        '', 'The metric is remaining hours until the current run finishes, capped at 168. Unfinished trajectories stay in the mean at 168. '
        'Deaths, mushroom spend and no-death clear probability are retained. This is not a whole-week completion forecast.',
        '', '## Paired confirmation','',
        'For each budget, screening preselected up to 12 key reversals and 12 health reversals. Each selected endpoint receives 2,048 fresh futures for each of all three stones. '
        'The same future root is used across branches and endpoints; different decisions can consume different draws. '
        'The policy never sees the RNG seed or future rooms. Overlapping endpoints in separate reversal tests reuse exactly '
        'the same paired outcomes; they are not counted as extra independent samples.',
        '', f'All intervals below use normal-approximation paired intervals with Bonferroni simultaneous 95% coverage for a family of {family} endpoint pairwise and interaction contrasts '
        '(including companion budget studies when a larger joint family is specified). '
        'A pairwise reversal requires the first stone to win at the first endpoint and the second stone at the second endpoint. '
        'A best-offer reversal additionally requires beating the third offered stone at both endpoints. '
        'Intervals cover Monte Carlo sampling uncertainty under the model, not uncertainty in real game mechanics.',
        '', '| Case | Budget | Held stones | Factor / endpoints | Preference switch | Advantage at first endpoint [95% CI], hours | Advantage at second endpoint [95% CI], hours | Pairwise confirmed | Best of all three confirmed |',
        '|---:|---:|---|---|---|---|---|---|---|']
    for r in comparisons:
        e0,e1=r['endpoints'];p0,p1=r['margins'];fmt=lambda p:f'{p["mean"]:.2f} [{p["ci"][0]:.2f}, {p["ci"][1]:.2f}]'
        states=' / '.join(f'{e["state"]["hp"]}% HP, {e["state"]["keys"]} keys' for e in r['endpoints'])
        lines.append(f'| {r["id"]} | {r["budget"]} | '+ ' + '.join(r['menu']['held'])+f' | {r["family"]}: {states} | '
            f'{e0["selected"]} -> {e1["selected"]} | {fmt(p0)} | {fmt(p1)} | '
            f'{r["supported_pairwise_reversal"]} | {r["supported_best_offer_reversal"]} |')
    if dense_info:
        lines += ['', '## Every-integer HP follow-up','',
            f'An additional **{dense_info["trajectories"]:,} continuations** test HP1..77 and keys0..10 for two menus chosen '
            'from screening before consulting confirmation. Each stone/cell has 256 fresh futures. '
            f'{dense_info["supported_cells"]} of {dense_info["cells"]} cells distinguish a best offer after simultaneous '
            f'95% correction across {dense_info["simultaneous_contrasts"]:,} comparisons. Grey cells remain unresolved.',
            '', '![Conditional choice grids](dense-hp/decision-grid.png)',
            '', '[Dense decision table](dense-hp/decision-table.csv) records both provisional winners and supported winners separately.']
    lines += ['', '## What this establishes','',
        'A fixed universal stone ranking can miss state-dependent choices. However, these are values under the current downstream policy. '
        'They do not establish that the continuation plays each combination optimally. The controlled tests can provide training targets '
        'and identify decisions worth replacing after a separate full-event evaluation.',
        '', 'Coverage is exhaustive over held-pair/candidate identity in the configured pools, not every possible dungeon state. '
        'Unmeasured dimensions include all temporary-effect combinations, other offer/rejection histories, overnight timing, remaining event time, '
        'prior refill prices and other mushroom budgets. A menu preference is valid for its recorded context; do not silently mix different histories.',
        '', 'The current UI stone scores are neural preferences, not these measured outcome estimates. The deployed policy is unchanged.',
        '', '## Reproducibility','',
        '[Compressed screening table](screening.csv.gz), [confirmation table](confirmation.csv), [full paired comparisons](comparisons.json). '
        'Every raw paired outcome is retained in compressed NumPy shards. `plan.json` records menu histories, grid, seed scheme and frozen hashes; '
        '`selection.json` is fixed before confirmation. Focused native/Python tests cover menu eligibility, every pair/candidate, '
        'living low HP, preservation of keys/HP, deterministic replay and selection logic. Artifact verification checks hashes, all cell IDs, '
        'array dimensions, censoring and budget limits. No mechanics or deployed policy were changed.', '']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    verification=dict(passed=True,screening_states=len(seen),screening_trajectories=screening_count,
        confirmation_trajectories=confirmation_count,unique_confirmation_trajectories=unique_confirmation,
        pairwise_reversals=support,best_offer_reversals=best,simultaneous_contrasts=family,dense=dense_info,
        by_budget={str(b):dict(pairwise_reversals=sum(r['budget']==b and r['supported_pairwise_reversal'] for r in comparisons),
            best_offer_reversals=sum(r['budget']==b and r['supported_best_offer_reversal'] for r in comparisons)) for b in plan['budgets']})
    (out/'verification.json').write_text(json.dumps(verification,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(verification,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--family-comparisons',type=int)
    a=p.parse_args();render(a.output.resolve(),a.family_comparisons)
