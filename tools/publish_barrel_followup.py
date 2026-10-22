"""Publish manually inspected native barrel rewards; never infer labels from HUD counts."""
import collections
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'out/vods/barrel-followup-2026-10-12'
# Manual review of every contact sheet on 2026-10-12. I=instant potion,
# B=persistent blessing, C=curse, S=left without opening. Frame is the readable
# airborne reward, not a later door's reward or a change in active slot count.
LABELS=[('I',16),('B',13),('B',14),('I',14),('C',13),('C',12),('B',16),('C',13),('C',16),
        ('C',14),('B',12),('C',13),('C',13),('B',13),('C',13),('C',13),('I',19),('B',14),
        ('B',14),('B',13),('C',13),('B',13),('C',13),('I',12),('C',12),('C',13),('S',7),
        ('B',15),('B',13),('B',10),('C',10),('C',16),('B',13)]


def wilson(k,n):
    if not n:return None
    z=1.95996398454;p=k/n;den=1+z*z/n
    mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [mid-half,mid+half]


def stratum(row):
    return ','.join(sorted(set(row['context']['gems'])&{'gambler','time_traveler','thirsty'})) or 'unmodified'


def summarize(rows):
    groups=collections.defaultdict(list)
    for row in rows:
        if row['source_id'].isdigit() and row['context'].get('stone_status')=='verified':groups[stratum(row)].append(row)
    result={}
    for group,rs in groups.items():
        opened=[r for r in rs if r['interaction']=='opened' and r['reward_complete']]
        known=[r for r in opened if r['reward_flags'].get('persistent_blessing') is not None and r['reward_flags'].get('instant_healing') is not None]
        k=sum(r['reward_flags']['persistent_blessing'] or r['reward_flags']['instant_healing'] for r in known)
        result[group]={'visits':len(rs),'opened_known':len(known),'blessings':k,'curses':sum(r['reward_flags'].get('curse') is True for r in known),
                       'skipped':sum(r['interaction']=='skipped' for r in rs),'blessing_fraction':k/len(known) if known else None,
                       'descriptive_wilson95':wilson(k,len(known)),
                       'sources':len({r['source_id'] for r in known}),'passages':len({(r['source_id'],r['context'].get('run_id')) for r in known})}
    return result


