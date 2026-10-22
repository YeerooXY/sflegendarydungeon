"""Verify and rank all third-stone additions for the HP1% / five-mushroom study."""
import argparse
import csv
import gzip
from itertools import combinations
import json
from math import comb
from pathlib import Path
from statistics import NormalDist
import numpy as np
import train_rl
from sfld_rl.final_stones import KEYS, METRICS, counts
from sfld_rl.player_ideas import profile_values

SHORT={'rabbit':'Rabbit','moonstone':'Moonstone','spying':'Spying Gem','pendant':'Pendant',
       'greasy':'Greasy','gambler':'Gambler','greed':'Greed','hero':'Hero','time_traveler':'Time Traveler',
       'thirsty':'Thirsty','pearl':'Pearl','blood':'Blood Drop','explorer':'Explorer','misadventurer':'Misadventurer',
       'devil':'Devil','deceit':'Deceit','lodestone':'Lodestone','bull':'Bull','hick':'Hick','rusty':'Rusty'}
FULL={'rabbit':'Soul of the Rabbit','moonstone':'Cursed Moonstone','spying':'Spying Gem',
      'pendant':'Pendant of the Key Master','greasy':'Greasy Healing Stone','gambler':'Boulder of the Gambler',
      'greed':'Boulder of Greed','hero':'Treasure of the Hero','time_traveler':'Diamond of the Time Traveler',
      'thirsty':'Hope of the Thirsty One','pearl':'Cursed Pearl','blood':'Blood Drop of Sacrifice',
      'explorer':'Emerald of the Explorer','misadventurer':'Sapphire of the Misadventurer','devil':'Crown Jewel of the Devil',
      'deceit':'Pebble of Deceit','lodestone':'Lodestone','bull':'Eye of the Bull',
      'hick':'Erratic Boulder of the Hick','rusty':'Rusty Healing Stone'}


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def paired(values,z):
    """Equal weight to each frozen history, independent futures within strata."""
    h,n=values.shape
    mean=float(values.mean())
    se=float(np.sqrt(values.var(axis=1,ddof=1).sum()/n/h**2))
    return dict(mean=mean,se=se,ci=[mean-z*se,mean+z*se])


def rank_arrays(candidates,arrays,z):
    # Each candidate is H x replicates x metric; histories are fixed strata.
    means=np.array([x.mean(axis=(0,1)) for x in arrays]);order=np.argsort(means[:,0],kind='stable')
    eliminated=set();comparisons={}
    for a,b in combinations(range(len(candidates)),2):
        result=paired(arrays[a][...,0]-arrays[b][...,0],z);comparisons[a,b]=result
        if result['ci'][0]>0:eliminated.add(a)
        elif result['ci'][1]<0:eliminated.add(b)
    contenders=[candidates[i] for i in order if i not in eliminated]
    if not contenders:raise RuntimeError('Confidence elimination produced no contender')
    winner=int(order[0]);ranks=[]
    for rank,i in enumerate(order,1):
        # Positive values mean the candidate is slower than the estimated winner.
        gap=paired(arrays[i][...,0]-arrays[winner][...,0],z)
        estimate=paired(arrays[i][...,0],z)
        ranks.append(dict(stone=candidates[i],rank=rank,mean_hours=float(means[i,0]),hours_se=estimate['se'],
            mean_mushrooms=float(means[i,3]),mean_deaths=float(means[i,2]),
            completion_probability=float(means[i,1]),no_death_probability=float(means[i,5]),
            hours_above_best=gap['mean'],gap_95_low=gap['ci'][0],gap_95_high=gap['ci'][1],
            contender=candidates[i] in contenders))
    return dict(estimated_best=candidates[winner],supported_best=contenders[0] if len(contenders)==1 else None,
                contenders=contenders,rankings=ranks),comparisons


