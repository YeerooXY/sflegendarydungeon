# S&F Legendary Dungeon simulator

An independent C++20 research simulator for Shakes & Fidget's Legendary Dungeon.
Compare gem preferences, barrel decisions, shop rerolls, recovery thresholds and
mushroom budgets using reproducible Monte Carlo experiments.

**Version 0.6.11 is an experimental model, not a validated reconstruction of the live
game.** The [VOD-informed defaults](docs/VOD_CALIBRATION_2026_10_08.md) incorporate
reviewed room pairs, traps, conditional mystery contents and rewards. Remaining
gaps use explicit assumptions. Results must not be presented as measured game
averages or an optimal stone ranking.

Version 0.6.11 applies newly acquired Recovery on room exit, prevents combat
curses on One Hit victories, and limits paid HP refills to recovery. The
[October 14 evidence record](docs/RECOVERY_TIMING_2026_10_14.md) distinguishes
footage observations from player corrections. Historical policy benchmarks
retain their original engine and do not establish performance on this version.

Version 0.6.9 applies global combat modifiers to both fights and failed escapes,
heals before poison at room exit, and fixes floating-point survival at zero HP.
The visual lab shows three blessing slots plus three curse slots, with scene
controls, keyboard play, moving keys and a single recommended adviser. With the
RL dependencies installed, it now uses the frozen neural checkpoint for ordinary
recovery, doors and stones.

The [latest interaction study](docs/INTERACTION_TRAINING_2026_10_15.md) adds
**33.55 million decisions** on engine 0.6.11. The adviser tested there averaged
**3.717 / 6.756 / 10.173** clears per week at **100 / 500 / 1,000** mushrooms,
versus **3.569 / 6.516 / 9.774** for its parent on the same fresh events.
The desktop comparison includes its actual recommendation corrections. The
overall gain and the 20–1,000-mushroom gains pass simultaneous 99% checks;
the zero-mushroom mean dips slightly and its difference remains inconclusive.
The new policy uses cheap partial revives more often. A separately confirmed
correction takes the one-key Time Traveler full heal at 50% HP or less when
the policy would otherwise reroll. The [October 14 study](docs/RL_CONTINUATION_2026_10_14.md)
retains its original engine 0.6.10 results.

The [subsequent hungry-chest and early-key audit](docs/HUNGRY_CHESTS_AND_EARLY_KEYS_2026_10_15.md)
adds a correction to collect already-paid hungry chests. Forced early key farming
raised first-boss key balances but lowered completion averages in screening, so
that intervention remains experimental. The preceding benchmark predates the
hungry-chest correction.

The [mushroom-budget and Ultimate study](docs/budget-ultimate-2026-10-14/RESULTS.md)
adds a separate **16.78-million-decision** high-budget specialist. The frozen
policies average **9.68 / 30.52 / 51.18 / 84.31** normal clears per week at
**1,000 / 5,000 / 10,000 / 20,000** mushrooms on engine **0.6.11**, across
**20,480 fresh simulated weeks**. Training and pilot selection used 0.6.10;
the final test transferred the policies and entry allocations unchanged.
The graphs include 95% mean confidence intervals, diminishing returns, gold,
epics and Ultimate's **650-mushroom fee per run**. Normal play yields more
legendary items than the tested Ultimate plans, including the requested
triple-item scenario. The desktop default is unchanged; this is not a proof
of optimality. [Open the graphs](docs/budget-ultimate-2026-10-14/index.html)
or [download the results](docs/budget-ultimate-2026-10-14/results.csv).

The [Windows desktop preview](desktop/README.md) hosts the original browser game
with separate login profiles and a simulator Practice view. Practice includes
the trained adviser, Watch play and a prominent mimic warning before interaction.
The original-game mimic overlay is version-checked and tested with synthetic
responses; live dungeon validation is pending. Desktop live recommendations and
background autoplay are not connected yet.

The default adviser optimizes freely within the event budget. Shop rerolls,
key spending and recovery follow the tested policy; the older fixed shop and
final-boss rules remain separate research comparisons. The browser and bounded
live adapter share the same checkpoint. Authentication and full live-game
coverage remain unfinished; simulator gains do not validate live deployment.

