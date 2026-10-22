"""Archive the fresh four-policy check after targeted exploration."""
import argparse
import gzip
import itertools
import json
from pathlib import Path
import shutil
from statistics import NormalDist,mean
from publish_effect_study import sha,read,write,stats

ROOT=Path(__file__).resolve().parents[1]


def publish(source,main,target,report):
    result=read(source/'summary.json');plan=result['plan'];training=read(source/'exploration/training-summary.json')
    if plan['smoke'] or training['state']['steps']!=plan['steps']:raise ValueError('Need completed real training')
    if plan.get('allow_seed_reuse'):raise ValueError('Reproduction reuses archived seeds; it is not an independent promotion study')
    for p,h in plan['sources'].items():
        if sha(source/'source'/p)!=h:raise ValueError('Source snapshot changed')
    if read(source/'selection.json')!=result['selection']:raise ValueError('Selection changed')
    if read(source/'training-selection.json')!=result['candidates']:raise ValueError('Candidates changed')
    for name,key in [('parent-checkpoint.pt','parent_sha256'),('sfld_rl.dll','library_sha256'),('synthetic.profile','profile_sha256')]:
        if sha(source/name)!=plan[key]:raise ValueError('Input changed')
    cells={};rows=[]
    for c in result['candidates']:
        if sha(source/c['checkpoint'])!=c['sha256']:raise ValueError('Checkpoint changed')
        for b in plan['budgets']:
            cell=read(source/f'final-{c["name"]}-{b}.json');identity=cell['identity']
            if (identity['candidate']!=c or identity['checkpoint_sha256']!=c['sha256'] or identity['cohort']!=plan['final'] or
                identity['budget']!=b or identity['profile_sha256']!=plan['profile_sha256'] or identity['library_sha256']!=plan['library_sha256']):raise ValueError('Final identity mismatch')
            if [r['event'] for r in cell['rows']]!=list(range(plan['final']['events'])):raise ValueError('Missing final events')
            for r in cell['rows']:
                if (r['invalid_actions'] or not 0<r['actions']<100000 or r['mushrooms']>b or
                    r['mushrooms']!=r['recovery_mushrooms']+r['reroll_mushrooms'] or not 167.999<=r['elapsed_hours']<=168.00000001):raise ValueError('Invalid accounting')
            cells[c['name'],b]=cell;rows+=cell['rows']
    names=[c['name'] for c in result['candidates']];pairs=list(itertools.combinations(names,2));comparisons=[]
    if len(pairs)!=6:raise ValueError('Expected four-policy comparison')
    z=NormalDist().inv_cdf(1-.01/(2*42))
    for old,new in pairs:
        differences=[]
        for b in plan['budgets']:
            delta=[a['completions']-p['completions'] for a,p in zip(cells[new,b]['rows'],cells[old,b]['rows'])]
            differences.append(delta);comparisons.append({'old':old,'new':new,'budget':b,**stats(delta,z)})
        comparisons.append({'old':old,'new':new,'budget':'equal-budget mean',**stats([mean(v) for v in zip(*differences)],z)})
    winner=result['selection']['winner'];contrast=next((c for c in comparisons if c['new']==winner and c['old']=='parent' and c['budget']=='equal-budget mean'),None)
    deployment={'selected_on_validation':winner,'promote':contrast is not None and contrast['family_99_ci'][0]>0,'comparison':contrast,
        'selected_checkpoint':next(c for c in result['candidates'] if c['name']==winner),'rule':plan['promotion_gate']}
    counts=read(source/'exploration/exploration-counts.json');audit=read(source/'policy-choices.json')
    if audit['candidates']!=result['candidates']:raise ValueError('Choice audit used different models')
    if target.exists() or report.exists():raise FileExistsError('Archive/report already exists')
    target.mkdir(parents=True);(target/'.gitattributes').write_text('** -text\n',encoding='utf-8')
    for file in ('plan.json','seed-audit.json','selection.json','training-selection.json','invocation.json',
                 'sfld_rl.dll','synthetic.profile','policy-choices.json'):
        shutil.copyfile(source/file,target/file)
    for c in result['candidates']:
        p=target/c['checkpoint'];p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/c['checkpoint'],p)
    for file in ('curve.jsonl','run.json','best.json','latest.json','training-summary.json','exploration-counts.json'):
        shutil.copyfile(source/'exploration'/file,target/'exploration'/file)
    latest=read(source/'exploration/latest.json');endpoint=source/'exploration'/latest['checkpoint']
    if sha(endpoint)!=latest['sha256'] or latest['steps']!=plan['steps']:raise ValueError('Endpoint checkpoint changed')
    shutil.copyfile(endpoint,target/'exploration'/latest['checkpoint'])
    if (source/'endpoint-choice-diagnostic.json').exists():
        shutil.copyfile(source/'endpoint-choice-diagnostic.json',target/'endpoint-choice-diagnostic.json')
    shutil.copytree(source/'source',target/'source')
    for file in ('publish_effect_exploration.py','publish_effect_study.py','audit_effect_choices.py','probe_effect_choices.py'):
        shutil.copyfile(ROOT/'tools'/file,target/'source/tools'/file)
    (target/'source/tests').mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'tests/test_effect_exploration.py',target/'source/tests/test_effect_exploration.py')
    with gzip.open(target/'final-rows.jsonl.gz','wt',encoding='utf-8') as stream:
        for row in rows:stream.write(json.dumps(row,allow_nan=False)+'\n')
    selection_cells=[{k:v for k,v in read(p).items() if k!='rows'} for p in sorted(source.glob('selection-*.json'))]
    write(target/'selection-cells.json',selection_cells)
    summary={**result,'training':training,'comparisons':comparisons,'deployment':deployment,'exploration_counts':counts}
    write(target/'summary.json',summary);write(target/'deployment.json',deployment)
    main_result=read(main/'summary.json');total=2*main_result['plan']['steps_per_branch']+plan['steps']
    lines=['# Effect decisions: targeted exploration, 2026-10-11','',
        f'This extension adds **{plan["steps"]:,} PPO decisions**, bringing this effect-slot study to **{total:,} new decisions**. It addresses an inherited blind spot: the old policy assigned roughly **2.7e-10 probability** to opening a cursed chest in the tested full-curse position. Simply presenting that state more often did not provide much exploration.','',
        'Training used higher sampling temperature only at containers/blessing doors with multiple blessings, and cursed chests/doors with three curses. The same distribution is used for PPO likelihoods. Greedy inference needs no temperature wrapper because positive temperature preserves action ordering. No gameplay odds, rewards or final evaluation rules were changed.','',
        f'The extension completed **{training["state"]["completed_whole_events"]:,} full training weeks** plus **{training["state"]["completed_partial_scenarios"]:,} partial challenges**. It visited full-curse chests **{counts["full_curse_chest_visits"]:,} times and opened them {counts["full_curse_chest_opens"]:,} times**; this confirms the alternative was actually sampled.','',
        '## Fresh seven-day comparison','',
        f'All four candidates were selected before the final test. This separate test contains **{len(rows):,} fresh simulated weeks**, with {plan["final"]["events"]:,} matched events per budget and policy. The first study has another 36,864 final weeks; its final events were not reused here.','',
        '| Mushrooms | Previous policy | Ordinary continuation | Effect curriculum | Extra exploration |',
        '| ---: | ---: | ---: | ---: | ---: |']
    for b in plan['budgets']:
        values=[cells[n,b]['summary']['completions']['mean'] for n in names]
        lines.append(f'| {b} | '+' | '.join(f'{x:.4f}' for x in values)+' |')
    lines+=['','Selected checkpoint steps: '+', '.join(f'{c["name"]}: {c["steps"]:,}' for c in result['candidates'])+'. A zero-step selection means internal validation retained the initializer; additional training still ran to its declared endpoint.','',
        '| Comparison with previous policy | Equal-budget change | Family-adjusted 99% interval |',
            '| --- | ---: | --- |']
    for c in comparisons:
        if c['old']=='parent' and c['budget']=='equal-budget mean':
            lo,hi=c['family_99_ci'];lines.append(f'| {c["new"]} | {c["mean"]:+.4f} | [{lo:+.4f}, {hi:+.4f}] |')
    lines+=['',f'Independent selection chose **{winner}**. '+('It passes the declared promotion gate.' if deployment['promote'] else 'The promotion gate does not pass; keep the existing browser policy.'),
        'The intervals adjust for all 42 declared weekly contrasts and cluster repeated event indices across budgets. One training seed per method does not prove that any method is generally superior.','',
        '## What the decisions look like','',
        'The following rows are exact greedy choices from the fixed no-mushroom diagnostic positions at room 60. They are engineered comparisons, not the frequency of encountering these situations. The [paired-action study](EFFECT_SLOTS_2026_10_11.md) measures opening versus leaving with a fixed continuation policy.','',
        '| Position | Previous policy | Ordinary | Curriculum | Exploration |',
        '| --- | --- | --- | --- | --- |']
    for case in ('two_good-crate-greed','one_hit_oldest-blessing_door-hero','recovery_oldest-crate-greed',
                 'recovery_oldest-blessing_door-hero','expiring-crate-greed','poison_oldest-cursed_chest-h50',
                 'poison_newest-cursed_chest-h50','short_curses-cursed_chest-h50'):
        choices=[next(r['chosen'] for r in audit['rows'] if r['case']==case and r['budget']==0 and r['candidate']==n) for n in names]
        lines.append('| '+case+' | '+' | '.join(choices)+' |')
    lines+=['','## Limits','',
        'The confirmed rule is that a duplicate replaces its existing slot, even if weaker. Live acquisition tick timing remains uncertain: the simulator delays newly acquired container effects until the next room, which can suppress an existing Poison or Recovery tick when replaced. Curse identity weights and individual container probabilities still include assumptions.',
        'The completion objective does not price skipped gold/items. The diagnostic action test measures remaining clear time, while weekly validation measures total completions; those objectives can prefer different actions when mushrooms are available. The existing shop constraint can still buy a blessing that displaces an older one.','',
        '## Reproduce','',
        '```powershell',
        'out/rl-env/Scripts/python.exe tools/run_effect_exploration.py --study out/effect-study-new --output out/effect-exploration-new --allow-seed-reuse',
        '```','',
        'First create the ordinary/curriculum study described in [Effect slots](EFFECT_SLOTS_2026_10_11.md). This reruns the design with current code and marks seed reuse as reproduction, not independent evidence. Archived source snapshots preserve the historical implementation. Choose and preregister fresh roots for a new final test. Generic checkpoint loading uses the deployment distribution (temperature 1); the exploration training wrapper is needed to reproduce sampling likelihoods.','',
        f'[Machine-readable archive]({target.name}/summary.json).','']
    report.write_text('\n'.join(lines),encoding='utf-8')
    write(target/'manifest.json',{'schema':'sfld-effect-exploration-archive-v1','files':{p.relative_to(target).as_posix():sha(p) for p in sorted(target.rglob('*')) if p.is_file()}})
    print(json.dumps({'total_new_decisions':total,'final_weeks':len(rows),'exploration_counts':counts,'deployment':deployment,'report':str(report)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--main',type=Path,required=True)
    p.add_argument('--target',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();publish(a.source.resolve(),a.main.resolve(),a.target.resolve(),a.report.resolve())