def run():
    manifest=json.loads((WORK/'manifest.json').read_text());frames=json.loads((WORK/'frames/index.json').read_text())
    if len(LABELS)!=len(manifest['rows']) or len(frames)!=len(LABELS):raise ValueError('Incomplete review')
    reviews=[]
    for index,((kind,peak),r,f) in enumerate(zip(LABELS,manifest['rows'],frames)):
        if r['observation_id']!=f['observation_id'] or f['missing_timestamps']:raise ValueError('Missing/incorrect evidence')
        ev=f['frames']; before=min(5,len(ev)-1);after=min(peak+8,len(ev)-1)
        flags={n:False for n in ('gold','nothing','persistent_blessing','instant_healing','item','epic_item','legendary_item','curse')}
        field={'I':'instant_healing','B':'persistent_blessing','C':'curse'}.get(kind)
        if field:flags[field]=True
        if kind=='S':flags={n:None for n in flags}
        row={'schema':1,'observation_id':r['observation_id'],'source_id':r['source_id'],
             'source_url':f'https://www.twitch.tv/videos/{r["source_id"]}?t={int(f["source_start"])}s',
             'source_start':f['source_start'],'source_end':ev[after]['source_seconds'],
             'context':r['context'],'initial_style':'barrel','interaction':'skipped' if kind=='S' else 'opened',
             'reward_review_status':'verified_native_sequence','reward_complete':kind!='S',
             'reward_flags':flags,'effect_identity_and_strength_verified':False,
             'review_method':'Manual native sequence: closed barrel, opening, airborne reward card, destination effect slot or instant healing, then departure. Existing effects do not establish a drop.',
             'evidence':[dict(ev[n],role=role) for n,role in [(before,'before'),(peak,'reward' if kind!='S' else 'closed_before_departure'),(after,'after')]],
             'cohort':'outcome_blind_barrel_followup_2026_10_12','sample_index':index,'notes':[]}
        if index==6:row['notes'].append('Time Traveler is the only visible stone. Blue blessing card moves to a blessing slot; this contradicts the former zero-blessing barrel model.')
        if index in (16,24):row['notes'].append('Other door rewards visible before/after this barrel are excluded.')
        if index==31:row['notes'].append('Poison received from barrel; subsequent death does not erase the observed curse outcome.')
        reviews.append(row)
    dest=ROOT/'data/vods/barrel-followup-2026-10-12.jsonl'
    dest.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in reviews),encoding='utf8')
    old=[json.loads(l) for l in (ROOT/'data/vods/full-container-rewards-2026-10-09.jsonl').read_text().splitlines()]
    old=[r for r in old if r.get('initial_style')=='barrel' and r.get('reward_review_status','').startswith('verified')]
    if {r['observation_id'] for r in old}&{r['observation_id'] for r in reviews}:raise ValueError('Recounted old review')
    report={'schema':1,'new':summarize(reviews),'combined_with_previous_native_reviews':summarize(old+reviews),
            'dataset_sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),
            'manifest_sha256':hashlib.sha256((WORK/'manifest.json').read_bytes()).hexdigest(),
            'frame_count_extracted':sum(len(f['frames']) for f in frames),
            'limitations':['33 new visits from existing footage, not 33 new videos or a random sample of all game play.',
                          'Modifier strata are a census of remaining recognized candidates; unmodified visits were hash-sampled before review.',
                          'Wilson intervals describe binomial sampling only; repeated characters and passages may be correlated.',
                          'Unknown event-run status, player choices, source and detector selection limit generalization.',
                          'No evidence here verifies the Time Traveler potion full-heal mechanic, exact curse multiplier or combined Gambler/Time Traveler arithmetic.'],
            'counterexample':{'observation_id':reviews[6]['observation_id'],'source_url':reviews[6]['source_url'],'reward_frame':reviews[6]['evidence'][1]},
            'priority_conflicts':[
              {'mechanic':'Time Traveler barrel curse modifier','finding':'A blessing is observed with Time Traveler alone; zero chance is false for this recorded event. 1.5x base curse probability gives provisional 25% blessings at a 50% baseline.'},
              {'mechanic':'Later-run stage-one shops','finding':'Existing shop-timing audit found no shop in 28/29 counter-complete passages; two were rechecked at native resolution. Event first-run class remains unknown. Do not equate observed passage index with run class.'},
              {'mechanic':'Paid partial-refill escalation','finding':'Official help currently says 10,15,then20 mushrooms. Player report and current profile use uncapped +5. Need consecutive purchases in a single run, not mixed full-refill quotes.',
               'source':'https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/57-legendary-dungeon/'}]}
    (ROOT/'docs/barrel-followup-2026-10-12.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Barrel evidence follow-up, 2026-10-12','',
           'Manually reviewed 33 previously unreviewed barrel visits at native resolution: 32 openings and one skip. The selection was frozen before inspecting rewards. All come from the existing current-event footage; these are new annotations, not new videos.','',
           '## Newly reviewed outcomes','', '| Stone condition | Blessings / openings | Curses | 95% descriptive interval for blessings |','|---|---:|---:|---:|']
    for group,s in sorted(report['new'].items()):
        lo,hi=s['descriptive_wilson95'];lines.append(f'| {group} | {s["blessings"]}/{s["opened_known"]} | {s["curses"]} | {lo:.1%}–{hi:.1%} |')
    lines+=['','The unmodified group excludes Gambler, Time Traveler and Thirsty. It can contain other stones. The small modifier samples cannot establish exact chances. A skip is excluded from outcome denominators.','',
            '## Direct contradiction of the old model','',
            f'Time Traveler alone yields a clearly visible blessing at [this recorded opening]({reviews[6]["source_url"]}). The former simulator made blessings impossible at its 50% baseline. This restriction is contradicted. A 25% blessing scenario, interpreting the tooltip as a relative 50% increase in curse probability, remains an inference rather than a measured exact formula.','',
            '## Combined evidence','', '| Stone condition | Blessings / openings | 95% descriptive interval |','|---|---:|---:|']
    for group,s in sorted(report['combined_with_previous_native_reviews'].items()):
        if s['descriptive_wilson95']:
            lo,hi=s['descriptive_wilson95'];lines.append(f'| {group} | {s["blessings"]}/{s["opened_known"]} | {lo:.1%}–{hi:.1%} |')
    lines+=['','Each observation ID is counted once. The combined cohort includes the earlier native reviews, which were not drawn with this follow-up sampling design. These intervals omit streamer/run clustering and recognition bias.','',
            '## What most needs checking next','',
            '- Stage-one shop presence and first-five-room rules, with event run number visibly established. See `VOD_SHOP_TIMING_2026_10_09.md` for the existing conflict.',
            '- Consecutive partial refill prices. [Official help](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/57-legendary-dungeon/) describes 10, 15, then 20; the player-confirmed working model continues by five. The published help may lag the client.',
            '- Time Traveler instant potion before/after HP, potion strength and keys paid; none of these barrel visits tests that full-heal observation.',
            '- Joint Gambler/Time Traveler barrel modifier and more isolated modifier visits.',
            '- Shop strength/identity frequencies including every reroll, corpse waking/loot, conditional mystery contents and effect replacement/tick timing.',
            '', 'Dataset: `data/vods/barrel-followup-2026-10-12.jsonl`. Summary and counterexample hashes: `docs/barrel-followup-2026-10-12.json`. Local images are excluded from the repository. Reproduce publication with `python tools/publish_barrel_followup.py`.']
    (ROOT/'docs/BARREL_FOLLOWUP_2026_10_12.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps({'new':report['new'],'combined':report['combined_with_previous_native_reviews']},indent=2))


if __name__=='__main__':run()
