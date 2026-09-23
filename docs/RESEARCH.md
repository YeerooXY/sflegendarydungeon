# Legendary Dungeon: research and simulation feasibility

Researched 2026-09-23. Scope: Shakes & Fidget's seasonal Legendary Dungeon, including separate free-to-play and mushroom-efficiency comparisons. This dossier records the public-source investigation that informed the experimental simulator. It is not a measured strategy ranking. For what the current version actually implements, see [MODEL.md](MODEL.md); the design and acceptance criteria below also include future work.

There is enough public information to begin an offline reconstruction. A trustworthy estimate of average completion time also needs probability distributions and validation data that I did not find published in the sources checked.

## What already exists

| Source | Useful contribution | Limitation |
| --- | --- | --- |
| [Official overview](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/57-legendary-dungeon/) | Run structure, death persistence, effects and paid recovery | Not a stochastic model |
| [Official doors](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/282-legendary-dungeon---doors/) | Door requirements and interactions with gems | Spawn weights are generally unspecified |
| [Official chests](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/283-legendary-dungeon---chests/) | Container outcomes and barrel modifiers | No complete probability table |
| [Official regular rooms](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/294-legendary-dungeon---regular-rooms/) | Interactions, skipping, shops, bosses | Outcome frequencies are missing |
| [Official golden rooms](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/284-legendary-dungeons---golden-rooms/) | Special encounters and theme restrictions | Quantitative detail varies |
| [LD Gadget](https://ldgadget.12hp.de/) | Damage and recovery calculator; gem tier list | Does not simulate complete runs |
| [Underlying spreadsheet](https://docs.google.com/spreadsheets/d/14L4sgycMt5w0L8m50Uk3PIdMMJCdrrk3HyIAo9PFme4/edit) | Earlier community estimates | Must not be mixed silently with the current calculator |
| [API research](https://github.com/DasAoD/sf-ld-research/blob/4edd63fd0f628e89e0beb0dba959231c06173767/ld_api_research.md) | Public notes on observed state transitions, actions and effect identifiers | Work in progress; the GitHub snapshot has notes, not the underlying run dataset |
| [S&F Tavern strategy repost](https://www.reddit.com/r/shakesandfidget/comments/16oqd8h/deleted_by_user/) | Candidate policies for keys, barrels, reviving and shop rerolls | Heuristics from 2023; no controlled comparison |
| [SFTools](https://github.com/HafisCZ/sf-tools) | Existing combat simulation infrastructure | The inspected dungeon module simulates ordinary fights, not this 100-room minigame |
| [Mercy SF module documentation](https://mercysf.app/docs/guide/legendary-dungeon) | Demonstrates existing automated room decisions and spending limits | The inspected documentation does not publish an offline LD model or policy benchmark |

The searches covered English and German terminology, public GitHub repository searches, SFTools' repository tree and dungeon module, official help articles, and the calculator's served JavaScript. No reproducible full-run comparison answering the requested questions was found. This does not establish that none exists; Discord-only work, private tools and unindexed projects remain outside this result.

## Evidence quality and conflicts

The calculator reports a September 20, 2026 page modification and thousands of observed fights. Its JavaScript computes damage averages as endpoint midpoints, with a linear survival estimate; neither proves an empirical distribution. [Calculator implementation](https://ldgadget.12hp.de/res/scripts/main.js)

The calculator's final-boss interval is 43.03–52.50% of maximum dungeon HP; the research table gives 33.96–56.63%. Both need validation against the target game version. [Calculator](https://ldgadget.12hp.de/), [research damage table](https://github.com/DasAoD/sf-ld-research/blob/4edd63fd0f628e89e0beb0dba959231c06173767/ld_api_research.md#monster-schadenstabelle--der-max-ld-hp)

Other issues to resolve before treating a simulator as calibrated:

- Damage modifier stacking, rounding and the order of healing, poison, damage and death.
- Effect refresh and replacement, and exactly which counters advance on which actions.
- The interpretation of percentage increases: a relative increase and a percentage-point increase produce different probabilities.
- Current gem availability and event-specific encounter pools.
- Prices and progression of the full-refill option compared with repeated 20-point purchases.
- First-run exceptions versus later runs.

The research notes themselves flag unresolved details. The linked upstream bot PR returned HTTP 404 during this check, so its implementation was not independently verified.

## What the available strategy advice supports

Soul of the Rabbit leads the published tier list. No hours-saved estimate accompanies it. [LD Gadget](https://ldgadget.12hp.de/)

The community strategy post promotes Greasy Healing Stone for frequent low-HP re-entry, and argues for more barrel risk at lower HP. It also proposes limited shop rerolls. These are sensible hypotheses for experiments; they do not establish universal optimality. [Strategy discussion](https://www.reddit.com/r/shakesandfidget/comments/16oqd8h/deleted_by_user/)

The official barrel rule explicitly changes with Diamond of the Time Traveler: barrels then give curses. A baseline policy should therefore skip them in that state. More complicated curse-replacement interactions would need separate testing. [Official chests](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/283-legendary-dungeon---chests/)

My interpretation: the best action depends on remaining HP, effect slots, remaining effect duration, keys, distance to a boss, current gems and the objective. A barrel can waste healing at full HP, replace a valuable blessing, or save an otherwise failing run. Its blessing probability alone does not determine its value.

## Time and mushrooms: quantities we can calculate

The community model uses 24 hours for full recovery. This implies 4 hours 48 minutes for 20 points. [Recovery calculator](https://ldgadget.12hp.de/)

The official overview lists successive 20-point heals at 10, 15 and then 20 mushrooms within a run. [Official overview](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/57-legendary-dungeon/)

| 20-point heal | Mushrooms | Equivalent regeneration minutes per mushroom |
| --- | ---: | ---: |
| First | 10 | 28.8 |
| Second | 15 | 19.2 |
| Later | 20 | 14.4 |

The last column is calculated as 288 minutes divided by the purchase price. It assumes the entire heal is usable, no passive recovery is already available, and the cited recovery model applies. Boss thresholds, death and player availability can change its practical value.

Full refill must be a separate action: recent research reports two first-use UI quotes consistent with a different formula, but no completed purchase validation. Do not infer its complete pricing schedule from those two observations. [Recovery research](https://github.com/DasAoD/sf-ld-research/blob/4edd63fd0f628e89e0beb0dba959231c06173767/ld_api_research.md#heilung-nach-0-hp)

## Simulator design

The proposed implementation is an offline state-transition engine with a batch runner. A graphical recreation is optional and unnecessary for the measurements.

State must include event/theme and rules version, run number, room and interaction stage, current and maximum dungeon HP, keys, held gems, ordered blessings/curses and counters, offered doors, trap indicators, revealed encounter, trial progress, relevant resources, paid-heal history, mushroom budget, recovery clock and player availability.

Implement the following action families, retaining the different costs and stages:

1. Choose an available door; pay keys, resources, HP or an effect where required.
2. Fight or attempt to flee; resolve damage and rewards; handle bosses separately.
3. Interact with or skip containers, barrels, skeletons and optional encounters.
4. Buy a shop effect, exchange a curse for keys, reroll offers or leave.
5. Choose one of the three offered gems after each eligible boss.
6. Resolve golden rooms, timed rooms and theme-specific encounters.
7. Continue or exit trials, including their distinct death behavior.
8. Die, clear temporary effects as applicable, preserve run progress, regenerate, purchase recovery and re-enter.
9. Complete the run, collect rewards and initialize the next run.

These categories are drawn from the [door catalog](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/282-legendary-dungeon---doors/), [room catalog](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/294-legendary-dungeon---regular-rooms/) and [special-room catalog](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/284-legendary-dungeons---golden-rooms/). Their existence is documented; not every numerical transition is known.

Separate the rules engine, random outcome model, strategy, availability schedule and reporting. Strategies may inspect only information available to the player. Future rooms and random outcomes must remain hidden.

Every numerical parameter needs provenance and a status: official rule, community estimate, measured observation, disputed value or explicit assumption. Unknown probabilities should remain missing until supplied. A sensitivity experiment may use hypothetical values, but its output must name those assumptions. The accompanying JSON intentionally cannot generate complete runs.

## Separate comparison objectives

| Experiment | Optimize | Report |
| --- | --- | --- |
| Free-to-play | Completion time with zero mushrooms | Mean, median and 90th percentile; deaths; probability of finishing before the event deadline; completions per event |
| Mushroom efficiency | Spending needed for a specified completion deadline or success probability | Mushrooms per completion, completion probability, elapsed time, reroll and recovery spend separately |

Purely minimizing mushrooms without a deadline can collapse to the zero-spend policy. Use a time/spending tradeoff curve to make the second comparison meaningful. Keep Normal and Ultimate separate; verify Ultimate's fee scope and include it before reporting cost per legendary item.

Candidate experiments:

- **Gems:** compare each offered choice from the same decision state, then let the subsequent strategy adapt to that gem. Do not grant a preferred gem before it could be offered.
- **Barrels:** always skip, always use, HP-threshold policies, and policies that also consider gems, effect slots and distance to a boss.
- **Shops:** spending keys now versus retaining them; different blessing priorities; zero, one or several rerolls; repeatable stopping rules.
- **Recovery:** re-enter at several HP thresholds, with separate boss handling; compare paid steps, full refill and natural recovery.
- **Availability:** immediate return when healed versus realistic login windows. Report active playing time and elapsed wall-clock time separately.

Use paired random scenarios where feasible, with independent random streams for different event types. Report uncertainty intervals and rank sensitivity to disputed parameters. Large simulated sample sizes reduce sampling noise; they cannot correct an inaccurate model.

Do not average only successful runs under an event cutoff. Include unfinished runs through a completion-probability metric and an explicitly defined censored-time measure, or simulate beyond the cutoff for the completion-time distribution and report event success separately.

## Data collection and acceptance criteria

For each observed decision, record: anonymous run ID, game version, theme, run number, sequence number, timestamp, complete visible state, both available choices, chosen action, and the resulting state. For random effects, record the effect identity, strength, duration and source. For payments, record the displayed quote and the actual deducted amount separately.

The high-value missing distributions are:

- Joint door offers and traps, conditional on floor, theme and gems.
- Hidden contents conditional on visible door type.
- Barrel outcomes, including effect identity and strength, conditional on relevant gems.
- Damage samples by encounter and modifiers, plus escape success and key-drop rates.
- Shop offers and rerolls, gem offers, and special-room outcomes.

A log only reveals the chosen door's contents. Do not treat the unchosen door as observed. Likewise, successful fights alone censor the high-damage tail when lethal hits are clipped at zero HP. These sampling effects must be handled explicitly.

Validate deterministic transitions by replaying observations. Then reserve entire runs for validation of room frequencies, HP loss, deaths, recovery and completion outcomes. Only after that should simulator output be described as an estimate of live-game averages. Before calibration, it can still answer conditional questions such as which choice wins across a plausible range of barrel odds.

Recommended implementation order: observation/replay format, deterministic mechanics, calibrated random transitions, then strategy search and charts. The immediate missing input is evidence for transition probabilities, not graphical assets.
