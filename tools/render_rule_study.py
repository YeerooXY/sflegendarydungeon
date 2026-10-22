"""Regenerate the frozen 2026-10-09 rule report and guide from saved results."""
from pathlib import Path
import json, hashlib, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from benchmark_rule_final import mix
P=ROOT/'docs/rule-strategy-2026-10-09'

def read(p): return json.loads(p.read_text(encoding='utf-8'))
def report(case): return read(P/('final-free' if 'free' in case else 'final-paid')/f'working--{case}.json')
def rows(case): return report(case)['evaluation']['budgets']
def fields(case): return dict(line.split('=',1) for line in (P/'rules'/f'{case}.rules').read_text().splitlines() if '=' in line)
def nums(case,k): return ', '.join(fields(case)[k].split(','))

reports={case:report(case) for case in ['score-free','human-free','score-free-flat','score-paid','human-paid','score-paid-flat']}
for part in ['final-free','final-paid','sensitivity']:
 m=read(P/part/'manifest.json')
 for item in m['results']:
  path=P/part/item['report'];d=read(path);case=next(c for c in m['cases'] if c['id']==item['case'])
  assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
  assert d['rules_text']==case['rules_text']
  assert d['settings']['controller']==case['controller']

# All actual training/selection event seeds are disjoint from both evaluation
# cohorts. Shared seeds between frozen candidates are intentional comparisons.
index=read(P/'study-index.json');seen=set();audit=[]
for study in index['searches']:
 for c in study['seed_plan']['cohorts']:
  if c['name']=='final_evaluation':continue
  seeds={mix(int(c['master_seed'])^i) for i in range(c['events'])}
  assert not seen.intersection(seeds);seen.update(seeds)
  audit.append({'study':study['name'],'cohort':c['name'],'events':len(seeds),'overlap':0})
for name,root,n in [('held-out',711100,20000),('sensitivity',711200,3000)]:
 master=(0x73<<56)|((mix(root)&0xffffffff)<<24);seeds={mix(master^i) for i in range(n)}
 assert not seen.intersection(seeds);seen.update(seeds)
 audit.append({'study':name,'events':n,'overlap':0,'master_seed':str(master)})
(P/'seed-audit.json').write_text(json.dumps({'schema':1,'actual_cohorts':audit,'unique_event_seeds':len(seen),'shared_across_candidates_intentionally':True},indent=2)+'\n')

names={'rabbit':'Soul of the Rabbit','greasy':'Greasy Healing Stone','moonstone':'Cursed Moonstone','gambler':'Boulder of the Gambler','pearl':'Cursed Pearl','greed':'Boulder of Greed','spying':'Spying Gem','time_traveler':'Diamond of the Time Traveler','hero':'Treasure of the Hero','thirsty':'Hope of the Thirsty One','blood':'Blood Drop of Sacrifice','explorer':'Emerald of the Explorer','misadventurer':'Sapphire of the Misadventurer','devil':'Crown Jewel of the Devil','deceit':'Pebble of Deceit','lodestone':'Lodestone','pendant':'Pendant of the Key Master','bull':'Eye of the Bull','hick':'Erratic Boulder of the Hick','rusty':'Rusty Healing Stone'}
def rank(case): return sorted(names, key=lambda k:-float(fields(case)['gem.'+k]))

