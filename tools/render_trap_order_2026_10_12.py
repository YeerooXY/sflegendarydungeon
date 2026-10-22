"""Render the reproducible trap-order analysis; no new statistical judgments."""
from __future__ import annotations


def pct(p):
    return '—' if p is None else f'{100*p:.1f}%'


def ci(bounds):
    return '—' if bounds is None else f'{100*bounds[0]:.1f}–{100*bounds[1]:.1f}%'


def measure(s):
    return f"{s['k']}/{s['n']} ({pct(s['estimate'])}); 95% CI {ci(s['wilson_95'])}"


def video(r):
    t = int(r['source_seconds'])
    sid = r['source_id']
    if sid.startswith('youtube-'):
        return f'https://www.youtube.com/watch?v={sid[8:]}&t={t}'
    return f'https://www.twitch.tv/videos/{sid}?t={t//3600:02d}h{t//60%60:02d}m{t%60:02d}s'


def render(r):
    primary = r['primary_latest']
    a,b = primary['strata']['1'],primary['strata']['2']
    m = r['presence_model_comparison']
    role = r['role_census_monster_mystery']
    delta = m['delta_aic_stop_first_minus_single_coin']
    lines = ['# Trap eligibility and generation order — 12 October 2026', '',
        'This is a direct footage audit of the proposed trap-generation sequence. The C++ calibration is unchanged.', '',
        f"The study reviewed {r['cohort_flow']['one_eligible']['unique_reviewed']} unique one-eligible choices and "
        f"{r['cohort_flow']['two_eligible']['unique_reviewed']} unique two-eligible choices. It also reviewed a census of "
        f"{r['role_flow']['current_candidates']} current-event monster/mystery candidates, of which "
        f"{r['role_flow']['confirmed_current']} retained that identity and passed scope checks. "
        'The role census overlaps the two-eligible sample and is not added to its prevalence denominator.', '',
        '## Measured incidence', '',
        'Only ordinary rooms beyond room 5 enter these estimates. Bosses, shops, stone selection, trial rounds and other forced screens are excluded. '
        'Monster and mystery openings are the eligible options; walls and physical doors do not enter the per-entrance denominator. '
        'The main one-eligible cohort excludes golden companions. Unknown artwork stays unknown.', '',
        '| Eligible entrances in the choice | Reviewed choices, known stones | Any trap | 95% Wilson CI | Unknown presence | Traps per readable eligible entrance |',
        '|---|---:|---:|---:|---:|---:|']
    for n,s in [(1,a),(2,b)]:
        t=s['any_trap'];o=s['per_eligible_option']
        lines.append(f"| {n} | {s['pairs']} | {t['k']}/{t['n']} = {pct(t['estimate'])} | {ci(t['wilson_95'])} | {t['unknown']} | {o['k']}/{o['n']} = {pct(o['estimate'])} |")
    lines += ['', 'The per-entrance figure is the individual-room trap rate requested by the maintainer. '
        'The any-trap figure is a different measurement needed to test how the pair is generated. '
        'A positive on one readable side establishes that a choice contains a trap even if the other side is obscured; '
        'a negative requires every eligible side to be readable.', '',
        'The study deliberately takes equal numbers of one-eligible and two-eligible choices. '
        'Do not pool their individual entrances into an overall random-room rate without weighting by how frequently those choice types occur.', '',
        f"Per-entrance 95% Wilson intervals are {ci(a['per_eligible_option']['wilson_95'])} with one eligible entrance and "
        f"{ci(b['per_eligible_option']['wilson_95'])} with two. The latter is descriptive: two entrances in one choice are dependent.", '',
        f"When both eligible sides must be readable, the two-eligible estimate is {measure(m['fully_readable_comparison']['two'])}. "
        f"Double traps: {measure(r['double_traps'])}. Zero observed double traps does not prove they are impossible.", '',
        'Missingness bounds (treat all unknown presences as absent versus present) are '
        f"{ci(a['any_trap']['worst_case_missingness_bounds'])} for one eligible option and "
        f"{ci(b['any_trap']['worst_case_missingness_bounds'])} for two. These are sensitivity bounds, not confidence intervals.", '',
        '## Does the second eligible option get another roll?', '',
        '| Candidate mechanism | One eligible | Two eligible: any trap | Two traps possible? |',
        '|---|---:|---:|---|',
        '| One coin for the choice, then place it | p | p | No |',
        '| Roll each eligible option, stop after success | p | 2p − p² | No |',
        '| Independent rolls without stopping | p | 2p − p² | Yes, p² |', '',
        f"Using the observed one-eligible rate ({pct(a['any_trap']['estimate'])}) as p, stopping after the first success predicts "
        f"{pct(m['two_eligible_prediction_using_single_eligible_rate']['p_any_two_eligible'])} of two-eligible choices containing a trap. "
        f"The observed rate is {pct(b['any_trap']['estimate'])}.", '',
        f"Fitting both cohorts jointly gives p={pct(m['stop_first_any_trap']['p'])} for the stop-first model and "
        f"p={pct(m['single_coin_per_pair']['p'])} for the one-coin model. "
        f"The one-coin model's conditional 95% profile-likelihood interval is {ci(m['single_coin_per_pair']['profile_likelihood_95'])}. "
        f"AIC(stop-first) − AIC(one-coin) = {delta:.2f}; positive values favour the one-coin model. "
        'This compares specific constant-p models, not all possible server implementations.', '']
    if delta >= 6:
        lines += ['The one-coin model fits these incidence counts substantially better than the tested equal-p sequential model. '
                  'That is evidence against this particular sequential formulation, not proof of the actual code.', '']
    elif delta <= -6:
        lines += ['The tested sequential incidence model fits substantially better than a constant single coin. '
                  'Incidence alone still cannot identify which room is processed first.', '']
    else:
        lines += ['The incidence comparison does not clearly separate the two tested mechanisms.', '']
    lines += [f"Allowing a separate p for each source yields summed ΔAIC={m['source_specific_p']['delta_aic_sum']:.2f}; "
        f"a separate p for each stage yields {m['stage_specific_p']['delta_aic_sum']:.2f}. "
        'Only strata containing both eligibility classes enter these comparisons. '
        f"Restricting to mystery options, with no monster opening in the pair, yields ΔAIC="
        f"{r['mystery_only_presence_model_comparison']['delta_aic_stop_first_minus_single_coin']:.2f}. "
        'These are descriptive sensitivity checks, not independent replications.', '',
        '## Does the monster get trap priority?', '',
        '**The user’s prediction is correct under its assumptions.** If the monster-capable role is always processed first, '
        'both roles use the same p, and processing stops at the first success, then:', '',
        '- P(monster trapped) = p.',
        '- P(mystery trapped) = (1 − p)p.',
        '- Among monster/mystery choices containing exactly one trap, the monster share is 1/(2 − p).', '',
        'At p=25%, that is 57.1% monster versus 42.9% mystery. A single trap coin followed by uniform eligible placement predicts 50/50. '
        'Randomizing which role gets the first attempted roll also predicts 50/50, despite sequential stopping. '
        'A final left/right display shuffle changes neither role prediction.', '',
        f"Current-event footage: **{measure(role)}** of resolved single-trap monster/mystery choices put the trap on the monster. "
        f"There are {role['zero_trap_pairs']} confirmed trap-free pairs, {role['both_trapped_pairs']} double-trap pairs, "
        f"and {role['unknown_sides']} pairs with at least one unresolved side. "
        f"The directional binomial p-value for monster placement above 50% is {role['one_sided_binomial_p_monster_above_50pct']:.3f}; "
        f"the two-sided value is {role['two_sided_binomial_p_vs_50pct']:.3f}.", '']
    lines += [f"If at most one trap per choice is assumed, visible traps with obscured companions can also be included: "
        f"{measure(role['assuming_at_most_one_trap'])} on the monster. "
        'The primary comparison above does not rely on that assumption.', '']
    if role['wilson_95'][0] <= .5 <= role['wilson_95'][1]:
        lines += ['The interval includes 50%, so this audit does not establish monster-first trap priority. '
                  'An inconclusive result also does not establish uniform placement.', '']
    else:
        lines += ['The interval excludes 50% under the independent-observation approximation. '
                  'Check source, stage, stones, recognition error and type-specific trap rates before attributing this to generation order.', '']
    lines += ['Different base trap probabilities by room type could imitate a priority effect. The one-eligible checks are:', '',
        '| Only eligible opening | Trap incidence | 95% Wilson CI | Unknown |',
        '|---|---:|---:|---:|']
    for kind,s in primary['single_eligible_by_kind'].items():
        lines.append(f"| {kind} | {s['k']}/{s['n']} = {pct(s['estimate'])} | {ci(s['wilson_95'])} | {s['unknown']} |")
    lines += ['', 'These are observational comparisons with different source, stage and stone mixes. '
        'They are not controlled estimates of an intrinsic room-type effect.', '',
        '## Stones, sources and older footage', '',
        '| Cohort | One eligible: any trap | Two eligible: any trap |',
        '|---|---|---|']
    for label,key in [('Known stones','primary_latest'),('No known generation-modifying stone','primary_no_generation_modifiers'),
                      ('No stones equipped','primary_no_stones'),('Including unknown stone context','primary_including_unknown_stones')]:
        s=r[key]['strata'];lines.append(f"| {label} | {measure(s['1']['any_trap'])} | {measure(s['2']['any_trap'])} |")
    lines += ['', '| Source | One eligible: traps / resolved choices | Two eligible: traps / resolved choices |',
        '|---|---:|---:|']
    for sid,ns in primary['by_source'].items():
        lines.append(f"| {sid} | {ns['1']['k']}/{ns['1']['n']} | {ns['2']['k']}/{ns['2']['n']} |")
    old=r['role_by_era']['2024']
    lines += ['', f"The historical 2024 role cohort has {old['all_offers']} confirmed monster/mystery choices, "
        f"with monster trap placement {measure(old)}. It remains separate from the current event. "
        'This is too little historical evidence to establish that the generation rules are unchanged.', '',
        'Source-resampled intervals for the primary any-trap rates are '
        f"{ci(a['source_bootstrap_95'])} and {ci(b['source_bootstrap_95'])}. "
        'These resample VODs, not independent people: a streamer can appear in several VODs and play several characters. '
        'Wilson intervals describe sampling uncertainty under an independence approximation. Neither interval method covers systematic missed traps, '
        'incorrect eligibility assumptions or unobserved event changes.', '',
        '## Audit trail and next measurement', '',
        f"{len(r['verified_duplicate_visits'])} duplicate visit reference(s) were collapsed. "
        f"{len(r['offer_kind_corrections'])} offer-type label(s) were corrected. "
        'The frozen plans and corrections remain available; replacements for duplicates follow the original ranking rather than trap outcomes. '
        'The first single-eligible cohort was source-balanced; the expansion and two-eligible cohort use outcome-independent SHA256 rankings. '
        'The two cohorts are not identically distributed random samples of all game rooms.', '',
        'Direct examples from the reviewed role census:', '',
        '| Example | Source | Room |', '|---|---|---:|']
    for kind in ('monster','mystery'):
        candidates=[x for x in r['role_rows'] if x['era']=='2026' and None not in x['sides']
                    and sum(x['sides'])==1 and x['kinds'][x['sides'].index(True)]==kind]
        if candidates:
            x=candidates[0]
            lines.append(f"| Trap on {kind} | [Footage at {int(x['source_seconds'])}s]({video(x)}) | {x['room'] if x['room'] is not None else 'unreadable'} |")
    power=r['role_test_sample_size'];matched=r['matched_entry_cohort']
    lines += ['', f"At p=25%, detecting the predicted 57.1% versus 50% directional bias with 80% power requires about "
        f"{power['trapped_role_revealing_pairs']} resolved trapped monster/mystery choices, or roughly "
        f"{power['approx_total_monster_mystery_offers']} total such choices under that sequential model. "
        f"At this audit's observed usable-trap fraction, obtaining {power['trapped_role_revealing_pairs']} would instead take roughly "
        f"{power['approx_total_at_observed_resolved_trapped_fraction']:,} choices. "
        'These are planning approximations; clustering increases the requirement. The target is trapped pairs, not merely 200 total pairs.', '',
        f"The earlier double-mystery entry audit stopped at {matched['reviewed']}/{matched['planned']} after the user reprioritized. "
        'Its unresolved choices remain explicit in JSON. It does not establish that selecting an untrapped mystery predicts its hidden contents.', '',
        'The simulator currently tries traps in random side order with p=29%, stopping at the first success: '
        '29% any-trap probability with one eligible option and 49.59% with two. '
        'This audit records the disagreement or agreement without changing the engine. A generation change should be validated against '
        'an additional held-out set and then followed by fresh strategy evaluation.', '',
        f"Integrity validation checked {r['evidence_integrity']['unique_local_evidence_files']:,} unique cited local files with no missing files or hash mismatches. "
        'This validates file integrity, not visual-label correctness.', '',
        'Reproduce with `python tools/validate_trap_order_evidence.py` followed by `python tools/analyze_trap_order_2026_10_12.py`. '
        'Counts, strata, model fits, normalized rows and input SHA256 hashes are in '
        '[the machine-readable report](trap-order-2026-10-12.json). '
        'Temporal annotations and duplicate proof are in `data/vods/trap-order-2026-10-12/`; '
        'native video and frames remain local under `out/` and are not redistributed.', '']
    return '\n'.join(lines)
