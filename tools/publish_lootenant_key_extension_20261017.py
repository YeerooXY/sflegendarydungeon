"""Join explicit visual annotations to a frozen, outcome-independent scope."""
from pathlib import Path
from collections import Counter
import json
import hashlib
import math

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/vods/key-drop-extension-2026-10-17'
BASE=ROOT/'out/key-drop-extension-2026-10-17/lootenant'

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def stats(rs):
    n=len(rs);k=sum(r['key_drop']>0 for r in rs);counts=Counter(r['key_drop'] for r in rs)
    z=1.959963984540054
    if n:
        d=1+z*z/n;c=(k/n+z*z/(2*n))/d;r=z*math.sqrt(k/n*(1-k/n)/n+z*z/(4*n*n))/d
        ci=[max(0,c-r),min(1,c+r)]
    else:ci=None
    return {'n':n,'drops_0':counts[0],'drops_1':counts[1],'drops_2':counts[2],
            'any_key_fraction':k/n if n else None,'any_key_wilson_95':ci,
            'mean_keys_per_kill':sum(r['key_drop'] for r in rs)/n if n else None}

def main():
    scope=read(DATA/'lootenant-scope.json');raw=read(BASE/'index.json')
    labels=read(DATA/'lootenant-manual-working.json')
    context_path=ROOT/'data/vods/opening-audit-2026-10-17/run-context.json'
    modes_path=ROOT/'data/vods/opening-audit-2026-10-17/run-modes.json'
    contexts={r['run_id']:r for r in read(context_path)['runs']}
    modes={r['run_id']:r for r in read(modes_path)['rows']}
    assert len(raw)==len(labels)==scope['candidate_count']==154
    assert [r['offer_id'] for r in raw]==scope['candidate_offer_ids']
    assert {r['ordinal'] for r in labels}==set(range(154))
    assert all(r['disposition'] in ['include','excluded'] for r in labels)
    candidates=[];kills=[];image_hashes={}
    for label in labels:
        origin=raw[label['ordinal']];rid=origin['run_id'];context=contexts[rid];mode=modes[rid]
        row={**origin,**label,'verification':'manual_native_sequence_and_enlarged_hud',
             'gems':origin['previously_reviewed_gems'],'stone_set_complete':True,
             'stone_verification':'Gem icons checked across native sequence; full cumulative set visible in the HUD.',
             'run_class':context['classification'],'mode':mode['mode'],
             'run_context_evidence':{'path':context_path.relative_to(ROOT).as_posix(),'run_id':rid},
             'mode_evidence':{'path':modes_path.relative_to(ROOT).as_posix(),'run_id':rid}}
        assert not {'spying','lodestone'}.intersection(row['gems'])
        assert origin['previously_reviewed_stone_set_complete']
        if 'additional_evidence_index' in row:
            row['additional_evidence']=read(ROOT/row['additional_evidence_index'])
        for e in row['evidence']+row.get('additional_evidence',[]):
            p=ROOT/e['image'];assert p.is_file();assert digest(p)==e['sha256']
            image_hashes[e['image']]=e['sha256']
        if row['disposition']=='include':
            assert row['key_moment_active'] is False and row['effect_state_complete']
            assert row['encounter_origin']=='living_monster_on_entry'
            assert row['room']%25!=0
            assert row['next_room']==row['room']+1
            assert row['keys_before'] is not None and row['keys_after'] is not None
            row.update(action='fight',outcome='monster_killed',key_drop=row['keys_after']-row['keys_before'])
            assert row['key_drop'] in [0,1]
            kills.append(row)
        candidates.append(row)
    original=ROOT/'data/vods/opening-audit-2026-10-17/key-drops.json'
    assert digest(original)==scope['original_60_ledger_sha256'],'Original ledger changed'
    old_ids={r['offer_id'] for r in read(original)['baseline_kills']}
    assert not old_ids.intersection(r['offer_id'] for r in kills)
    result={
        'schema':1,'review_date':'2026-10-18','source_id':'2878439002',
        'source_url':'https://www.twitch.tv/videos/2878439002',
        'scope_path':(DATA/'lootenant-scope.json').relative_to(ROOT).as_posix(),
        'scope_sha256':digest(DATA/'lootenant-scope.json'),
        'scope':'All 154 retained decision transitions in stages 2-4 of two fixed complete runs; no reward-based stopping or scope expansion.',
        'sampling':'Fixed contiguous source windows selected from previously verified full gem sets before key outcome review. Same streamer and reused characters, so this is disjoint from the old 60 kills but not an independent sample of players.',
        'method':'All 154 nine-frame native sequences reviewed. Included before/after HUDs enlarged and re-read; two ambiguous returns received dense 0.125/0.25-second sequence review. Attack hover alone never established a kill. Excluded Key Moment according to pre-hit state, including its final tick.',
        'baseline':stats(kills),
        'by_run':{rid:stats([r for r in kills if r['run_id']==rid]) for rid in sorted({r['run_id'] for r in candidates})},
        'by_stage':{str(s):stats([r for r in kills if r['stage']==s]) for s in [2,3,4]},
        'by_mode':{m:stats([r for r in kills if r['mode']==m]) for m in ['normal','ultimate']},
        'by_one_hit_wonder':{str(b):stats([r for r in kills if r['one_hit_wonder']==b]) for b in [False,True]},
        'dispositions':dict(Counter(r['disposition'] for r in candidates)),
        'exclusion_counts':dict(Counter(r['reason'] for r in candidates if r['disposition']=='excluded')),
        'clean_kills':kills,'candidates':candidates,
        'limitations':[
            'Only 26 of the 154 decision intervals contain an eligible kill; most monster encounters are escapes. The denominator is kills, not offered monsters or room choices.',
            '25 of the 26 clean kills use One Hit Wonder. This audit mainly informs OHW kills under the recorded stone sets and cannot establish equivalence to ordinary attacks.',
            'No Spying, Lodestone, Pendant or Key Moment is active for included kills. Full other-stone and blessing states are preserved for each kill.',
            'Bosses, trial encounters, awakened corpses and the golden lantern zombie are separate mechanics and excluded. All retain evidence and exclusion reasons.',
            'The first 60-kill ledger is frozen and is not silently pooled into this extension.',
            'Wilson intervals assume independent binomial outcomes within a stratum; they do not account for within-run dependence, streamer selection or unknown mechanics.',
            'No simulator probabilities or profiles were modified.'
        ],
        'validation':{'all_154_candidates_dispositioned':True,'unique_baseline_offer_ids':len({r['offer_id'] for r in kills}),
                      'native_image_hashes_verified':len(image_hashes),'original_60_ledger_unchanged':True,
                      'no_overlap_with_original_60_kills':True,'included_pre_hit_key_moment_absent':True,
                      'context_sha256':digest(context_path),'modes_sha256':digest(modes_path)},
        'simulator_modified':False,
    }
    (DATA/'lootenant.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['baseline','by_stage','by_mode','by_one_hit_wonder','dispositions','exclusion_counts','validation']},indent=2))

if __name__=='__main__':main()
