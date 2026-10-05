# Installed client inspection, 2026-10-05

Static inspection of the installed Steam client recovered useful protocol and
display rules, including the stage-specific monster-curse descriptions. It did
not establish the escape, barrel, curse, or shop-generation probabilities. The
simulator remains a synthetic model. The inspection initially left v0.3 mechanics
unchanged; v0.4 subsequently adopted the floor-curse identities provisionally,
as recorded in [MECHANICS.md](MECHANICS.md).

## Build and method

| Item | Inspected value |
| --- | --- |
| Steam app / build | 438040 / 25345890 |
| Game version | 31.001.260916.1 |
| Unity | 6000.3.11f1 |
| Code format | Windows x64 IL2CPP; metadata version 39 |
| Main code | GameAssembly.dll, 47,861,248 bytes |
| Metadata | global-metadata.dat, 12,505,916 bytes |

[Cpp2IL](https://github.com/SamboyCoding/Cpp2IL/tree/f92ff8bcc8a05e2d9d263b875a25c4ae476a4a24)
recovered type definitions, field layouts, method addresses and native
disassembly. Native instructions and referenced constants were inspected;
generated C# declarations with empty method bodies were used only as metadata,
not as recovered implementations. Capstone independently decoded the rounding
helper used by the full-heal quote. UnityPy read the dungeon asset bundles.

The game version came from PlayerSettings. UnityPy's strict read of that object
reported a four-byte size mismatch, so only its named version fields were used
with the end-of-object check disabled. The eight dungeon bundles parsed with
strict checks. Steam build ID and Unity executable version independently identify
the installation. [The evidence file](client-inspection-2026-10-05.json) records
hashes, tool versions and method RVAs for reproduction.

Inspection did not run or modify the game, send game requests, or observe an
account. Generated declarations, disassembly and extracted asset data stay in the
ignored `out/client-inspection/2026-10-05/` directory. This document and the evidence
JSON contain findings, not a redistributed game implementation.

To reproduce the code inspection with the recorded Cpp2IL build, run these in a
local scratch directory, substituting the installed game path:

```powershell
Cpp2IL.exe --game-path '<installation>' --exe-name shakesandfidget --use-processor attributeinjector --output-as diffable-cs --output-to metadata
Cpp2IL.exe --game-path '<installation>' --exe-name shakesandfidget --output-as isil --output-to native
```

Match the input hashes first. Compare the named methods and RVAs in the evidence
JSON with the annotated metadata and disassembly; RVAs are specific to this build.

## Stage-specific monster curses

The overview's `DotOverviewCard.AddDebuffIcon` calls
`DotLocaHelper.GetFloorDebuffInfo` using a zero-based floor index. The corresponding
icon fields explicitly distinguish the second, third and fourth floors. The
description selects these curse names:

| Stage | Ordinary rooms | Curse described by the client |
| --- | --- | --- |
| 1 | 1-24 | No monster-curse description |
| 2 | 26-49 | Gold Rush Hangover: ID 104, internal name Modesty |
| 3 | 51-74 | Poison: ID 102, internal name Poisoned |
| 4 | 76-99 | Broken Armor: ID 101 |

This mapping is confirmed **client overview behavior**. It is not a measured
drop rate, a server implementation, or proof of when the curse triggers. The
helper has no theme parameter. Actual theme exceptions, curse strength, failed
escape behavior and One Hit Wonder interactions still need observations.

The simulator currently samples combat curses from a shared table. The overview
mapping is therefore a concrete lead for replacing that approximation after its
live scope is checked; the existing 10% probability remains a placeholder.

## Escape and combat outcomes

`MonsterRoom.OnIgnore` creates a dungeon interaction request with action 21;
`OnInteract` uses action 20 for fighting. The escape coroutine reads the model's
returned HP difference to choose success/failure presentation. The inspected
path does not compute the escape roll locally.

There is a `Random.value > 0.5` check in the escape coroutine. Its result is passed
as the `mirror` argument to the monster attack animation, **not used to decide
whether escape succeeded**. Treating that constant as proof of 50% escape odds
would be incorrect.

Likewise, `iadungeonchances` sounds promising but its handler populates equipment
drop chances and weapon bad-luck counters. It is not the escape/barrel probability
table. No baseline escape probability or additive-versus-relative modifier rule
was established in this inspection. The same applies to the sampled combat
damage distribution.

## Recovery pricing and timing

The client calculates its displayed recovery fraction from the estimated server
time and `IADungeonLastTry`, using a divisor of 86,400 seconds and clamping to
0-1. The arithmetic uses single-precision floats.

The full-heal **UI quote** is:

```text
f = displayed recovery fraction, between 0 and 1
quoted mushrooms = clamp(48 - floor(48 * f), 0, 48)
```

At fractions 0%, 20%, 50%, 80% and 100%, this gives 48, 39, 24, 10 and 0 mushrooms.
The native multiplier is 48. The rounding helper uses downward rounding; it is
not rounding to the nearest integer. These are calculated examples, not recorded
purchases.

The 20% heal price is read from the server's `iadungeon20cost` response. Its
handler does not calculate a local price ladder. Neither finding establishes the
server's actual deductions, timer changes after purchases, or how full refills
affect the partial-heal purchase counter. The simulator's existing coefficient
48 now has direct client support for the quote, but those transaction details
remain provisional.

## Effects, shops and progress data

The client catalog includes eight blessing IDs (1-8), five curse IDs (101-105),
and 23 stone IDs. Catalog membership does not prove availability in a particular
run or theme.

Both `DungeonBuff` and `DungeonMerchantItem` decode a nonnegative packed integer:

```text
remaining uses / offered duration = packed / 10000, integer division
effect power = packed % 10000
```

Active effects also have a separate original-duration field. For example, a
synthetic packed value of 50080 decodes to five uses and power 80; this example
does not prove the meaning of power 80 in an escape calculation.

The merchant handler consumes triplets of effect ID, packed duration/power and
key price. It divides the response length by three; there is no leading offer
count in this handler. Rerolling sends action 80 and receives updated offers.
Offer generation, identity weights, strength probabilities and correlations are
not implemented by the inspected handler. The estimated 50/50 strength split
therefore remains unverified.

Stone records carry their ID, positive effect ID/value, negative effect ID/value,
and a room-generation effect ID. Those values are supplied in the response; the
existence of a `FleeChance` effect ID does not establish its arithmetic.

`iadungeonsave` describes current and previous HP, maximum HP, ordered effect
slots, effect powers, original durations, keys, room state and room choices.
These fields would support an offline progress importer and before/after
observations. No such importer or real response dataset was produced here.

## Themes and boss identities

Eight installed `dot_data_*` bundles contain monster presentation mappings and
boss overview identities: birthday, cthulhu, dragontemple, gloria, holiday, mmo,
treat and wizard. Their boss IDs are recorded in the evidence JSON. For example,
cthulhu lists 5297-5300; this corroborates the theme-specific boss IDs reported
in the earlier community research.

These are asset catalogs, not spawn tables or damage records. Some themes list
eight boss appearances and others four. This alone does not establish how a
first versus later run chooses appearances, monsters or stones.

The room enum also names Empire and Mariachi Band rooms (327 and 328). Their
server outcomes and correspondence to existing simulator encounters have not
been verified. A room name in an enum is insufficient to claim full mechanic
coverage.

## Remaining evidence needed

Escape probability and modifier arithmetic, barrel outcomes, monster-curse
frequency, damage distributions, joint door offers, hidden-room frequencies,
stone availability, and shop reroll distributions still require server-supplied
configuration if available, or observations from ordinary play. The next useful
data are complete before/after states with active effects and all offers, as
described in [DATA.md](DATA.md). In particular, validate the stage-curse mapping
and full-heal quote against live outcomes before treating them as server rules.
