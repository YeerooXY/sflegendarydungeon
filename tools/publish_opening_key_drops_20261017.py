"""Publish explicit manual labels from reviewed native strips, not predictions."""
from pathlib import Path
import json
import math
import hashlib
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'out/opening-audit-2026-10-17/keys'
TARGET = ROOT / 'data/vods/opening-audit-2026-10-17/key-drops.json'
CONTEXT_PATH = ROOT / 'data/vods/opening-audit-2026-10-17/run-context.json'
CONTEXT = {r['run_id']: r for r in json.loads(CONTEXT_PATH.read_text(encoding='utf-8'))['runs']}
MODES_PATH = ROOT / 'data/vods/opening-audit-2026-10-17/run-modes.json'
MODES = {r['run_id']: r for r in json.loads(MODES_PATH.read_text(encoding='utf-8'))['rows']}

# start, keys before, keys after, one-hit-wonder active
# Every listed strip and the enlarged before/after HUD were visually inspected.
LABELS = {
    '2877355041-2179-2420': [
        (2179,0,1,1),(2201.625,1,2,1),(2208.75,2,3,1),(2222.875,3,4,1),
        (2239.125,4,4,1),(2244.5,4,5,1),(2250.375,5,6,0),(2288.25,6,7,0),
        (2313.625,7,7,0),(2339,7,8,0),(2355.625,8,9,0)],
    '2877531246-1056-1500': [
        (1102.875,0,1,0),(1109.5,1,2,0),(1115.625,2,3,0),(1122,3,4,0),
        (1128.375,4,5,0),(1416.625,0,1,0),(1424.125,1,2,0),(1435.625,2,3,1),
        (1440.25,3,4,1),(1444.75,4,5,1),(1459.875,5,6,0)],
    '2878439002-2942-3120': [(2986,0,1,0),(2996.125,1,2,0)],
    '2878439002-3656-3744': [
        (3656.625,0,1,0),(3670.25,1,2,0),(3673.125,2,2,0),(3680.625,1,1,0),
        (3730.375,1,2,0),(3734.25,2,3,0),(3736.375,3,4,0),(3738.25,4,5,0),
        (3740.25,5,6,0)],
    '2878439002-4198-4383': [
        (4202.875,0,1,1),(4225.375,2,3,0),(4228.625,3,4,0),(4238.25,3,3,0),
        (4243.25,3,4,0),(4249.25,4,4,0),(4357,4,5,0),(4359.25,5,5,0),
        (4372.5,5,6,0)],
    '2877531246-1837-2000': [
        (1837.125,0,1,0),(1845.375,1,2,0),(1851.5,2,2,0),(1857.75,2,3,0),
        (1867.375,3,4,1),(1872.5,4,5,1),(1885.625,5,6,1),(1913.375,7,8,0),
        (1925.625,8,9,0),(1961,7,8,0),(1972.875,8,8,0),(1979.375,8,9,0),
        (1986.125,9,9,0)],
    '2877531246-2738-2900': [
        (2738.5,0,1,0),(2747.125,1,2,0),(2753.125,2,3,0),(2764.75,3,4,0),
        (2771.375,4,5,0)],
}

def wilson(k,n):
    if not n:return None
    z=1.959963984540054;d=1+z*z/n;c=(k/n+z*z/(2*n))/d
    r=z*math.sqrt((k/n)*(1-k/n)/n+z*z/(4*n*n))/d
    return [c-r,c+r]

def summary(rows):
    n=len(rows);counts=Counter(r['key_drop'] for r in rows)
    return {'n':n,'drops_0':counts[0],'drops_1':counts[1],'drops_2':counts[2],
            'any_key_fraction':sum(r['key_drop']>0 for r in rows)/n if n else None,
            'any_key_wilson_95':wilson(sum(r['key_drop']>0 for r in rows),n),
            'mean_keys_per_encounter':sum(r['key_drop'] for r in rows)/n if n else None}