The [runtime benchmark](docs/PERFORMANCE_2026_10_11.md) measures **3.16x faster**
batched simulation with the neural policy on GPU, **1.47x on CPU**, and **1.48x**
for the native vector environment plus its Python adapter. Every recorded
output matches across the old and optimized implementations on the fixed trace.
These are computation timings, not a claim about total PPO training duration.
The [further runtime work](docs/PERFORMANCE_2026_10_12.md) gives another measured
**9.2% improvement on a shop-constrained GPU rollout workload**, with exact
output and optimizer parity. This figure does not measure the unrestricted
policy or establish a combined speedup with the older benchmark.
The [selected unrestricted policy's own benchmark](docs/PERFORMANCE_UNRESTRICTED_2026_10_12.md)
shows essentially unchanged rollout speed, again with exact output parity.

The [effect-slot study](docs/EFFECT_SLOTS_2026_10_11.md) and its
[exploration extension](docs/EFFECT_EXPLORATION_2026_10_11.md) add **41.94 million
PPO decisions** and **294,912 paired-action trajectories**. They test preserving
valuable blessings, replacing harmful curses, and the difference between long
effects and ones about to expire. Full-week policy comparisons and remaining
clear-time probes are reported separately.
The curriculum candidate passes the second study's numerical gate, but a browser
test caught an early-stage trap choice that conflicts with the requested play.
The [acceptance follow-up](docs/EARLY_TRAP_GUARD_2026_10_11.md) tests it with that
preference restored on another 36,864 simulated policy-weeks. Its gain is
inconclusive, so that study retained the previous shop-aware checkpoint.

The [paired-action learning study](docs/EFFECT_RESIDUAL_2026_10_12.md) and
[focused refinement](docs/EFFECT_BOUNDARIES_2026_10_12.md) then trained eight
small action-value candidates using **329,856 simulated continuations**.
The refinement learns several of the local exceptions, but none of the
candidates beat that study's adviser in fresh weekly screening. The closest
result was effectively tied, so it did not select a replacement.

The earlier [continued-learning study](docs/RL_CURRENT_2026_10_11.md) adds **67.11 million
PPO decisions** on the corrected engine, then compares **61,440 untouched
simulated weeks**. Its selected shop-aware checkpoint averaged **3.3052 /
5.9575 / 9.2153** weekly clears at **100 / 500 / 1,000** mushrooms, compared with
**3.2686 / 5.8647 / 9.0356** for its predecessor on the same events. The overall
gain and the higher-budget gains pass the study's confidence checks; the
100-mushroom gain remains uncertain individually.

The constrained comparison retains five refresh credits per paid shop, early
purchases of useful offers and a safe final-boss wait after two partial refills
when time permits. Stone choices remain learned. The unrestricted learner
achieves more clears; both versions and the earlier
[key-saving experiments](docs/PRACTICAL_TACTICS_2026_10_11.md) are reported separately.

The [escape and loot follow-up](docs/ESCAPE_LOOT_2026_10_11.md) compares 122,880
fresh simulated weeks. Searching sleeping soldiers under guaranteed effective
escape increases modeled gold without a detectable completion change. Broader
forced escape routing and a Pendant fallback do not improve completions. Gold
is reported at the requested **10 million per relative reward unit**, separately
from actual account balances. A [bounded automatic live pilot](docs/LIVE_STUDY.md)
now connects the learned policy to supported client screens; it is tested
offline and still requires live validation. The application session layer now
supports recommendation previews and explicit opt-in autoplay over an already
authenticated driver. It is not yet a public login or full live-play service.

Version 0.6.8 makes rooms 1–5 trap-free on every run and prevents empty
containers throughout the first event run. Otherwise-empty first-run rewards
provisionally become gold, preserving the current item/blessing odds. The
visual lab now advances through results and empty rooms without confirmation
clicks. Earlier reported averages use the old mechanics.

The [corrected-engine player-idea comparison](docs/PLAYER_IDEAS_2026_10_11.md)
evaluates 110,592 fresh simulated weeks with the same frozen learner. It tests
21% recovery, extra pre-boss health, Recovery fights, guaranteed escapes, key
saving and Pendant priority separately and together. The full bundle reduces
weekly clears; the key rule improves shop affordability but loses completion
speed. These results include both mechanic fixes and keep free-to-play and
mushroom budgets separate. They do not replace the interactive viewer adviser.

Version 0.6.6 excludes all previously offered stones until the run ends. The
[controlled comparison](docs/STONE_EXCLUSION_2026_10_11.md) finds about 2.7–3.2%
more weekly completions with the existing neural policy; rejected-stone history
also persists in saved progress.

Version 0.6.7 exposes that already-seen stone history to the learner, increasing
its observation vector from 437 to 460 inputs. Existing neural checkpoints need
their archived library or an explicit, verified input migration. The action
menu and game mechanics are unchanged from 0.6.6.

The [offered-history training study](docs/RL_HISTORY_2026_10_11.md) adds 83.89
million PPO decisions. On 5,000 fresh matched weeks per budget, its preselected
overall policy reaches **2.8218 / 3.0088 / 3.2090 / 3.5622 / 6.4316** completions
at caps **0 / 20 / 50 / 100 / 500**, improving the previous policy by 1.4–2.7%
under the same corrected mechanics. The report includes uncertainty, unsuccessful
branches, indexed outcomes, figures and the default checkpoint.

The [high-budget shop and arena study](docs/SHOP_ARENA_2026_10_11.md) adds a
1,000-mushroom specialist and tests strong-blessing targets, five-round arena
pushes and cheap Greasy revives. It separates completion counts from legendary
yield and checks both 10% and 6.25% final-chest legendary assumptions. All
116,736 evaluated weeks and the separate specialist checkpoint are archived.

The C++ simulator is MIT licensed, has no runtime dependencies and makes no
game-server requests. Optional Python study tools can record observations and
run a bounded live pilot with a separately configured local login adapter.
The repository contains independently written code and no game artwork or assets.
Shakes & Fidget belongs to its respective owners; this project is unaffiliated.

## What works

- A state machine for 100 rooms, bosses, keys, traps, effects, gem offers, shops,
  containers, special encounters, trials, death and recovery.
- Separate damage models for fighting, failed escape and bosses; ordered effect
  slots with room, fight, trap and door counters.
- Hard mushroom budgets, paid recovery, shop rerolls and a simple login schedule.
- Rolling shop-refresh allowances, a minimum-key gate, survival-aware combat
  and optional early key targets, with separate experiment results.
- Multithreaded simulations with seeded randomness and identical reports across
  thread counts on the same build.
- JSON summaries, completion-probability intervals, deadline-aware time metrics
  and deterministic action traces with replay verification.
- Six policy sweeps: barrels, gems, budgets, revival thresholds, reroll limits
  and shop strategies, including hunting for eight-room One Hit Wonder.
- Forecasts from entered progress, including observed doors, walls, shop/gem
  offers, recovery, active effects and previous healing purchases.
- Repeated-run events with a shared deadline and budget, recovery-only refills,
  a visible-state agent protocol, and policies trained by population search.
- A batched C++ learning interface, optional masked PPO trainer, and independent
  future rollouts for comparing decisions from the same observed position.
- A selectable strategic heuristic for door pairs, combat, effects, shops and
  refills, with separate free-to-play and mushroom-budget comparisons.
- A local browser lab for manual play, policy playback, progress entry, shop
  inspection, undo, saved decisions and strategy comparisons. Optional game art
  is read from a local installation and excluded from the repository.
- A private SQLite study journal, per-character/event spending caps, restart
  recovery, anonymous JSONL/CSV exports and a UI-reviewed live pilot driver.
- An optional VOD research pipeline for selected Twitch/YouTube downloads,
  dungeon screening, door-candidate review and conditional frequency reports.

Many numerical rules and some special-room transitions remain approximations.
Read [the model and limitations](docs/MODEL.md) before interpreting output.
The [mechanics audit](docs/MECHANICS.md) lists every modeled blessing, curse and
golden-room variant, including what remains uncertain or simplified.
The [live study guide](docs/LIVE_STUDY.md) covers data collection and controller
setup. Its live menus still need event-time validation; it is not an unattended
dungeon autoplayer.
The [VOD research guide](docs/VOD_RESEARCH.md) covers the supplied streams,
efficient local screening and the review needed before estimating game odds.
The [new VOD search](docs/VOD_HUNT_2026_10_12.md) adds 35 preview-confirmed leads
and 5 hours 29 minutes of downloaded footage from seven sources, with a local
gallery for reviewing the extracted candidates.
The [latest wheel review](docs/DESTINY_FOLLOWUP_2026_10_12.md) separates actual
spins from hover animations and previous-room rewards. The
[0.6.10 comparison](docs/SHOP_RESERVE_2026_10_12.md) evaluates the corrected
opening/shop/Pendant mechanics and the requested key-reserve rules.

## Play the simulator

After building, run `python tools/visual_lab.py` with the RL Python environment and open
[the local lab](http://127.0.0.1:8787). See [setup and local artwork](docs/VISUAL_LAB.md).
Without the optional neural dependencies, manual play and a labeled rule fallback
remain available.
The current profile uses 40% base escape and assumes one compulsory shop per stage.
Shops are sampled uniformly within rooms 30–40, 61–70 and 80–85 in stages two
through four. Stage one stays at room 5 on the first event run and uniformly
within rooms 6–24 on later runs. The [shop-position audit](docs/VOD_SHOP_TIMING_2026_10_09.md)
supports using the narrower later-stage ranges as working estimates; it does
not establish hard bounds or uniform odds, and its stage-one quota conflict
remains unresolved.
[Updated comparisons](docs/CURRENT_COMPARISON.md) supersede the earlier
19.38-hour result; [the official tutorial](docs/OFFICIAL_VIDEO.md) supplies the
escape evidence and supports testing early key collection.
The [shop-refresh experiments](docs/POLICY_REFRESH_2026_10_09.md) compare
allowances of two, five and eight mushrooms per shop under a shared event budget.

The [balanced stone comparison](docs/balanced-gems-2026-10-09/report.html)
gives all 20 eligible stones equal search effort, adapting combat and shop rules
while preserving random offers. It includes 42,240 candidate evaluations and
20,000 fresh final events per frozen case, with separate 0/20/50/100/500 budgets.
Selected-policy averages are 2.3854 / 2.50495 / 2.64050 / 2.90630 / 5.06575
completions per week. Avoiding Bull/Hick has a negligible effect on the selected
policies; it does not establish two universally wrong choices. The
[data and paired intervals](docs/balanced-gems-2026-10-09/results.json) and
[reproduction archive](docs/balanced-gems-2026-10-09/reproduction.zip) retain the
full comparison. This finite search does not enumerate all priority orders.

The [preceding rule-strategy comparison](docs/RULE_STRATEGY_2026_10_09.md) evaluates
trained stone charts and explicit player rules on 20,000 fresh events per case.
Use [the stone guide](http://127.0.0.1:8787/rule-guide.html) after starting the lab
to compare your actual offers. Detailed scoring reaches 2.39 free completions
per week; simpler rules reach 2.36 under the current assumptions. The guide
keeps their charts and results separate. [Search and reproduction commands](docs/RULE_SEARCH_METHOD.md)
cover both mushroom budgets and sensitivity tests.

The [reinforcement-learning workflow](docs/RL_TRAINING.md) extends this to
state-dependent decisions across the entire event. Training actions and complete
events are counted separately; validation chooses checkpoints and untouched
events compare their performance. The [first full-state study](docs/RL_STUDY_2026_10_09.md)
trained on 31.46 million PPO decisions and improved fresh-event means from
2.401 to 2.535 free completions/week, and from 5.0535 to 5.584 at a 500-mushroom
cap. The [interactive report](docs/rl-study-2026-10-09/report.html) separates all
five budgets. Its small extracted trees lost substantial performance and are
not replacements for the neural policy or existing player guide.

The [fine-tuning follow-up](docs/RL_FINETUNE_2026_10_10.md) adds 52.43 million
decisions across candidate continuations. On 5,000 new matched events per
budget, its selected policy improves free completions from 2.5230 to 2.6978,
and the 500-mushroom case from 5.5876 to 6.1344. All five budgets improve within
the unchanged model. The [updated interactive report](docs/rl-finetune-2026-10-10/report.html)
provides the paired results, spending and downloadable checkpoints.

## Build

Use CMake 3.20+ and a C++20 compiler with `std::jthread` support. GCC, Clang and
MSVC are supported; there are no packages to download during configuration.

```sh
git clone https://github.com/YeerooXY/sflegendarydungeon.git
cd sflegendarydungeon
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
ctest --test-dir build -C Release --output-on-failure
```

On Linux/macOS the executable is `build/sfld`. Visual Studio generators place it
at `build/Release/sfld.exe`. Run examples from the repository root or provide an
explicit `--profile` path. Windows users with Visual Studio but no CMake on PATH
can run `./tools/build.ps1`, which locates the bundled CMake and runs the tests.

## Continue from your current position

```sh
build/sfld state-template --output my-run.state
# Edit the example to match your current room, HP, keys, stones and effects.
build/sfld audit --state my-run.state
build/sfld simulate --state my-run.state --allow-assumptions --runs 100000 --deadline-hours 48 --budget 0 --output results/from-here.json
```

With `--state`, the deadline and mushroom budget apply **from that position
onward**. Previous healing purchases affect the next price. Known choices stay
fixed; unknown future rooms are sampled. The [progress guide](docs/PROGRESS.md)
includes examples for recovery, shops and the three stones currently offered.
First/later-run stone pools use [the maintainer's observations](docs/GEM_POOLS.md)
and remain configurable; Rusty's later-only restriction is tentative.

## Seven-day agent experiments

```sh
build/sfld event --allow-assumptions --budget 0 --deadline-hours 168 --runs 1000
build/sfld event --allow-assumptions --budget 500 --deadline-hours 168 --runs 1000
build/sfld event --allow-assumptions --strategy strategic --budget 500 --deadline-hours 168 --runs 1000
build/sfld train --allow-assumptions --budget 500 --save-policy out/paid.policy
build/sfld agent --allow-assumptions --budget 500
```

`event` maximizes repeated completions within one event budget. `train` learns
action scores from scratch and evaluates separate seeds. `agent` lets external
controllers choose doors, fights, stones, refills and shop actions through stdin
and JSON observations. [Experiment instructions](docs/EXPERIMENTS.md) describe
the protocol, fairness limits, metrics and sensitivity settings. Give fresh
participants [the rules](docs/AGENT_RULES.md), without an existing strategy.
The [recorded pilot](docs/EVENT_PILOT.md) includes frozen AI-authored controllers,
learned coefficients, separate budget comparisons and damage sensitivity results.
It uses historical engine 0.4.0. Version 0.4.1 fixes trial evasion and adds the
reported room-51 entrance; the older results have not been re-evaluated under
these corrections. See the pilot's pinned commit for reproduction.

The [working-value review](docs/PLACEHOLDER_VALUES.md) lists the proposed numeric
assumptions and remaining generator gaps, with 40,000-event baselines on engines
0.4.2 and 0.4.3. Their linked reports preserve the results, commands and full
scenario snapshots. Version 0.4.3 applies the trap, arena, Destiny, fixed opening
shop and conditional mystery corrections. These are provisional adaptive-policy
runs, not a new trained-agent study.

Version 0.4.4 allows empty ordinary key rooms (10% placeholder) and adds
`--strategy strategic`. In [176,000 simulated events](docs/STRATEGY_PILOT.md),
this handwritten policy improved on the previous baseline across the tested
budgets and probability variations. The report separates free-to-play time,
completion counts and mushroom efficiency, and includes the scenario snapshots
and reproduction command. These results remain conditional on the model.

## Free-to-play completion time

Zero mushrooms is an enforced budget, including recovery and shop rerolls.

```sh
build/sfld audit
build/sfld compare --allow-assumptions --sweep barrels --budget 0 --runs 100000 --threads 8 --seed 42 --output results/f2p-barrels.json
build/sfld compare --allow-assumptions --sweep gems --budget 0 --runs 100000 --output results/f2p-gems.json
build/sfld compare --allow-assumptions --sweep revive --budget 0 --login-hours 8 --runs 100000 --output results/f2p-recovery.json
```

`--allow-assumptions` acknowledges the synthetic scenario. Each report records
that status and its profile fingerprint. It does not certify the model.

The default horizon is 240 hours, a chosen experiment setting rather than the
duration of a particular event. Set `--deadline-hours` to your intended horizon.
Unfinished runs contribute the entire horizon to the restricted mean; they are
also counted in the completion probability. Mean, median and p90 among completed
runs are explicitly named `completed_only_*`. Those values alone can make a
failure-prone strategy look deceptively fast.

A gem sweep compares **preferences when a gem is actually offered**, against a
baseline priority list. It does not grant a stone at the start, force offers, or
estimate the isolated causal value of a stone in every decision state.

## Mushroom efficiency

Use a common deadline and compare completion probability, elapsed time and spend.
Minimizing mushrooms without any time constraint simply favors waiting.

```sh
build/sfld compare --allow-assumptions --sweep budgets --budgets 0,10,25,50,100 --deadline-hours 72 --runs 100000 --output results/mushroom-budgets.json
build/sfld compare --allow-assumptions --sweep rerolls --budget 50 --deadline-hours 72 --runs 100000 --output results/mushroom-rerolls.json
```

Recovery and reroll spending are reported separately. The
`total_mushrooms_divided_by_completions` metric includes spending on unsuccessful
attempts. It is a cohort ratio, not the expected expense of an unlimited retry
strategy, and it excludes Ultimate entry fees. The shipped recovery policy is a
baseline heuristic; these sweeps are not a proof of global optimality.

To compare reroll strategies while healing only through waiting:

```sh
build/sfld compare --allow-assumptions --sweep shops --rerolls 10 --budget 30 --recovery-budget 0 --deadline-hours 72 --runs 100000 --output results/shop-strategies.json
build/sfld simulate --state examples/reroll-shop.state --allow-assumptions --shop-policy one-hit-8 --rerolls 10 --budget 10 --recovery-budget 0 --runs 100000 --output results/hunt-one-hit.json
```

Each shop offers two different blessings. Weak/strong chances are configurable;
the shipped 50/50 strength split is a player estimate. `one-hit-8` can reject a
four-room version while searching for an eight-room one. It stops at the cap or
budget, then buys the best useful current offer or leaves. Four keys are normally
needed for the strong version; first-run first-floor freebies are supported.
The [shop guide](docs/SHOPS.md) explains these choices and their assumptions.

## Inspect a run

```sh
build/sfld simulate --allow-assumptions --runs 1 --seed 42 --trace results/run.tsv --output results/run.json
build/sfld replay --allow-assumptions --trace results/run.tsv
build/sfld --help
```

Replay verifies the simulator's own action sequence and state digests. It does
not yet import or validate recordings from the live game. Trace compatibility is
limited to the same engine version, profile and floating-point environment.

## Research and contributions

The [expanded crate/chest review](docs/VOD_CONTAINER_CENSUS_2026_10_09.md)
adds 587 native-footage reviews, separates Greed and chest styles, and reports
blessing drops and overlapping rewards with their uncertainty.

[Public-source research](docs/RESEARCH.md) explains what already exists and what
was not found. The most useful next contributions are anonymized observations of
door offers, barrel outcomes, damage, effect timing and actual full-refill
purchases. [Data collection](docs/DATA.md) explains the necessary fields and
sampling limitations. [Contributing](CONTRIBUTING.md) describes the code layout
and validation requirements.

An [initial local benchmark](docs/BENCHMARK.md) completed one million synthetic
runs in about 2.8 seconds with eight workers on a Ryzen 9 9950X.

The [evidence inventory](docs/mechanics-evidence.json) deliberately leaves unknown
live probabilities as `null`. The runnable [synthetic profile](profiles/synthetic.profile)
is a separate hypothetical scenario. Changing either requires preserving that
distinction.