def verify_load(out):
    plan=read(out/'plan.json')
    if read(out/'status.json')['phase']!='complete':raise RuntimeError('Study incomplete')
    if plan['smoke']:raise RuntimeError('Smoke data cannot produce a full ranking')
    for name,digest in plan['sources'].items():
        if train_rl.sha(out/'source'/name)!=digest:raise RuntimeError('Frozen source mismatch: '+name)
    for name,key in [('checkpoint.pt','checkpoint_sha256'),('synthetic.profile','profile_sha256'),('sfld_rl.dll','library_sha256')]:
        if train_rl.sha(out/name)!=plan[key]:raise RuntimeError('Frozen input mismatch: '+name)
    profile=profile_values(out/'synthetic.profile');data={};total=0
    expected={(run,frozenset(t)) for run,key in ((1,'gems_first_run'),(2,'gems_later_runs'))
              for t in combinations(profile[key].split(','),3)}
    if plan['keys']!=KEYS or plan['hp']!=1 or plan['budget']!=5:raise RuntimeError('Requested grid changed')
    for entry in plan['entries']:
        path=out/'results'/f'set-{entry["id"]}.npz';meta=read(path.with_suffix('.json'))
        if train_rl.sha(path)!=meta['data_sha256']:raise RuntimeError('Result hash mismatch')
        if meta['stones']!=entry['stones'] or meta['run']!=entry['run'] or meta['keys']!=KEYS:
            raise RuntimeError('Result identity mismatch')
        with np.load(path) as archive:array=archive['data']
        shape=(len(KEYS),len(entry['histories']),plan['futures']['per_history'],len(METRICS))
        if array.shape!=shape or not np.isfinite(array).all():raise RuntimeError('Incomplete result array')
        if not np.all((array[...,0]>=0)&(array[...,0]<=plan['horizon'])):raise RuntimeError('Invalid finish time')
        if not np.all((array[...,1]==0)|(array[...,1]==1)):raise RuntimeError('Invalid completion indicator')
        if not np.all((array[...,1]==1)|(array[...,0]==plan['horizon'])):raise RuntimeError('Discarded unfinished trajectories')
        if not np.all((array[...,3]>=0)&(array[...,3]<=5)):raise RuntimeError('Five-mushroom cap violation')
        identity=(entry['run'],frozenset(entry['stones']))
        if identity in data:raise RuntimeError('Duplicated final set')
        data[identity]=array;total+=int(np.prod(array.shape[:-1]))
    if set(data)!=expected or total!=read(out/'summary.json')['trajectories']:raise RuntimeError('Set/trajectory coverage mismatch')
    return plan,profile,data,total


