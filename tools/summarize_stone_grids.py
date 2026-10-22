"""Join separately frozen zero/paid-budget studies without pooling contexts."""
import argparse
import json
from pathlib import Path
import train_rl


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def run(studies,target):
    plans=[read(s/'plan.json') for s in studies];all_cases=[];checks=[];budget_rows=[]
    total_screen=total_confirm=unique_confirm=total_dense=0;budgets=set()
    family=sum(len(read(s/'selection.json'))*7 for s in studies)
    for s,p in zip(studies,plans):
        v=read(s/'verification.json');c=read(s/'comparisons.json');summary=read(s/'summary.json')
        if not v['passed'] or c['simultaneous_contrasts']<family:raise RuntimeError('Joint confidence verification missing')
        for name in ('checkpoint_sha256','profile_sha256','library_sha256'):
            if p[name]!=plans[0][name]:raise RuntimeError('Study inputs differ: '+name)
        if budgets&set(p['budgets']):raise RuntimeError('Duplicate budget studies')
        budgets.update(p['budgets']);all_cases.extend((s,r) for r in c['comparisons'])
        total_screen+=v['screening_trajectories'];total_confirm+=v['confirmation_trajectories']
        unique_confirm+=v['unique_confirmation_trajectories'];total_dense+=(v['dense'] or {}).get('trajectories',0)
        for b in p['budgets']:
            rows=[r for r in c['comparisons'] if r['budget']==b]
            budget_rows.append(dict(budget=b,screening=sum(m['run'] in (1,2) for m in p['menus'])*len(p['health'])*len(p['keys'])*p['screening']['trials']*3,
                tested_reversals=len(rows),key_reversals=sum(r['family']=='keys' and r['supported_pairwise_reversal'] for r in rows),
                hp_reversals=sum(r['family']=='health' and r['supported_pairwise_reversal'] for r in rows),
                best_offer_reversals=sum(r['supported_best_offer_reversal'] for r in rows),
                supported_baseline_misses=sum(e['supported_best'] and e['selected']!=e['baseline'] for r in rows for e in r['endpoints'])))
        checks.append(dict(study=str(s),plan_sha256=train_rl.sha(s/'plan.json'),verification_sha256=train_rl.sha(s/'verification.json')))
    if budgets!={0,20,50,100}:raise RuntimeError('Requested budget grid is incomplete')
    lines=['# Context-dependent final stones: 0, 20, 50 and 100 mushrooms','',
        'Keys and health can change which of the three offered stones is best under the current continuation policy. '
        'This study measures that by forcing each first choice from the same room-76 state, after the room-75 boss.',
        '', f'Completed **{total_screen+total_confirm+total_dense:,} simulated continuations**: {total_screen:,} broad screening, '
        f'{total_confirm:,} confirmation executions ({unique_confirm:,} distinct state/action/seed continuations), '
        f'and {total_dense:,} dense HP/key follow-up. Raw paired results and frozen inputs are retained.',
        '', '## Coverage and confidence','',
        'Every eligible held-pair/third-stone identity is represented in both run classes: 190 later-run pairs and 45 first-run pairs. '
        'There are 1,275 legal three-offer menus, keys 0 through 10 and nine initial HP levels (1,10,20,30,40,50,60,75,100). '
        'The 100% row is a stress test; ordinary immediate post-boss HP tops out at 77.05% in the current profile. '
        'All four remaining-mushroom budgets are separate. The initial state is alive, with no temporary effects and no previous paid refills.',
        '', 'Broad screening uses eight futures per candidate and is deliberately provisional. '
        'For each budget, 24 promising reversals receive 2,048 new futures per candidate at each endpoint. '
        f'The joint confidence correction covers {family} contrasts across all four budgets, using paired normal-approximation intervals with a Bonferroni correction. '
        'Two screening-selected menus also have every integer HP from 1% through 77% and keys 0 through 10 tested with 256 fresh futures per candidate; '
        'their separate confidence family covers 5,082 contrasts and leaves close cells unresolved.',
        '', '| Remaining mushrooms | Screening continuations | Confirmed key preference reversals | Confirmed HP preference reversals | Confirmed best-of-three reversals | Supported misses by current policy* |',
        '|---:|---:|---:|---:|---:|---:|']
    for r in sorted(budget_rows,key=lambda r:r['budget']):
        lines += [f'| {r["budget"]} | {r["screening"]:,} | {r["key_reversals"]} | {r["hp_reversals"]} | '
                  f'{r["best_offer_reversals"]} | {r["supported_baseline_misses"]} |']
    lines += ['', '*Counts refer to the deliberately selected diagnostic endpoints. They are not the frequency of mistakes in normal play.',
        '', '## Concrete examples','']
    for b in sorted(budgets):
        valid=[(s,r) for s,r in all_cases if r['budget']==b and r['supported_best_offer_reversal']]
        for factor in ('keys','health'):
            found=next(((s,r) for s,r in valid if r['family']==factor),None)
            if found is None:continue
            s,r=found;m=r['menu'];e0,e1=r['endpoints'];a,c=r['arms']
            lines += [f'### {b} mushrooms: {factor}', '',
                'Held: **'+' + '.join(m['held'])+'**. Offered: **'+', '.join(m['offered'])+'**. '
                'Prior rejected stones: '+', '.join(m['rejected'])+f'. Run class: {m["run"]}.', '',
                '| HP | Keys | Chosen stone | Mean remaining hours | Mean mushrooms spent | Current policy picks |',
                '|---:|---:|---|---:|---:|---|']
            for e,arm in ((e0,a),(e1,c)):
                lines += [f'| {e["state"]["hp"]}% | {e["state"]["keys"]} | {e["selected"]} | '
                          f'{e["mean_hours"][arm]:.3f} | {e["mean_mushrooms"][arm]:.2f} | {e["baseline"]} |']
            lines += ['', 'Paired advantage over the other switching stone, in hours: '+ '; '.join(
                f'{x["mean"]:.3f} [{x["ci"][0]:.3f}, {x["ci"][1]:.3f}]' for x in r['margins'])+
                '. Brackets are joint-adjusted 95% intervals.', '']
    lines += ['## What remains unproven','',
        'These are measured first-action values under the current downstream policy, not globally optimal play. '
        'The broad pass covers stone identities, not every possible three-offer menu, rejected history, effect combination, '
        'refill-price history, event deadline or sleep schedule. Most broad-grid cells need more samples before they can support a recommendation.',
        '', 'The paid-budget objective here is fastest remaining completion within a fixed spending cap. '
        'Mushrooms spent are reported separately. Spending more to finish this run sooner can leave less for later runs; '
        'a whole-event test is required before promoting a conditional rule into the deployed adviser.',
        '', 'No simulator mechanics, deployed checkpoint or live account behavior changed. '
        'The new data can supply training targets and exact-state comparisons. The existing web stone scores remain neural preferences.',
        '', '## Files and exact-state lookup','']
    for s in studies:lines += [f'- [{s.name} report]({s.name}/report.md), [confirmation table]({s.name}/confirmation.csv), '
                               f'[complete compressed screening table]({s.name}/screening.csv.gz).']
    lines += ['- [Dense HP/key diagram](stone-grid-2026-10-15/dense-hp/decision-grid.png) and '
              '[decision table](stone-grid-2026-10-15/dense-hp/decision-table.csv). Grey/unresolved cells have no supported winner.',
        '', '`tools/query_stone_grid.py` accepts held stones, offers, run class, HP, keys, remaining budget and optional rejected history. '
        'It prefers confirmation data to dense data to screening, returns confidence-supported and provisional choices separately, '
        'and refuses to substitute a nearby unstudied HP/state.', '',
        '```powershell', 'out/rl-env/Scripts/python.exe tools/query_stone_grid.py --studies docs/stone-grid-2026-10-15 docs/stone-grid-paid-2026-10-15 --run 2 --held pendant,rusty --offers greed,spying,gambler --hp 60 --keys 10 --budget 0', '```', '']
    target.write_text('\n'.join(lines),encoding='utf-8')
    target.with_suffix('.json').write_text(json.dumps(dict(studies=checks,simultaneous_contrasts=family,budgets=budget_rows,
        screening=total_screen,confirmation_executions=total_confirm,unique_confirmation=unique_confirm,dense=total_dense),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(report=str(target),total_executions=total_screen+total_confirm+total_dense,budgets=budget_rows),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--studies',nargs='+',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run([s.resolve() for s in a.studies],a.output.resolve())