def run_for(source,t):
    if source=='2877355041':return f'{source}:opening-2179','purple'
    if source=='2878439002':
        anchor=2942 if t<3300 else 3656 if t<4000 else 4198
        return f'{source}:opening-{anchor}','orange'
    if t<1300:return f'{source}:opening-1056','orange'
    if t<1700:return f'{source}:opening-1404','orange'
    if t<2200:return f'{source}:opening-1837','purple'
    return f'{source}:opening-2738','purple'

def record(folder,t,before,after,ohw=False):
    raw=next(r for r in json.loads((BASE/folder/'index.json').read_text()) if r['start']==t)
    rid,orb=run_for(raw['source_id'],t)
    context=CONTEXT[rid]
    mode=MODES[rid]
    amended=bool(mode.get('classification_override'))
    classification=mode.get('classification_override',context['classification'])
    run='first' if classification.startswith('first_') else 'later'
    return {**raw,'verification':'manual_native_before_action_after_and_hud',
            'action':'fight','outcome':'monster_killed','run_id':rid,'run_class':run,
            'run_class_detail':classification,
            'run_class_basis':mode['classification_basis'] if amended else context['basis'],
            'run_class_evidence':{'path':(MODES_PATH if amended else CONTEXT_PATH).relative_to(ROOT).as_posix(),
                                  'run_id':rid,'reviewer':'opening_run_classes',
                                  'independent_of_room5_shop':True if amended else context['independent_of_room5_shop']},
            'game_mode':mode['mode'],
            'mode_evidence':{'path':MODES_PATH.relative_to(ROOT).as_posix(),'run_id':rid,
                             'basis':mode['basis'],'reviewer':'opening_run_classes'},
            'hud_orb':orb,'hud_orb_is_mode_proxy':True,
            'encounter_origin':'living_monster_on_entry',
            'gems':[],'stone_set_complete':True,'key_moment_active':False,
            'key_modifiers':'none_visible','one_hit_wonder_active':bool(ohw),
            'keys_before':before,'keys_after':after,'key_drop':after-before,
            'before_evidence_index':0,'after_evidence_index':3 if t in [1128.375,1913.375] else 6,
            'notes':['Native strip shows ordinary living monster defeated; no paid-key action between before and after.']}

