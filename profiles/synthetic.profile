# DEVELOPMENT SCENARIO. Unknown weights below are deliberately invented.
# Results from this profile are conditional experiments, not live-game estimates.
# See docs/MODEL.md for every implemented convention and excluded mechanic.
schema=1
id=synthetic-generic-v1
evidence=synthetic
doors=monster:25,mystery:25,locked:15,double_locked:0,unlocked:3,epic:3,golden:8,shop:5,cursed:3,sacrifice:3,blessing:2,destiny:2,wood:1,stone:1,souls:1,metal:1,arcane:1,hourglasses:1,trial:1,wall:3
mystery=monster:40,barrel:15,crate:15,silver:8,bronze:5,skeleton:5,mimic:2,empty:10
locked=barrel:25,crate:25,silver:20,bronze:10,skeleton:10,mimic:5,epic:5
golden=fountain:10,cleansing_fountain:3,rocks:5,lava:3,narrator:5,flooded:2,wishing_well:5,rps:5,sewers:5,undead:4,sarcophagus:5,locked_sarcophagus:3,wood:5,wheel:5,spider_legs:3,spider_head:3,spider_full:3,souls:5,arcane:5,curse_shop:5
# Current calculator pool; archived gems are implemented but excluded here.
gems=rabbit,moonstone,spying,pendant,greasy,gambler,greed,hero,time_traveler,thirsty,pearl,blood,explorer,misadventurer,devil,deceit,lodestone,bull,hick
# Endpoints from LD Gadget as inspected 2026-09-23. UNIFORM sampling is our assumption.
monster_damage=0.1178:0.1440,0.1683:0.2057,0.1457:0.2338,0.2104:0.2571
escape_damage=0.1178:0.1440,0.1683:0.2057,0.1457:0.2338,0.1731:0.2571
boss_damage=0.1608:0.1962,0.2295:0.2805,0.2295:0.2805,0.4303:0.5250
trial_multipliers=1.0,1.1,1.2,1.3,1.4
# Order: raider, one_hit, escape, disarm, lockpick, key_moment, elixir, recovery.
blessings=1,1,1,1,1,1,1,1
# Order: broken_armor, poison, clumsy, gold_hangover, hard_lock.
curses=1,1,1,1,1
# Order: blessing, normal item, gold, empty.
containers=15,20,45,20
trap_chance=0.10
cursed_trap_chance=0.05
barrel_blessing=0.50
strong_effect=0.25
# Shop offers have DISTINCT identities. Frequencies below remain hypothetical.
shop_blessings=1,1,1,1,1,1,1,1
shop_curses=1,1,1,1,1
# Player estimate on 2026-09-23, not an observed sample proportion.
shop_strong_effect=0.50
# Account has two weapons or weapon + shield, per LD Gadget's Armory rule.
armory_bonus_eligible=false
armory_legendary_chance=0.10
# Placeholder: the auction promises an item, only sometimes epic.
auction_epic_chance=0.50
escape_chance=0.50
key_chance=0.50
combat_curse_chance=0.10
skeleton_wakes=0.50
hidden_monster_chance=0.20
recovery_hours=24
action_seconds=2
# Hypothetical extension of first-use UI quotes; not a verified price schedule.
full_heal_cost_per_fraction=48
trial_legendary_chance=0.01
# Abstract donation units per resource, not live-game wood/stone quantities.
initial_resources=10
free_first_level_shops=true
