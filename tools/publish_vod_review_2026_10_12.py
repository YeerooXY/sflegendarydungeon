"""Publish the October 12 native-footage audit without fitting the simulator.

Manual ledgers are the inputs. Scene detections never enter rate denominators.
Run after inventory_vod_hunt.py --verify and summarize_vod_review_2026_10_12.py.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil

from sfld_vod.media import sha256, write_json
from summarize_vod_review_2026_10_12 import interval, wilson

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'out/vod-hunt-2026-10-12'
DEST = ROOT / 'data/vods/latest-event-review-2026-10-12'
DOC = ROOT / 'docs/VOD_REVIEW_2026_10_12.md'
EVENT = 'https://home.sfgame.net/en/blog/recurring-events/legendary-dungeon-september/'
ULTIMATE = 'https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/198-legendary-dungeon-ultimate/'
HELP = 'https://playa-games.helpshift.com/hc/de/4-shakes-fidget-1653988985/faq/57-legendary-dungeon/'


def read(name):
    return json.loads((WORK / name).read_text(encoding='utf-8'))


def pct(x):
    if x is None: return 'unknown'
    return f'{100*x:.3f}%' if 0 < x < .001 else f'{100*x:.1f}%'


def ci(bounds):
    return 'not estimable' if bounds is None else f'{100*bounds[0]:.1f}–{100*bounds[1]:.1f}%'


def row(label, result, model=None):
    return f'| {label} | {result["k"]}/{result["n"]} | {pct(result["estimate"])} | {ci(result["wilson_95"])} | {pct(model) if model is not None else "—"} |'


def table_header(label='Outcome', model='Current model'):
    return [f'| {label} | Observed | Estimate | Nominal 95% Wilson interval | {model} |',
            '|---|---:|---:|---:|---:|']


def main():
    inventory = read('acquisition-inventory.json')
    offers = read('offer-statistics.json')
    spinner = read('spinner-sequence-review.json')
    mystery = read('mystery-sequence-review.json')
    containers = read('container-sequence-review.json')
    traps = read('trap-validation/dense-reviewed.json')
    mechanics = read('mechanic-checks.json')
    if not (spinner['complete'] and mystery['complete'] and traps['complete']):
        raise ValueError('Required temporal reviews are not finished')
    if inventory['validation_failures'] or inventory['validated_clips'] != inventory['total_clips']:
        raise ValueError('Run the final acquisition integrity check first')
    if offers['trap_audit']['review_sha256'] != sha256(WORK/'trap-validation/dense-reviewed.json'):
        raise ValueError('Offer summary needs the final dense trap review')

    spins = [r for r in spinner['rows'] if r['included_in_primary_rates'] and r['status']=='selected']
    reward_names = ['blessing','curse','lucky_coins','gold','hourglasses','mushrooms']
    spinner_rates = {k:interval(spins, lambda r,k=k:r['spin_reward']['category']==k, 1/6) for k in reward_names}
    spinner_combined = interval(spins, lambda r:r['spin_reward']['category'] in {'blessing','curse'}, 1/3)
    spin_known = [r for r in spins if r['initial_contents'] not in {None,'unknown'}]
    spin_content = {k:interval(spin_known,lambda r,k=k:r['initial_contents']==k)
                    for k in ['monster','empty','crate','skeleton','bronze_chest','silver_chest','cursed_barrel']}
    mystery_rates = {}
    for group, model in [('mystery',.468),('wall',55/63),('locked_or_special',.8),('monster',0)]:
        rr = [r for r in mystery['rows'] if r['status']=='verified_mystery_entry' and r['reviewed_other_door_group']==group]
        mystery_rates[group] = interval(rr,lambda r:r['initial_contents']=='monster',model)
    mystery_exact = []
    for group in ['mystery','wall','locked_or_special','monster']:
        rr = [r for r in mystery['rows'] if r['status']=='verified_mystery_entry' and r['reviewed_other_door_group']==group]
        for gems in sorted({tuple(sorted(r['gems'] or [])) for r in rr}):
            subset = [r for r in rr if tuple(sorted(r['gems'] or []))==gems]
            mystery_exact.append({'other_choice':group,'gems':list(gems),'monster_rate':interval(subset,lambda r:r['initial_contents']=='monster')})

    ordinary = [r for r in containers['rows'] if r['ordinary_opening_denominator']]
    barrels = [r for r in containers['rows'] if r['barrel_denominator']]
    barrel_groups = defaultdict(list)
    for r in barrels:
        gems = r['context']['gems']
        key = 'unknown_stones' if not r['context']['gem_set_complete_in_frozen_offer'] else (
            '+'.join(sorted(set(gems or []) & {'gambler','time_traveler','thirsty'})) or 'no_barrel_modifier')
        barrel_groups[key].append(r)
    barrel_rates = {k:interval(v,lambda r:r['outcomes']['blessing'] is True) for k,v in barrel_groups.items()}
    opening_blessings = {}
    for name in ['greed','no_greed','unknown']:
        value = {'greed':True,'no_greed':False,'unknown':None}[name]
        rr = [r for r in ordinary if r['context']['greed'] is value]
        known = [r for r in rr if r['outcomes']['blessing'] is not None]
        k = sum(r['outcomes']['blessing'] is True for r in known)
        u = len(rr)-len(known)
        opening_blessings[name] = {'n':len(rr),'positive':k,'unknown':u,
            'sample_fraction_bounds':[k/len(rr),(k+u)/len(rr)] if rr else None,
            'wilson_missingness_envelope':[wilson(k,len(rr))[0],wilson(k+u,len(rr))[1]] if rr else None,
            'rate':interval(rr,lambda r:r['outcomes']['blessing'] is True) if rr and u==0 else None}
    container_style_rates=[]
    for style in ['crate','bronze','silver','corpse']:
        for greed in [False,True,None]:
            rr=[r for r in ordinary if r['container']==style and r['context']['greed'] is greed]
            if not rr: continue
            unknown=sum(r['outcomes']['blessing'] is None for r in rr)
            container_style_rates.append({'container':style,'greed':greed,'n':len(rr),'unknown':unknown,
                'blessing_rate':interval(rr,lambda r:r['outcomes']['blessing'] is True) if not unknown else None})
    box_chest_rates={}
    for name,greed in [('no_greed',False),('greed',True)]:
        rr=[r for r in ordinary if r['container'] in {'crate','bronze','silver'} and r['context']['greed'] is greed]
        box_chest_rates[name]=interval(rr,lambda r:r['outcomes']['blessing'] is True) if all(r['outcomes']['blessing'] is not None for r in rr) else None

    DEST.mkdir(parents=True,exist_ok=True)
    files = ['acquisition-inventory.json','offer-statistics.json','offer-evidence-validation.json','spinner-sequence-review.json',
             'mystery-sequence-review.json','container-sequence-review.json','mechanic-checks.json',
             'trap-validation/dense-reviewed.json','expansion-layout-review.json']
    for optional in ['container-agent-blessing-review.json','trap-validation/dense-corrections.json',
                     '2881887759/review-preparation/run-sequence-proof.json',
                     '2881887759/review-preparation/review-validation.json',
                     '2881887759/review-preparation/phase-corrections.json',
                     '2885567293/review-preparation/run-transition-proof.json',
                     'expansion-scenes-summary.json','additional-discovery-review.json']:
        if (WORK/optional).exists(): files.append(optional)
    inputs=[]
    for name in files:
        filename = name.replace('/','--')
        shutil.copy2(WORK/name,DEST/filename)
        if name.endswith('run-transition-proof.json'):
            # Keep identity-comparison evidence local; publish anonymous run IDs.
            original=json.loads((WORK/name).read_text())
            labels=[original['conclusion'].get(k) for k in ['character_name','guild','server']]
            clean=(DEST/filename).read_text(encoding='utf-8')
            for label in labels:
                if label: clean=clean.replace(label,'[public character identity omitted]')
            (DEST/filename).write_text(clean,encoding='utf-8')
        inputs.append({'original':str((WORK/name).relative_to(ROOT)),
                       'original_sha256':sha256(WORK/name),
                       'saved':str((DEST/filename).relative_to(ROOT)), 'sha256':sha256(DEST/filename)})
    result = {'schema':1,'reviewed_on':'2026-10-12','inputs':inputs,
        'model_profile':{'file':'profiles/synthetic.profile','sha256':sha256(ROOT/'profiles/synthetic.profile'),
                         'changed_by_this_review':False},
        'spinner_rewards':spinner_rates,'spinner_blessing_or_curse':spinner_combined,
        'spinner_initial_contents_known_only':spin_content,'spinner_unknown_contents':len(spins)-len(spin_known),
        'mystery_monster_by_other_choice':mystery_rates,'mystery_exact_stone_strata':mystery_exact,
        'barrel_blessings_by_relevant_stones':barrel_rates,'container_blessings_by_greed':opening_blessings,
        'container_style_blessings':container_style_rates,
        'box_chest_blessings_by_greed':box_chest_rates,
        'prior_arena_legendary':{'k':3,'n':48,'estimate':3/48,'wilson_95':wilson(3,48),
                                'new_observations_this_review':0},
        'confidence_interpretation':'Pointwise Wilson intervals describe sampling uncertainty under independent comparable draws. Source bootstrap and leave-one-source-out ranges are sensitivity checks. None cover video detection errors, player selection, unknown mode/run status or unseen event variants.',
        'no_automatic_pooling_with_prior_corpus':True,
        'evidence_path_base':'out/vod-hunt-2026-10-12',
        'media_note':'Native clips and game-art evidence stay local. Text ledgers retain source URLs, timestamps and SHA256 provenance.'}
    write_json(ROOT/'docs/vod-review-2026-10-12.json',result)

    neutral = offers['cohorts']['no_known_generation_modifier']
    no_stones = offers['cohorts']['no_stones']
    reviewed_sources = [s for s in inventory['sources'] if s['manual_candidate_review']]
    candidate_sources = [s for s in inventory['sources'] if s['scene_candidates_unverified']]
    lines = ['# Latest Legendary Dungeon footage audit — 12 October 2026','',
        f'Saved **{inventory["total_vods"]} VOD sources across {len(inventory["channels"])} channels**, '
        f'covering **{inventory["total_hours"]:.2f} hours of selected native footage** in {inventory["total_clips"]} clips. '
        'These are retained windows, not full-stream downloads or hours of continuous dungeon play. '
        f'{len(reviewed_sources)} VODs have manual offer reviews; the remaining sources are preserved for further review.', '',
        f'The latest event ran worldwide on **18–27 September 2026**, with the F9 event starting a day earlier. '
        f'The {inventory["cohort_counts"].get("F9_early_event",0)} early-date/F9-candidate sources stay separately labelled; their world identity is not verified for every recording. '
        f'[Official event announcement]({EVENT}).','',
        f'The offer ledger contains **{offers["candidate_views"]} manually reviewed candidate views**, '
        f'of which **{offers["decision_views"]} are retained decision offers**. '
        f'**{offers["eligible_ordinary_known_room_complete_stones"]}** have an ordinary room number and complete stone context suitable for the main offer analysis. '
        'Repeated views and interior scenes are excluded; a genuinely regenerated pair after death is a new offer even if the room number is unchanged. '
        'Automated scene candidates in the acquisition inventory are not observations or unique rooms.','',
        '**Main conclusions:** the spinner strongly favours blessings/curses in this sample; mystery-room odds depend substantially on the other choice; '
        'ordinary death can regenerate the same numbered room; trial entry is not fixed at room 51. '
        'Exact hidden probabilities and a globally optimal policy are not established.','',
        '## How to read the confidence ranges','',
        'The tables use **pointwise 95% Wilson confidence intervals**, assuming independent, comparable outcomes. '
        'A 95% procedure would cover the underlying probability in about 95% of repeated comparable samples. '
        'It is not a 95% guarantee that these nonrandom VOD samples recover the game’s exact hidden constant. '
        'Several rooms come from the same runs, players choose which doors to open, and fast or obscured actions can be missed. '
        'The JSON also gives source-cluster bootstrap ranges (10,000 resamples, fixed seed) and leave-one-source-out sensitivity; '
        'a video source is not necessarily an independent character. With few sources these are diagnostics, not a cure for bias. '
        'Zero sightings retain a nonzero upper limit. Unknown outcomes never become negative observations.','',
        '## Door-pair frequencies','',
        f'The model-comparison cohort has **{neutral["n"]} ordinary offers without known door-generation modifier stones**. '
        'Opening rooms, bosses, shops and trial choices are excluded. Spying/unlocked, Lodestone/double-locked, Pendant and other generation-modified cohorts remain separate. '
        f'A stricter no-stone cohort has {no_stones["n"]} offers; its stage distribution is {no_stones["stage_counts"]}, so it is not an all-stage baseline.','']
    lines += table_header('Pair')
    for name,r in neutral['pairs'].items(): lines.append(row(name.replace('/',' + '),r,r['model_nominal_probability']))
    lines += ['', 'Most pair probabilities are compatible with the working model. '
              'Double mystery is somewhat more common here (15.5% versus the model’s 12.7%); the model is just below its nominal Wilson range, '
              'but remains inside the source-cluster sensitivity range. '
              'A point estimate moving is not by itself evidence of a changed game constant. '
              'All exact stone combinations and both baseline definitions are saved in the offer statistics.','',
              '### Physical door styles in that same cohort','',
              'The denominator is physical alternatives, including walls, rather than all left/right positions. '
              'Hungry resources are combined. This does not estimate the extra unlocked/double-locked/epic variants under their stones.','']
    lines += table_header('Physical alternative')
    physical_model = {'locked':18.5222414789/34,'cursed':3.81051415367/34,'sacrifice':3.53552859619/34,
                      'wall':2.19988445985/34,'destiny':1.8856152513/34,'blessing':1.8856152513/34,
                      'hungry':6*.360100134797/34,'unlocked':0,'double_locked':0,'epic':0}
    for name,r in neutral['physical_styles'].items(): lines.append(row(name,r,physical_model[name]))
    spying=offers['isolated_generation_modifiers']['spying']['unlocked_among_locked_or_unlocked']
    lines += ['', f'**Spying Gem, separate cohort:** {spying["k"]}/{spying["n"]} locked-or-unlocked wooden alternatives were unlocked '
              f'({pct(spying["estimate"])}, 95% interval {ci(spying["wilson_95"])}), compared with the model’s 23%. '
              'This cohort excludes other known door-generation modifiers. Its denominator is wooden locked/unlocked alternatives, '
              'not all rooms. Lodestone has no isolated cohort in this new review; exact combined-stone counts remain in JSON.']
    lines += ['', '## Spinner door','',
              f'All {spinner["summary"]["primary_offers"]} detected spinner offers in the original seven reviewed sources were followed; '
              f'{len(spins)} were selected and {spinner["summary"]["unselected_offers"]} were not. '
              'The supplemental spinner found while following another sequence stays outside this prespecified denominator. '
              'Choice and reward category are resolved for every selected primary spin.','']
    lines += table_header('Spin reward')
    for name,r in spinner_rates.items(): lines.append(row(name,r,1/6))
    lines += ['', f'Blessing or curse combined: **{spinner_combined["k"]}/{spinner_combined["n"]} = {pct(spinner_combined["estimate"])}**, '
              f'95% interval **{ci(spinner_combined["wilson_95"])}**; the equal-six-outcome model predicts 33.3%. '
              'That placeholder is strongly challenged. The exact replacement distribution still needs an independent sample; '
              'do not turn zero observed mushrooms into a zero-probability rule.','',
              f'The room behind the spinner was readable in {len(spin_known)}/{len(spins)} entries. '
              'The following denominator excludes one unknown interior; the JSON preserves all-entry missingness bounds.','']
    lines += table_header('Initial room behind spinner')
    content_model = {'monster':.4,'empty':.12,'crate':.15,'skeleton':.05,'bronze_chest':.05,'silver_chest':.08,'cursed_barrel':.15}
    for name,r in spin_content.items(): lines.append(row(name,r,content_model[name]))
    lines += ['', 'The modeled barrel category corresponds to the visible colored/cursed barrel here; fine barrel variants were not fitted. '
              'Empty interiors are more common in this sample than the 12% placeholder, while the 40% monster assumption remains plausible.','',
              'Raw quantities were retained: six coin rewards showed 30, two gold rewards showed 15 million, and the two primary hourglass rewards showed 10 and 30. '
              'Do not use those as universal base amounts. Ultimate multiplies resources and gold by three, excluding blessings, curses and keys; '
              f'the mode was not directly established for every sequence. [Official Ultimate rules]({ULTIMATE}).','',
              '## Mystery-room contents','',
              'The frozen sample contains 224 offers balanced across seven sources and other-door/stone strata. '
              '104 mystery entries have a directly resolved choice and interior; 67 entered choices remain unresolved, '
              '26 chose the other side, 22 were shop-sign misclassifications corrected during review, and five did not enter within the bounded sequence. '
              'The pooled content percentage is deliberately not used as a universal mystery probability.','']
    lines += table_header('Other offered choice: monster on mystery entry')
    for name,r in mystery_rates.items(): lines.append(row(name,r,r['model_nominal_probability']))
    lines += ['', '**Double mystery:** all 68 selected interiors are attributed without having to guess which door was used. '
              'The 46.8% model value lies just above the nominal interval; treat this as a signal to test a lower-monster scenario, '
              'not proof of a single replacement constant. Exact stone strata and source variation are retained.','',
              '**Wall:** 21/24 monster entries support the existing high-monster wall pool. '
              '**Locked/special opposite mystery:** 9/9 verified selections are not a defensible 100% estimate. '
              'Another 52 entered choices remain ambiguous, and readable choice may depend on the resulting animation. '
              'The possible monster fraction after allocating those ambiguous choices spans 40.9–100%; this is an identification bound, not a confidence interval.','',
              '**Visible monster opposite mystery:** no confirmed counterexample to the no-initial-monster rule was found. '
              'Only three mystery selections have an independently resolved door transition; another 15 ambiguous selections all led to nonmonster interiors. '
              'This sample alone cannot prove a zero probability. A corpse awakening is a different event.','',
              '## Traps','']
    t=offers['trap_audit']
    lines += [f'The fixed validation sample contains {t["sampled_eligible_pairs"]} eligible ordinary pairs, '
              f'with {t["unresolved_pairs"]} unresolved pair judgments after dense native review at eight frames per second. '
              'All available offer frames, including the entrance transition, were checked; source blocks were chosen before trap outcomes.','']
    lines += table_header('At least one visible trap in the offered pair')
    lines.append(row('Dense reviewed pairs',t,t['model_nominal_probability']))
    lines += ['', f'Source-cluster sensitivity interval: **{ci(t["source_cluster_bootstrap_95"])}**. '
              'The model comparison is matched to each sampled pair: 29% is a per-eligible-side parameter, '
              'so two eligible alternatives predict 1 − 0.71², rather than 29% per pair. '
              'This sample is balanced by source and is not a population-weighted all-event trap estimate. '
              'Its lower observed frequency warrants another independent audit before changing the default.','',
              '## Containers and barrels','',
              f'The frozen locked-offer census has {containers["summary"]["candidate_count"]} candidates in five source blocks and '
              f'{len(ordinary)} confirmed ordinary openings: {dict(Counter(r["container"] for r in ordinary))}. '
              'It follows the selected room, so do not reuse these counts as a proven locked-door-content distribution when the chosen side is ambiguous. '
              'A container can give several reward types; item-sale gold is not a separate chest drop.','',
              'Blessing observations are separated by Boulder of Greed. The following interval envelope includes unresolved outcomes when present; '
              'only fully resolved groups receive a point estimate.','',
              '| Stone context | Openings | Confirmed blessings | Unknown blessing outcome | Estimate / possible sample fraction | 95% interval or missingness envelope |',
              '|---|---:|---:|---:|---:|---:|']
    for name,r in opening_blessings.items():
        lines.append(f'| {name} | {r["n"]} | {r["positive"]} | {r["unknown"]} | '
                     f'{pct(r["rate"]["estimate"]) if r["rate"] else ci(r["sample_fraction_bounds"])} | {ci(r["wilson_missingness_envelope"])} |')
    lines += ['', 'Container styles, still separated by Greed:','']
    lines += table_header('Container / Greed')
    for r in container_style_rates:
        if r['blessing_rate']:
            lines.append(row(f'{r["container"]} / {r["greed"]}',r['blessing_rate']))
    lines += ['', 'Small style-specific cohorts cannot establish that styles share a probability or differ. '
              'Other stones, stages and run classes are retained in the individual records.','',
              'For other reward types, the current audit retains unresolved extra rewards rather than treating them as absent:','',
              '| Marginal reward, all 77 openings | Confirmed positive | Unknown | Possible sample fraction | 95% missingness envelope |',
              '|---|---:|---:|---:|---:|']
    for kind in ['gold','item','blessing','curse']:
        r=containers['summary']['ordinary']['outcomes'][kind]
        lines.append(f'| {kind} | {r["observed_positive"]} | {r["unknown"]} | {ci(r["sample_fraction_bounds"])} | {ci(r["wilson_95_envelope_assuming_independent_comparable_draws"])} |')
    lines += ['', 'The current non-Greed box/bronze/silver model is 113/520 = 21.7%, and Greed adds 20 percentage points provisionally. '
              'Those are prior-corpus estimates, not newly collected samples. The new grouping above also contains two corpse searches; '
              'container-specific and exact-stone context stays in the ledger. This is observational association, not a causal test of Greed.','',
              'Sixteen of the 77 ordinary openings showed none of gold, item, new blessing or new curse (20.8%; 95% interval 13.2–31.1%). '
              'That does not prove zero keys or zero other resources. Five ornate epic/gold chest openings are stored separately. '
              'Rarity is left unknown where the item itself was not readable; chest appearance alone does not establish a legendary drop.','',
              'Barrel consumption was densely reviewed. Relevant stone modifiers stay separate:','']
    for name,r in box_chest_rates.items():
        if r:
            lines.insert(-2,f'Boxes/chests only, **{name}**: {r["k"]}/{r["n"]} = {pct(r["estimate"])}, '
                         f'95% interval {ci(r["wilson_95"])}. This excludes the corpse searches.')
            lines.insert(-2,'')
    lines += table_header('Barrel context: blessing outcome')
    for name,r in barrel_rates.items(): lines.append(row(name,r,.5 if name=='no_barrel_modifier' else .25 if name=='time_traveler' else None))
    lines += ['', 'These are small cohorts. No new Gambler estimate is available in this barrel sample. '
              'The baseline still fits a 50/50 blessing/curse rule; this pass cannot establish a precise percentage.','',
              '## Structural findings and model checks','',
              '| Rule | New evidence | Assessment |','|---|---|---|',
              '| Ordinary deaths advance the room | Continuous Loot room-93 monster death, refill, new pair still labelled 93 | Counterexample to current engine; retained native proof |',
              '| Trial always begins at 51 | Verified trial entries occur at displayed rooms 51–55; native room-53 transition retained | Fixed-51 rule contradicted; uniform placement is not established |',
              '| Only monster/mystery during opening | Destiny and blessing alternatives appear in opening rooms, including Nils’s visibly subsequent run | Supports the recently broadened opening pool |',
              '| Later run has a room-five shop | Nils completes room 100, then reaches a blessing/monster choice at room 5 | Supports no compulsory room-five shop on later runs |',
              '| Free opening shop implies no stage-one sacrifice | Sensei shows room-five shop followed by a room-seven sacrifice alternative; free price/run status not confirmed in that quick transition | Conditional inconsistency; do not label confirmed first-run counterexample |',
              '| Shops can be missed when a mysterious symbol is beside a wall | 36 shop-sign errors in the original offer labels were corrected with native footage | Annotation error, not evidence that shops disappeared |',
              '| Hungry doors request gold/lucky coins | Native enlarged icons showed wood/hourglasses | Earlier visual suspicion withdrawn |','',
              f'Shop offer count: {offers["shops"]["n"]}; displayed-room histogram: `{offers["shops"]["room_histogram"]}`. '
              'Readable ordinary shops fit the current stage windows. This does not establish uniform timing, exact once-per-stage coverage, '
              'a 50% strong-offer rate, or blessing identity frequencies.','',
              f'Trial-entry histogram: `{offers["trial_entries"]["room_histogram"]}`. '
              'Unknown counters remain unknown. There are no newly classified tier-five legendary rewards in this pass. '
              f'The prior 3/48 = 6.25% legendary estimate still has a wide nominal interval of {ci(wilson(3,48))}.','',
              'A separate official-help discrepancy needs targeted footage: the German help page says partial refills cost 10, then 15, '
              'then 20 mushrooms from the third purchase onward. That differs from our unbounded +5-per-purchase assumption. '
              'The help article is old, so retain this as a conflict pending a visible fourth partial-refill quote, rather than silently overriding the player’s report. '
              f'The same page supports continuing in the room where death occurred. [Official help]({HELP}).','',
              '## Saved evidence and reproducibility','',
              '- [Machine-readable comparison](vod-review-2026-10-12.json).',
              '- [Acquisition inventory](../data/vods/latest-event-review-2026-10-12/acquisition-inventory.json): source URLs, retained times, mode/date cohorts, clip hashes and validation.',
              '- [Offer statistics](../data/vods/latest-event-review-2026-10-12/offer-statistics.json) and [individual offer ledger](../data/vods/reviewed-offers-2026-10-12.jsonl).',
              '- [Spinner sequences](../data/vods/latest-event-review-2026-10-12/spinner-sequence-review.json), [mystery sequences](../data/vods/latest-event-review-2026-10-12/mystery-sequence-review.json), [containers](../data/vods/latest-event-review-2026-10-12/container-sequence-review.json), [dense traps](../data/vods/latest-event-review-2026-10-12/trap-validation--dense-reviewed.json).',
              '- [Structural proof records](../data/vods/latest-event-review-2026-10-12/mechanic-checks.json).',
              '',f'All {inventory["total_clips"]} retained clip hashes passed, with start/middle/end decoding checks. '
              'This is not a full decode of every video frame. Sequence reviewers separately validated their referenced evidence-file hashes. '
              'The media remains local under `out/vod-hunt-2026-10-12`; saved JSON evidence paths are relative to that directory. '
              'The simulator profile and trained policy were not refitted during this data-gathering pass.','',
              'Reproduce after the manual ledgers are present:','',
              '```powershell',
              '.\\out\\vod-env\\Scripts\\python.exe tools/inventory_vod_hunt.py --verify',
              '.\\out\\vod-env\\Scripts\\python.exe tools/summarize_vod_review_2026_10_12.py',
              '.\\out\\vod-env\\Scripts\\python.exe tools/publish_vod_review_2026_10_12.py',
              '```','',
              '## Highest-value remaining checks','',
              '1. Review the newly preserved sources as held-out confirmation before tuning the spinner and trap probabilities.',
              '2. Resolve mystery-versus-special-door selection from native door transitions; do not identify the chosen side from the interior result.',
              '3. Record full container before/after sequences with Greed, container style, run number and mode, including instant healing and replacements of existing effects.',
              '4. Track complete runs across ordinary deaths and trial entry; distinguish regenerated offers from duplicated screenshots.',
              '5. Read tier-five reward items directly, and capture fourth-or-later partial-refill prices, shop strengths and opening-shop/run status.',
              '', 'For an isolated probability near 50%, roughly 1,067 independent comparable outcomes are needed for about ±3 percentage points at 95%, '
              'or 2,401 for ±2 points. Those counts apply per relevant mechanic/context, not to the total number of room screenshots. '
              'Repeated runs and recognition uncertainty can require substantially more observations.','']
    DOC.write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({'report':str(DOC.relative_to(ROOT)),'saved_inputs':len(inputs),
                      'vods':inventory['total_vods'],'manual_vods':len(reviewed_sources),
                      'candidate_sources':len(candidate_sources),'spinner_n':len(spins),
                      'mystery_n':sum(v['n'] for v in mystery_rates.values()),'ordinary_openings':len(ordinary)},indent=2))


if __name__ == '__main__':
    main()
