"""Render the computed monster audit without fixed dataset counts."""

def render(result):
    o=result['overall_descriptive'];c=o['coverage'];model=result['configured_model']
    pct=lambda x:'unknown' if x is None else f'{100*x:.2f}%'
    def fraction(a,n):return f'{a}/{n} ({pct(a/n)})' if n else '0/0 (untested)'
    def measures(s):
        n=s['coverage'].get('eligible_decisions',0)
        m=sum(v for k,v in s['visible_pair_counts'].items() if 'monster' in k.split('/'))
        content=s['selected_mystery_initial_contents']
        return fraction(m,n),fraction(content.get('monster',0),sum(content.values()))
    lines=['# Reviewed monster rates and configured model','',
      f"Input: `{result['inputs']['observations']}`. Reproduce with `python tools/analyze_vod_monsters.py`.",'',
      'Pooled totals describe the reviewed dataset. They mix contexts and are not fitted live-game probabilities.','',
      '| Source | Visible monster offered / ordinary pairs | Initial monster / verified selected mystery entries |',
      '|---|---:|---:|']
    for sid,s in result['by_source'].items():
        x,y=measures(s);lines.append(f'| {sid} | {x} | {y} |')
    x,y=measures(o);lines.extend([f'| All, descriptive only | {x} | {y} |',''])
    increments=', '.join(f'{pct(p)} {name}' for name,p in model['enemy_increment_beyond_regular_monster'].items()) or 'none'
    lines.extend([f"The current `{model['id']}` profile uses {pct(model['regular_monster_probability'])} regular monsters without a visible monster alternative. Its {pct(model['enemy_on_entry_probability'])} enemy-on-entry total adds {increments}. Corpses are a separate {pct(model['corpse_probability'])}, with a {pct(model['corpse_awake_after_interaction'])} later wake-up assumption. Corpses and later chest/corpse transformations are excluded from the living-entry rate.",'',
      '| Visible alternative | Eligible offers | Verified mystery entries | Initial monster / entries | Entry or selection unresolved |',
      '|---|---:|---:|---:|---:|'])
    for alt,s in o['alternative_conditioned_audit'].items():
        lines.append(f"| {alt} | {s['offered_pairs']} | {s['verified_selected_entries']} | {fraction(s['initial_monsters'],s['verified_selected_entries'])} | {s['unresolved_entry_or_selection']} |")
    lines.append('')
    lines.extend(['Here `wall` means a literal stone wall, not every physical door. '
      'The [door-context audit](vod-door-context-analysis-2026-10-08.md) separates '
      'mystery/mystery, mystery/monster, mystery/golden and mystery/physical-door '
      'offers, then preserves every physical subtype. A wall-only content estimate '
      'does not establish the contents beside locked, cursed or other doors.',''])
    for alt in ['mystery','wall']:
        s=o['alternative_conditioned_audit'][alt];bounds=s.get('extreme_all_unresolved_nonmonster_or_monster_range')
        if bounds:
            lines.append(f"For alternative `{alt}`, assigning all {s['unresolved_entry_or_selection']} unresolved entries to nonmonsters or monsters gives {pct(bounds[0])} to {pct(bounds[1])} across all {s['offered_pairs']} offers. This is an arithmetic missing-data bound, not a live-game confidence interval.")
    rule=result['beside_visible_monster_rule']
    pair_rule=('It allows double-mystery pairs' if model['double_mystery_possible'] else 'It rejects double-mystery pairs')
    lines.extend(['',f"Beside a visible monster, the engine removes enemy-on-entry weights. There are {rule['verified_selected_mystery_entries']} verified selected mystery entries among {rule['eligible_offers']} such offers, with {rule['confirmed_live_entry_violations']} verified living-entry violations and {len(rule['unlinked_living_entry_candidates'])} unlinked living-entry candidates. A later living monster with an unverified selected side cannot establish a violation. With zero linked entries the exclusion rule remains untested.",'',
      f"The visible-door generator samples weighted first and compatible second doors. Its base ordinary no-stone later-run probability of a visible monster is {pct(model['base_door_kernel_later_run']['visible_monster_offer_probability'])}; the raw monster weight is not an offer percentage. {pair_rule}, with a configured base probability of {pct(model['base_door_kernel_later_run']['double_mystery_probability'])}; this reviewed sample contains {o['visible_pair_counts'].get('mystery/mystery',0)} double-mystery offers. The conditional pairing distribution and weights remain assumptions, not fitted frequencies. Matching each observed full stone set gives a synthetic prediction of {pct(o['model_visible_monster_probability_range'][0])} to {pct(o['model_visible_monster_probability_range'][1])} visible-monster offers. Exact source/version/stage/full-stone/mode/generation strata and first/later-run uncertainty are retained in the JSON.",'',
      f"Of {o['records']} records, {result['status_counts'].get('decision',0)} are decisions, {result['status_counts'].get('duplicate',0)} repeats, {result['status_counts'].get('not_decision',0)} nondecisions and {result['status_counts'].get('uncertain',0)} uncertain records. {c.get('nonordinary_or_unclassified',0)} decisions are nonordinary or generation-unclassified, and {c.get('doors_unverified',0)} lack verified door types, leaving {c.get('eligible_decisions',0)} eligible pairs.",'',
      f"Among {c.get('mystery_offered',0)} eligible mystery offers, {c.get('mystery_selection_unknown',0)} have unknown selection, {c.get('eligible_mystery_outcomes',0)} have a linked initial outcome, {c.get('selected_mystery_outcome_unknown',0)} have selected mystery but unknown content, and {c.get('mystery_not_selected',0)} have a verified different selection. Unknown selection is uncertainty in the annotation, not a special game decision. A reviewed pair can remain useful for offer frequencies while being excluded from selected-mystery outcome counts.",'',
      f"Across all decision records, {c.get('decision_choice_unverified',0)} have an unverified selected side and {c.get('decision_choice_verified',0)} a verified side. A double-mystery entry can establish selected mystery content even while the left/right side remains unknown. {result['selection_unknown_but_side_value_present']} eligible mystery rows contain a side value that is not verified; those values are not treated as evidence.",'',
      'These outcomes were selected by players and successfully observed. Choices depend on alternatives, keys, health, stones and traps. Lethal traps without established entry are excluded, not coded empty. Within-run dependence and selection bias prevent pooled percentages from directly calibrating the game.','',
      'No simulator weights were changed. Native pair counterexamples and unresolved transition strips are preserved in the separate container-audit evidence directory.'])
    return '\n'.join(lines)+'\n'
