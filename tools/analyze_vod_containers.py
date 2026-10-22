"""Reproduce bounded, manually reviewed container reward counts; no fitting."""
import argparse,collections,hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def validate_rows(rows):
    seen=set()
    for row in rows:
        ident=row.get('observation_id')
        if not ident or ident in seen:
            raise ValueError('Container appearances need unique observation IDs.')
        seen.add(ident)
        context=row.get('context',{})
        if context.get('stone_status')!='verified' or not isinstance(context.get('gems'),list):
            raise ValueError('Container comparisons require a verified complete stone set.')
        interaction=row.get('interaction')
        if interaction not in {'opened','skipped','awakened_mimic','unknown'}:
            raise ValueError('Unknown container interaction status.')
        expected=(interaction=='opened' and row.get('initial_style') in {'wooden_box','bronze','silver'})
        if type(row.get('ordinary_loot_eligible')) is not bool or row['ordinary_loot_eligible']!=expected:
            raise ValueError('Ordinary loot must be an opened ordinary container, excluding mimics and skips.')
        for key in ('persistent_blessing','instant_healing'):
            value=row.get(key)
            if value is not None and type(value) is not bool:
                raise ValueError('Effect outcomes must be true, false, or unknown.')
            if interaction!='opened' and value is not None:
                raise ValueError('Unopened or unobserved loot must not become a negative effect draw.')
    return rows

