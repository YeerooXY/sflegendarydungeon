"""Publish direct manual review of the frozen Maexel Spying cohort."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'out/spying-key-audit-2026-10-18/2878297081'
DEST = ROOT / 'data/vods/spying-key-audit-2026-10-18/maexel.json'

# candidate, before-key count, settled count, One Hit Wonder, pre-hit evidence index
KILLS = [(10,3,3,True,3),(11,3,3,True,2),(12,3,4,True,2),
         (13,4,5,True,1),(14,5,5,True,3),(15,5,6,True,2),
         (16,6,6,True,2),(17,6,6,False,3),(19,6,7,False,1),
         (26,7,8,True,2),(27,8,9,True,2),(30,6,7,False,2),
         (32,7,8,False,2)]
DISPOSITIONS = {
    0:'failed_escape',1:'death_without_defeat',2:'mimic_escape',3:'barrel',
    4:'trap_then_successful_escape',5:'corpse_left_asleep',6:'corpse_left_asleep',
    7:'empty',8:'shop',9:'empty_chest',18:'trap_then_successful_escape',
    20:'crate_item',21:'corpse_left_asleep',22:'empty_crate',23:'failed_escape',
    24:'cursed_chest_left',25:'chest_blessing_and_gold',28:'shop',29:'empty',
    31:'empty',33:'successful_escape',34:'successful_escape',35:'empty_crate',
    36:'corpse_left_asleep',37:'successful_escape',38:'successful_escape',
    39:'mimic_escape',40:'cursed_chest_gold',
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def wilson(k,n):
    z=1.959963984540054
    d=1+z*z/n
    center=(k/n+z*z/(2*n))/d
    half=z*math.sqrt(k/n*(1-k/n)/n+z*z/(4*n*n))/d
    return [center-half,center+half]


def main():
    candidates=json.loads((BASE/'index.json').read_text())
    scope=json.loads((BASE/'scope.json').read_text())
    assert len(candidates)==41
    assert set(DISPOSITIONS).isdisjoint({x[0] for x in KILLS})
    assert set(DISPOSITIONS)|{x[0] for x in KILLS}==set(range(41))
    rows=[]
    for ordinal,before,after,one_hit,pre in KILLS:
        c=candidates[ordinal]
        evidence=c['evidence'].copy()
        before_ev=evidence[pre]
        if ordinal==19:
            dense=json.loads((BASE/'dense019.json').read_text())
            evidence+=dense
            before_ev=dense[1]
        post_ev=c['evidence'][7]
        rows.append({
            'id':f'2878297081:spying:{ordinal:03}', 'source_id':'2878297081',
            'source_url':'https://www.twitch.tv/videos/2878297081',
            'offer_id':c['offer_id'], 'candidate_ordinal':ordinal,
            'run_id':'2878297081:retained-spying-run-7650',
            'run_class':'unknown','mode':'unknown','room':None,'stage':c['stage_locator'],
            'source_seconds':before_ev['source_seconds'],
            'before_seconds':before_ev['source_seconds'],'after_seconds':post_ev['source_seconds'],
            'encounter':'ordinary_monster','action':'fight','one_hit_active':one_hit,
            'keys_before':before,'keys_after':after,'keys_gained':after-before,
            'gems':c['gems_locator'],'stone_set_complete':True,
            'spying_active':True,'lodestone_active':False,'key_moment_active':False,
            'verification':'manual_native_sequence',
            'kill_evidence':'Living monster on entry, followed by down-left shrinking/fading defeat or gold/key reward; no escape zoom. Active effect slots inspected before hit.',
            'notes':('Dense 8-fps sequence confirms the living swordsman and attack before the new Broken Armor curse appears.' if ordinal==19 else
                     'Last One Hit charge: gold 740,357 appears, keys remain six.' if ordinal==16 else
                     'Trap damage occurs separately before the ordinary attack.' if ordinal in (11,32) else
                     'Destiny result has already settled before the key counter used here; no Key Moment at hit.' if ordinal in (10,17) else ''),
            'evidence':evidence,'before_evidence':before_ev['image'],'after_evidence':post_ev['image'],
            'sheet':c['sheet'],
        })
    excluded=[{'candidate_ordinal':i,'offer_id':candidates[i]['offer_id'],
               'source_seconds':candidates[i]['start'],'reason':reason,
               'sheet':candidates[i]['sheet'],'evidence':candidates[i]['evidence']}
              for i,reason in DISPOSITIONS.items()]
    hashes={}
    for c in candidates:
        for e in c['evidence']:hashes[e['image']]=e['sha256']
    for e in json.loads((BASE/'dense019.json').read_text()):hashes[e['image']]=e['sha256']
    for p,digest in hashes.items():assert sha(ROOT/p)==digest,p
    k=sum(r['keys_gained']>0 for r in rows);n=len(rows)
    data={
        'schema':1,'review_date':'2026-10-18','source_id':'2878297081',
        'scope':{'file':(BASE/'scope.json').relative_to(ROOT).as_posix(),
                 'sha256':sha(BASE/'scope.json'),'reviewed_candidates':41,
                 'spying_locator_count':50,
                 'method':'All adjacent ordinary transitions with verified Spying/no Lodestone locators and gap 0.25–40 seconds; fixed before reading outcomes.',
                 'pre_review_spying_exclusions':[x for x in scope['pre_review_exclusions'] if 'spying' in (x.get('gems_locator') or [])]},
        'rows':rows,'excluded':excluded,'pending':[],
        'summary':{'kills':n,'with_key':k,'without_key':n-k,'keys_gained':sum(r['keys_gained'] for r in rows),
                   'rate':k/n,'wilson_95':wilson(k,n),'key_counts':dict(Counter(r['keys_gained'] for r in rows))},
        'verification':{'referenced_image_hashes_checked':len(hashes),'all_candidates_dispositioned':True,
                        'no_lodestone_or_key_moment_at_hit':True},
        'limitations':['One retained run, stages 3–4; exact room counter obscured.',
                       'Run classification and Normal/Ultimate mode are not inferred from visual theme.',
                       'One >40-second gap and boss/trial offers excluded by the pre-outcome scope.',
                       'Wilson interval assumes independent representative trials; selection, parser omissions and classification errors are not covered.'],
    }
    DEST.parent.mkdir(parents=True,exist_ok=True)
    DEST.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(data['summary']))


if __name__=='__main__':main()
