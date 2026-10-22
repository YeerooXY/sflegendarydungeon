"""Make the report and figure from the completed, frozen calibration study."""
import argparse
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def publish(folder):
    study = read(folder / 'summary.json')
    plan = read(folder / 'plan.json')
    verification = read(folder / 'verification.json')
    assert verification['passed'] and len(study['cells']) == 48
    lookup = {(c['case'], c['budget'], c['sleep_until_hour']): c for c in study['cells']}
    contrasts = {(c['old'], c['new'], c['budget'], c['sleep_until_hour']): c for c in study['contrasts']}
    budgets = plan['budgets']

    def metric(case, budget, wake, name='completions'):
        return lookup[case, budget, wake]['summary'][name]

    def mean(case, budget, wake, name='completions'):
        return metric(case, budget, wake, name)['mean']

    def ci(values, decimals=3):
        return f"{values[0]:.{decimals}f} to {values[1]:.{decimals}f}"

    def delta(old, new, budget, wake):
        return contrasts[old, new, budget, wake]['simultaneous_95']

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(2, 1, figsize=(8, 10), sharex=True)
    y = np.arange(len(budgets))
    xmax = max(mean(c, b, w) for c in ('previous', 'updated') for b in budgets for w in (0, 8)) + .7
    for ax, wake, title in zip(axes, (0, 8), ('Always available', 'No play from midnight to 08:00')):
        for case, offset, color, marker, label in (
            ('previous', -.13, '#526777', 'o', 'Previous values'),
            ('updated', .13, '#007b60', 'D', '85% keys + revised damage modifiers'),
        ):
            values = [metric(case, b, wake) for b in budgets]
            means = np.array([v['mean'] for v in values])
            half = np.array([v['ci'][1] - v['mean'] for v in values])
            ax.errorbar(means, y + offset, xerr=half, fmt=marker, color=color,
                        markersize=6, capsize=3, elinewidth=1.4, label=label)
        ax.set_yticks(y, [f'{b:,}' for b in budgets])
        ax.invert_yaxis()
        ax.set_title(title, loc='left', fontsize=13, fontweight='bold', pad=12)
        ax.set_ylabel('Weekly mushroom budget')
        ax.set_xlim(0, xmax)
        ax.grid(axis='x', color='#dfe5e8', linewidth=.7)
        ax.set_axisbelow(True)
        ax.tick_params(axis='both', length=0, pad=8)
        ax.tick_params(axis='x', labelbottom=True)
        ax.set_xlabel('Average completed runs in seven days')
    fig.suptitle('More keys improve weekly completions', fontsize=18, fontweight='bold', y=.98)
    fig.text(.10, .942, 'Same trained policy · 2,048 weeks per estimate · random eligible stones', fontsize=10)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower left', bbox_to_anchor=(.085, .045), frameon=False, fontsize=10)
    fig.text(.10, .025, 'Bars: 95% Monte Carlo confidence intervals. Game-mechanic uncertainty is additional.', fontsize=9)
    fig.subplots_adjust(left=.13, right=.97, top=.89, bottom=.15, hspace=.30)
    fig.savefig(folder / 'weekly-completions.png', dpi=180, facecolor='white')
    fig.savefig(folder / 'weekly-completions.svg', facecolor='white')
    plt.close(fig)

    lines = [
        '# Key and damage calibration: weekly simulation, 18 October 2026',
        '',
        'The new provisional key rates increase completions with the current trained policy. '
        'This comparison changes the mechanics while keeping the policy fixed; it does not establish an optimal strategy.',
        '',
        f"**{study['total_simulated_weeks']:,} simulated weeks:** four scenarios, six weekly mushroom caps, "
        'two play schedules and 2,048 events per cell. Each scenario receives the same event seeds. '
        'Events last 168 hours and start at 17:00. The sleep schedule prevents actions from midnight to 08:00; '
        'health still recovers overnight. All cases use random eligible stones and ordinary Legendary Dungeon.',
        '',
        '## Average completed runs',
        '',
        '| Weekly cap | Previous, continuous | Updated, continuous | Previous, sleep 00–08 | Updated, sleep 00–08 |',
        '|---:|---:|---:|---:|---:|',
    ]
    for b in budgets:
        lines.append(f"| {b:,} | {mean('previous',b,0):.3f} | {mean('updated',b,0):.3f} | "
                     f"{mean('previous',b,8):.3f} | {mean('updated',b,8):.3f} |")
    lines += ['', 'Updated means 85% base key chance, 70% with Spying alone, and the revised damage-stone scope. '
              'The old matched control uses the former 50% base / 35% Spying placeholder. '
              'The main updated scenario caps Lodestone at 100%, including the Spying + Lodestone combination. '
              'This additive modifier interpretation is provisional.', '',
              '![Weekly completions](key-calibration-2026-10-18/weekly-completions.png)', '',
              '## Gain and simulation uncertainty', '',
              'These are paired differences, with simultaneous 95% confidence intervals corrected across '
              'all 60 predeclared comparisons. They describe simulation sampling error under each model; '
              'they do not include uncertainty about the real game mechanics.', '',
              '| Cap | Continuous gain | 95% interval | Sleep-schedule gain | 95% interval |',
              '|---:|---:|---|---:|---|']
    for b in budgets:
        a, c = delta('previous','updated',b,0), delta('previous','updated',b,8)
        lines.append(f"| {b:,} | {a['mean']:+.3f} | {ci(a['ci'])} | {c['mean']:+.3f} | {ci(c['ci'])} |")

    lines += ['', '## Separate the key and damage changes', '',
              '| Cap | Old values | Only keys changed | Keys + damage scope | Damage-only difference, simultaneous 95% interval |',
              '|---:|---:|---:|---:|---|']
    for b in budgets:
        d = delta('keys85','updated',b,0)
        lines.append(f"| {b:,} | {mean('previous',b,0):.3f} | {mean('keys85',b,0):.3f} | "
                     f"{mean('updated',b,0):.3f} | {d['mean']:+.3f} ({ci(d['ci'])}) |")
    lines += ['', 'This table uses continuous play. The revised scope applies Hero, Bull and Deceit reductions '
              'and Rabbit/Devil penalties to Fight only. Pearl and Hick still affect failed Escape only; '
              'Broken Armor still affects both. Boss damage is unchanged. '
              'The source and the directly observed failed escape are in the '
              '[LD Gadget audit](LDGADGET_AUDIT_2026_10_18.md).', '',
              '## Do more keys reach the shop?', '',
              'The following milestones refer to the first event run, under continuous play. '
              'The free room-five shop is excluded from the paid-shop measurement.', '',
              '| Cap | Keys after boss 25, old → updated | Keys at first paid shop, old → updated | At least four shop keys, old → updated |',
              '|---:|---:|---:|---:|']
    for b in budgets:
        boss = [mean(c,b,0,'first_boss25_keys') for c in ('previous','updated')]
        shop = [mean(c,b,0,'first_paid_shop_keys') for c in ('previous','updated')]
        enough = [mean(c,b,0,'first_paid_shop_at_least_four_keys') for c in ('previous','updated')]
        lines.append(f'| {b:,} | {boss[0]:.2f} → {boss[1]:.2f} | {shop[0]:.2f} → {shop[1]:.2f} | '
                     f'{enough[0]:.1%} → {enough[1]:.1%} |')

    lines += ['', '## Spying sensitivity', '',
              'The footage audit found 28/48 key drops with Spying: 58.3%, nominal Wilson 95% interval '
              '44.3–71.2%. We also test that point estimate, keeping the base chance at 85%. '
              'This is an average under the current stone choices, not a conclusion that the penalty '
              'is unimportant in every Spying run. This sensitivity is not a substitute for estimating all uncertain inputs. '
              'It assumes an additive 26.6667-point Spying penalty, so Spying + Lodestone becomes 88.3333%; '
              'that joint-stone rate is an extrapolation, not measured evidence.', '',
              '| Cap | Continuous: Spying 70% | Continuous: Spying 58.3% | Difference, simultaneous 95% interval | Sleep: 70% / 58.3% |',
              '|---:|---:|---:|---|---:|']
    for b in budgets:
        d = delta('updated','spying_observed',b,0)
        lines.append(f"| {b:,} | {mean('updated',b,0):.3f} | {mean('spying_observed',b,0):.3f} | "
                     f"{d['mean']:+.3f} ({ci(d['ci'])}) | {mean('updated',b,8):.3f} / {mean('spying_observed',b,8):.3f} |")
    lines += ['', 'The unmodified fight audit found 180/209 drops (86.1%; nominal Wilson 95% interval '
              '80.8–90.2%). The 85% input is a plausible rounded working estimate, not a verified server constant. '
              'The engine also reuses this base key chance for other won non-boss fights; the footage audit '
              'does not establish separate rates for mimics, awakened soldiers or arena monsters. '
              'Both audits contain selected encounters from a limited number of runs; those nominal intervals '
              'do not address clustering or selection bias. See the '
              '[base-rate audit](KEY_DROP_EXTENSION_2026_10_18.md) and '
              '[Spying audit](SPYING_KEY_AUDIT_2026_10_18.md).', '',
              '## Time, spending and rewards', '',
              'Continuous-play results. First-clear time is conditioned on completing at least once. '
              'Spend is the average used within the cap, not an extra charge.', '',
              '| Cap | First clear hours, old → updated | Updated spend: total / recovery / rerolls | Updated legendary items per week |',
              '|---:|---:|---:|---:|']
    for b in budgets:
        t = [mean(c,b,0,'first_completion_hours_among_completed') for c in ('previous','updated')]
        spend = [mean('updated',b,0,k) for k in ('mushrooms','recovery_mushrooms','reroll_mushrooms')]
        lines.append(f"| {b:,} | {t[0]:.2f} → {t[1]:.2f} | {' / '.join(f'{v:.2f}' for v in spend)} | "
                     f"{mean('updated',b,0,'legendaries'):.3f} |")
    lines += ['', f"At the 100-mushroom cap, recovery spending falls from {mean('previous',100,0,'recovery_mushrooms'):.2f} "
              f"to {mean('updated',100,0,'recovery_mushrooms'):.2f}, while rerolls rise from "
              f"{mean('previous',100,0,'reroll_mushrooms'):.2f} to {mean('updated',100,0,'reroll_mushrooms'):.2f}. "
              'These are outcomes of the frozen policy under the different mechanics, not new spending rules.', '',
              'The tier-five trial chest already uses weights **19 gold / 20 regular item / 6 epic / 3 legendary** '
              '(39.58% / 41.67% / 12.50% / 6.25%). Those reward odds and the five trial base-damage '
              'multipliers stay the same in every arm. The revised stone-modifier scope also applies to '
              'trial combat. The study interprets “new trial values” as the latest provisional '
              'key and damage values, rather than introducing a new arena reward table. '
              'Key Moment retains its separate 70% chance of two keys.', '',
              '## Policy, configuration and validation', '',
              'The actor and its current interaction guards are frozen. The optional runtime final-stone '
              'rollout adviser is excluded in every arm. No retraining or policy selection occurred. '
              'More training under the calibrated mechanics may change the strategy, including opening fights '
              'and stone priorities; these results do not prove that the current policy is optimal.', '',
              'The shipped profile and the running app were not changed. The experiment adds two optional '
              'profile fields: `spying_key_penalty` (default `0.15`, absolute percentage-point subtraction) and '
              '`fight_only_damage_stones` (default `false`, preserving the prior behavior). '
              'The archived `updated.profile` sets `key_chance=0.85` and `fight_only_damage_stones=true`.', '',
              '- Native mechanics, VOD calibration, RL environment and daily schedule suites: **4/4 passed**.',
              '- Python practical-policy tests: **6/6 passed**.',
              '- Old/default behavior exactly matches the previous DLL on 16 original events at each of '
              '0, 100 and 1,000 mushrooms: **97,296 compared vector decisions**.',
              '- Smoke run: **64 weeks**, covering all four scenarios and both schedules.',
              '- Main run validates legal actions, budget accounting, event length, sleep windows, paired '
              'event IDs and unchanged source/artifact hashes.', '',
              'Artifacts: [plan and hashes](key-calibration-2026-10-18/plan.json), '
              '[all estimates and intervals](key-calibration-2026-10-18/summary.json), '
              '[verification](key-calibration-2026-10-18/validation-report.json), '
              '[figure](key-calibration-2026-10-18/weekly-completions.png). '
              'The study directory also retains the frozen sources, DLL, checkpoint, profiles and all '
              'per-event records as compressed JSON.', '',
              'Command used for this study, after building the calibration DLL in '
              '`build-calibration-20261018/Release`:', '',
              '```powershell',
              '.\\out\\rl-env\\Scripts\\python.exe tools/benchmark_key_calibration_20261018.py --output docs/key-calibration-2026-10-18 --events 2048 --workers 4',
              '```', '',
              'The seed audit deliberately prevents reusing the registered cohort as a fresh study. '
              'Use the archived plan and sources for replay; select a new documented root seed for a new '
              'independent experiment.', '',
    ]
    report = ROOT / 'docs/KEY_CALIBRATION_SIMULATION_2026_10_18.md'
    report.write_text('\n'.join(lines), encoding='utf-8')
    parity = read(ROOT / 'out/key-calibration-validation-2026-10-18/baseline-parity.json')
    validations = {**verification, 'native_suites': {'passed': 4, 'total': 4},
                   'python_practical_tests': {'passed': 6, 'total': 6},
                   'smoke': read(ROOT / 'out/key-calibration-smoke-2026-10-18/verification.json'),
                   'legacy_parity': parity,
                   'policy_retrained': False, 'shipped_profile_changed': False,
                   'report': report.relative_to(ROOT).as_posix()}
    (folder / 'validation-report.json').write_text(json.dumps(validations, indent=2) + '\n', encoding='utf-8')
    shutil.copy2(ROOT / 'build-calibration-20261018/Testing/Temporary/LastTest.log', folder / 'native-tests.log')
    print(json.dumps({'report': str(report), 'figure': str(folder / 'weekly-completions.png'),
                      'weeks': study['total_simulated_weeks']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, default=ROOT / 'docs/key-calibration-2026-10-18')
    publish(parser.parse_args().study.resolve())