human_actions=read(P/'human-actions.json')
(P/'human-actions.json').write_text(json.dumps(human_actions,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
short={}
for case in ['human-free','human-paid']:
 f=fields(case);paid=case.endswith('paid')
 short[case]=[
  f"Farm keys below the stage targets {nums(case,'fight_keys')}. Stop farming once escape reaches 50% or modeled fight death risk exceeds {float(f['max_fight_death_risk'])*100:g}%. One Hit overrides these limits; bosses must be fought.",
  f"Without One Hit, favor mystery beside a visible monster; check its trap. With One Hit, normally choose the monster. Favor golden rooms and try to retain {nums(case,'door_reserve')} keys across the four stages. Reserves are preferences, not spending bans.",
  "With certain escape, prefer a trap-free monster to costly or cursed alternatives. Keep safe mystery and golden rooms ahead. Avoid a trap when the other route has no greater expected immediate damage; strongly avoid lethal entry damage.",
  "Take One Hit from the free opening shop. Later, favor useful recovery, low-health potions, One Hit and strong escape. Buy Key Moment when farming keys or Lockpick below two keys. Skip redundant effects, ordinary Disarm and gold-only offers. The exact shop-score floor is 80.",
  ("Allow up to 12 new refresh credits per eligible shop, carrying leftovers forward. This is a ceiling, not forced spending. Refresh only with at least four keys; accept progressively weaker useful offers as the visit uses its credits. Buy at most one." if paid else "Spend no mushrooms. Take the best qualifying shop offer you can afford with keys, or leave."),
  "Open barrels unless Time Traveler makes their blessing chance zero. Search sleeping soldiers with One Hit, or when the risk limit allows it and you need keys or own Greed. Open ordinary containers and use fountains.",
  "Leave mimics unless One Hit is active. Skip the arena route, rock-paper-scissors, wheel, rainbow, cursed chests and sacrifice chests for this completion objective. Optional special-room exceptions are listed in the full rule reference.",
  ("Use the shared budget for rescue and recovery. Compare mushroom cost per HP: full refills usually beat repeated 20% purchases. Near the final boss, a cheaper sufficient partial refill can win. With no money, recover naturally." if paid else "After ordinary death, return at 20% HP. For a boss retry, wait until you can survive its maximum configured hit. Restart at full health after room 100. The test assumes you return as soon as ready.")
 ]
score_actions=[
 "This profile uses detailed action scores. These notes summarize it; select a simple-rules profile for the separate, more easily followed strategy and its measured result.",
 "Value keys most in stage 1, less in stage 2 and least late in a run. Fight when the expected keys justify the health cost; escape when survival is more valuable. One Hit makes ordinary fights especially attractive.",
 "Without One Hit, the current scored policy always takes mystery beside a visible monster. Other pairs compare expected room contents, entry traps, key cost and stone effects. This unconditional mystery override remains a model-policy limitation when the mystery side is trapped.",
 "Buy useful healing and survival effects after considering key cost, active effects and replacement losses. Skip offers whose estimated benefit is not worth their cost; there is no single fixed blessing order.",
 "Drink from a barrel only when its weighted blessing benefit exceeds its curse cost. Skip barrels with Time Traveler. Ordinary containers, optional encounters and corpses use the corresponding estimated benefit or inherited room rule.",
 "Recover to 20% after an ordinary death; wait for enough HP to survive the maximum configured boss hit on a retry. Restart after room 100. These results assume continuous availability."
]
guide={'schema':1,'notice':'20,000 fresh seven-day events per policy and budget, random stone offers, return as soon as recovery allows. These are simulator estimates under the working assumptions, not verified live-game optima. Points are preferences, not odds.','profiles':[]}
titles={'score-free':'Free: strongest tested score policy','human-free':'Free: simple rules','score-paid':'20â€“500 mushrooms: strongest tested score policy','human-paid':'20â€“500 mushrooms: simple rules'}
for case in ['score-free','human-free','score-paid','human-paid']:
 actions=short[case] if case.startswith('human') else list(score_actions)
 if case=='score-paid':actions.insert(4,'Accrue five refresh credits per eligible shop, carry unused credits forward and refresh only with at least four keys. Stop early for a useful offer. Rescue/refills and refreshes share the event cap; full refills are compared by price per HP.')
 if case=='score-free':actions.insert(4,'No mushrooms are spent. The free base chart supplies almost all the measured gain; its conditional adjustments add very little to the weekly average.')
 desc=('Everyday action thresholds and a stone chart. Exact special-room and shop rules are documented in RULE_POLICY_REFERENCE.md.' if case.startswith('human') else 'Stone chart tested with the existing detailed action-scoring controller. Its weekly result belongs to that complete policy, not to a shortened checklist alone.')
 if case=='score-paid':desc+=' Time Traveler ranks fourth here under the assumed full-healing instant potion; the simple paid policy ranks it lower.'
 guide['profiles'].append({'id':case,'title':titles[case],'description':desc,'rules_text':(P/'rules'/f'{case}.rules').read_text(),'results':[{'budget':x['budget'],'mean_completions':x['completions']['mean']} for x in rows(case)],'actions':actions})
(ROOT/'web/rule-guide-data.json').write_text(json.dumps(guide,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

table=['| Event cap | Previous control | Detailed scores + trained stones | Simple rules |','|---|---:|---:|---:|']
ci=['| Policy | Cap | Completions/week | 95% Monte Carlo interval |','|---|---:|---:|---|']
for b in [0,20,50,100,500]:
 suffix='free' if b==0 else 'paid';a=next(x for x in rows('score-'+suffix) if x['budget']==b);h=next(x for x in rows('human-'+suffix) if x['budget']==b)
 control='legacy' if b==0 else 'survival';old=next(x for x in report('score-'+suffix)['controls'][control]['budgets'] if x['budget']==b)
 table.append(f"| {b} | {old['completions']['mean']:.5f} | {a['completions']['mean']:.5f} | {h['completions']['mean']:.5f} |")
 for label,row in [('Detailed scores',a),('Simple rules',h)]:
  lo,hi=row['completions']['normal_95_ci'];ci.append(f"| {label} | {b} | {row['completions']['mean']:.5f} | {lo:.5f}â€“{hi:.5f} |")
spending=['| Cap | Scored policy spent | On refills | On refreshes | Simple policy spent | On refreshes |','|---|---:|---:|---:|---:|---:|']
efficiency=['| Cap | Extra completions versus free scored policy | Extra completions per 100 mushrooms actually spent |','|---|---:|---:|']
for a,h in zip(rows('score-paid'),rows('human-paid')):
 spending.append(f"| {a['budget']} | {a['mean_mushrooms']:.2f} | {a['mean_recovery_mushrooms']:.2f} | {a['mean_reroll_mushrooms']:.2f} | {h['mean_mushrooms']:.2f} | {h['mean_reroll_mushrooms']:.2f} |")
 gain=a['completions']['mean']-rows('score-free')[0]['completions']['mean']
 efficiency.append(f"| {a['budget']} | {gain:.4f} | {100*gain/a['mean_mushrooms']:.3f} |")
ranking=['| Base rank | Free scored chart | Paid scored chart |','|---|---|---|']
for i,(a,b) in enumerate(zip(rank('score-free'),rank('score-paid')),1):ranking.append(f'| {i} | {names[a]} | {names[b]} |')
sens=['| Alternative assumption | Scored free | Gain over free control | Scored 500 cap | Gain over paid control |','|---|---:|---:|---:|---:|']
for p in sorted((P/'sensitivity').glob('*--score-free.json')):
 name=p.stem.split('--')[0];f=read(p);d=read(P/'sensitivity'/f'{name}--score-paid.json');a=f['evaluation']['budgets'][0];b=d['evaluation']['budgets'][-1]
 sens.append(f"| {name} | {a['completions']['mean']:.4f} | {f['paired_vs_controls']['legacy']['budgets'][0]['completion_difference']['mean']:+.4f} | {b['completions']['mean']:.4f} | {d['paired_vs_controls']['survival']['budgets'][-1]['completion_difference']['mean']:+.4f} |")
ablation=['| Cap | Base scores only | Scores + conditions | Difference |','|---|---:|---:|---:|']
for suffix in ['free','paid']:
 for a,b in zip(rows('score-'+suffix+'-flat'),rows('score-'+suffix)):
  x=a['completions']['mean'];y=b['completions']['mean'];ablation.append(f"| {a['budget']} | {x:.5f} | {y:.5f} | {y-x:+.5f} |")
text=f'''# Strongest tested strategies, 2026-10-09

The strongest frozen policies tested reach **{rows('score-free')[0]['completions']['mean']:.2f} free completions** and **{rows('score-paid')[-1]['completions']['mean']:.2f} completions with a 500-mushroom cap** per seven-day event. The explicit player rules reach **{rows('human-free')[0]['completions']['mean']:.2f}** and **{rows('human-paid')[-1]['completions']['mean']:.2f}**, respectively. At the more modest 20â€“100 caps, their gap is smaller.

These are the strongest candidates found in this bounded search, not a proof of global optimality or live-game performance. The user selected returning whenever ordinary recovery reaches 20%; boss retries wait for enough HP to survive the configured maximum hit. Completion of room 100 restores full health. Random offered stones use the configured 11-stone first-event-run pool and 20-stone later-run pool; no favorable set is forced.

Open the [interactive stone guide](http://127.0.0.1:8788/rule-guide.html). With the usual lab port, use `/rule-guide.html` on port 8787 instead. Enter the three actual offers, owned stones and choice stage. Select the policy whose chart you want: the detailed-score result and simple-rule result belong to different complete strategies.

## Independent results

{chr(10).join(table)}

Each cell uses **20,000 seven-day events**, master root 711100, the v3 working profile `c33c28fade2fd377`, two simulated seconds per action and continuous availability. Zero is free play; positive amounts are gross event spending caps shared by recovery and refreshes. The free control is the old key-stockpile controller; the paid control includes its survival-aware combat and five-credit refresh allowance, with the original stone chart. Thus the paid comparison isolates the new stone chooser rather than silently removing refreshes from the control.

The detailed free policy gains **0.15635 completions/week** over its matched control (paired 95% interval **0.14967â€“0.16303**). The simple free policy gains **0.12560** (paired interval **0.11695â€“0.13425**). First completion averages are **{rows('score-free')[0]['conditional_first_completion_hours']:.2f} hours** for detailed scoring and **{rows('human-free')[0]['conditional_first_completion_hours']:.2f} hours** for simple rules. Weekly completions are not `168 / first-clear time`: later runs have different opening rules and stone pools and begin at full health.

{chr(10).join(spending)}

Most spending goes to healing. The scored policy earns five refresh credits per eligible shop and requires four keys before refreshing. The simple paid policy's search selected a ceiling of 12 credits, but it spends much less in practice; this is not a recommendation to burn 12 mushrooms at every shop. Credits carry across shops and runs, and useful offers stop the search early. The simple policy buys only offers scoring at least 80; both methods may leave a shop.

{chr(10).join(efficiency)}

These efficiency ratios compare the separately selected paid and free strategies and divide by actual average spending, not the cap. They are descriptive budget comparisons, not proof that one cap has the best underlying mushroom efficiency; no direct paired interval for these ratios is reported.

## Stone choices

All four selected policies put **Rabbit, Greasy Healing Stone and Cursed Moonstone** at the top of their base charts. The free scored and free simple charts agree on their first seven: **Rabbit â†’ Greasy â†’ Moonstone â†’ Gambler â†’ Pearl â†’ Greed â†’ Spying**. Choose from the actual offers; the ranking does not assume these stones appear.

{chr(10).join(ranking)}

The scored free chart has two adjustments: subtract four preference points when completing Gambler + Time Traveler, and subtract 12 from Pearl when permanent escape is already 100%. The paid scored chart adds four for completing Rabbit + Moonstone, adds two to Greasy at the third pick, subtracts 12 for Gambler + Time Traveler, and subtracts four for Spying + Pendant. These are preference points, not probability modifiers. All applicable adjustments stack, and ties use the leftmost offer. The guide applies the exact chart.

Greasy benefits this completion objective because repeated ordinary reentries can yield healing; its lost epic chests are not rewarded by the objective. Time Traveler rises to fourth in the paid scored chart, partly under the assumed full-healing instant potion. The simple paid controller instead ranks Greed fourth and Time Traveler tenth. That disagreement is a reason to avoid presenting one universal paid tier list.

The free simple chart also promotes completing Rabbit + Moonstone (+12), gives Greasy +2 at the second pick, and penalizes Gambler + Time Traveler (âˆ’4). Its recorded +4 Greasy bonus at permanent 100% escape is redundant under the current three-stone limit: Greasy already beats every remaining offer in that situation. See [all exact conditions and defaults](STONE_RULE_REFERENCE.md); the default chart there is the old starting point, not this result.

## Rules a player can follow

Use the simple profiles for an explicit rule list. For free play, farm toward **10 keys in stage 1 and two in stage 2**, then stop voluntary key farming. Fight with One Hit; otherwise escape when escape chance reaches 50% or the modeled fight death risk exceeds 5%. The latter means keeping enough HP to survive nearly the largest possible hit, including poison and other effects, rather than fighting just because HP exceeds the average hit.

Prefer mystery beside a visible monster when One Hit is absent, checking its trap. Spend keys more reluctantly early: the free door reserves are **6 / 4 / 4 / 1**. These are preferences, not hard locks. Prefer a trap-free monster over costly or cursed doors when escape is certain, while retaining golden/safe mystery preferences. Avoid a trap when the other choice is no worse; do not blindly exchange a small trap for a larger fight.

Take the free opening One Hit. Later, prioritize useful healing, One Hit and strong escape; avoid redundant effects and cheap utility offers that consume keys without enough benefit. Open barrels under this simple rule unless Time Traveler removes their blessing chance; use ordinary containers and fountains. Leave mimics without One Hit and skip the arena route and harmful optional games when optimizing completions. This is not a strategy for maximizing all loot or arena legendary rewards.

The paid simple rule uses key targets **10 / 1 / 1 / 0**, a 20% fight-death-risk threshold, and reserves **6 / 6 / 6 / 1**. It compares refills by their actual mushroom cost per HP, using a cheaper sufficient partial refill near the final boss when appropriate. Its shop ceiling differs from the scored policy, as described above. [Full player instructions](rule-strategy-2026-10-09/human-actions.json) and the [exact action reference](RULE_POLICY_REFERENCE.md) preserve every special-room exception and shop score; the short instructions are a summary, not a claim that an approximate checklist reproduces every simulated decision.

## Do the conditional stone rules help?

{chr(10).join(ablation)}

The flat comparison preserves the same learned base scores and non-stone controller, setting every conditional stone adjustment to zero. Almost all the free improvement comes from the ranking. These reports contain paired intervals against the old controls, but not a direct paired interval between the full and flat charts, so small differences here are descriptive rather than individually established effects. We did not retune from these final results.

## Sensitivity to uncertain mechanics

Frozen policies were also evaluated on 3,000 independent events per case and budget under each of 11 scenarios (working plus ten alternatives), root 711200. Only one assumption changes at a time; these scenarios are not confidence limits on the real game.

{chr(10).join(sens)}

The scored free and paid-500 policies beat their matching old controls in every tested scenario. This supports the overall improvement, not the precise rank of every stone. Absolute results move much more across mystery/trap assumptions than their Monte Carlo error bars. Keep measuring those rates, Spying/Pendant door interactions, curse and blessing distributions, first/later stone offers and the Time Traveler instant-potion behavior. Escape/drop-chance increases use the engine's additive probability interpretation; these modifier sizes were not re-estimated by this search. The alternatives do not cover every uncertain mechanic or combinations of errors.

Time Traveler's barrel penalty overrides Gambler in the current profile, consistent with [the official regular-room help](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/294-legendary-dungeon---regular-rooms/). The full-healing instant potion is a separate player-reported assumption. The paid-refill schedule remains the user-confirmed model (20% purchases keep escalating within a run, full refill is 48 from empty); [the official general page](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/57-legendary-dungeon/) describes a cap of 20 for repeated partial purchases. This source conflict was not silently used to change the user's selected model.

## Sampling precision and reproducibility

{chr(10).join(ci)}

These intervals cover event sampling only. They do not include uncertain rules, observation bias, or the unsearched strategy space. A rare stone being selected seldom does not establish its individual value. Budget settings were balanced by relative training performance; final claims use raw per-budget results, not the normalized fitness intervals.

The search evaluated **{index['candidate_evaluations_across_searches']:,} candidate configurations across 12 searches**, including repeats across searches, for **{index['search_event_simulations_including_controls']:,} event simulations including controls and validation**. It explored stone rankings and nine simple conditions, combat/key thresholds, door reserves, trap/barrel decisions, shop selection and refill choices. Validation selected a simpler candidate within one paired standard error of the best shortlisted candidate. This finite population search cannot establish a global optimum.

[Reproduction instructions](RULE_SEARCH_METHOD.md), [search index](rule-strategy-2026-10-09/study-index.json), [full compressed search archive](rule-strategy-2026-10-09/search-archive.zip), [free results](rule-strategy-2026-10-09/final-free/manifest.json), [paid results](rule-strategy-2026-10-09/final-paid/manifest.json), [sensitivity results](rule-strategy-2026-10-09/sensitivity/manifest.json), and [exact seed audit](rule-strategy-2026-10-09/seed-audit.json) preserve configurations, commands, inputs and hashes. Earlier and final source versions have separate manifests. The audit confirms no training/selection seed overlap with the final or sensitivity cohorts; frozen candidates intentionally share evaluation seeds.

Implementation validation: Release build and all **16 CTest suites passed**; focused rule tests cover legal actions, budgets, shop floors, recovery, ties and state/RNG purity. The guide's stone scorer matched the C++ scoring semantics across all current stones and owned-stone sets in an independent exhaustive audit. The benchmark helper passed a smoke evaluation and rejected overlapping event seeds before creating output.
'''
(ROOT/'docs/RULE_STRATEGY_2026_10_09.md').write_text(text,encoding='utf-8')
print('Rendered study report and interactive guide data; all artifact hashes and seed cohorts verified.')
