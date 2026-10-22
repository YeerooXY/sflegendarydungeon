"""Write the verified October 15 interaction research report."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def read(p):return json.loads(p.read_text())


def main():
    core=ROOT/'docs/interaction-training-2026-10-15';runtime=ROOT/'docs/interaction-runtime-2026-10-15'
    diagnostic=ROOT/'docs/interaction-diagnostics-2026-10-15'
    study=read(core/'summary.json');result=read(runtime/'summary.json');plan=read(runtime/'plan.json')
    deploy=read(ROOT/'docs/deployment-2026-10-15.json');candidate=result['deploy_candidate']
    cells={(c['candidate']['name'],c['budget']):c for c in result['final']}
    key=candidate+' minus parent';contrasts={r['budget']:r for r in result['contrasts'][key]}
    states=study['training'];decisions=sum(s['steps'] for s in states.values())
    fresh=sum(s.get('completed_whole_events',s['completed_training_events']) for s in states.values())
    partial=sum(s.get('completed_partial_scenarios',0) for s in states.values())
    lines=['# Interaction audit and learning, October 15, 2026','',
        f'The current default uses the further-trained engine 0.6.11 checkpoint `{deploy["checkpoint"]}`.',
        'It passed an independent comparison using the desktop recommendation rules. These are simulator results; '
        'they do not prove the globally optimal strategy or measure uncertainty in the real game odds.','',
        '## Actual desktop-policy comparison','',
        'Each cell contains 4,096 fresh simulated seven-day events. Both policies use the same engine, working profile, '
        'random eligible stone offers and mushroom cap. No purchases or key-saving quotas are forced. '
        'The comparison includes the existing UI corrections for Recovery replacements and safe free doors.','',
        '| Weekly mushroom cap | Previous adviser | Installed adviser | Paired gain | Simultaneous 99% interval for gain |',
        '|---:|---:|---:|---:|---:|']
    for b in plan['budgets']:
        old=cells['parent',b]['summary']['completions']['mean'];new=cells[candidate,b]['summary']['completions']['mean']
        d=contrasts[b];lo,hi=d['family_99_ci']
        lines.append(f'| {b:,} | {old:.4f} | {new:.4f} | {d["mean"]:+.4f} | [{lo:+.4f}, {hi:+.4f}] |')
    d=contrasts['equal-budget mean'];lo,hi=d['family_99_ci']
    lines+=['',f'Equal-budget gain: **{d["mean"]:+.5f}** clears/week, simultaneous 99% interval '
        f'**[{lo:+.5f}, {hi:+.5f}]**. The zero-mushroom mean dipped; do not describe this as a proven improvement at every budget.',
        'Intervals use paired normal Monte Carlo errors, with 21 predeclared comparisons covered by a Bonferroni adjustment. '
        'The six budgets share event-index clusters when computing the equal-budget interval.','',
        '## Training and selection','',
        f'Two continuation branches added **{decisions:,} decisions**: ordinary PPO and PPO with 25% of reset episodes '
        'starting in 417 declared player-interaction positions. The latter covers effect replacement, shops, curse trades, '
        'Recovery fights, guaranteed escape, pre-shop keys, trial decisions and recovery. It supplies no target action labels.',
        f'Training completed **{fresh:,} fresh whole weeks** and **{partial:,} partial-state scenarios**. These counts are separate.',
        'Ordinary continuation won independent selection. Its first final test used 8,192 weeks per policy and budget '
        '(98,304 policy-weeks), and measured 9.7355 → 10.1270 clears at 1,000 mushrooms. That test used the core controller; '
        'the separate table above uses the exact desktop controller and new independent seeds.',
        'Desktop screening compared the old adviser, the new base and five narrow guard variants on 512 events per budget. '
        'The Time Traveler potion rule won screening. Final testing included old, new base and the selected guard '
        '(73,728 policy-weeks). The fallback to the base if the extra guard failed was declared before final data.','',
        '## Which decisions changed','']
    if candidate=='new-cheap_elixir':
        c=result['contrasts']['new-cheap_elixir minus new-base'][-1];lo,hi=c['family_99_ci']
        lines += ['The added rule buys the **one-key instant full heal with Time Traveler at 50% HP or less**, '
            'when the learned action would otherwise reroll. It preserves an existing learned purchase decision. '
            'It requires the configured Time Traveler full-heal mechanic and a legal affordable offer.',
            f'Its incremental equal-budget gain over the new base is {c["mean"]:+.5f} clears/week '
            f'[{lo:+.5f}, {hi:+.5f}], simultaneous 99%.']
    else:
        lines += ['The extra Time Traveler rule did not pass the declared incremental confirmation gate. '
                  'The installed adviser uses the improved base checkpoint and retains the previous runtime corrections.']
    replay=read(diagnostic/'interaction-behavior-2026-10-15.json')['rows'];old,new=replay
    lines += ['', 'An exact replay of 256 already-counted 1,000-mushroom core-policy weeks explains some behavior changes. '
        'This replay is not additional independent evidence.','',
        f'- Partial refills: **{old["counts"]["heal_step"]} → {new["counts"]["heal_step"]}**. '
        'The new policy bought 257 at 10 mushrooms and 157 at 15 mushrooms; later prices were much less common.',
        f'- First-run keys after the room-25 boss: **{old["first_run_room25_keys"]:.2f} → {new["first_run_room25_keys"]:.2f}**. '
        'This remains below the reported 7–10-key player examples; those examples are not a matched comparison.',
        f'- Recovery purchases: **{old["counts"]["buy_recovery"]:,} → {new["counts"]["buy_recovery"]:,}**; '
        f'Elixir purchases: **{old["counts"]["buy_elixir"]:,} → {new["counts"]["buy_elixir"]:,}**.',
        '- The new model protects stacked Recovery in several free-play diagnostic states that the old model mishandled. '
        'It still sometimes rerolls past strong One Hit Wonder and exits guaranteed-escape trials early.','',
        '## Local action tests and remaining uncertainty','',
        'The audit examined 417 visible states at three budgets per checkpoint. It then ran **39,424 paired-branch '
        'remaining-week simulations** from selected positions. One first move is forced; the frozen parent controls '
        'the rest of the 168-hour horizon. Different arms start with matching fresh future seeds. These are deliberately '
        'chosen positions, not natural encounter-frequency estimates.','',
        '- With strong Recovery oldest, One Hit Wonder and a third blessing, taking an untrapped ordinary monster instead '
        'of a blessing door improves the conditional mean by 0.043 clears at budget 0 and 0.066 at budget 1,000. '
        'Both pass the within-budget simultaneous 95% diagnostic check.',
        '- At room 60, 85% HP, four keys and Time Traveler, buying the offered strong One Hit Wonder instead of rerolling '
        'improves the conditional mean by 0.098 at budget 1,000. This passes that diagnostic check.',
        '- Blanket crate skipping, forced Recovery fights, curse-chest replacements and stronger curse-for-key trades '
        'remain inconclusive in these local comparisons. Their confidence intervals include both gains and losses.',
        '- The trial, stacked-Recovery and high-health One Hit guard variants did not win full-week screening. '
        'They remain experiments. Screening alone does not prove them worse, and the completion objective does not '
        'independently optimize trial loot.',
        '- Early key targets, live-game probabilities and high budgets above 1,000 have not been newly validated here. '
        'The previously published high-budget/Ultimate graph retains its original policies and cohort.','',
        '## Validation and saved evidence','',
        'All final events have the declared event indices, legal actions, valid spending identities and deadline termination. '
        'The publishers independently recompute selection, paired contrasts and promotion gates before archiving. '
        'Model/schema compatibility, desktop/native observation parity and all declared interaction fixtures were tested.',
        'An already-running desktop/backend needs a restart to load the new checkpoint. No live-game actions were executed.',
        '', '[Training plan and frozen source](interaction-training-2026-10-15/plan.json), '
        '[training results](interaction-training-2026-10-15/summary.json), '
        '[desktop comparison](interaction-runtime-2026-10-15/summary.json), '
        '[desktop experiment plan](interaction-runtime-2026-10-15/plan.json), '
        '[diagnostic traces](interaction-diagnostics-2026-10-15/manifest.json), '
        '[deployment record](deployment-2026-10-15.json).','']
    (ROOT/'docs/INTERACTION_TRAINING_2026_10_15.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':main()
