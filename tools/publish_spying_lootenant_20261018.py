"""Publish the fixed-scope, manually verified Lootenant Spying key audit."""
from pathlib import Path
from collections import Counter
import json,hashlib,math

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/vods/spying-key-audit-2026-10-18'
BASE=ROOT/'out/spying-key-audit-2026-10-18/lootenant'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def stats(rows):
    n=len(rows);k=sum(r['key_drop']>0 for r in rows);c=Counter(r['key_drop'] for r in rows);z=1.959963984540054
    if n:
        d=1+z*z/n;centre=(k/n+z*z/(2*n))/d;half=z*math.sqrt(k/n*(1-k/n)/n+z*z/(4*n*n))/d;ci=[max(0,centre-half),min(1,centre+half)]
    else:ci=None
    return {'n':n,'drops_0':c[0],'drops_1':c[1],'drops_2':c[2],'any_key_fraction':k/n if n else None,'any_key_wilson_95':ci,'mean_keys_per_kill':sum(r['key_drop'] for r in rows)/n if n else None}

def main():
    scope=read(DATA/'lootenant-scope.json');raw=read(BASE/'index.json');labels=read(DATA/'lootenant-manual-working.json')
    cp=ROOT/'data/vods/opening-audit-2026-10-17/run-context.json';mp=ROOT/'data/vods/opening-audit-2026-10-17/run-modes.json'
    contexts={r['run_id']:r for r in read(cp)['runs']};modes={r['run_id']:r for r in read(mp)['rows']}
    assert len(raw)==len(labels)==scope['candidate_count']==153
    assert [r['offer_id'] for r in raw]==scope['candidate_offer_ids']
    assert {r['ordinal'] for r in labels}==set(range(153))
    candidates=[];kills=[];hashes={}
    for label in labels:
        r={**raw[label['ordinal']],**label};rid=r['run_id']
        assert r['disposition'] in ['include','excluded','pending']
        r.update(verification='manual_native_sequence_and_enlarged_hud',gems=r['previously_reviewed_gems'],stone_set_complete=True)
        assert r['previously_reviewed_stone_set_complete'] and 'spying' in r['gems'] and 'lodestone' not in r['gems']
        r['stone_verification']='Full cumulative gem icons checked in native frames; no missing stone slots at this stage.'
        if rid in contexts:
            r['run_class']=contexts[rid]['classification'];r['run_context_evidence']={'path':cp.relative_to(ROOT).as_posix(),'run_id':rid}
        else:r['run_class']='unknown';r['run_context_note']='Partial run already in progress; no independent first/later proof established in this audit.'
        if rid in modes:
            r['mode']=modes[rid]['mode'];r['mode_evidence']={'path':mp.relative_to(ROOT).as_posix(),'run_id':rid}
        else:r['mode']='unknown';r['mode_note']='Purple dungeon HUD is only a proxy; mode not established from an explicit entry screen.'
        if 'additional_evidence_index' in r:r['additional_evidence']=read(ROOT/r['additional_evidence_index'])
        for e in r['evidence']+r.get('additional_evidence',[]):
            p=ROOT/e['image'];assert p.is_file() and digest(p)==e['sha256'];hashes[e['image']]=e['sha256']
        if r['disposition']=='include':
            assert r['key_moment_active'] is False and r['effect_state_complete']
            assert r['encounter_origin']=='living_monster_on_entry' and r['room']%25!=0
            assert r['next_room']==r['room']+1
            assert not {'lodestone','pendant'}.intersection(r['gems'])
            r.update(action='fight',outcome='monster_killed',key_drop=r['keys_after']-r['keys_before'])
            assert r['key_drop'] in [0,1,2]
            r['hud_contact_sheet']=(BASE/('hud-%03d.jpg'%r['ordinal'])).relative_to(ROOT).as_posix()
            assert (ROOT/r['hud_contact_sheet']).is_file()
            kills.append(r)
        candidates.append(r)
    old=ROOT/'data/vods/opening-audit-2026-10-17/key-drops.json';previous=ROOT/'data/vods/key-drop-extension-2026-10-17/lootenant.json'
    assert digest(old)==scope['original_60_ledger_sha256'];assert digest(previous)==scope['previous_lootenant_audit_sha256']
    prior_ids={r['offer_id'] for r in read(old)['baseline_kills']}|{r['offer_id'] for r in read(previous)['clean_kills']}
    assert not prior_ids.intersection(r['offer_id'] for r in kills)
    result={'schema':1,'review_date':'2026-10-18','source_id':'2878439002','source_url':'https://www.twitch.tv/videos/2878439002',
        'scope_path':(DATA/'lootenant-scope.json').relative_to(ROOT).as_posix(),'scope_sha256':digest(DATA/'lootenant-scope.json'),
        'scope':'Every one of 153 previously reviewed complete-stone decision offers with Spying and without Lodestone, across four run segments. The full scope was frozen before inspecting outcomes.',
        'sampling':'Fixed source census of retained offers, not a census of all video frames. Merged intervals were inspected and explicitly dispositioned. Same streamer and reused characters; no claim of independent players.',
        'method':'All 153 native sequences visually reviewed. Before, pre-hit, and after HUDs reread for every included kill. Three follow-ups resolved an action after menus, a merged room interval, and a zero-key OHW kill. Attack-hover alone never establishes a kill. Key Moment is tested at the actual hit, including blessing-door acquisition.',
        'baseline':stats(kills),
        'by_run':{rid:stats([r for r in kills if r['run_id']==rid]) for rid in dict.fromkeys(r['run_id'] for r in candidates)},
        'by_stage':{str(s):stats([r for r in kills if r['stage']==s]) for s in [3,4]},
        'by_mode':{m:stats([r for r in kills if r['mode']==m]) for m in ['normal','ultimate','unknown']},
        'by_run_class':{cl:stats([r for r in kills if r['run_class']==cl]) for cl in ['first_confirmed','later_confirmed','unknown']},
        'by_one_hit_wonder':{str(b):stats([r for r in kills if r['one_hit_wonder']==b]) for b in [False,True]},
        'dispositions':dict(Counter(r['disposition'] for r in candidates)),
        'exclusion_counts':dict(Counter(r['reason'] for r in candidates if r['disposition']=='excluded')),
        'clean_kills':kills,'candidates':candidates,
        'limitations':[
            '17 eligible kills; 15 use One Hit Wonder and only 2 are ordinary attacks. This small, mixed-stone sample cannot establish an exact Spying reduction.',
            'All included kills have Spying, no Lodestone, no Pendant and no pre-hit Key Moment. Other effects are retained per hit, including poison and Broken Armor where present.',
            'Both incomplete run segments remain first/later unknown and mode unknown. Purple HUD color alone is not proof of Ultimate mode.',
            'Five actual kills with Key Moment are excluded, including zero-key outcomes. Bosses, trials, corpses and escapes are also excluded.',
            'Wilson intervals assume independent binomial outcomes and do not cover streamer selection, within-run dependence or unmeasured mechanics.',
            'The old 60-kill and no-Spying Lootenant extension ledgers are frozen and not pooled here. No simulator/profile/model probability was changed.'
        ],
        'validation':{'all_153_candidates_dispositioned':True,'pending_candidates':sum(r['disposition']=='pending' for r in candidates),'unique_baseline_offer_ids':len({r['offer_id'] for r in kills}),'native_image_hashes_verified':len(hashes),'original_60_ledger_unchanged':True,'previous_no_spying_lootenant_ledger_unchanged':True,'no_overlap_with_prior_kills':True,'included_pre_hit_key_moment_absent':True,'context_sha256':digest(cp),'modes_sha256':digest(mp)},'simulator_modified':False}
    (DATA/'lootenant.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    r=raw[126];e=r['evidence'];damage=1031651538-760493393
    separate={'schema':1,'source_id':'2878439002','source_url':'https://www.twitch.tv/videos/2878439002?t=1h19m48s','review_date':'2026-10-18','parent_spying_candidate_ordinal':126,'run_id':r['run_id'],'room':99,'stage':4,'mode':'normal','run_class':'later_confirmed','gems':['time_traveler','deceit','spying'],'action':'failed_escape','action_basis':'Running-man cursor followed by monster staying opaque/alive during forward lunge/zoom, then exit to room100; no monster-death dissolve or reward. Keys remain11.',
        'hp_before':1031651538,'hp_after':760493393,'hp_max':1146279487,'damage':damage,'damage_percent_max_hp':100*damage/1146279487,
        'blessings_before':[{'kind':'rags_to_riches','remaining':6}], 'curses_before':[{'kind':'ponderous','remaining':8}],
        'no_entry_trap':True,'no_poison':True,'no_recovery':True,'no_broken_armor':True,'keys_before':11,'keys_after':11,
        'evidence_roles':{'before':2,'escape_lunge':6,'after':8},'evidence':[e[i] for i in [0,2,3,4,5,6,7,8]],'sequence_contact_sheet':r['sheet'],
        'comparison':{'community_stage4_upper_damage_pct':27.27,'hypothesis_both_actions_deceit_multiplier':0.8,'predicted_upper_pct_if_deceit_applied':27.27*.8,'observation_exceeds_predicted_upper':100*damage/1146279487>27.27*.8},
        'interpretation':'This failed-escape hit exceeds the community stage4 upper damage times0.8. It supports Deceit reducing fight damage only, conditional on that upper bound and the recorded effect interpretation. Ponderous changes escape odds; its tooltip was not opened here. One case is not a server-formula proof.',
        'simulator_modified':False}
    (DATA/'lootenant-deceit-failed-escape.json').write_text(json.dumps(separate,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['baseline','by_stage','by_mode','by_one_hit_wonder','dispositions','exclusion_counts','validation']},indent=2))

if __name__=='__main__':main()