def publish(out):
    plan,profile,data,total=verify_load(out);sizes=counts(profile)
    family=sum(row['held_pairs']*len(KEYS)*comb(row['potential_additions'],2) for row in sizes)
    z=NormalDist().inv_cdf(1-.05/(2*family));rows=[];contrast_count=0
    with gzip.open(out/'paired-contrasts.csv.gz','wt',encoding='utf-8',newline='') as f:
        writer=csv.writer(f);writer.writerow(['run','held_1','held_2','keys','stone_a','stone_b','hours_a_minus_b','se','ci_95_low','ci_95_high'])
        for run,field in ((1,'gems_first_run'),(2,'gems_later_runs')):
            pool=profile[field].split(',')
            for held in combinations(pool,2):
                candidates=[g for g in pool if g not in held]
                for key_index,keys in enumerate(KEYS):
                    arrays=[data[run,frozenset((*held,g))][key_index] for g in candidates]
                    result,contrasts=rank_arrays(candidates,arrays,z)
                    for (a,b),p in contrasts.items():
                        writer.writerow([run,*held,keys,candidates[a],candidates[b],p['mean'],p['se'],*p['ci']]);contrast_count+=1
                    rows.append(dict(run=run,held=list(held),keys=keys,hp=1,budget=5,
                                     samples_per_candidate=int(np.prod(arrays[0].shape[:-1])),**result))
    if len(rows)!=1175 or contrast_count!=family:raise RuntimeError('Pair/key comparison coverage mismatch')
    (out/'rankings.json').write_text(json.dumps(dict(simultaneous_contrasts=family,z=z,rows=rows),indent=2)+'\n',encoding='utf-8')
    with (out/'rankings.csv').open('w',encoding='utf-8',newline='') as f:
        fields=['run','held_1','held_2','hp','keys','budget','samples','stone','rank','mean_hours','hours_se',
                'mean_mushrooms','mean_deaths','completion_probability','no_death_probability',
                'hours_above_best','gap_95_low','gap_95_high','contender','supported_best']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows:
            for rank in row['rankings']:
                writer.writerow(dict(run=row['run'],held_1=row['held'][0],held_2=row['held'][1],hp=1,keys=row['keys'],budget=5,
                    samples=row['samples_per_candidate'],supported_best=row['supported_best'] or '',**rank))
    with (out/'best-third.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.writer(f);writer.writerow(['run','held_1','held_2','hp','keys','budget','estimated_best','supported_best',
            'mean_hours','mean_mushrooms','contenders_95','runner_up','gap_to_runner_up','gap_95_low','gap_95_high'])
        for row in rows:
            best,second=row['rankings'][:2]
            writer.writerow([row['run'],*row['held'],1,row['keys'],5,row['estimated_best'],row['supported_best'] or '',
                best['mean_hours'],best['mean_mushrooms'],','.join(row['contenders']),second['stone'],second['hours_above_best'],
                second['gap_95_low'],second['gap_95_high']])
    overview=[]
    for run in (1,2):
        subset=[r for r in rows if r['run']==run];pairs=list(dict.fromkeys(tuple(r['held']) for r in subset))
        lookup={(tuple(r['held']),r['keys']):r for r in subset}
        title='First event run' if run==1 else 'Second and later event runs'
        lines=[f'# Best third stone: {title}','',
            'Start alive after room75 with 1% HP, no temporary effects, and at most five mushrooms to finish rooms76–100. '
            'Each cell shows the estimated fastest addition and mean remaining hours under the current continuation policy.', '',
            '**†** means the leading estimate is unresolved: another stone remains in the simultaneous 95% contender set. '
            'Unmarked cells have a unique supported best within this study. '
            'Rank only the stones actually offered; previously discarded stones cannot be selected.', '',
            '| Held stones | 0 keys | 1 key | 3 keys | 5 keys | 10 keys |',
            '|---|---|---|---|---|---|']
        for held in pairs:
            cells=[]
            for k in KEYS:
                row=lookup[held,k];top=row['rankings'][0]
                cells.append(f'{SHORT[top["stone"]]}'+(' †' if not row['supported_best'] else '')+f' ({top["mean_hours"]:.2f} h)')
            lines.append('| '+' + '.join(SHORT[g] for g in held)+' | '+' | '.join(cells)+' |')
        lines += ['', '[All candidate ranks and spending](rankings.csv), [confidence contender sets](best-third.csv), '
                  '[method and limitations](report.md).', '', '## Stone names', '', '| Table name | Full name |', '|---|---|']
        pool=profile['gems_first_run' if run==1 else 'gems_later_runs'].split(',')
        lines += [f'| {SHORT[g]} | {FULL[g]} |' for g in pool]
        (out/('first-run.md' if run==1 else 'later-runs.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8')
        for k in KEYS:
            group=[r for r in subset if r['keys']==k];freq={g:sum(r['estimated_best']==g for r in group) for g in pool}
            common=sorted(freq,key=lambda g:(-freq[g],g))[:3]
            overview.append(dict(run=run,keys=k,cells=len(group),supported=sum(bool(r['supported_best']) for r in group),
                common=[dict(stone=g,count=freq[g]) for g in common]))
    counts_lines=['| Run class | Stones | Held pairs | Final three-stone sets | Potential additions per pair | Pair/key states |',
                  '|---|---:|---:|---:|---:|---:|']
    for c in sizes:
        counts_lines.append(f'| {c["run"]} | {c["stones"]} | {c["held_pairs"]} | {c["final_sets"]} | '
                            f'{c["potential_additions"]} | {c["pair_key_states"]} |')
    supported=sum(bool(r['supported_best']) for r in rows)
    lines=['# Every held pair: best third stone at 1% HP and five mushrooms','',
        f'Completed **{total:,} distinct simulated continuations**. Reusing identical final sets covers all **1,175 pair/key situations** '
        'and **18,900 candidate additions** without replaying each final set three times. '
        f'**{supported} situations** have a unique supported winner; the remaining {len(rows)-supported} retain multiple confidence contenders.',
        '', '## The complete tables', '',
        '- [Later runs: every one of the 190 held pairs](later-runs.md).',
        '- [First run: every one of the 45 held pairs](first-run.md).',
        '- [Best third stone and confidence contenders, CSV](best-third.csv).',
        '- [Every candidate, average time and mushrooms spent, CSV](rankings.csv).',
        '', '## Number of combinations','',*counts_lines,'',
        'Counts are unordered: choosing A then B gives the same held pair as B then A once the other starting conditions are fixed. '
        'The first-run pool is a subset of the later-run pool; its 120 sets are not 120 additional unique sets, but their run context is tested separately. '
        'For one exact prior history, four previously rejected stones reduce possible new additions from 18 to 14 on later runs, or from 8 to 4 on the first run. '
        'The real screen then offers only three of those. These tables rank potential additions across histories, not an unrestricted in-game choice.',
        '', '## Conditions and method','',
        'The state is alive immediately after the room75 boss, before room76, at exactly 1% HP. '
        'It has no temporary blessings/curses, no previous paid refills, correct consumed-shop history and an unused stage4 shop. '
        'The five starting key levels are 0,1,3,5,10. Gross mushroom spending cannot exceed five, including shop refreshes and healing; spending all five is not compulsory. '
        'The 20% re-entry rule remains active after death. Natural recovery and the frozen deployed actor with its normal guards determine subsequent actions.',
        '', 'For each final set, the first-run experiment averages all seven legal six-rejection sets with 128 fresh futures per history (896 total per key level). '
        'Later runs average eight frozen legal history samples, again with 128 fresh futures each (1,024 total per key level). '
        'Every history contains three chosen and six discarded stones. The game cannot distinguish which of the three owned stones was picked last after the choice; '
        'native parity tests verify identical observations, masks and subsequent transitions for that reuse.',
        '', 'The objective is mean remaining completion time, capped at 168 hours. An unfinished continuation contributes 168 hours rather than disappearing. '
        'Continuous availability starts at17:00, without a sleep window. These are final-stage continuations, not full-week forecasts.',
        '', '## Confidence','',
        f'The family contains **{family:,} paired candidate comparisons** across every held pair and all five key levels. '
        f'Intervals use a normal approximation and a Bonferroni correction for simultaneous 95% coverage (z={z:.4f}). '
        'Variance is stratified by the frozen history panel. Shared future seeds pair different candidates and key levels; branches can consume different random draws. '
        'A candidate stays in the confidence set unless another candidate is significantly faster. A sole remaining candidate is marked supported. '
        'The lowest sample mean is always reported, but it is not called a proven winner when alternatives remain.',
        '', 'These intervals cover simulation sampling uncertainty conditional on the declared history panels. '
        'They do not cover all possible offer histories, inaccurate game probabilities, active-effect combinations, '
        'different recovery policies, sleep schedules or global policy optimality.',
        '', '## Which additions lead most often in this grid?','',
        'These counts weight every held pair equally. They are not offer probabilities or frequencies in normal play.', '',
        '| Run class | Keys | Unique supported winners / pair states | Three most frequent leading estimates (pair count) |',
        '|---:|---:|---:|---|']
    for row in overview:
        lines.append(f'| {row["run"]} | {row["keys"]} | {row["supported"]}/{row["cells"]} | '+
            ', '.join(f'{SHORT[x["stone"]]} ({x["count"]})' for x in row['common'])+' |')
    lines += ['', '## Looking up an actual offer','',
        'The lookup can restrict the ranking to the three stones on screen and remove known prior rejections. '
        'This filters availability; the outcome estimates still average the study history panel.', '',
        '```powershell',
        'out/rl-env/Scripts/python.exe tools/query_final_stones.py --study docs/final-stones-five-2026-10-15 --run 2 --held rabbit,moonstone --keys 3 --offers greasy,greed,pendant',
        '```', '', '## Reproducibility','',
        'The checkpoint, native library, profile, source files, legal history panels and raw outcomes are frozen in this directory. '
        '[All paired intervals](paired-contrasts.csv.gz), [machine-readable rankings](rankings.json), [verification](verification.json). '
        'No mechanics or deployed policy changed. The exact study command was:', '', '```powershell',
        'out/rl-env/Scripts/python.exe tools/benchmark_final_stones.py --output docs/final-stones-five-2026-10-15 --workers 8', '```', '']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    root=Path(__file__).resolve().parents[1]
    analysis_sources={}
    for source in (Path(__file__),root/'tools/query_final_stones.py',root/'tests/test_publish_final_stones.py'):
        name=source.relative_to(root).as_posix();target=out/'analysis-source'/name
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
        analysis_sources[name]=train_rl.sha(target)
    verification=dict(passed=True,trajectories=total,final_set_key_cells=len(data)*len(KEYS),pair_key_cells=len(rows),
        candidate_additions=sum(len(r['rankings']) for r in rows),simultaneous_contrasts=family,z=z,
        supported_cells=supported,unresolved_cells=len(rows)-supported,overview=overview,
        max_observed_spend=max(float(a[...,3].max()) for a in data.values()),
        plan_sha256=train_rl.sha(out/'plan.json'),rankings_sha256=train_rl.sha(out/'rankings.json'),analysis_sources=analysis_sources)
    (out/'verification.json').write_text(json.dumps(verification,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(verification,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);a=p.parse_args();publish(a.output.resolve())
