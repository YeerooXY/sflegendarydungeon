# Offline agent challenge rules (engine 0.4.0)

Maximize **completed 100-room runs** within 168 simulated hours. Evaluate a
zero-mushroom budget separately from a 500-mushroom budget. Time and spending are
shared by all runs in an event. The simulator is a hypothetical normal Legendary
Dungeon, with documented mechanics and explicitly assumed probabilities. A good
score here is not evidence of the best live-game strategy.

## Interface

Run `build/Release/sfld.exe agent --allow-assumptions --budget 500 --seed N`
from the repository root. Stdout emits one JSON observation, then waits for one
line on stdin: `ACTION INDEX VALUE`. Use an action from `legal_actions`, e.g.
`choose_door 0 0`, `buy 1 0`, `heal_full 0 0`, or `wait 0 4.8`. Flush stdin.
Read the next observation and repeat until `terminal` is true. Stderr is
diagnostic output. An invalid command emits an error followed by the unchanged
observation. EOF ends the session early; it is not a completed event.

The observation contains only revealed state. Indices start at zero. Door
contents and future random outcomes are hidden. The legal menu includes waits
to 20/40/60/80/100% HP, clipped to the time left. Other positive wait durations
are accepted in recovery. `--state PATH` can start from existing progress.
`--trace PATH` records observations and accepted actions for inspection.
Agent thinking time does not advance the simulation clock. Each non-wait
action normally costs two simulated seconds.

## Progress, health and spending

- Two door positions; a wall is blocked. Choosing a door pays its cost and
  reveals its room. Resolve that room to progress. Room 25/50/75/100 has a boss.
- Defeating a boss at 25/50/75 offers three distinct stones. Choose one. Owned
  stones stay for the run. Eligible stones have equal offer weight; first-run
  and later-run pools follow the player observations in `GEM_POOLS.md`: first
  run has 11 stones; later runs add pendant, thirsty, pearl, explorer,
  misadventurer, time_traveler, devil, lodestone and provisionally rusty.
- Death keeps room, keys, stones and external resources, and clears blessings
  and curses. Recovery is linear over 24 hours from zero. Reenter at >=20% HP.
  Lethal room-exit poison commits the cleared room before recovery.
- Full and partial mushroom refills are available during active play and
  recovery. Partial refill adds 20% HP for 10, then 15, then 20 mushrooms each
  run. Full refill costs `ceil(48 * missing_HP_fraction)` and sets HP to 100%.
  Either type increments the same paid-heal counter (an unverified convention).
  Refilling does not clear effects or complete a room. Free waiting heals only
  during recovery in this model.
- Room 100 adds one completion. `restart` begins the next run. Keys, stones,
  effects, trial status and heal-price history reset; resources, event time and
  spending persist. Default next-run HP is full, as reported by the maintainer;
  `--restart-health carry|empty` supports alternative experiments.

## Combat and effects

All damage is a fraction of maximum HP. Uniform sampling inside these intervals
is the default assumption; profiles can use triangular, midpoint, low or high.

| Rooms | Monster damage | Failed escape damage | Boss damage |
| --- | --- | --- | --- |
| 1–25 | 11.78–14.40% | 11.78–14.40% | 16.08–19.62% |
| 26–50 | 16.83–20.57% | 16.83–20.57% | 22.95–28.05% |
| 51–75 | 14.57–23.38% | 14.57–23.38% | 22.95–28.05% |
| 76–100 | 21.04–25.71% | 17.31–25.71% | 43.03–52.50% |

Fight wins if damage is survived. Flee succeeds with assumed 50% probability
and no damage; failure deals damage but still leaves the room if survived.
Bosses cannot be fled and ignore combat modifiers, one-hit, poison and recovery
ticks in this model. Ordinary won fights have 50% chance for one key. Key Moment
replaces that with 70% chance for two keys. From stage two, won non-boss fights
have assumed 10% curse chance: gold hangover, poison, broken armor on successive
stages. Trigger probability and timing remain uncertain.

Three blessing and three curse slots. A repeat replaces its old instance;
a fourth distinct effect replaces the oldest. New room effects start in the
next room; rerolls and refills do not tick them. Poison damage precedes healing.

