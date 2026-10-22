"""Archive the fixed v4 practical-rule experiment without rewriting older studies."""
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
from statistics import NormalDist
from benchmark_player_ideas import interval

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'out/practical-v4-study'
TARGET=ROOT/'docs/practical-2026-10-11'

def main():
    if TARGET.exists():raise RuntimeError('Use a new archive destination')
    source=json.loads((SOURCE/'summary.json').read_text());plan=source['plan']
    cells={(c['budget'],c['case']):c for c in source['cells']};all_rows=[];by_cell={}
    for budget in plan['budgets']:
        for case in plan['cases']:
            cell=json.loads((SOURCE/f'cap-{budget}-{case}.json').read_text())
            if cell['plan']!=plan:raise RuntimeError('Plan mismatch')
            rows=cell['rows'];by_cell[budget,case]=rows;all_rows.extend(rows)
    comparisons=source['comparisons']
    for budget in plan['budgets']:
        a=by_cell[budget,'patient_safe'];b=by_cell[budget,'patient52']
        comparisons.append({'budget':budget,'case':'patient52','control':'patient_safe',
            'deltas':{k:interval([y[k]-x[k] for x,y in zip(a,b)]) for k in ('completions','legendaries','mushrooms','gold')}})
    n=sum(len(c['deltas']) for c in comparisons);z=NormalDist().inv_cdf(1-.01/(2*n))
    for c in comparisons:
        for stats in c['deltas'].values():stats['family_99_ci']=[stats['mean']-z*stats['standard_error'],stats['mean']+z*stats['standard_error']]
    source['inference']={'family':'all completion, legendary, mushroom and gold contrasts','contrasts':n,'method':'Bonferroni normal paired event intervals','family_confidence':.99}
    TARGET.mkdir()
    (TARGET/'summary.json').write_text(json.dumps(source,indent=2)+'\n','utf-8')
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(all_rows[0]));writer.writeheader();writer.writerows(all_rows)
    (TARGET/'events.csv.gz').write_bytes(gzip.compress(stream.getvalue().encode(),mtime=0))
    for name in ('sfld_rl.dll','synthetic.profile','benchmark_practical.py','practical.py','escape_loot.py','player_ideas.py','env.py','benchmark_player_ideas.py','ppo.py','train_rl.py','plan.json'):
        shutil.copyfile(SOURCE/name,TARGET/name)
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in TARGET.iterdir()}
    (TARGET/'manifest.json').write_text(json.dumps(hashes,indent=2)+'\n','utf-8')
    labels={'learned':'Frozen policy + safe soldier search','shop5':'Five-refresh allowance; stop for useful offer',
            'keys':'+ stronger key saving','trials':'+ favorable trial attempts','trades':'+ protected curse trades',
            'tactics':'+ all three tactics','patient52':'All tactics + 52% final-boss wait','patient_safe':'All tactics + 52.6% final-boss wait'}
    table=['| Policy | 100 mushrooms | 500 mushrooms | 1,000 mushrooms |','|---|---:|---:|---:|']
    for case in plan['cases']:table.append('| '+labels[case]+' | '+' | '.join(f"{cells[b,case]['summary']['completions']['mean']:.4f}" for b in plan['budgets'])+' |')
    text='''# Practical tactics and corrected first-run stone pool, 2026-10-11

49,152 synthetic seven-day events: 2,048 per policy/budget cell, eight policies,
three positive mushroom budgets. Each policy uses the same paired event seeds.
This tests explicit rules around a frozen neural checkpoint; it is not retraining.

'''+ '\n'.join(table)+'''

Rows for keys, trials and trades add just that change to the five-refresh row.
The last three rows combine all three changes. “Five” is an earned allowance,
not a requirement to pay five times. Unused credits carry forward; rerolls need
four keys and enough of the shared event budget. One Hit, Recovery, useful
Escape, and a strong Lockpick at healthy HP can stop the search early.

## What the experiment supports

- The tested early-stop/five-refresh package reduced completions compared with
  the unconstrained frozen policy at all three budgets. This does **not** isolate
  the cost of “five” alone: the package also changes purchase selection and the
  four-key gate. That distinction matters before optimizing the allowance.
- The more aggressive key-saving rule also reduced mean clears. Its benefit is
  not established. Do not replace the learned door choices with blanket hoarding.
- Favorable trial attempts increased arena reward collection a little. The
  completion differences do not establish a gain. The rule checks current
  effective escape, health, poison and effect expiry at every tier. An 80%
  escape chance is not five guaranteed escapes.
- Protected curse trades did not show a clear completion gain. One Hit/Recovery
  duration matters: the current room consumes an old blessing before a newly
  accepted curse starts. Low-health trades count the retained keys, recovery
  delay, remaining event time and lost blessings.
- 52% is below the baseline final boss's 52.5% upper bound. Under this profile's
  uniform distribution, a 52% attempt dies on roughly 5.3% of hits. Waiting to
  52.6% takes 8.64 minutes more from the same HP and avoids that modeled risk.
  The 52.6% candidate performed better than 52% at the higher budgets; its change
  against the otherwise identical learned recovery was very small.

The final-boss waiting intervention applies only after two partial refills,
with enough time left to wait and fight. It leaves paid recovery available near
the deadline. It never changes ordinary recovery into a blanket 52% wait.

At the 1,000-mushroom cap, the trial candidate collected 118 tier-five prizes
versus 39 for the five-refresh policy, across 2,048 weeks each. Bonus legendary
items above completion rewards numbered seven versus two. That small count
does not establish a reliable legendary-yield improvement; total legendaries
still averaged 9.0444 versus 9.0552 because clears were slightly lower.

## Model and limits

Profile `vod-working-2026-10-11-v4` excludes Rabbit on the first run of each
event. Later runs still include it. Previous v3 studies retain their original
pool and results. Eligible offered stones remain random, with all previously
offered stones excluded until the run ends. No fixed favorable stones are granted.

The current five-tier arena table is 19 gold, 20 ordinary items, 6 epics and
3 legendaries out of 48 reviewed rewards: the simulation therefore uses 6.25%
for a tier-five legendary, not the earlier 10% placeholder. These rare observed
rewards remain uncertain. Damage, several room probabilities and interactions
also retain model uncertainty. Gold is reported at the requested 10M/unit scale.

The archive includes per-event rows, exact binary/profile/source snapshots,
hashes, ordinary paired 99% intervals and simultaneous Bonferroni 99% intervals
over all 96 completion/legendary/mushroom/gold contrasts. Intervals quantify
simulation sampling error, not accuracy against the live game. Do not treat a
zero paired difference or zero observed rare reward as proof of impossibility.

## Browser and live pilot

The browser now uses the same frozen checkpoint through a visible-state adapter,
verified against all 460 native features and the 23-action mask on 400 states.
Its default follows the requested five-refresh package and learned stone/door
choices. It also caps final-boss waits just above the selected damage ceiling,
and waits there after two partial refills if time permits. This final narrow
wait adjustment is separate from the combined-tactics rows above. The stronger
key-hoarding, partial-escape trial and curse-trade rules remain experiment
candidates, rather than being described as a proven optimum.

The optional C++ rule adviser has corresponding candidate tactics behind
`--practical-tactics true`, and `--patient-boss-hp .52` or `.526` for controlled
comparisons. It is not the neural checkpoint in this table.

The real-game pilot is still unvalidated. Its two prepared characters have a
five-mushroom reroll cap each, ten total, and no paid-healing commands. MFbot's
active sessions block its preflight. No live dungeon move was sent by this work.
'''
    (ROOT/'docs/PRACTICAL_TACTICS_2026_10_11.md').write_text(text,'utf-8')
    print(f'Archived {len(all_rows)} events and {n} paired contrasts.')

if __name__=='__main__':main()
