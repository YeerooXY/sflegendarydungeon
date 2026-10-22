"""Archive the UI acceptance follow-up without selecting on its final results."""
import argparse
import json
from pathlib import Path
import shutil
from publish_effect_study import sha, read, write

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--target',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    result=read(a.source/'summary.json');plan=result['plan'];cells={}
    if plan['candidate_selected_before_final']!='guarded':raise ValueError('Wrong declared candidate')
    for name,h in plan['sources'].items():
        if sha(a.source/'source'/name)!=h:raise ValueError('Source snapshot changed')
    for c in result['candidates']:
        if sha(a.source/c['checkpoint'])!=c['sha256']:raise ValueError('Checkpoint changed')
        for b in plan['budgets']:
            cell=read(a.source/f'final-{c["name"]}-{b}.json')
            if cell['identity']['candidate']!=c or cell['identity']['cohort']!=plan['final']:raise ValueError('Cell identity differs')
            if [r['event'] for r in cell['rows']]!=list(range(2048)):raise ValueError('Wrong event cohort')
            if any(r['invalid_actions'] or r['mushrooms']>b for r in cell['rows']):raise ValueError('Invalid event')
            cells[c['name'],b]=cell
    if a.target.exists() or a.report.exists():raise FileExistsError('Archive already exists')
    a.target.mkdir(parents=True)
    for file in ('parent.pt','curriculum.pt','sfld_rl.dll','synthetic.profile','plan.json','candidates.json',
                 'seed-audit.json','summary.json','final-rows.jsonl.gz'):
        shutil.copyfile(a.source/file,a.target/file)
    shutil.copytree(a.source/'source',a.target/'source')
    shutil.copyfile(__file__,a.target/'source/tools/publish_early_trap_guard.py')
    (a.target/'source/tests').mkdir()
    shutil.copyfile(ROOT/'tests/test_early_trap_guard.py',a.target/'source/tests/test_early_trap_guard.py')
    (a.target/'.gitattributes').write_text('** -text\n',encoding='utf-8')
    lines=['# Early-stage trap preference: acceptance follow-up', '',
        'The browser regression test caught a concrete difference: at room 10, 90% HP and three keys, with a trapped mystery room beside a safe monster, the new curriculum network selected the trap. The previous adviser selected the monster, matching the player\'s requested behavior.', '',
        'The declared follow-up adds one narrow rule: before room 25, replace a proposed trapped-door choice with the other door when it is an available, untrapped visible monster. Paid healing and every unrelated choice remain unchanged. No retraining, damage or probability changes are involved.', '',
        'The guarded curriculum was designated as the candidate before this fresh test. All three policies were tested on 2,048 matched events per budget, 36,864 simulated policy-weeks total. These seeds are separate from both previous studies.', '',
        '| Mushrooms | Previous adviser | Curriculum | Curriculum + early trap rule |',
        '| ---: | ---: | ---: | ---: |']
    for b in plan['budgets']:
        lines.append('| '+str(b)+' | '+' | '.join(f"{cells[n,b]['summary']['completions']['mean']:.4f}" for n in ('parent','curriculum','guarded'))+' |')
    lines += ['', '| Equal-budget contrast | Clears/week difference | Family-adjusted 99% interval |',
              '| --- | ---: | --- |']
    for c in result['contrasts']:
        if c['budget']=='equal-budget mean':
            lo,hi=c['family_99_ci'];lines.append(f"| {c['new']} minus {c['old']} | {c['mean']:+.4f} | [{lo:+.4f}, {hi:+.4f}] |")
    lines += ['', ('The guarded candidate passes its declared promotion gate and is used by the viewer.' if result['promotion'] else
        'The guarded candidate does not pass its declared promotion gate. Keep the previous viewer checkpoint. The unguarded network passed the earlier statistical gate but did not meet this tactical acceptance check; it remains an archived research candidate.'), '',
        'Intervals adjust for 21 declared contrasts and cluster the same event index across budgets. The comparison is conditional on the simulator and its uncertain mechanics. No globally optimal strategy is established. The missed crate-preservation and curse-replacement choices remain open research problems.', '',
        '[Full results and declared plan]('+a.target.name+'/summary.json). [Earlier exploration comparison](EFFECT_EXPLORATION_2026_10_11.md). [Runtime improvements](PERFORMANCE_2026_10_11.md).', '']
    a.report.write_text('\n'.join(lines),encoding='utf-8')
    write(a.target/'manifest.json',{'files':{p.relative_to(a.target).as_posix():sha(p) for p in sorted(a.target.rglob('*')) if p.is_file()}})
    print(json.dumps({'promotion':result['promotion'],'report':str(a.report)}))


if __name__=='__main__':main()