def main():
    rows=[record(folder,*label) for folder,labels in LABELS.items() for label in labels]
    # Separately reviewed escapes: never counted as no-drop kills.
    escapes=[]
    for t,k,outcome in [(1088,0,'successful_escape'),(1146.5,4,'successful_escape'),
                        (1156.25,3,'successful_escape'),(1455.375,5,'failed_escape'),
                        (1466.25,6,'successful_escape'),(1491.75,3,'failed_escape')]:
        r=record('2877531246-1056-1500',t,k,k)
        r.update(action='escape',outcome=outcome)
        r['notes']=['Escape cursor/motion is visible; key counter unchanged. Trap damage before an escape is not a failed-escape hit.']
        escapes.append(r)
    # Direct two-key-drop example, separate because Key Moment is visible.
    modified=record('2878439002-3656-3744',3710.375,1,3)
    modified.update(key_moment_active=True,key_moment_strength='unresolved',key_modifiers='key_moment')
    modified['notes']=['The active Key Moment icon and floating 2x key reward are visible. Strength is not established by a tooltip; not used for baseline.']
    # Early baseline finding prompted an extra first-run character and a later
    # opening. This is a convenience audit, not a pre-registered random sample.
    result={
        'schema':1,'reviewed_on':'2026-10-17','scope':'September 2026 native footage, ordinary stage-one monsters, rooms 1-24',
        'method':'Manual before/action/after visual review of native crops plus enlarged HUD recheck for every included baseline kill. No inferred drops from auto labels.',
        'sampling':'Convenience sample of readable stage-one windows in three newer VODs. Extra Sensei first/later windows reviewed after unexpectedly high initial counts. Not a random or confirmatory sample.',
        'sources':[{'source_id':s,'url':f'https://www.twitch.tv/videos/{s}',
                    'source_metadata':f'out/vod-hunt-2026-10-12/{s}/source.json'} for s in sorted({r['source_id'] for r in rows})],
        'baseline':summary(rows),
        'by_run_class':{k:summary([r for r in rows if r['run_class']==k]) for k in ['first','later']},
        'by_run_class_detail':{k:summary([r for r in rows if r['run_class_detail']==k])
                               for k in ['first_confirmed','first_supported','later_confirmed']},
        'run_classification':{'path':CONTEXT_PATH.relative_to(ROOT).as_posix(),
                              'sha256':hashlib.sha256(CONTEXT_PATH.read_bytes()).hexdigest(),
                              'amendments_path':MODES_PATH.relative_to(ROOT).as_posix(),
                              'amendments_sha256':hashlib.sha256(MODES_PATH.read_bytes()).hexdigest(),
                              'combined_first_row':'All included first runs have independent first-entry achievement confirmation, including two upgraded by the denser run-modes audit.'},
        'by_one_hit_wonder':{str(k):summary([r for r in rows if r['one_hit_wonder_active']==k]) for k in [False,True]},
        'by_source':{s:summary([r for r in rows if r['source_id']==s]) for s in sorted({r['source_id'] for r in rows})},
        'by_hud_orb':{c:summary([r for r in rows if r['hud_orb']==c]) for c in ['orange','purple']},
        'by_confirmed_mode':{m:summary([r for r in rows if r['game_mode']==m]) for m in ['normal','ultimate']},
        'by_mode_and_run_class':{f'{m}_{c}':summary([r for r in rows if r['game_mode']==m and r['run_class']==c])
                                for m in ['normal','ultimate'] for c in ['first','later']},
        'opening_rooms_1_to_5':summary([r for r in rows if r['room']<=5]),
        'escape_summary':summary(escapes),
        'escapes_by_outcome':{o:summary([r for r in escapes if r['outcome']==o])
                              for o in ['successful_escape','failed_escape']},
        'baseline_kills':rows,'escapes':escapes,'modified_examples':[modified],
        'exclusions_and_limits':[
            'No bosses, deaths before a kill, corpse searches, or unverified fight/escape sequences count as baseline kills.',
            'No Spying Gem, Lodestone, Pendant, or Key Moment observations are pooled into baseline.',
            'Key Moment begins at Lootenant room8 around3005s and room11 around3688s; those subsequent affected kills are excluded.',
            'Sensei room8 around2785s gains Key Moment; that kill and following affected kills are excluded.',
            'The dense offer catalog has gaps; this is an explicitly enumerated kill subset, not every kill in the videos.',
            'Visible unchanged keys alone cannot establish a kill; native action sequence was required.',
            'First/later status uses the separate run-context.json audit with run-modes.json amendments. Every included first run has a first-entry achievement; every later run has an observed previous completion for the same character.',
            'Every included baseline monster was living on entry, not an awakened sleeping corpse. Corpse awakenings are not pooled with these kills.',
            'Orange and purple HUD orb appearances are only mode proxies. Confirmed Normal/Ultimate strata use separately reviewed entry transitions from run-modes.json, not orb color. No mode equivalence is established by these samples.',
            'Wilson intervals assume binomial independent draws within the stated stratum. They do not account for stream selection, event changes, or unknown mechanics.',
            'No precise universal rate, first/later difference, stone modifier rate, or Key Moment strength probability is established by this sample.'
        ],
        'conclusion':'A 50% ordinary-kill baseline is substantially below this reviewed stage-one sample. About 80% is a plausible working scenario, not an exact verified rule; expand independent footage before fixing an authoritative probability.',
        'simulator_modified':False,
    }
    verified_evidence_count=0
    for r in rows+escapes+[modified]:
        for e in r['evidence']:
            p=ROOT/e['image'];assert p.is_file()
            assert hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256']
            verified_evidence_count+=1
        assert r['key_drop'] in [0,1,2]
    assert len({r['offer_id'] for r in rows})==len(rows)==60
    result['validation']={'native_evidence_hashes_verified':verified_evidence_count,
                          'baseline_unique_offer_ids':len(rows),
                          'no_duplicate_baseline_offer_ids':True,
                          'run_context_references_resolved':True}
    TARGET.parent.mkdir(parents=True,exist_ok=True)
    TARGET.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['baseline','by_run_class','by_run_class_detail','by_one_hit_wonder','by_source','opening_rooms_1_to_5','escape_summary']},indent=2))

if __name__=='__main__':main()