def describe(rows):
    validate_rows(rows)
    opened=[r for r in rows if r['interaction']=='opened']
    ordinary=[r for r in rows if r['ordinary_loot_eligible']]
    by_greed={}
    for greed in [False,True]:
        rs=[r for r in ordinary if ('greed' in r['context']['gems'])==greed]
        blessing=collections.Counter(str(r['persistent_blessing']).lower() for r in rs)
        instantaneous=sum(r['instant_healing'] is True for r in rs)
        positive=sum(r['persistent_blessing'] is True or r['instant_healing'] is True for r in rs)
        unresolved=sum(r['persistent_blessing'] is None and r['instant_healing'] is not True for r in rs)
        categories=collections.Counter()
        for r in rs:
            categories.update(set(e['kind'] for e in r['rewards']))
        by_greed[str(greed).lower()]={'opened_ordinary_containers':len(rs),'persistent_blessing':dict(blessing),'instant_healing':instantaneous,
            'positive_effect_at_least_one':positive,'positive_effect_unresolved':unresolved,
            'positive_effect_fraction_bounds':[positive/len(rs),(positive+unresolved)/len(rs)] if rs else None,
            'reward_event_presence':dict(categories),'observation_ids':[r['observation_id'] for r in rs]}
    return {'records':len(rows),'initial_styles':dict(collections.Counter(r['initial_style'] for r in rows)),
        'interaction_status':dict(collections.Counter(r['interaction'] for r in rows)),
        'opened_containers':len(opened),'opened_ordinary_loot_containers':len(ordinary),'by_greed':by_greed}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,default=ROOT/'out/vods/container-audit-2026-10-08/reviewed-interactions.json')
    ap.add_argument('--output',type=Path,default=ROOT/'docs/vod-container-audit-2026-10-08.json')
    a=ap.parse_args();rows=json.loads(a.input.read_text(encoding='utf-8'))
    assert len({r['observation_id'] for r in rows})==len(rows)
    for r in rows:
        assert r['context']['stone_status']=='verified'
        for e in r['evidence']+[r['hud_before'],r['hud_after']]:
            target=(ROOT/'out/vods'/e['image']).resolve()
            assert target.is_relative_to((ROOT/'out/vods').resolve())
            assert hashlib.sha256(target.read_bytes()).hexdigest()==e['sha256'],target
    summary=describe(rows)
    result={'schema':1,'input':str(a.input.relative_to(ROOT)),'input_sha256':hashlib.sha256(a.input.read_bytes()).hexdigest(),
        'summary':summary,'by_full_context':[{'context':dict(zip(['run_id','stage','gems'],[run,stage,list(gems)])),**describe(rs)} for (run,stage,gems),rs in sorted(_groups(rows).items())],
        'coverage':{'source':'2881153716','full_available_window':[8880,12737],
           'selected_mystery_subset':{'target_cases':99,'known_crate_or_chest_entries':25,'reviewed_entries':25,'basis':'Every chest/crate first-visible entry in the prior systematic eligible double-mystery or forced mystery/wall audit.'},
           'greed_stage2_passage':{'source_start':10494.125,'source_end':10588.25,'ordinary_offers_reviewed':24,'room_counter_range':[26,49],'extra_attempt_counter':32,'scheduled_shop_counter':39,'crate_or_chest_entries':5,'ordinary_opened':3,'closed_skipped':1,'awakened_mimic':1},
           'deduplicated_overlap':['2881153716:visual:O338'],'native_sampling_seconds':.125},
        'definitions':{'unit':'One observed container appearance through departure, not one mouse click. Multiple sequential rewards remain separate events in that visit.',
           'ordinary_loot':'Opened wooden_box, bronze or silver without mimic transformation. Epics, barrels, searched corpses with unestablished interaction, skipped objects and mimics are separate.',
           'persistent_blessing':'true: animated blessing card plus HUD transition; false: complete interaction with no effect card and comparable HUD; null: existing effect counter/strength cannot safely exclude a refresh.',
           'instant_healing':'Blue red-cross potion card and observed health increase. Count separately from persistent HUD blessings.',
           'nothing':'Observed opened empty/smoke result and no reward/effect through departure; never assigned to unseen or skipped objects.',
           'gold':'New gold popup from that container visit. Prior-room lingering popups and item-sale proceeds are separate.',
           'full_stones':'All three HUD slots were manually verified in the source canonical stone pass; empty is visibly empty, not missing annotation.'},
        'findings':{'no_greed_persistent_blessing_ids':['2881153716:visual:O266','2881153716:visual:R50'],
            'no_greed_instant_heal_id':'2881153716:visual:O627',
            'effect_plus_gold_same_container_ids':['2881153716:visual:R50','2881153716:visual:O627'],
            'barrel_curse_refresh_id':'2881153716:visual:O324'},
        'limitations':['Bounded observational audit, not a random sample of all containers. Extra Greed stage is a separate sampling stratum.',
          'Greed sample is too small to estimate an increase in blessing odds; no comparative or causal claim is made.',
          'Exact blessing identity and strong/normal magnitude are unverified unless the visual event itself establishes the quantity. O83 and O338 retain unresolved possible refreshes.',
          'Multi-event visits do not establish whether one click or repeated clicks caused each event. Do not fit an exclusive one-result draw to these visits.',
          'Existing effects, stage, account and item choices may change rewards; full context is preserved. No simulator weights are fitted or changed.']}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    public=ROOT/'data/vods/container-interactions-2026-10-08.jsonl'
    public.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    lines=['# Native container interaction audit','',
      'Two ordinary crates visibly granted persistent blessings without Greed. A bronze chest without Greed also granted an instant healing potion. These are direct mechanism counterexamples to a strict Greed-only gate, not calibrated probabilities.','',
      f"Reviewed {summary['records']} distinct appearances: 29 crates/chests, one barrel and one corpse. Of the crates/chests, two became mimics, one stayed closed through departure, and 26 opened; two opened ornate epic chests are separated, leaving {summary['opened_ordinary_loot_containers']} ordinary-loot visits.",'',
      '| Full stone set includes Greed | Opened ordinary containers | Persistent blessing | Instant heal | Unresolved possible refresh |',
      '|---|---:|---:|---:|---:|']
    for greed,s in summary['by_greed'].items():
        lines.append(f"| {greed} | {s['opened_ordinary_containers']} | {s['persistent_blessing'].get('true',0)} | {s['instant_healing']} | {s['persistent_blessing'].get('none',0)} |")
    lines.extend(['',
      'No-Greed has 3 observed positive effects in 20 opened ordinary visits, with one further possible refresh unresolved (15%–20% descriptive bounds). Greed has 0 observed in 4, with one possible refresh unresolved (0%–25%). The sample does not measure a Greed advantage or establish population odds. Persistent-only positives are 2/20 without Greed and 0/4 with Greed.','',
      '| Observation | Native source time | Verified result |',
      '|---|---|---|',
      '| O266 | 10049.500–10052.625 | Wooden crate; [thirsty, moonstone]; animated new blessing; HUD adds counter10. |',
      '| R50 | 10947.625–10951.500 | Wooden crate; three empty stone slots; new blessing counter4, then 24,726,338 gold in the same visit. |',
      '| O627 | 12630.375–12634.000 | Bronze chest; [misadventurer, hero, spying]; potion card, +24,448,540 health (25% max), then 37,089,507 gold. |',
      '| O324 | 10532.500–10535.625 | Barrel with Greed; purple curse card refreshes an existing counter4 to10. This is not a blessing. |','',
      'The sampling frame is all 25 known crate/chest entries in the prior 99-case selected-mystery audit, plus all 24 ordinary offers in the Greed-active stage-2 passage (room26–49, including one repeated room32 attempt and excluding the scheduled room39 shop). Its five crate/chest appearances include one overlapping case, so there are 29 distinct crate/chest appearances.','',
      'Rewards preserve multiple events. Item-sale gold is recorded separately from direct drops; three-item reward cards remain three items. Mimic transformation and skipped containers never become empty/no-blessing loot trials. The corpse interaction remains unknown. Exact effect names and strength are withheld; two existing HUD counters could not safely rule out a refresh.','',
      'Reproduce labels and enlarged HUD proof sheets with `python tools/record_container_audit.py`, then validate evidence hashes and regenerate this report/public metadata with `python tools/analyze_vod_containers.py`. Native extraction is `python tools/prepare_container_audit.py containers`, `... greed-onsets`, and `... greed-transitions`.','',
      'Metadata: `data/vods/container-interactions-2026-10-08.jsonl`. Proof frames and full chronological manifests: `out/vods/container-audit-2026-10-08/`. No simulator odds were changed.'])
    a.output.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))

def _groups(rows):
    groups=collections.defaultdict(list)
    for r in rows:
        c=r['context'];groups[(c['run_id'],c['stage'],tuple(sorted(c['gems'])))].append(r)
    return groups

if __name__=='__main__':main()
