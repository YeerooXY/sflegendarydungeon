"""Describe reviewed offers/entries and compare to configured synthetic pools.

No fitting, relabeling, or simulator changes. Uses the same eligibility function
as the published conditional counts; pooled counts are descriptive only.
"""
from __future__ import annotations
import argparse,collections,hashlib,json
from pathlib import Path
from sfld_vod.temporal import observation_counts
from sfld_vod.review import wilson

ENEMIES={'monster','boss','mimic','undead','tube','beta','shakes','valaraukar','pig','trial_monster'}
ENTRY_ENEMIES={'monster','mimic','lantern_zombie'}

def profile(path):
    fields={}
    for line in path.read_text().splitlines():
        line=line.split('#',1)[0].strip()
        if '=' in line:
            k,v=line.split('=',1);fields[k]=v
    return fields

def weights(value):
    return {k:float(v) for k,v in (part.split(':') for part in value.split(','))}

def pair_kernel(p,gems,stage,first_run):
    """Exact ordered ordinary-pair kernel in engine.cpp, conditional on no shop.

    The shipped fixed arena/shop schedule is excluded from ordinary data.
    Trap assignment does not alter pair identities. A core monster/mystery/golden
    choice remains accessible, so ensure_open_path does not change these pairs.
    """
    w=weights(p['doors']);g=set(gems)
    assert p['shop_schedule']=='one_per_stage' and int(p['trial_entry_room'])!=0
    w['shop']=w['trial']=0
    for name in ('unlocked','double_locked','epic'):w[name]=0
    if 'greed' in g:w['mystery']*=2
    if 'explorer' in g:w['mystery']*=.5
    if 'spying' in g:w['unlocked']=w['locked']*.5;w['locked']*=.5
    if 'lodestone' in g:w['double_locked']+=w['locked']*.5
    if 'devil' in g:w['epic']+=8
    if 'greasy' in g:w['epic']=0
    for stone,door,multiplier in [('blood','sacrifice',2),('hick','sacrifice',.5),('thirsty','cursed',2),('misadventurer','cursed',.5)]:
        if stone in g:w[door]*=multiplier
    if first_run and stage==1:w['cursed']=w['sacrifice']=0
    total=sum(w.values());first={'locked':1} if 'pendant' in g else {k:v/total for k,v in w.items() if v}
    pairs=collections.Counter();core={'monster','mystery','golden'}
    for a,pa in first.items():
        allowed={b:v for b,v in w.items() if v and not(a not in core and b not in core) and not(a==b=='monster')}
        denominator=sum(allowed.values())
        if not denominator:allowed={'mystery' if a=='monster' else 'monster':1};denominator=1
        for b,v in allowed.items():pairs[tuple(sorted((a,b)))]+=pa*v/denominator
    assert abs(sum(pairs.values())-1)<1e-10
    return {'visible_monster_offer_probability':sum(v for pair,v in pairs.items() if 'monster' in pair),
            'double_mystery_probability':pairs[('mystery','mystery')]}

def context(row):
    c=row['context']
    return {'source_id':row['source_id'],'version':c.get('version'),'stage':c.get('stage'),
            'run_class':c.get('run_class'),'gems':sorted(set(c.get('gems') or [])),
            'theme':c.get('theme'),'mode':c.get('mode'),'generation':row.get('generation'),'phase':c.get('phase','ordinary')}

def selected_alternative(row):
    ds=row['doors'];fs=row['verified_fields']
    if all(d['kind']=='mystery' for d in ds) and 'entered_room'in fs and 'initial_content'in fs:return 'mystery'
    side=row.get('chosen_side')
    if 'chosen_side'in fs and type(side)is int and side in(0,1) and ds[side]['kind']=='mystery':return ds[1-side]['kind']
    return None

