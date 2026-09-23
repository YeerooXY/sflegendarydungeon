# Shop and reroll experiments

Version 0.3 implements the maintainer's observed shop structure: two different
blessings, each with its own strength; buying one ends the visit. Rerolling
replaces both and costs one mushroom. It does not advance the room, consume
keys, age effects, or trigger room healing/poison. The mushroom cost is also
reported in the [public API notes](https://github.com/DasAoD/sf-ld-research/blob/4edd63fd0f628e89e0beb0dba959231c06173767/ld_api_research.md).
Effect-tick timing still needs comparison with recorded play.

| Policy | When it accepts an offer |
| --- | --- |
| `adaptive` | Any affordable effect with positive heuristic value after key cost and replacement |
| `one-hit` | A useful One Hit Wonder of either strength, before considering other offers |
| `one-hit-8` | A useful One Hit Wonder with at least eight rooms, before considering other offers |

Targeted policies can reroll even while another useful blessing is offered.
They stop when the target is bought, `--rerolls` is reached, or the shared
`--budget` runs out. They avoid searching when the target is unaffordable,
impossible under the profile, or already covered by an active One Hit effect.
Once searching stops, they fall back to the best useful current offer or leave.
They do not remember an offer that was discarded by a previous reroll.

These policies make repeatable comparisons possible; they are not proven optimal.
A One Hit offer lasts rooms, including empty rooms and bosses that consume time
without receiving its combat benefit. It does not prevent trap or poison damage.
Acquiring an eight-room offer with Time Traveler extends it to nine in this model.

## Separate spending objectives

```sh
# True free-to-play: no recovery purchases and no paid rerolls.
build/sfld simulate --allow-assumptions --budget 0 --runs 100000

# Allow mushrooms only for rerolls. All recovery is natural.
build/sfld compare --allow-assumptions --sweep shops --rerolls 10 --budget 30 --recovery-budget 0 --deadline-hours 72 --runs 100000 --output results/shops.json

# From an observed shop, compare reroll limits while hunting the strong offer.
build/sfld compare --state examples/reroll-shop.state --allow-assumptions --sweep rerolls --shop-policy one-hit-8 --budget 20 --recovery-budget 0 --runs 100000 --output results/one-hit-limits.json
```

On Windows with Visual Studio, use `build/Release/sfld.exe`.
The shop sweep includes a no-reroll baseline and all three policies. A reroll-limit
sweep uses caps 0, 1, 3 and 5 while retaining the selected policy. For a different
cap, run `simulate --rerolls N`. Already-used rerolls from an input snapshot count
against the per-shop limit, but earlier mushroom spending is not charged again.

The total budget is shared. `--recovery-budget 0` prevents healing from consuming
it before later shops. A positive recovery cap allows limited healing; omitting
the option allows healing to use the entire shared budget.

Reports include `shop_policy`, `reroll_limit`, `paid_recovery_budget`,
`mean_reroll_mushrooms`, `mean_recovery_mushrooms`, `mean_shop_purchases`, and
`mean_strong_one_hit_purchases`, alongside completion time and probability.
All means include unfinished attempts unless explicitly labeled completed-only.

## Distribution assumptions

`shop_blessings` lists eight identity weights in this order: Raider, One Hit,
Escape, Disarm, Lockpick, Key Moment, Elixir, Recovery. `shop_curses` lists Broken
Armor, Poison, Clumsy, Gold Hangover, Hard Lock. Both need at least two positive
weights. Missing tables inherit general effect weights. Sampling is without
replacement within a pair, but a reroll may repeat previous identities.

`shop_strong_effect=0.50` is the maintainer's rough impression on 2026-09-23.
There is no sample count. Strengths are drawn independently for the two offers;
both can be weak or both strong. Equal identity weights are an assumption.
Shop and barrel distributions are separate. Curse-shop offer correlation has
not been confirmed by the same observation.

For illustration only, with eight equally likely distinct identities and 50%
strong odds, an eight-room One Hit appears in a fresh pair with probability
`(2 / 8) * 0.5 = 12.5%`. Starting from an unsuccessful pair, five independent
rerolls have about a 48.7% chance to show it. This is a calculation under the
profile, not a measured game statistic, and does not imply it can be afforded
or that pursuing it is the best action.

To test sensitivity, copy the profile and vary `shop_strong_effect` and identity
weights. Do not replace unknown probabilities with a claim of calibration.
