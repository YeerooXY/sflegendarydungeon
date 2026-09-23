# Model v0.1

This document describes the implemented engine. It is not a claim that all of
these transitions match the server. Sources were inspected on 2026-09-23; the
research links and disagreements are recorded in [RESEARCH.md](RESEARCH.md).

## Evidence boundary

| Class | Examples | Treatment |
| --- | --- | --- |
| Officially documented structure | 100 rooms, bosses every 25, gem choices, key costs, death persistence, three slots per effect category | Implemented; exact ordering still needs observed traces |
| Community model | Damage intervals, 24-hour recovery, combat modifiers, some effect strengths | Adopted provisionally, with source conflicts retained |
| Explicit assumption | Door weights, barrel odds, uniform damage, offer distribution, several special rooms | Synthetic scenario only |
| Not implemented | Ultimate economy, successive runs sharing one event, inventory and item values, observed-game replay, calibrated probabilities | No claims about these outputs |

All shipped scenarios are synthetic. The loader rejects other evidence labels.
Tests establish the engine's declared behavior, including assumptions; they do
not establish that the live game behaves that way.

## State and actions

Health is a `double` fraction of maximum dungeon health. Character class, gear,
level and ordinary combat statistics are absent. State includes room, turn, run
number, health, keys, six resource pools, gems, ordered effect slots, visible door
offers, revealed encounter, trial progress, shop offers, death count, purchases,
budget and elapsed/active time.

Phases are doors, encounter, blessing shop, curse shop, gem selection, recovery
and complete. Actions have phase-specific legality checks before mutation. Bosses
occupy rooms 25, 50, 75 and 100. The first three award an epic and a choice of one
of three gems; room 100 completes the run and awards a legendary. The room-five
shop and free first-floor shops apply to run one only. `--run-number 2` uses later
run rules but still starts an independent run with fresh keys, resources and HP.

Key/HP/resource payment and encounter resolution are distinct stages. Death on
an entry trap resumes the already-paid encounter. Death in combat retries that
encounter. A lethal poison tick on exit commits the completed room and its rewards
before entering recovery. These retry and timing conventions need live validation.

## Randomness and offered information

The profile supplies weighted door/content tables, gem pool, damage intervals,
effect weights and scalar probabilities. Independent door draws are an assumption;
actual paired door offers could be correlated. The generator forces boss/shop
rooms and applies gem modifiers. If both doors become inaccessible it replaces
the second with an untrapped monster door. This synthetic progress safeguard also
applies when death removes a lockpick. It is not an observed server rule.

All door trap indicators are treated as visible. The engine does not expose the
unrevealed encounter or future random draws to policies. Gem offers are sampled
uniformly without replacement from the configured pool, excluding already held
gems. The default pool follows the inspected calculator, not a verified current
server pool. Theme-specific pools must be selected in profiles; there is no event
calendar or automatic theme/version detection.

A counter-based SplitMix64 generator separates door, content, damage, escape,
key, effect, shop, gem and special-outcome streams by room. Each batch run uses
`mix(master_seed XOR run_index)`. Comparisons reuse master seeds. Diverging actions
can consume different draws within a stream; this is partial scenario coupling,
not guaranteed identical paths. Workers write to fixed run indices and summaries
use a fixed order, so thread scheduling does not affect output on the same build.
Cross-compiler bit-identical floating-point results are not promised.

## Combat

