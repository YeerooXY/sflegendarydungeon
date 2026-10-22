# VOD-INFORMED WORKING SCENARIO, 2026-10-09. Still partly synthetic.
# Current-event filtered observations inform pairs/styles, two mystery pools,
# trap frequency, ordinary joint loot and tier-five arena rewards.
# Damage and all remaining assumptions retain their documented sources.
# See docs/CURRENT_COMPARISON.md and docs/VOD_CONTAINER_CENSUS_2026_10_09.md.
schema=1
id=vod-working-2026-10-11-v4
evidence=synthetic
doors=locked:18.5222414789,cursed:3.81051415367,sacrifice:3.53552859619,wall:2.19988445985,destiny:1.8856152513,blessing:1.8856152513,wood:0.360100134797,stone:0.360100134797,souls:0.360100134797,metal:0.360100134797,arcane:0.360100134797,hourglasses:0.360100134797,monster:25,mystery:25,golden:8,shop:5,trial:1,unlocked:0,double_locked:0,epic:0
# Joint families: door/mystery, door/monster, mystery/mystery, door/golden,
# monster/mystery, golden/mystery, golden/monster, golden/golden.
# 2191 current-event offers without known door-generation modifier stones.
# Physical styles normalized to legacy mass34; six hungry resources split equally.
door_pairs=1287,394,278,50,99,71,11,1
# Isolated Spying cohort:96 unlocked/(328 locked+96 unlocked); rounded.
spying_unlock_fraction=0.23
# Beside a visible monster door, enemy-on-entry weights are removed and the
# remaining contents renormalized; a corpse may still awaken when searched.
mystery=monster:40,barrel:15,crate:15,silver:8,bronze:5,skeleton:5,mimic:2,empty:10
# Player-selected working value 2026-10-09: 80% initial monsters in the
# fallback mystery pool; rescale other outcomes while keeping their relative mix.
# Double-mystery and wall overrides below, and beside-monster exclusion, remain.
# This adopts the prior higher-monster scenario; it is not a newly measured rate.
mystery_without_monster=monster:80,barrel:4,crate:2.5,silver:1.5,bronze:1,skeleton:7.5,mimic:1,empty:2.5
# Exact alternative overrides:250 double-mystery interiors,63 mystery/wall.
# Chest identity unresolved: preserve2% overall mimic in double mystery,
# then use the same5/42 chest-conditional mimic share for wall; unmeasured.
# Remaining ordinary chest weight uses old silver:bronze8:5 imputation.
mystery_beside_mystery=monster:117,barrel:19,crate:34,silver:22.7692307692,bronze:14.2307692308,skeleton:22,mimic:5,empty:16
mystery_beside_wall=monster:55,barrel:0,crate:0,silver:0.542124542125,bronze:0.338827838828,skeleton:0,mimic:0.119047619048,empty:7
# Separate room contents behind a Destiny Door; probabilities are placeholders.
destiny=monster:40,barrel:15,crate:15,silver:8,bronze:5,skeleton:5,empty:12
# Mandatory spin: blessing, gold, lucky coins, hourglasses, mushrooms, curse.
# Equal weights are a starting guess, not measured wheel sectors.
destiny_rewards=1,1,1,1,1,1
# Gold units, lucky coins, hourglass donation units, mushrooms: guessed amounts.
destiny_reward_amounts=1,10,1,1
# Player reports empty key rooms. Use 10% provisionally; scale the old mix to 90%.
locked=barrel:22.5,crate:22.5,silver:18,bronze:9,skeleton:9,mimic:4.5,epic:4.5,empty:10
golden=fountain:10,cleansing_fountain:3,rocks:5,lava:3,narrator:5,flooded:2,wishing_well:5,rps:5,sewers:5,undead:4,sarcophagus:5,locked_sarcophagus:3,wood:5,wheel:5,spider_legs:3,spider_head:3,spider_full:3,souls:5,arcane:5,curse_shop:5
# Equal weights. Player image/observations 2026-10-05 supply the run restrictions.
# Rusty is restored from the player's image; its later-only restriction is tentative.
# Unlisted restrictions are unknown. See docs/GEM_POOLS.md for evidence and sensitivity.
gems=rabbit,moonstone,spying,pendant,greasy,gambler,greed,hero,time_traveler,thirsty,pearl,blood,explorer,misadventurer,devil,deceit,lodestone,bull,hick,rusty
# Player correction 2026-10-11: Rabbit is unavailable on the event's first run.
gems_first_run=moonstone,spying,greasy,gambler,greed,hero,blood,deceit,bull,hick
gems_later_runs=rabbit,moonstone,spying,pendant,greasy,gambler,greed,hero,time_traveler,thirsty,pearl,blood,explorer,misadventurer,devil,deceit,lodestone,bull,hick,rusty
# 2026-10-10 image audit: all offered stones leave the pool until run completion.
# False restores the former held-only exclusion for controlled comparisons.
exclude_offered_gems=true
# Endpoints from LD Gadget as inspected 2026-09-23. UNIFORM sampling is our assumption.
monster_damage=0.1178:0.1440,0.1683:0.2057,0.1457:0.2338,0.2104:0.2571
escape_damage=0.1178:0.1440,0.1683:0.2057,0.1457:0.2338,0.1731:0.2571
boss_damage=0.1608:0.1962,0.2295:0.2805,0.2295:0.2805,0.4303:0.5250
damage_distribution=uniform
trial_multipliers=1.0,1.1,1.2,1.3,1.4
# Player report 2026-10-05: room 51 offers a monster or the five-tier trial arena.
# Applied each run; event/run restrictions, door ordering and damage scaling need data.
# 0 restores the former weighted entrance placement (not the old flee behavior).
trial_entry_room=51
# Order: raider, one_hit, escape, disarm, lockpick, key_moment, elixir, recovery.
blessings=1,1,1,1,1,1,1,1
# Order: broken_armor, poison, clumsy, gold_hangover, hard_lock.
curses=1,1,1,1,1
# Legacy corpse loot only: blessing, item, gold, empty; still unmeasured.
containers=15,20,45,20
container_blessing_requires_greed=true
# Ordinary box/bronze/silver joint rewards;520 current complete known outcomes
# without Greed or Hero from the expanded census. Pooled styles/stages/runs.
# Mask bits:blessing1,item2,gold4;0meansnothing.
# A blessing includes instant healing; identity/strength still use effect tables.
container_joint=133,44,149,35,125,34,0,0
# Greed +20 percentage points is a working tooltip interpretation supported by
# observed24/58=41.38%, not a causal estimate of the hidden modifier formula.
# Move nonblessing mass to matching blessing masks, preserving item/gold odds.
# Baseline113/520=21.73%; with Greed41.73%. Corpse gate remains separate/unknown.
container_joint_greed=40299,31740,45147,29741,37875,26838,0,0
# Player observation 2026-10-08; instant potion only, independently unverified.
time_traveler_elixir_full_heal=true
# Current blind sample:30/96 pairs; fitting per-eligible-side rolls gives0.289879.
trap_chance=0.29
# No independent cursed-trap roll. Gambler converts every triggered trap to a curse.
barrel_blessing=0.50
strong_effect=0.25
# Shop offers have DISTINCT identities. Frequencies below remain hypothetical.
shop_blessings=1,1,1,1,1,1,1,1
shop_curses=1,1,1,1,1
# Player estimate on 2026-09-23, not an observed sample proportion.
shop_strong_effect=0.50
# First event run, room 5: One Hit (4 rooms) and Escape (5), both free.
# Player-confirmed: no rerolls. normal/fixed are alternate research scenarios only.
opening_shop_rerolls=disabled
# Account has two weapons or weapon + shield, per LD Gadget's Armory rule.
armory_bonus_eligible=false
armory_legendary_chance=0.10
# Placeholder: the auction promises an item, only sometimes epic.
auction_epic_chance=0.50
# Official F2P tutorial, 7:13: https://www.youtube.com/watch?v=jocVqAO6gkw&t=433s
# English transcript checked 2026-10-07; Rabbit adds 40 points, Moonstone adds 20.
escape_chance=0.40
key_chance=0.50
combat_curse_chance=0.10
# Client floor tooltip and community guide agree on identities; chance is unknown.
floor_specific_curses=true
# Legs/full estimates are placeholders; head's equal odds are officially described.
spider_success=0.80,0.50,0.20
# No sourced immediate bite-damage amount. Poison still applies after failure.
spider_bite_damage=0
# Community report; the official guide mentions only the blessing on an RPS win.
rps_epic_reward=true
skeleton_wakes=0.50
hidden_monster_chance=0.20
recovery_hours=24
action_seconds=2
# Hypothetical extension of first-use UI quotes; not a verified price schedule.
full_heal_cost_per_fraction=48
# Legacy tier1-4 behavior remains assumed; tier4 keeps1%.
# Tier5 uses the joint reward table below, overriding its legacy rate.
trial_legendary_chances=0,0,0,0.01,0.0625
# Current-event five-round chests,n48:gold,arcane,epic,legendary.
# Resource quantities remain abstract units, not calibrated live amounts.
trial_rewards_5=19,20,6,3
# Abstract donation units per resource, not live-game wood/stone quantities.
initial_resources=10
free_first_level_shops=true
# Player correction 2026-10-12: first run has a free room-five shop;
# later runs have no stage-one shop. Stages 2-4 each have one shop/wall.
# The ordinary door shop weight above is ignored under this schedule.
shop_schedule=one_per_stage
# User-selected 2026-10-09 default from reviewed shop positions. Uniform within
# these bounds is a modeling assumption; stage-one bounds are inactive below.
# See docs/VOD_SHOP_TIMING_2026_10_09.md for evidence and quota conflicts.
shop_windows=6:24,30:40,61:70,80:85
later_stage_one_shop=false
# Opening alternatives to a visible monster, before the fixed free shop.
# Existence is observed, frequencies are NOT fitted: 80% mystery, 8% each
# destiny/blessing, 4% hungry across six resources. No traps in rooms 1-5.
opening_doors=mystery:120,destiny:12,blessing:12,wood:1,stone:1,souls:1,metal:1,arcane:1,hourglasses:1
# Native barrel audit falsifies zero blessings with Traveler. Relative +50%
# curse chance is a provisional tooltip interpretation: 25% blessings at base50%.
time_traveler_barrel_curse_multiplier=1.5
