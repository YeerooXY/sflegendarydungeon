# Mechanics audit: effects, shops and golden rooms

Audited 2026-09-23 for engine 0.3.0. This is a coverage inventory, not a claim
that the entire live game has been reproduced. An implemented transition can
still depend on unmeasured probabilities or provisional timing. The
[model specification](MODEL.md) covers doors, gems, trials, death and recovery.

Evidence: [official golden rooms](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/284-legendary-dungeons---golden-rooms/),
[official regular rooms](https://playa-games.helpshift.com/hc/en/4-shakes-fidget-1653988985/faq/294-legendary-dungeon---regular-rooms/),
[LD Gadget](https://ldgadget.12hp.de/), and the maintainer observations recorded in
[RESEARCH.md](RESEARCH.md#maintainer-observations-2026-09-23).

## Blessings

All eight effect identities have implemented behavior. These are the engine's
normal weak/strong templates. HP numbers are percentages of maximum dungeon HP.

| Name / input ID | Weak | Strong | Remaining uncertainty |
| --- | --- | --- | --- |
| Raider / `raider` | Chest gold x2, 5 rooms | 10 rooms | Exact currency and stacking with Gold Hangover |
| One Hit Wonder / `one_hit` | No normal fight damage, 4 rooms | 8 rooms | Exact live counter timing |
| Escape Assistant / `escape` | +80 percentage points, 5 rooms | 10 rooms | Percentage points versus a relative or absolute chance |
| Disarm / `disarm` | Negate 4 traps | 8 traps | Event/version-dependent tiers and trap interactions |
| Lockpick / `lockpick` | 2 free locked doors | 4 doors | Live counter timing and coffin interactions |
| Key Moment / `key_moment` | 70% for 2 keys, 4 won fights | 8 fights | Ordinary key-drop odds and gem stacking |
| Elixir of Life / `elixir` | Immediate +25 HP points | +50 points | Other event/mode tiers |
| Road to Recovery / `recovery` | +10 HP points per room, 3 rooms | +20, 6 rooms | Order relative to poison and death |

One Hit does not prevent boss, failed-escape, trap or poison damage. Victories
still award keys. A shop purchase starts its room counter after leaving the
shop. Empty rooms and boss rooms still consume a room-duration charge in the
current convention. Elixirs heal immediately and do not occupy a lasting slot.

The 70% Key Moment value is supported by both LD Gadget and the maintainer's
current tooltip report. The unrelated 50% code-comment claim is not used.

## Curses

All five identities now affect state, including Gold Hangover's reward reduction.

| Name / input ID | Weak | Strong | Remaining uncertainty |
| --- | --- | --- | --- |
| Broken Armor / `broken_armor` | +50% combat damage, 4 rooms | 8 rooms | Modifier stacking |
| Poison / `poison` | -5 HP points per room, 5 rooms | 10 rooms | Tick order and lethal-tick progression |
| Clumsy / `clumsy` | -80 percentage points of escape chance, 5 rooms | 10 rooms | Percentage interpretation |
| Gold Hangover / `gold_hangover` | Half chest gold, 5 rooms | 10 rooms | Multiplicative combination with Raider is provisional |
| Hard Lock / `hard_lock` | Double locked-door key cost, 4 rooms | 8 rooms | Rooms versus used doors needs confirmation |

There are three ordered slots for blessings and three for curses. Reacquiring
the same identity resets its strength/duration in place; a new fourth identity
evicts the oldest slot. Death clears both sets. A cleansing fountain clears only
curses. Time Traveler extends lasting blessings and Moonstone extends curses.
The exact counter behavior on acquisition, replacement and boss rooms needs
recorded-game validation; tests verify the declared engine convention.

## Golden-room coverage

The normal and cleansing fountains are distinct encounters. Curse removal is a
documented special variant, consistent with the maintainer's observation.
Their relative appearance rates are unknown.

| Encounter / input ID | Implemented transition | Gaps or assumptions |
| --- | --- | --- |
| Fountain / `fountain` | +25 HP points | Spawn rate; healing tick order |
| Cleansing fountain / `cleansing_fountain` | Clear all curses, +25 HP points | Spawn rate |
| Storyteller / `narrator` | +25 HP points and a blessing, or skip | Blessing distribution |
| Lantern zombie / `undead` | Fight or flee; half normal base damage | Sampled damage distribution and special key/curse interactions |
| Rocks / `rocks` | Mandatory passage, stone reward | Resource amount represented by an abstract donation unit |
| Woodpile / `wood` | Mandatory passage, wood reward | Abstract donation unit |
| Lava / `lava` | Mandatory -10 HP points | Live ordering with ongoing effects |
| Flooded room / `flooded` | Exit; an action taking at least 10 seconds is lethal | Simplified timer/action model |
| Wishing well / `wishing_well` | Blessing or epic item | 50/50 placeholder; gold payment omitted |
| Rock-paper-scissors / `rps` | Choice, win/blessing, loss/damage+curse, draw, skip | Uniform opponent assumption; community-reported item reward not yet represented |
| Sewers / `sewers` | Epic reward or skip | Item types and toilet eligibility not tracked |
| Sarcophagus / `sarcophagus` | Relative gold reward | Exact gold amount |
| Locked sarcophagus / `locked_sarcophagus` | Key payment and epic | Lockpick/Hard Lock interaction is provisional |
| Wheel / `wheel` | Blessing, curse, gold, key gain/loss, or skip | Five equal outcome weights are placeholders, not wheel-sector measurements |
| Spider legs / `spider_legs` | 1 key or poison, or skip | 80% success placeholder |
| Spider head / `spider_head` | 2 keys or poison, or skip | Official equal odds; poison timing still provisional |
| Whole spider / `spider_full` | 5 keys or poison, or skip | 20% success placeholder |
| Soul bath / `souls` | Soul reward or skip | Abstract donation unit |
| Arcane cave / `arcane` | Arcane reward or skip | Abstract donation unit |
| Curse merchant / `curse_shop` | Curse in exchange for keys, reroll or leave | Distinct offers and strength model extrapolated from blessing shop |
| Armory / `armory` | Rooms 90-98; epic or eligible legendary | 10% community rate; equipment eligibility must be configured; replacement-versus-additional reward needs validation |
| Locker / `locker` | Epic reward | Birthday pool must be selected explicitly |
| Flying Tube / `tube` | Half base damage; victory gives 10 lucky coins | Theme pool; Ultimate multiplier absent |
| Beta room / `beta` | Fight/flee with half base damage | Birthday pool and encounter-specific modifiers |
| 3D Shakes / `shakes` | Provisional 30% base-damage fight and blessing | Separate win/loss behavior is not established; no independent outcome roll |
| Valaraukar / `valaraukar` | Provisional 60% base-damage fight and Recovery | Reward strength, modifiers and exact flee semantics |
| Auction / `auction` | Item with configurable epic chance, or skip | Epic rate unknown; no inventory model |
| Rainbow rack / `rainbow` | -20 HP points and blessing, or skip | Blessing distribution |
| Pig / `pig` | Fight costs 40 HP points; surviving victory heals 60; free skip | Modifiers and theme pool |

Generic golden rooms are in the shipped profile. Theme-specific transitions can
be selected in a profile's `golden` table or entered directly in a progress file.
There is no automatic event/theme detector. Listing a room above does not imply
that every event can generate it.

## Shops and interaction tests

See [SHOPS.md](SHOPS.md) for targeted rerolls, key affordability, separate recovery
budgets and the maintainer's estimated 50/50 strength split. The test suite checks
distinct offers, one-purchase visits, unchanged counters during rerolls, target
acceptance, budget/limit stopping, and eviction of valuable active effects.

Additional mechanics tests cover fountain cleansing versus ordinary healing,
storyteller rewards, eight-room One Hit expiry, the 70% two-key outcome, gold
curse/blessing stacking, lantern damage, Flying Tube coins, Armory eligibility,
pig death/skip behavior and duration gems.

## What is still needed for full fidelity

The highest-impact unresolved issues are joint door offers, room frequencies,
barrel/effect probabilities, escape modifier semantics, exact effect tick order,
and theme/floor-specific combat curses. Combat curses currently use a shared
random table; the live floor-specific choice shown in the dungeon overview has
not been mapped. Shop identity frequencies and correlations also remain unknown.

Several resource, inventory, trial and special-room rewards are simplified as
listed above and in MODEL.md. Normal versus Ultimate economy is not modeled.
No tests or large simulation sample can establish these missing live rules.
Observations should include the visible state before and after each action;
[DATA.md](DATA.md) describes the format needed to calibrate and replay them.
