# Forecast from your current run

Version 0.2 adds `--state FILE` to `simulate`, `compare` and `audit`. Enter your
current visible position once, then simulate many possible continuations. Known
choices stay fixed. Future outcomes still use the scenario's assumed probabilities.
This is manual offline input, not an account connection or game-log importer.

## Quick start

From the repository root on Windows:

```powershell
./tools/build.ps1
./build/Release/sfld.exe state-template --output my-run.state
# Edit the example to match your current screen.
./build/Release/sfld.exe audit --state my-run.state
./build/Release/sfld.exe simulate --state my-run.state --allow-assumptions --runs 100000 --deadline-hours 48 --budget 0 --output results/from-here.json
```

On Linux/macOS use `build/sfld` after the usual CMake build. The template command
prints to stdout if you omit `--output`, and refuses to overwrite an existing file.
The generated values are an example, not information about your account.

```ini
schema=1
room=42
run_number=2
phase=doors
hp_percent=63
keys=4
gems=rabbit
blessings=recovery:weak:2
curses=none
doors=golden,wall
paid_heals=1
```

This describes room 42 on the second run, 63% HP, four keys, Rabbit already held,
weak Recovery with two rooms left, a golden door on the left and a wall on the
right. One paid healing purchase has already been used this run.

## Time, money and run number

`--deadline-hours 48` means **48 hours available from the entered position**.
`--budget 25` allows **25 additional mushrooms**. Reported time, spending, deaths,
actions and new gem picks start at zero from that position. Earlier spending and
elapsed time are not counted again.

`paid_heals` retains the earlier purchase count for the next 10/15/20 healing
price. Under the provisional full-refill model, a full refill counts as one
purchase too. `shop_rerolls` retains the count already used at the current shop
for the per-shop policy limit, without spending the new budget again.

`run_number=1` enables first-run shop rules; `2` or more uses later-run rules.
This differs from `--runs 100000`, which means 100,000 simulations of the same
position. A conflicting CLI `--run-number` is rejected.

## Which room and screen?

`room` is the room currently being resolved (1..100). Its floor/damage band is
derived automatically. Choose the matching phase:

| Current screen | `phase` | Additional input |
| --- | --- | --- |
| Choosing left/right | `doors` | `doors=golden,wall`, or `doors=unknown` |
| Already inside a room | `encounter` | `encounter=barrel`, `monster`, `boss`, etc. |
| Blessing shop | `shop` | Two `offers`, or `offers=unknown` |
| Curses in exchange for keys | `curse_shop` | Two `offers`, or `offers=unknown` |
| Choosing a stone after a boss | `gems` | Three `gem_offers`, or `gem_offers=unknown` |
| Recovering after death | `recovery` | `resume_phase` and input for that screen |

For gem selection, use the **next** room number: 26 after boss 25, 51 after boss
50, or 76 after boss 75. Enter only gems already held, excluding the unchosen one.
For an undefeated boss, use its actual room and `encounter=boss`, or enter its
two door positions, e.g. `doors=boss,wall`.

Death clears blessings/curses. Recovery input must leave those empty, even when
Greasy will grant Recovery on re-entry. Enter HP regenerated **now**, not before
death. If an entrance cost was already paid, resume the encounter rather than
doors to avoid paying again. Trial death resumes normal doors with
`trial_seen=true` and `trial_depth=0`.

Examples: [doors](../examples/progress.state), [recovery](../examples/recovery.state),
[gem selection](../examples/gem-choice.state), [shop](../examples/shop.state).

## Input fields

Use `key=value`, comma-separated lists and `#` comments. Unknown fields,
duplicates and inconsistent states are rejected.

| Field | Meaning / default |
| --- | --- |
| `schema`, `room`, `run_number`, `phase`, `keys` | Required; schema is `1` |
| `hp_percent` | Required health, 0..100; zero only in recovery |
| `gems` | Held IDs, or `none`; count must match completed gem selections |
| `blessings`, `curses` | Up to three ordered effects each, oldest first; default `none` |
| `paid_heals`, `shop_rerolls` | Earlier purchases in this run/current shop; default `0` |
| `resources` | Six abstract donation-unit balances: wood, stone, souls, metal, arcane, hourglasses; default all zero |
| `trial_seen` | Trial entrance already used this run; default `false` |
| `trial_depth` | Active trial wins, 0..5; default `0` |
| `pending_trial_reward` | Won trial depth for the current `prize` encounter |
| `donated_resource` | Resource paid for the current `sated_chest`, index 0..5 |
| `resume_phase` | Required in recovery: `doors`, `encounter`, `shop` or `curse_shop` |

Canonical report/trace snapshots use `hp_fraction` (0..1) to preserve exact
floating-point HP. Input accepts either form, never both. Historical result
counters and game account data are not accepted fields.