| Effect | Weak / strong |
| --- | --- |
| raider | Double chest gold, 5 / 10 rooms |
| one_hit | Zero non-boss fight damage, 4 / 8 rooms |
| escape | +80 percentage points flee chance, 5 / 10 rooms |
| disarm | Ignore next 4 / 8 traps |
| lockpick | Next 2 / 4 locked doors cost no keys |
| key_moment | Two-key roll, next 4 / 8 won fights |
| elixir | Immediate 25% / 50% HP |
| recovery | 10% / 20% HP after rooms, 3 / 6 rooms |
| broken_armor | +50% non-boss damage, 4 / 8 rooms |
| poison | Lose 5% HP after rooms, 5 / 10 rooms |
| clumsy | −80 percentage points flee chance, 5 / 10 rooms |
| gold_hangover | Half chest gold, 5 / 10 rooms |
| hard_lock | Double key costs, 4 / 8 rooms |

Additive flee modifiers, clamped to [0,1], are an assumption, not established
tooltip semantics. Blessing shops show two different types with independently
50/50 weak/strong strength. Buy one to leave, skip, or reroll both for one
mushroom. Displayed key prices are authoritative for the simulation. First-run
stage-one blessing shops are free. Curse shops give keys in exchange for a curse.

## Stones in the default pool

Modifiers to non-boss damage add before multiplication. Qualitative door-weight
changes below are invented quantitative implementations, not measured rates.

| ID | Modeled effects |
| --- | --- |
| rabbit | +40 points escape; +25% fight damage |
| moonstone | +20 points escape; curses last one extra charge |
| spying | Half locked weight becomes unlocked; −15 points key chance |
| pendant | Force one locked door; 40% key chance on successful escape |
| greasy | No epic chests/doors; reenter grants 10% healing for 3 rooms |
| gambler | +50 points barrel blessing chance; traps curse instead of hurting |
| greed | Double mystery weight; +20 points container blessing chance |
| hero | −20% fight damage; 25% chance to lose some chest epic rewards |
| time_traveler | Blessings last one extra charge; barrels always curse |
| thirsty | +40 points strong barrel blessing chance; double cursed-door weight |
| pearl | −40% failed-escape damage; 20% curse chance on successful escape |
| blood | −40% sacrifice-door damage; double sacrifice-door weight |
| explorer | Halve mystery weight; +20 points strong curse chance |
| misadventurer | Halve cursed-door weight; +10 points combat curse chance |
| devil | +8 epic-door weight; +25% fight damage |
| deceit | −20% fight damage; 20% hidden monster chance in locked/epic rooms |
| lodestone | +30 points key chance; extra double-locked door weight |
| bull | −20% fight damage; −30 points escape |
| hick | Halve sacrifice-door weight; +30% failed-escape damage |
| rusty | Room healing ×1.20; reenter grants 5% poison for 5 rooms; refill/wait healing unchanged in this model |

## Other rooms and doors

Locked/epic doors cost one key; double-locked cost two. Traps usually cost 10%
HP; cursed traps give a curse. Sacrifice doors cost 12% HP, then their chest
costs 15% if opened. Cursed doors and chests each give a curse. Resource doors
cost one abstract resource unit; their chest gives two of a different resource.
Trials are optional sequences of up to five fights with increasing damage;
the exit grants a reward, while death or fleeing ends the sequence.

Barrels are assumed 50/50 blessing/curse with uniform identities and 25% strong
effects. Containers can grant blessings, ordinary items, gold or nothing.
Skeletons have 50% chance to wake as a monster. Mimics can be skipped.

Fountains heal 25%; the cleansing variant also removes curses. Narrator heals
25% and gives a blessing. Lava costs 10%. Water kills after ten cumulative
seconds inside the encounter, including refills; interact/skip exits. RPS uses
a uniform opponent: win blessing plus epic (community report), draw nothing,
loss 10% HP and curse. Wishing well gives blessing/epic equally. Spider
legs/head/full offer 1/2/5 keys with assumed 80/50/20% success; failure gives
poison. Immediate bite damage is configurable and defaults to zero (unknown).
All spiders can be skipped. Wheel has five equally weighted outcomes: blessing,
curse, gain key, lose key, gold. Resource rooms grant an abstract unit.
Sarcophagus gives gold; locked sarcophagus costs a key for an epic.
Sewers/locker give epics. Undead lantern zombie/tube/beta deal half normal
damage; tube also gives ten lucky coins. Event-specific rooms include shakes
(30% damage + blessing), valaraukar (60% + recovery), rainbow (20% + blessing),
pig (40% damage then 60% heal if survived; skippable), auction (50% epic) and
armory (rooms 90–98, equipment-dependent reward). These theme variants are
implemented but not in the generic spawn pool. Empire/Mariachi Band effects
remain unresolved and are excluded.

The exact spawn weights are in `profiles/synthetic.profile`; they are scenario
assumptions. Do not inspect engine internals, random streams, other controllers,
or held-out evaluation results when taking part in an independent agent trial.