Each damage draw is **uniform over the configured interval**, in max-HP units.
The endpoints below follow the inspected [LD Gadget implementation](https://ldgadget.12hp.de/res/scripts/main.js).
The distribution itself is unknown. Bounds from a community calculator are not
guaranteed bounds on the live game.

| Floor | Fight damage | Failed escape damage | Boss damage |
| --- | --- | --- | --- |
| 1 | 11.78-14.40% | 11.78-14.40% | 16.08-19.62% |
| 2 | 16.83-20.57% | 16.83-20.57% | 22.95-28.05% |
| 3 | 14.57-23.38% | 14.57-23.38% | 22.95-28.05% |
| 4 | 21.04-25.71% | 17.31-25.71% | 43.03-52.50% |

Normal fight multiplier starts at 1 and adds: Hero -0.20, Bull -0.20, Deceit -0.20,
Rabbit +0.25, Devil +0.25 and Broken Armor +0.50. Failed escape instead applies
Hick +0.30, Pearl -0.40 and Broken Armor +0.50. Additive stacking is provisional.
One Hit prevents normal fight damage, but not failed-escape or boss damage.

Escape starts at the profile rate (0.50 in the example), adding Rabbit +0.40,
Moonstone +0.20, Bull -0.30, Escape blessing +0.80 and Clumsy -0.80, then clamps
to [0,1]. These are modeled as percentage-point changes. A surviving failed
escape advances the room without fight rewards; a successful escape does too.
Pendant may give a key on a successful escape (0.40), while Pearl may give a curse
(0.20). Bosses cannot be fled and ignore combat modifiers. Room poison/recovery
ticks are skipped in boss rooms, while room durations still decrement.

Ordinary key drops use the profile rate plus Lodestone +0.30 and Spying -0.15.
Key Moment replaces that with two keys at probability 0.70. A survived normal
fight after floor one can add a curse at the profile rate, plus 0.10 with
Misadventurer. Special enemies use simplified damage/reward rules (see below).

## Effects and timing

Blessings and curses each have three ordered slots. Reacquiring an existing kind
replaces its strength/duration in place; a new fourth kind removes the oldest slot.
New room rewards and shop purchases start next room. Entry-door effects and
re-entry effects start immediately. An elixir heals immediately without a slot.
At an ordinary room's end: poison, death check, recovery, duration consumption,
then room advance. Healing never rescues a lethal poison tick. Death clears both
effect categories while preserving gems, keys, resources and progress.

| Blessing | Default strength and weak duration | Strong version |
| --- | --- | --- |
| Raider | Abstract gold reward doubled; 5 rooms | 10 rooms |
| One Hit | Normal fight damage zero; 4 rooms | 8 rooms |
| Escape | +0.80 escape probability; 5 rooms | 10 rooms |
| Disarm | Negate 4 traps | 8 traps |
| Lockpick | Open 2 locked doors without keys | 4 doors |
| Key Moment | 0.70 chance of two keys; 4 survived fights | 8 fights |
| Elixir | Heal 0.25 immediately | Heal 0.50 |
| Recovery | Heal 0.10 per room for 3 rooms | 0.20 for 6 rooms |

| Curse | Default strength and weak duration | Strong version |
| --- | --- | --- |
| Broken Armor | +0.50 combat damage multiplier; 4 rooms | 8 rooms |
| Poison | Lose 0.05 max HP; 5 rooms | 10 rooms |
| Clumsy | -0.80 escape probability; 5 rooms | 10 rooms |
| Gold Hangover | Stored but not applied to valued currency; 5 rooms | 10 rooms |
| Hard Lock | Double key cost; 4 rooms | 8 rooms |

The default strong-effect probability is 0.25. Explorer adds 0.20 for curses;
Thirsty adds 0.40 for barrel blessings. Time Traveler adds one charge/room to all
lasting blessings; Moonstone adds one to all lasting curses. Extending non-room
counters this way is a convention requiring confirmation.

## Gems and generation assumptions

Combat effects are listed above. Additional implemented changes are:

| Gem ID | Implemented generation/interaction rule |
| --- | --- |
| `greed` | Double mystery-door weight; +0.20 container blessing probability |
| `explorer` | Halve mystery-door weight; stronger curses as above |
| `spying` | Transfer half the locked-door weight to unlocked doors |
| `pendant` | Force one offered door to locked; escape key chance as above |
| `greasy` | Suppress epic chests/doors; after re-entry gain 0.10 recovery for 3 rooms |
| `gambler` | +0.50 barrel blessing probability; HP traps give a curse instead |
| `time_traveler` | Barrels always curse; extend blessings |
| `lodestone` | Add double-locked weight equal to half the locked-door weight |
| `devil` | Add 8 absolute epic-door weight units; +fight damage |
| `blood` | Double sacrifice-door weight; entry HP sacrifice reduced by 40% |
| `hick` | Halve sacrifice-door weight; +failed-escape damage |
| `thirsty` | Double cursed-door weight; stronger barrel blessings |
| `misadventurer` | Halve cursed-door weight; +combat curse chance |
| `deceit` | Configured hidden-monster chance behind locked/epic doors |
| `hero` | 25% chance to lose certain chest epic rewards; damage reduction |
| `old_sacrifice` | 20% sacrifice-chest replacement behind locked doors; chest HP cost -20% |
| `kidney` | 20% cursed-chest replacement behind locked doors; 10% fight blessing chance |
| `masochist` | Force a trap on one offered door; trap damage -20% |
| `rusty` | Re-entry poison 0.05 for 5 rooms; room healing multiplied by 1.20 |

The table describes conventions, including arbitrary rates for qualitative spawn
changes. It is not a measured gem specification. The last four archived gems are
excluded from the default pool. Diamond/Time Traveler takes precedence over
Gambler for barrels; that combination needs an observed test. Greasy suppression
applies to epic chests, not the mandatory boss rewards or all special rewards.

## Doors, containers and special rooms

Locked/epic doors cost one key, double-locked two; Hard Lock doubles this and
Lockpick overrides it. Normal modeled traps cost 0.10 max HP; cursed traps give
a curse. Sacrifice doors cost 0.12 before encountering their chest, which can
separately cost 0.15 to open. Cursed doors grant a curse on entry. Resource doors
cost one **abstract donation unit**, then give two units of a different resource.
These resource units have no conversion to real account quantities.

Barrels choose blessing/curse at the configured probability, then identity and
strength from effect tables. Crates, silver and bronze containers share one
synthetic reward mixture. Skeletons wake at a configured chance, otherwise use
that mixture. Mimics are optional fights. Curse shops exchange a curse for keys;
blessing shops exchange keys for an effect. One purchase completes the room.
Rerolling costs one mushroom and replaces both offers independently. Offer
strengths, independence, key prices and all-in-one purchase semantics need replay
validation. Blessing weak key costs are 1,2,3,2,1,1,1,1 in profile effect order;
strong costs double, except strong elixir/recovery cost 3. Curse weak rewards are
1,1,2,1,2; strong rewards double.

Special encounters use explicit placeholders where exact rules are missing:

| Encounter family | Current convention |
| --- | --- |
| Fountain, cleansing fountain | Heal 0.25; cleansing also clears curses |
| Rocks, wood, souls, arcane | Add one abstract resource unit |
| Lava | Lose 0.10 HP |
| Narrator | Heal 0.25 and gain a random blessing |
| Flooded | A single action taking at least 10 seconds kills; faster action exits |
| Wishing well | Equal chance blessing or epic, with no modeled gold payment |
| Rock/paper/scissors | Uniform opponent; win blessing, loss 0.10 HP plus curse, draw exits |
| Sewers, auction, armory, locker | Simplified epic reward, no inventory/auction economy |
| Sarcophagus | Abstract gold; locked version spends a key for an epic |
| Wheel | Equal blessing, curse, key gain, key loss or gold |
| Spider legs/head/full | Key reward 1/2/5 at probability 0.80/0.50/0.20; otherwise poison |
| Undead, tube, beta | Half ordinary base damage |
| Shakes, Valaraukar, pig | Base damage 0.30/0.60/0.40; survived fights give blessing/recovery/heal |
| Rainbow | Lose 0.20 HP and gain a blessing |

Theme encounters outside the default golden table still have callable transitions
but are not all present in default runs. This catalog is coverage for experimentation,
not faithful implementation of every live sub-choice, cost or reward.

Trials can start once, after floor one and early enough in a floor to avoid a boss.
The synthetic generator allows them through room 18 of later floors. Victories
increment depth (up to five) and advance the normal room count; damage multipliers
are 1.0/1.1/1.2/1.3/1.4. Exit consumes a prize room: depth 1 gives gold, depth 2+
an epic, depth 4+ has a configured legendary chance. Death or escape clears the
trial chain and resumes the normal path. Trial counting/reward timing is especially
provisional. The baseline policy avoids entry and exits at the first opportunity.

## Time and spending

Only explicit recovery waits regenerate HP. Full natural recovery takes 24 hours
in the example, with 20% HP required for re-entry. Each other action costs the
profile's action seconds, added to elapsed and active time. This ignores passive
regeneration while playing, menus, network latency, player breaks during runs and
the exact behavior of real recovery timers. `--login-hours H` rounds free recovery
returns up to an H-hour grid starting at experiment time zero. It is not a daily
sleep/work schedule.

Paid 20-point heals cost 10, then 15, then 20 mushrooms per purchase in a run. The
full-refill action costs `ceil((1 - hp) * full_heal_cost_per_fraction)`, at least 1,
with an example coefficient of 48. This extrapolates two community first-use UI
quotes and is **not a verified recurring price schedule**. A full refill also
advances the paid-purchase counter by one in this implementation. Ordinary waits
and purchases are available only in the recovery phase. Any conclusions involving
repeat full refills must be revisited when their actual progression is known.

The budget caps all mushroom deductions. The baseline recovery policy aims for
the selected re-entry HP (at least the modeled boss upper bound +0.001 near a
boss), then buys affordable recovery or waits. It compares full-refill cost with
the necessary 20-point steps. It does not reserve a globally optimal amount for
future shops, decide optimally between partial waiting and buying, or optimize
the last minutes before a deadline.

## Policy and reporting limits

The supplied policy uses fixed door scores, early-floor key farming and mostly
escaping afterward. It values shop effects by rough saved-HP weights, only
rerolling when no current offer has positive heuristic value. It does not account
fully for eviction of another effect or future key value. Barrel policies are
skip, always open, low-HP except Time Traveler, and adaptive (also avoid replacing
One Hit; favor Gambler at HP below 0.80). These are reproducible baselines, not a
dynamic-programming solution.

Simulations stop at the explicit time horizon. Completion probability includes
all attempts; its interval is Wilson 95%. Restricted mean time averages
`min(completion_time, deadline)` across all attempts. Completed-only summaries
are conditional on success and can be misleading when success rates differ.
Mushrooms per completion divides total spend on all attempts by successes. Zero
successes produce `null` for that ratio and completed-only statistics.

An action cap is a debugging failure, not an event deadline: it invalidates
completion-probability and restricted-mean statistics and exits the CLI with code
2. Normal successful experiments exit 0; invalid input/runtime errors exit 1.
Profile fingerprints are FNV-1a over raw bytes (including comments and line endings),
for reproducibility rather than cryptographic security. Traces verify recorded
actions against the same simulator; they are not an observation importer.

There is no current calibrated damage distribution, empirical spawn model,
whole-event multi-run model, Ultimate mode, reward-value optimizer, graphical
recreation or live-game integration. Those gaps are material to the original
strategy questions; more simulated samples cannot remove them.
