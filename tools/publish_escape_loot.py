"""Archive the predeclared escape/loot comparison, including paired uncertainty."""
import csv
import gzip
import io
import json
from pathlib import Path
import shutil
import statistics

from benchmark_escape_loot import METRICS
from benchmark_player_ideas import interval, write_once
from sfld_rl.escape_loot import CASES_LOOT
from train_rl import sha

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT/'out/escape-loot-069'
    dest = ROOT/'docs/escape-loot-2026-10-11'
    dest.mkdir(parents=True, exist_ok=True)
    rows, summaries, comparisons, plans = [], [], [], {}
    for budget in (0,20,50,100,500,1000):
        folder = source/f'cap-{budget}'
        plan = json.loads((folder/'plan.json').read_text())
        assert plan['namespace'] == 232 and plan['events_per_cell'] == 4096
        assert plan['cases'] == list(CASES_LOOT) and plan['budgets'] == [budget]
        plans[str(budget)] = plan
        cells = {}
        for case in CASES_LOOT:
            cell = json.loads((folder/f'cap-{budget}-{case}.json').read_text())
            assert cell['plan'] == plan
            outcomes = cell['outcomes']
            assert [r['event_index'] for r in outcomes] == list(range(4096))
            for row in outcomes:
                assert row['budget'] == budget and row['case'] == case
                assert not row['invalid_actions'] and row['actions'] == row['decisions']
                assert 0 <= row['mushrooms'] <= budget
                assert row['mushrooms'] == row['recovery_mushrooms'] + row['reroll_mushrooms']
                assert row['gold'] == row['gold_units'] * plan['gold_per_unit']
            for metric in METRICS:
                assert cell['summary'][metric] == interval([r[metric] for r in outcomes])
            rows.extend(outcomes); summaries.append(cell['summary']); cells[case] = outcomes
        for case in CASES_LOOT:
            if case == 'control': continue
            comparisons.append({'budget':budget,'case':case,'deltas':{
                k:interval([y[k]-x[k] for x,y in zip(cells['control'],cells[case])]) for k in METRICS}})
    assert len({json.dumps(p['hashes'],sort_keys=True) for p in plans.values()}) == 1
    # 24 comparisons, two declared outcomes: completions and gold.
    critical = statistics.NormalDist().inv_cdf(1-.05/(2*len(comparisons)*2))
    for comparison in comparisons:
        for metric in ('completions','gold'):
            value = comparison['deltas'][metric]
            value['family_95_ci'] = [value['mean']-critical*value['standard_error'],
                                     value['mean']+critical*value['standard_error']]
    result = {'schema':'sfld-escape-loot-results-v1','events':len(rows),
              'plans_by_budget':plans,'summaries':summaries,'comparisons':comparisons,
              'family_comparison_count':len(comparisons)*2,'family_critical':critical}
    write_once(dest/'summary.json',result)
    with (dest/'events.csv.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as zipped:
            with io.TextIOWrapper(zipped,encoding='utf8',newline='') as stream:
                writer = csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
                writer.writeheader(); writer.writerows(rows)
    for key,filename in (('library','sfld_rl.dll'),('profile','synthetic.profile'),
                         ('runner','benchmark_escape_loot.py'),('rules','escape_loot.py'),
                         ('base_rules','player_ideas.py'),('adapter','env.py'),('helpers','benchmark_player_ideas.py')):
        src=source/'cap-0'/filename
        assert sha(src)==plans['0']['hashes'][key]
        shutil.copyfile(src,dest/filename)
    write_once(dest/'artifact-hashes.json',{p.name:sha(p) for p in sorted(dest.iterdir())
                                          if p.is_file() and p.name!='artifact-hashes.json'})
    s={(r['budget'],r['case']):r for r in summaries}
    lines=['# Guaranteed escape and loot, 2026-10-11','',
           '122,880 simulated seven-day events: 4,096 per policy/budget cell. Engine 0.6.9,',
           'fresh seed namespace 232, frozen completion-trained control, random stone offers.',
           'This is an intervention comparison, not new training or a proof of optimal play.','',
           '**Gold is a reporting scenario: 1 relative reward unit = 10,000,000 gold.**',
           'It does not change the policy inputs or learning objective. Real account gold is recorded separately.','',
           'The soldier-only rule searches a sleeping soldier when effective escape is guaranteed and',
           'the current healing/poison tick is survivable. It preserves preparatory healing choices.',
           'Successful escape can still give a curse with Cursed Pearl; this remains simulated.','',
           'The broader safe-escape rule also saves keys against an untrapped monster, avoids a selected',
           'trap when an untrapped legal alternative exists, and flees ordinary monsters unless One Hit',
           'is active. The optional Pendant fallback replaces only Bull or Hick when Pendant is offered.',
           'The previous escape rule is retained as a reference. None forces a complete stone ranking.','',
           '| Mushroom cap | Control clears | Soldier-only clears | Control gold, M | Soldier-only gold, M | Safe-escape clears | Safe + Pendant clears |',
           '|---:|---:|---:|---:|---:|---:|---:|']
    for budget in (0,20,50,100,500,1000):
        c=s[budget,'control']; a=s[budget,'soldier_only']
        lines.append(f"| {budget} | {c['completions']['mean']:.4f} | {a['completions']['mean']:.4f} | {c['gold']['mean']/1e6:.2f} | {a['gold']['mean']/1e6:.2f} | {s[budget,'safe_escape']['completions']['mean']:.4f} | {s[budget,'safe_escape_fallback']['completions']['mean']:.4f} |")
    lines+=['','Paired changes against control follow. Intervals adjust across 48 comparisons',
            '(four rules × six budgets × two outcomes) using a Bonferroni normal approximation.',
            'These measure Monte Carlo uncertainty within this simulator; uncertain mechanics remain.','',
            '| Cap | Rule | Change in clears [family 95% CI] | Change in gold, M [family 95% CI] |',
            '|---:|---|---:|---:|']
    for c in comparisons:
        d=c['deltas']; x=d['completions']; y=d['gold']; ci=x['family_95_ci']; gi=y['family_95_ci']
        lines.append(f"| {c['budget']} | {c['case']} | {x['mean']:+.4f} [{ci[0]:+.4f}, {ci[1]:+.4f}] | {y['mean']/1e6:+.2f} [{gi[0]/1e6:+.2f}, {gi[1]/1e6:+.2f}] |")
    lines+=['','The soldier-only comparison shows no detectable completion change and higher simulated',
            'gold at every budget. This supports testing that narrow rule in the pilot while retaining',
            'learned stone choices. The broader forced rules do not improve completions here; possible',
            'causes include fewer fight keys and changes to blessing consumption. This experiment does',
            'not isolate those causes. No stronger live-game guarantee follows from these results.','',
            '[All metrics and provenance](escape-loot-2026-10-11/summary.json),',
            '[per-event outcomes](escape-loot-2026-10-11/events.csv.gz). Actual spending, door keys,',
            'soldier searches, awakenings, epics and legendaries are included in those artifacts.','']
    (ROOT/'docs/ESCAPE_LOOT_2026_10_11.md').write_text('\n'.join(lines),encoding='utf8')
    print(json.dumps({'events':len(rows),'report':'docs/ESCAPE_LOOT_2026_10_11.md'}))


if __name__=='__main__': main()
