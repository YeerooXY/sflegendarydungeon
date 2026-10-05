# Stone availability observations, 2026-10-05

The maintainer identified later-run stones using a supplied tier-list image.
Icons were matched to the named images on [LD Gadget](https://ldgadget.12hp.de/)
and, for Rusty Healing Stone, the [community guide](https://www.en.sfporadnik.pl/legendary-dungeon.php).
The image's strategy tiers are **not** used as a learned ranking.

| Image reference | Stone | Engine ID | Maintainer confidence: unavailable until a completion |
| --- | --- | --- | --- |
| Key pendant, A right | Pendant of the Key Master | `pendant` | Reported |
| B blue, right | Hope of the Thirsty One | `thirsty` | Reported |
| D first | Cursed Pearl | `pearl` | Certain |
| D second | Emerald of the Explorer | `explorer` | Certain |
| D fourth, orange | Rusty Healing Stone | `rusty` | Almost certain; provisional |
| E first | Sapphire of the Misadventurer | `misadventurer` | Certain |
| E second | Diamond of the Time Traveler | `time_traveler` | Certain |
| E fourth, epic doors | Crown Jewel of the Devil | `devil` | Certain |
| F first, double-locked doors | Lodestone | `lodestone` | Certain |

This is one player's availability report, not a measured server distribution.
The installed client sends gem data from the server and did not establish a
hardcoded first/later pool. Other restrictions could exist.

The default profile now uses **11 stones on the first run, 20 on subsequent
runs**. It includes Rusty Healing Stone on later runs, reflecting the supplied
image even though the inspected calculator omitted it. Masochist, Old Sacrifice
and Kidney remain implemented but excluded; the image does not establish their
current availability. Eligible stones have equal weights without replacement,
excluding already held stones. Equal weights are a modeling assumption.

To test the tentative Rusty restriction, append `rusty` to `gems_first_run` in
a copied profile. To test the pre-observation hypothesis, remove both run-specific
lines; the common pool becomes eligible in every run. Such profiles are different
experiments and must not be pooled with results from the default profile.

Starting at `run_number=2` selects the later pool immediately. Completing room
100 and restarting advances the run number automatically. An observed set of
three offers supplied via a progress file is preserved even when it differs
from a future-generation pool.

The maintainer also confirmed that a new run after room 100 starts at full HP.
`--restart-health full` is now grounded in this player observation. The `carry`
and `empty` options remain useful counterfactual sensitivity scenarios.