Resource units are **not raw account quantities**: one unit pays for one hungry
door. Zero prevents spending resources you did not record. Conversion from actual
account resources and the broader economy remain unfinished.

### Effects and offers

An effect is `kind:weak|strong:remaining`, e.g.:

```ini
blessings=lockpick:weak:1,recovery:strong:2
curses=poison:weak:3,hard_lock:weak:1
```

Enter the count shown now, including any gem extension already applied. Counts
refer to rooms for most effects, doors for Lockpick, traps for Disarm and survived
normal fights for Key Moment. Loading does not grant or extend an effect again.
An optional fourth `:1` marks activation starting next room; default delay is zero.

Blessing IDs: `raider`, `one_hit`, `escape`, `disarm`, `lockpick`, `key_moment`,
`recovery`. `elixir` heals immediately and is only valid as a shop offer.
Curse IDs: `broken_armor`, `poison`, `clumsy`, `gold_hangover`, `hard_lock`.
Strengths are in [the effects table](MODEL.md#effects-and-timing).

Shop offers add their displayed key cost (key reward for curses):

```ini
offers=elixir:strong:1:3,one_hit:weak:4:2
```

That is a strong elixir for three keys, or four rooms of One Hit for two keys.
Use zero when an offer is free. Recorded offers stay fixed; rerolls are sampled.
`offers=unknown` samples the initial offers as well.

### Doors, walls and stones

Always enter two positions, left then right. `wall` is impassable. Trap suffixes
are optional: `doors=monster:trap,golden:cursed_trap`. Known doors are never silently
replaced to make the input traversable. If neither is affordable, check keys,
resources and effects. Use `doors=unknown` only when the pair has not been observed.

Door IDs: `monster`, `mystery`, `locked`, `double_locked`, `unlocked`, `epic`,
`golden`, `shop`, `cursed`, `sacrifice`, `blessing`, `destiny`, `wood`, `stone`,
`souls`, `metal`, `arcane`, `hourglasses`, `trial`, `exit_trial`, `boss`, `wall`.
Encounter IDs are listed in [model.hpp](../include/sfld/model.hpp).

Stone IDs use shortened English names:

| IDs | Corresponding stones |
| --- | --- |
| `rabbit`, `moonstone`, `spying` | Soul of the Rabbit, Cursed Moonstone, Spying Gem |
| `pendant`, `greasy`, `gambler` | Pendant of the Key Master, Greasy Healing Stone, Boulder of the Gambler |
| `greed`, `hero`, `time_traveler` | Boulder of Greed, Treasure of the Hero, Diamond of the Time Traveler |
| `thirsty`, `pearl`, `blood` | Hope of the Thirsty One, Cursed Pearl, Blood Drop of Sacrifice |
| `explorer`, `misadventurer`, `devil` | Emerald of the Explorer, Sapphire of the Misadventurer, Crown Jewel of the Devil |
| `deceit`, `lodestone`, `bull`, `hick` | Pebble of Deceit, Lodestone, Eye of the Bull, Erratic Boulder of the Hick |

Names follow the inspected [community calculator](https://ldgadget.12hp.de/).
Archived IDs `rusty`, `masochist`, `old_sacrifice`, `kidney` are also accepted,
but their rules are provisional and they are excluded from the default pool.

## First-run and later-run stone pools

Profiles optionally accept `gems_first_run` and `gems_later_runs`; the entered
run number selects the relevant pool. Missing overrides fall back to `gems`.
Each configured pool needs at least five distinct IDs so three unowned choices
remain at the third selection.

**The shipped scenario has no verified first/later-run pool split.** Both currently
fall back to the same hypothetical pool. This feature supports a verified split
when available; it does not invent one. Every report lists its future pool.
Already held gems and observed offers are preserved even if absent from that
pool: known observations take precedence over a hypothetical generation table.

## Comparisons and replay

```sh
build/sfld compare --state examples/gem-choice.state --allow-assumptions --sweep gems --runs 100000 --deadline-hours 48 --output results/my-gem-choice.json
build/sfld compare --state my-run.state --allow-assumptions --sweep budgets --budgets 0,10,25,50 --deadline-hours 48 --output results/my-budgets.json
build/sfld simulate --state my-run.state --allow-assumptions --runs 1 --trace results/from-here.tsv --output results/from-here.json
build/sfld replay --allow-assumptions --trace results/from-here.tsv
```

At an observed gem screen, the sweep compares those three choices plus the
baseline. Otherwise it compares unowned gems from the selected future pool.
Results remain conditional on future probabilities and the selected policy.

Reports include the normalized starting snapshot, its fingerprint, the future
gem pool and measurement origin. `gem_pick_counts` excludes already held gems.
Version 2 traces embed the starting state, so replay needs the matching profile
and engine but not the original progress file. The reader also supports version 1
fresh-run traces. These inputs initialize hypothetical continuations; they are
not saved copies of the live game's hidden random state.