def summary(rows,p):
    checked=[(r,observation_counts([r])['coverage'])for r in rows]
    eligible=[r for r,c in checked if c.get('eligible_decisions')]
    outcomes=[r for r,c in checked if c.get('eligible_mystery_outcomes')]
    visible=sum(any(d['kind']=='monster'for d in r['doors'])for r in eligible)
    live=sum(r['outcome']['initial_content']=='monster'for r in outcomes)
    alternatives=collections.defaultdict(list)
    for r in outcomes:alternatives[selected_alternative(r)].append(r)
    alternative_stats={}
    for other in ('monster','mystery','wall'):
        offers=[r for r in eligible if (all(d['kind']=='mystery'for d in r['doors']) if other=='mystery' else {d['kind']for d in r['doors']}=={'mystery',other})]
        known=alternatives.get(other,[]);living=sum(r['outcome']['initial_content']=='monster'for r in known)
        alternative_stats[other]={'offered_pairs':len(offers),'verified_selected_entries':len(known),'initial_monsters':living,'rate_among_verified_entries':living/len(known)if known else None,'unresolved_entry_or_selection':len(offers)-len(known)}
        if other in('mystery','wall')and offers:
            alternative_stats[other]['extreme_all_unresolved_nonmonster_or_monster_range']=[living/len(offers),(living+len(offers)-len(known))/len(offers)]
    prediction=[]
    for r in eligible:
        c=r['context'];states=[c['run_class']=='first']if c.get('run_class') else [False,True]
        values=[pair_kernel(p,c['gems'],c.get('stage'),state)['visible_monster_offer_probability']for state in states]
        prediction.append((min(values),max(values)))
    return {'records':len(rows),'coverage':observation_counts(rows)['coverage'],
            'visible_monster_offers':wilson(visible,len(eligible)) if eligible else None,
            'visible_monster_door_slots':{'count':sum(d['kind']=='monster'for r in eligible for d in r['doors']),'denominator':2*len(eligible)},
            'selected_mystery_initial_monsters':wilson(live,len(outcomes)) if outcomes else None,
            'selected_mystery_initial_contents':dict(collections.Counter(r['outcome']['initial_content']for r in outcomes)),
            'selected_mystery_by_other_choice':{a:{'opportunities':len(rs),'initial_monsters':sum(r['outcome']['initial_content']=='monster'for r in rs)}for a,rs in sorted(alternatives.items())},
            'alternative_conditioned_audit':alternative_stats,
            'visible_pair_counts':{'/'.join(pair):n for pair,n in sorted(collections.Counter(tuple(sorted(d['kind']for d in r['doors']))for r in eligible).items())},
            'model_visible_monster_expected_count_range': [sum(x[i]for x in prediction)for i in(0,1)],
            'model_visible_monster_probability_range':[sum(x[i]for x in prediction)/len(prediction) if prediction else None for i in(0,1)]}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--observations',type=Path,default=Path('data/vods/observations-2026-10-08.jsonl'));ap.add_argument('--profile',type=Path,default=Path('profiles/synthetic.profile'));ap.add_argument('--output',type=Path,default=Path('docs/vod-monster-analysis-2026-10-08.json'));a=ap.parse_args()
    rows=[json.loads(x)for x in a.observations.read_text().splitlines()if x.strip()];p=profile(a.profile)
    overall=summary(rows,p);by_source={sid:summary([r for r in rows if r['source_id']==sid],p)for sid in sorted({r['source_id']for r in rows})}
    groups=collections.defaultdict(list)
    for r in rows:
        if observation_counts([r])['coverage'].get('eligible_decisions'):groups[json.dumps(context(r),sort_keys=True)].append(r)
    strata=[{'context':json.loads(k),**summary(v,p)}for k,v in sorted(groups.items())]
    eligible=[r for r in rows if observation_counts([r])['coverage'].get('eligible_decisions')]
    beside=[r for r in eligible if {d['kind']for d in r['doors']}=={'monster','mystery'}]
    candidates=[r for r in beside if r.get('outcome',{}).get('initial_content')in ENTRY_ENEMIES]
    linked_beside=[r for r in beside if observation_counts([r])['coverage'].get('eligible_mystery_outcomes')]
    violations=[r for r in linked_beside if r.get('outcome',{}).get('initial_content')in ENTRY_ENEMIES]
    unlinked=[r for r in candidates if r not in linked_beside]
    table=weights(p.get('mystery_without_monster',p['mystery']));denominator=sum(table.values())
    enemy=sum(v for k,v in table.items()if k in ENEMIES)
    result={'schema':1,'inputs':{'observations':str(a.observations),'observations_sha256':hashlib.sha256(a.observations.read_bytes()).hexdigest(),'profile':str(a.profile),'profile_sha256':hashlib.sha256(a.profile.read_bytes()).hexdigest(),'engine_sha256':hashlib.sha256(Path('src/engine.cpp').read_bytes()).hexdigest()},
      'definitions':{'visible_monster_offer':'Eligible ordinary decisions with at least one visible monster door / all eligible ordinary decisions. Counts offers, not chosen doors.','mystery_initial_monster':'Verified selected mystery entries whose first visible content is monster / verified selected mystery entries. Corpse, chest, later awakening and mimic revelation are separate.','unknown_selection':'Door offer is reviewed but selected side is not established; hovered pointer or a later living monster alone is insufficient.','pooled_counts':'Descriptive audit totals only; source/version/stage/full-stone/mode groups must remain separate for inference.'},
      'overall_descriptive':overall,'by_source':by_source,'fully_conditioned_strata':strata,
      'status_counts':dict(collections.Counter(r['status']for r in rows)),
      'decision_generations':dict(collections.Counter(r['generation']for r in rows if r['status']=='decision')),
      'selection_unknown_but_side_value_present':sum(c.get('mystery_selection_unknown',0)and r.get('chosen_side')is not None for r in rows for c in [observation_counts([r])['coverage']]),
      'beside_visible_monster_rule':{'eligible_offers':len(beside),'verified_selected_mystery_entries':len(linked_beside),'confirmed_live_entry_violations':len(violations),'confirmed_violation_ids':[r['id'] for r in violations],'unlinked_living_entry_candidates':[{'id':r['id'],'source_id':r['source_id'],'source_seconds':r['source_seconds'],'context':r['context'],'chosen_side':r.get('chosen_side'),'verified_fields':r['verified_fields'],'outcome':r['outcome']}for r in unlinked],'interpretation':'Only a verified mystery selection and verified initial entry can test the rule. Unlinked living entries are not violations. Zero linked entries means the rule is untested.'},
      'configured_model':{'id':p['id'],'evidence':p['evidence'],'mystery_without_visible_monster_raw_weights':table,'regular_monster_probability':table.get('monster',0)/denominator,'enemy_on_entry_probability':enemy/denominator,'enemy_increment_beyond_regular_monster':{k:v/denominator for k,v in table.items()if k in ENEMIES and k!='monster'},'corpse_probability':table.get('skeleton',0)/denominator,'corpse_awake_after_interaction':float(p['skeleton_wakes']),'beside_visible_monster_enemy_on_entry_probability':0,'base_door_kernel_later_run':pair_kernel(p,[],2,False),'base_door_kernel_first_stage_first_run':pair_kernel(p,[],1,True),'double_mystery_possible':True,'door_kernel_scope':'Exact shipped ordinary pair kernel conditional on no scheduled shop/arena/boss/opening. Unknown first/later run gives a prediction range. No game calibration claimed.'},
      'limitations':['Linked outcomes are selected and successfully observed, not all mystery draws. Players choose using other doors, keys, health, stones and visible traps.','Lethal traps without entry and unknown selected sides are excluded, not recorded as empty/no monster.','Source-specific rates and all exact source/version/stage/full-stone/mode strata are retained. Pooled Wilson intervals do not remove selection bias or correlation within runs.','Unknown event first/later run labels require model prediction ranges. Version labels may be source-era placeholders rather than exact game builds.','Initial corpse/chest transformations are not relabeled living monsters. Initial enemy probabilities exclude corpses and later awakening.','Since engine 0.6.3, ordinary double-mystery pairs are allowed. The conditional pairing distribution and door/content weights remain assumptions; no observed frequencies were fitted.']}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    from render_vod_monster_report import render
    a.output.with_suffix('.md').write_text(render(result),encoding='utf-8')
    print(json.dumps({'overall':overall,'source_summaries':{k:{'coverage':v['coverage'],'initial':v['selected_mystery_initial_contents']}for k,v in by_source.items()}},indent=2))

if __name__=='__main__':main()
