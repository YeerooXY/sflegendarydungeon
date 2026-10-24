#include "sfld/batch.hpp"
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <chrono>

using namespace sfld;
namespace {
void require(bool ok, const char* expression, int line) {
    if (!ok) throw std::runtime_error("Line " + std::to_string(line) + ": " + expression);
}
#define CHECK(expression) require(bool(expression), #expression, __LINE__)
void near(double actual, double expected) { CHECK(std::abs(actual - expected) < 1e-9); }
template<class F> void rejects(F&& operation) {
    bool rejected = false;
    try { operation(); } catch (const std::exception&) { rejected = true; }
    CHECK(rejected);
}
// These fixtures exercise legacy knobs individually. Keep them independent of
// optional evidence tables in the shipped profile; vod_calibration.cpp covers
// the new tables and their interactions with the live mechanics.
Profile legacy_tables(Profile p) {
    p.opening_door_weights.reset(); p.later_stage_one_shop = true;
    p.time_traveler_barrel_curse_multiplier = 0;
    p.door_pair_weights.reset();
    p.shop_windows.reset();
    p.mystery_beside_mystery_weights.reset(); p.mystery_beside_wall_weights.reset();
    p.container_joint_weights.reset(); p.container_joint_greed_weights.reset();
    p.trial_reward_weights_5.reset();
    return p;
}
Profile profile() { return legacy_tables(Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile")); }
Profile fixed_damage(double damage = .2) {
    auto p = profile();
    p.monster_damage.fill({damage, damage}); p.escape_damage.fill({damage, damage});
    p.boss_damage.fill({.4, .4}); p.combat_curse_chance = 0; p.key_chance = 0;
    return p;
}
State encounter(Encounter e = Encounter::monster, int room = 1) {
    State s; s.phase = Phase::encounter; s.encounter = e; s.room = room; s.turn = room - 1;
    s.resources.fill(10); return s;
}
Effect effect(EffectKind k, double magnitude, int count, Clock clock = Clock::room) {
    return {k, magnitude, count, clock, 0};
}
struct Temp {
    std::filesystem::path path;
    Temp() {
        static unsigned id = 0;
        path = std::filesystem::temp_directory_path() / ("sfld-test-" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()) + "-" + std::to_string(id++) + ".txt");
    }
    ~Temp() { std::error_code ignored; std::filesystem::remove(path, ignored); }
};
void effects_replace_and_refresh() {
    Effects set;
    set.give(effect(EffectKind::one_hit, 1, 4));
    set.give(effect(EffectKind::recovery, .1, 3));
    set.give(effect(EffectKind::lockpick, 1, 2, Clock::door));
    set.give(effect(EffectKind::one_hit, 1, 8));
    CHECK(set.size == 3); CHECK(set.slots[0].remaining == 8);
    set.give(effect(EffectKind::one_hit, 1, 4));
    CHECK(set.size == 3); CHECK(set.slots[0].kind == EffectKind::one_hit);
    CHECK(set.slots[0].remaining == 4); // A weaker duplicate stays oldest.
    set.give(effect(EffectKind::disarm, 1, 4, Clock::trap));
    CHECK(!set.find(EffectKind::one_hit, 0)); CHECK(set.slots[0].kind == EffectKind::recovery);
    set.consume(Clock::room, 0);
    CHECK(set.find(EffectKind::recovery, 0)->remaining == 2);
    CHECK(set.find(EffectKind::lockpick, 0)->remaining == 2);
    set.consume(Clock::door, 0);
    CHECK(set.find(EffectKind::lockpick, 0)->remaining == 1);
}
void ordinary_damage_and_additive_modifiers() {
    auto p = fixed_damage(); auto s = encounter();
    s.gems[ix(Gem::hero)] = true; s.gems[ix(Gem::bull)] = true; s.gems[ix(Gem::rabbit)] = true;
    s.curses.give(effect(EffectKind::broken_armor, .5, 4));
    Game game(p, 1, s); game.step({ActionKind::fight});
    near(game.state().hp, .73); CHECK(game.state().room == 2);
}
void boss_ignores_combat_and_room_effects() {
    auto p = fixed_damage(); auto s = encounter(Encounter::boss, 25);
    s.gems[ix(Gem::hero)] = true; s.gems[ix(Gem::devil)] = true;
    s.blessings.give(effect(EffectKind::one_hit, 1, 4)); s.blessings.give(effect(EffectKind::recovery, .2, 3));
    s.curses.give(effect(EffectKind::broken_armor, .5, 4)); s.curses.give(effect(EffectKind::poison, .05, 5));
    Game game(p, 1, s); CHECK(!game.legal({ActionKind::flee})); game.step({ActionKind::fight});
    near(game.state().hp, .6); CHECK(game.state().phase == Phase::gems); CHECK(game.state().epics == 1);
    CHECK(game.state().blessings.find(EffectKind::one_hit, 25)->remaining == 3);
}
void one_hit_is_not_a_free_failed_escape() {
    auto p = fixed_damage(); p.escape_chance = 0;
    auto s = encounter(); s.blessings.give(effect(EffectKind::one_hit, 1, 4));
    Game flee(p, 1, s); flee.step({ActionKind::flee}); near(flee.state().hp, .8);
    Game fight(p, 1, s); fight.step({ActionKind::fight}); near(fight.state().hp, 1);
}
void failed_escape_has_separate_modifiers() {
    auto p = fixed_damage(); p.escape_chance = 0;
    auto s = encounter(); s.gems[ix(Gem::devil)] = true; s.gems[ix(Gem::hero)] = true;
    s.gems[ix(Gem::pearl)] = true; s.gems[ix(Gem::hick)] = true;
    Game game(p, 1, s); game.step({ActionKind::flee}); near(game.state().hp, .81);
}
void calibrated_damage_scope_is_optional_and_keeps_escape_only_stones() {
    auto p = fixed_damage(); p.fight_only_damage_stones = true; p.escape_chance = 0;
    for (auto gem : {Gem::hero, Gem::bull, Gem::deceit, Gem::rabbit, Gem::devil, Gem::pearl, Gem::hick}) {
        auto s = encounter(Encounter::monster, 61); s.gems[ix(gem)] = true;
        s.curses.give(effect(EffectKind::clumsy, .8, 4));
        s.curses.give(effect(EffectKind::broken_armor, .5, 4));
        const double fight_factor = (gem == Gem::hero || gem == Gem::bull || gem == Gem::deceit) ? 1.3 :
            (gem == Gem::rabbit || gem == Gem::devil) ? 1.75 : 1.5;
        const double flee_factor = gem == Gem::pearl ? 1.1 : gem == Gem::hick ? 1.8 : 1.5;
        Game fight(p, 1, s); fight.step({ActionKind::fight}); near(fight.state().hp, 1-.2*fight_factor);
        Game flee(p, 1, s); flee.step({ActionKind::flee}); near(flee.state().hp, 1-.2*flee_factor);
        s.encounter = Encounter::boss; s.room = 75;
        Game boss(p, 1, s); boss.step({ActionKind::fight}); near(boss.state().hp, .6);
    }
    auto s = encounter(); s.gems[ix(Gem::deceit)] = true;
    p.fight_only_damage_stones = false;
    Game legacy(p, 1, s); legacy.step({ActionKind::flee}); near(legacy.state().hp, .84);
}
void key_calibration_respects_spying_lodestone_and_key_moment() {
    auto p = fixed_damage(0); p.key_chance = .85;
    std::array<int, 4> counts{};
    for (unsigned seed = 0; seed < 12000; ++seed) {
        for (int group = 0; group < 4; ++group) {
            auto s = encounter(); s.gems[ix(Gem::spying)] = group == 1 || group == 3;
            s.gems[ix(Gem::lodestone)] = group == 2 || group == 3;
            Game game(p, seed, s); game.step({ActionKind::fight}); counts[group] += game.state().keys;
        }
    }
    CHECK(counts[0] > 9900 && counts[0] < 10500);
    CHECK(counts[1] > 8100 && counts[1] < 8700);
    CHECK(counts[2] == 12000 && counts[3] == 12000);
    p.spying_key_penalty = 1;
    auto s = encounter(); s.gems[ix(Gem::spying)] = true;
    Game zero(p, 1, s); zero.step({ActionKind::fight}); CHECK(zero.state().keys == 0);
    s.blessings.give(effect(EffectKind::key_moment, 1, 4, Clock::fight));
    Game moment(p, 1, s); moment.step({ActionKind::fight}); CHECK(moment.state().keys == 2);
    p.spying_key_penalty = -1; rejects([&] { p.validate(); });
    p.spying_key_penalty = 1.01; rejects([&] { p.validate(); });
    Temp text; std::ifstream original(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    std::ofstream output(text.path); output << original.rdbuf() << "\nfight_only_damage_stones=true\nspying_key_penalty=0.26666666666666666\n"; output.close();
    const auto parsed = Profile::load(text.path.string());
    CHECK(parsed.fight_only_damage_stones); near(parsed.spying_key_penalty, .26666666666666666);
}
void death_preserves_progress_and_clears_effects() {
    auto p = fixed_damage(); auto s = encounter(Encounter::monster, 37); s.hp = .1; s.keys = 7;
    s.gems[ix(Gem::hero)] = true; s.blessings.give(effect(EffectKind::lockpick, 1, 2, Clock::door));
    s.curses.give(effect(EffectKind::hard_lock, 2, 4));
    Game game(p, 1, s); game.step({ActionKind::fight});
    CHECK(game.state().phase == Phase::recovery); CHECK(game.state().room == 38); CHECK(game.state().keys == 7);
    CHECK(game.state().has(Gem::hero)); CHECK(game.state().blessings.size == 0); CHECK(game.state().curses.size == 0);
    CHECK(game.state().resources[0] == 10); near(game.state().hp, 0);
    const auto before = state_digest(game.state()); rejects([&] { game.step({ActionKind::reenter}); });
    CHECK(before == state_digest(game.state()));
    const double time = game.state().elapsed_hours;
    game.step({ActionKind::wait, 0, 4.8}); near(game.state().hp, .2); near(game.state().elapsed_hours - time, 4.8);
    game.step({ActionKind::reenter}); CHECK(game.state().phase == Phase::doors);
}
void recovery_precedes_poison_without_repeating_room_rewards() {
    auto p = fixed_damage(); auto s = encounter(Encounter::sarcophagus, 12); s.hp = .04;
    s.curses.give(effect(EffectKind::poison, .05, 5)); s.blessings.give(effect(EffectKind::recovery, .2, 3));
    Game protected_exit(p, 1, s); protected_exit.step({ActionKind::interact});
    CHECK(protected_exit.state().phase == Phase::doors); near(protected_exit.state().hp, .19);
    CHECK(protected_exit.state().room == 13); CHECK(protected_exit.state().gold_rewards == 1);
    std::vector<std::string> health_events;
    for (const auto& o : protected_exit.outcomes()) if (o.kind == "healing" || o.kind == "damage") health_events.emplace_back(o.kind);
    CHECK(health_events == std::vector<std::string>({"healing", "damage"}));
    s.hp = .99; Game capped(p, 1, s); capped.step({ActionKind::interact}); near(capped.state().hp, .95);
    s.hp = .04; s.blessings = {}; s.blessings.give(effect(EffectKind::recovery, .005, 3));
    Game game(p, 1, s); game.step({ActionKind::interact});
    CHECK(game.state().phase == Phase::recovery); near(game.state().hp, 0); CHECK(game.state().room == 13);
    CHECK(game.state().gold_rewards == 1);
    game.step({ActionKind::wait, 0, 4.8}); game.step({ActionKind::reenter});
    CHECK(game.state().phase == Phase::doors); CHECK(game.state().gold_rewards == 1);
}
void acquired_effect_starts_in_next_room() {
    auto p = fixed_damage(); auto s = encounter(Encounter::empty, 10); s.phase = Phase::shop;
    s.offers[0] = {effect(EffectKind::one_hit, 1, 4), 0};
    Game shop(p, 1, s); shop.step({ActionKind::buy, 0});
    CHECK(shop.state().effect(EffectKind::one_hit)); CHECK(shop.state().effect(EffectKind::one_hit)->remaining == 4);
    auto next = shop.state(); next.phase = Phase::encounter; next.encounter = Encounter::monster;
    Game fight(p, 2, next); fight.step({ActionKind::fight}); near(fight.state().hp, 1);
    CHECK(fight.state().effect(EffectKind::one_hit)->remaining == 3);
}
void purchased_recovery_heals_before_shop_exit_poison() {
    auto p = fixed_damage(); auto s = encounter(Encounter::empty, 30);
    s.phase = Phase::shop; s.hp = .04; s.keys = 2;
    s.curses.give(effect(EffectKind::poison, .05, 5));
    s.offers[0] = {effect(EffectKind::recovery, .1, 3), 1};
    for (bool refresh : {false, true}) {
        auto start = s;
        if (refresh) start.blessings.give(effect(EffectKind::recovery, .1, 2));
        Game shop(p, 1, start); shop.step({ActionKind::buy, 0});
        near(shop.state().hp, .09); CHECK(shop.state().deaths == 0);
        CHECK(shop.state().room == 31); CHECK(shop.state().phase == Phase::doors);
        CHECK(shop.state().keys == 1); CHECK(shop.state().shop_purchases == 1);
        CHECK(shop.state().effect(EffectKind::recovery)->remaining == 2);
        CHECK(shop.state().effect(EffectKind::poison)->remaining == 4);
        std::vector<std::string> health_events;
        for (const auto& event : shop.outcomes())
            if (event.kind == "healing" || event.kind == "damage") health_events.emplace_back(event.kind);
        CHECK(health_events == std::vector<std::string>({"healing", "damage"}));
    }
    s.hp = .99; Game capped(p, 1, s); capped.step({ActionKind::buy, 0}); near(capped.state().hp, .95);
    s.hp = .04; s.gems[ix(Gem::time_traveler)] = true;
    Game extended(p, 1, s); extended.step({ActionKind::buy, 0});
    near(extended.state().hp, .09); CHECK(extended.state().effect(EffectKind::recovery)->remaining == 3);
    s.gems.fill(false); s.curses = {}; s.curses.give(effect(EffectKind::poison, .2, 5));
    Game lethal(p, 1, s); lethal.step({ActionKind::buy, 0});
    CHECK(lethal.state().deaths == 1); CHECK(lethal.state().room == 31);
    CHECK(lethal.state().keys == 1); CHECK(lethal.state().phase == Phase::recovery);
}
void chest_recovery_heals_on_acquisition_exit_and_consumes_one_use() {
    auto p = fixed_damage(); p.container_joint_weights.reset();
    p.container_blessing_requires_greed = false; p.container_weights = {1, 0, 0, 0};
    p.blessing_weights.fill(0); p.blessing_weights[ix(EffectKind::recovery)] = 1;
    p.strong_effect = 1;
    for (bool refresh : {false, true}) {
        auto s = encounter(Encounter::silver, 64); s.hp = .04;
        s.curses.give(effect(EffectKind::poison, .05, 3));
        if (refresh) s.blessings.give(effect(EffectKind::recovery, .1, 2));
        Game chest(p, 1, s); chest.step({ActionKind::interact});
        near(chest.state().hp, .19); CHECK(chest.state().deaths == 0);
        CHECK(chest.state().effect(EffectKind::recovery)->remaining == 5);
        CHECK(chest.state().effect(EffectKind::poison)->remaining == 2);
        CHECK(chest.state().room == 65);
    }
}
void combat_curses_require_a_strike_without_one_hit() {
    auto p = fixed_damage(.1); p.combat_curse_chance = 1; p.strong_effect = 1;
    p.floor_specific_curses = true;
    auto s = encounter(Encounter::monster, 80);
    Game ordinary(p, 1, s); ordinary.step({ActionKind::fight});
    CHECK(ordinary.state().effect(EffectKind::broken_armor));
    CHECK(ordinary.state().effect(EffectKind::broken_armor)->remaining == 8);
    s.blessings.give(effect(EffectKind::one_hit, 1, 4));
    s.gems[ix(Gem::misadventurer)] = true;
    Game instant(p, 1, s); instant.step({ActionKind::fight});
    near(instant.state().hp, 1); CHECK(instant.state().curses.size == 0);
    for (double chance : {0., 1.}) {
        p.escape_chance = chance;
        Game escape(p, 1, s); escape.step({ActionKind::flee});
        CHECK(escape.state().curses.size == 0);
        near(escape.state().hp, chance == 1 ? 1 : .9);
    }
    s.blessings = {}; s.gems.fill(false); s.gems[ix(Gem::pearl)] = true;
    p.escape_chance = 1; int pearl_curses = 0;
    for (unsigned seed = 0; seed < 100; ++seed) {
        Game pearl(p, seed, s); pearl.step({ActionKind::flee});
        pearl_curses += pearl.state().curses.size > 0;
    }
    CHECK(pearl_curses > 0 && pearl_curses < 100);
}
void losing_lockpick_on_exit_death_keeps_a_legal_path() {
    auto p = fixed_damage(); p.door_weights.fill(0); p.door_weights[ix(Door::locked)] = 1;
    auto s = encounter(Encounter::empty, 12); s.hp = .04; s.keys = 0;
    s.blessings.give(effect(EffectKind::lockpick, 1, 2, Clock::door));
    s.curses.give(effect(EffectKind::poison, .05, 5));
    Game game(p, 1, s); game.step({ActionKind::interact});
    CHECK(game.state().room == 13); CHECK(game.state().phase == Phase::recovery);
    game.step({ActionKind::wait, 0, 4.8}); game.step({ActionKind::reenter});
    CHECK(game.state().phase == Phase::doors); CHECK(!game.state().effect(EffectKind::lockpick));
    CHECK(game.available(game.state().doors[0]) || game.available(game.state().doors[1]));
    Policy policy; CHECK(game.legal(policy.choose(game)));
    // Previously failed at room 96 during the larger barrel experiment.
    auto original = profile(); policy.barrels = BarrelPolicy::skip;
    const auto result = run_one(original, policy, 16852218668139171575ULL, {});
    CHECK(!result.action_limit); CHECK(result.complete);
}
void greasy_removes_epic_chests_from_mystery_doors_too() {
    auto p = fixed_damage(); p.mystery_without_monster_weights.reset(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::epic)] = 1;
    auto s = encounter(); s.phase = Phase::doors; s.doors = {{{Door::mystery}, {Door::wall}}};
    s.gems[ix(Gem::greasy)] = true;
    Game game(p, 1, s); game.step({ActionKind::choose_door, 0});
    CHECK(game.state().encounter == Encounter::empty);
}
void greasy_recovery_requires_surviving_the_room() {
    auto p = fixed_damage(.05); p.shop_schedule = "weighted"; p.trap_chance = 0; p.door_weights.fill(0); p.door_weights[ix(Door::monster)] = 1; auto s = encounter(Encounter::monster, 30); s.hp = .04; s.gems[ix(Gem::greasy)] = true;
    Game game(p, 1, s); game.step({ActionKind::fight}); game.step({ActionKind::wait, 0, 4.8}); game.step({ActionKind::reenter});
    near(game.state().hp, .2); CHECK(game.state().effect(EffectKind::recovery)->remaining == 3);
    game.step({ActionKind::choose_door, game.state().doors[0].kind == Door::monster ? 0 : 1});
    game.step({ActionKind::fight}); near(game.state().hp, .25); CHECK(game.state().effect(EffectKind::recovery)->remaining == 2);
}
void paid_steps_escalate_and_respect_hard_budget() {
    auto p = fixed_damage(); auto s = encounter(); s.phase = Phase::recovery; s.hp = 0;
    Game game(p, 1, s, {45});
    game.step({ActionKind::heal_step}); CHECK(game.state().mushrooms == 10);
    game.step({ActionKind::heal_step}); CHECK(game.state().mushrooms == 25);
    game.step({ActionKind::heal_step}); CHECK(game.state().mushrooms == 45); near(game.state().hp, .6);
    const auto before = state_digest(game.state()); rejects([&] { game.step({ActionKind::heal_step}); });
    CHECK(before == state_digest(game.state())); CHECK(game.state().recovery_mushrooms == 45);
    Game zero(p, 2, s, {0}); CHECK(!zero.legal({ActionKind::heal_step})); CHECK(!zero.legal({ActionKind::heal_full}));
}
void full_refill_is_separate_from_step_schedule() {
    auto p = fixed_damage(); auto s = encounter(); s.phase = Phase::recovery; s.hp = .03;
    Game game(p, 1, s, {47}); CHECK(game.full_price() == 47); game.step({ActionKind::heal_full});
    near(game.state().hp, 1); CHECK(game.state().mushrooms == 47); CHECK(game.state().paid_steps == 1);
}
void paid_refills_are_available_only_during_recovery() {
    auto p = fixed_damage();
    for (const auto phase : {Phase::doors, Phase::encounter, Phase::shop, Phase::curse_shop, Phase::gems, Phase::complete}) {
        auto s = encounter(Encounter::monster, 30); s.phase = phase; s.hp = .1;
        Game game(p, 1, s, {100});
        const auto before = state_digest(game.state());
        for (const auto kind : {ActionKind::heal_step, ActionKind::heal_full}) {
            CHECK(!game.legal({kind}));
            rejects([&] { game.step({kind}); });
            CHECK(before == state_digest(game.state()));
        }
    }
    auto s = encounter(Encounter::monster, 30); s.phase = Phase::recovery; s.hp = .2;
    Game game(p, 1, s, {100});
    CHECK(game.legal({ActionKind::heal_step})); CHECK(game.legal({ActionKind::heal_full}));
    game.step({ActionKind::heal_step}); near(game.state().hp, .4);
    game.step({ActionKind::reenter});
    CHECK(!game.legal({ActionKind::heal_step})); CHECK(!game.legal({ActionKind::heal_full}));
}
void locked_trap_does_not_charge_twice_after_death() {
    auto p = fixed_damage(); p.locked_weights.fill(0); p.locked_weights[ix(Encounter::monster)] = 1;
    auto s = encounter(Encounter::monster, 6); s.phase = Phase::doors; s.hp = .05; s.keys = 1;
    s.doors = {{{Door::locked, true}, {Door::wall}}};
    Game game(p, 1, s); game.step({ActionKind::choose_door, 0});
    CHECK(game.state().keys == 0); CHECK(game.state().phase == Phase::recovery);
    game.step({ActionKind::wait, 0, 4.8}); game.step({ActionKind::reenter});
    CHECK(game.state().phase == Phase::doors); CHECK(game.state().room == 7); CHECK(game.state().keys == 0); near(game.state().hp, .2);
}
void lockpick_disarm_use_event_counters() {
    auto p = fixed_damage(); p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    auto s = encounter(Encounter::monster, 6); s.phase = Phase::doors; s.doors = {{{Door::double_locked, true}, {Door::wall}}};
    s.blessings.give(effect(EffectKind::lockpick, 1, 2, Clock::door));
    s.blessings.give(effect(EffectKind::disarm, 1, 4, Clock::trap));
    s.curses.give(effect(EffectKind::hard_lock, 2, 4));
    Game game(p, 1, s); CHECK(game.door_cost(Door::double_locked) == 0); game.step({ActionKind::choose_door, 0});
    near(game.state().hp, 1); CHECK(game.state().effect(EffectKind::lockpick)->remaining == 1);
    CHECK(game.state().effect(EffectKind::disarm)->remaining == 3);
    game.step({ActionKind::interact}); CHECK(game.state().effect(EffectKind::lockpick)->remaining == 1);
}
void diamond_overrides_gambler_for_barrels() {
    auto p = fixed_damage(); p.barrel_blessing = 1; p.curse_weights = {0, 0, 0, 1, 0};
    auto s = encounter(Encounter::barrel, 40); s.gems[ix(Gem::time_traveler)] = true; s.gems[ix(Gem::gambler)] = true;
    Game game(p, 1, s); game.step({ActionKind::interact});
    CHECK(game.state().curses.size == 1); CHECK(game.state().blessings.size == 0); CHECK(game.state().barrels_opened == 1);
}
void only_offered_gems_can_be_selected() {
    auto p = fixed_damage(); auto s = encounter(); s.phase = Phase::gems;
    s.gem_offers = {Gem::greasy, Gem::hero, Gem::bull};
    Policy policy; policy.preferred_gem = Gem::rabbit;
    Game game(p, 1, s); const auto action = policy.choose(game);
    CHECK(action.index == 0); game.step(action); CHECK(game.state().has(Gem::greasy)); CHECK(!game.state().has(Gem::rabbit));
}
void free_shops_are_first_run_only() {
    auto p = fixed_damage(); Limits later_limits; later_limits.run_number = 2;
    Game from_limits(p, 1, later_limits); CHECK(from_limits.state().run_number == 2);
    auto s = encounter(); s.phase = Phase::doors; s.room = 5;
    s.doors = {{{Door::shop}, {Door::wall}}};
    Game first(p, 1, s); first.step({ActionKind::choose_door, 0}); CHECK(first.state().offers[0].keys == 0);
    s.run_number = 2;
    Game later(p, 1, s); later.step({ActionKind::choose_door, 0}); CHECK(later.state().offers[0].keys > 0);
}
void trial_exit_and_death_reset() {
    auto p = fixed_damage(.1); auto s = encounter(Encounter::trial_monster, 26); s.trial_seen = true;
    Game win(p, 1, s); win.step({ActionKind::fight}); CHECK(win.state().trial_depth == 1);
    win.step({ActionKind::choose_door, 1}); CHECK(win.state().encounter == Encounter::prize);
    win.step({ActionKind::interact}); CHECK(win.state().trial_depth == 0); CHECK(win.state().trial_seen);
    s.hp = .01;
    Game death(p, 1, s); death.step({ActionKind::fight}); CHECK(death.state().phase == Phase::recovery);
    death.step({ActionKind::wait, 0, 4.8}); death.step({ActionKind::reenter});
    CHECK(death.state().phase == Phase::doors); CHECK(death.state().trial_seen); CHECK(death.state().trial_depth == 0);
}
void trial_entrance_is_offered_at_51_and_can_be_declined() {
    auto p = fixed_damage(.01);
    // Even a custom table favoring trials must not move the fixed entrance.
    p.door_weights.fill(0); p.door_weights[ix(Door::trial)] = 1;
    for (int run : {1, 2}) {
        auto s = encounter(Encounter::boss, 50); s.run_number = run;
        Game game(p, 11, s); game.step({ActionKind::fight}); game.step({ActionKind::choose_gem, 0});
        CHECK(game.state().room == 51);
        CHECK(game.state().doors[0].kind == Door::monster); CHECK(game.state().doors[1].kind == Door::trial);
        CHECK(!game.state().doors[0].trap); CHECK(!game.state().doors[1].trap);
        game.step({ActionKind::choose_door, 0}); game.step({ActionKind::fight});
        CHECK(game.state().room == 52); CHECK(game.state().trial_depth == 0);
        for (auto door : game.state().doors) CHECK(door.kind != Door::trial);
    }
    for (int room : {26, 49, 52, 76}) {
        Game game(p, 11, encounter(Encounter::empty, room)); game.step({ActionKind::interact});
        for (auto door : game.state().doors) CHECK(door.kind != Door::trial);
    }
    p.trial_entry_room = 0;
    Game weighted(p, 11, encounter(Encounter::empty, 26)); weighted.step({ActionKind::interact});
    CHECK(weighted.state().doors[0].kind == Door::trial || weighted.state().doors[1].kind == Door::trial);
    for (int room : {-1, 25, 44, 50, 94}) { p.trial_entry_room = room; rejects([&] { p.validate(); }); }
}
void trial_fights_and_both_escape_outcomes_advance_to_five() {
    for (int route = 0; route < 3; ++route) {
        auto p = fixed_damage(.01); p.escape_chance = route == 0 ? 1 : 0;
        p.trial_legendary_chances = {0, 0, 0, 1, 1};
        auto s = encounter(Encounter::trial_monster, 51); s.trial_seen = true;
        Game game(p, 7, s);
        for (int tier = 1; tier <= 5; ++tier) {
            const double before = game.state().hp;
            near(game.damage_ceiling(false), .01 * p.trial_multipliers[static_cast<std::size_t>(tier - 1)]);
            game.step({route == 2 ? ActionKind::fight : ActionKind::flee});
            CHECK(game.state().trial_depth == tier); CHECK(game.state().room == 51 + tier);
            near(before - game.state().hp, route == 0 ? 0 : .01 * p.trial_multipliers[static_cast<std::size_t>(tier - 1)]);
            CHECK(game.state().legendaries == 0); CHECK(game.state().epics == 0);
            CHECK(game.state().gold_rewards == 0); CHECK(game.state().pending_trial_reward == 0);
            CHECK(game.state().doors[1].kind == Door::exit_trial);
            if (tier < 5) game.step({ActionKind::choose_door, 0});
        }
        CHECK(!game.legal({ActionKind::choose_door, 0}));
        game.step({ActionKind::choose_door, 1});
        CHECK(game.state().encounter == Encounter::prize); CHECK(game.state().pending_trial_reward == 5);
        game.step({ActionKind::interact});
        CHECK(game.state().legendaries == 1); CHECK(game.state().completed_runs == 0);
        CHECK(game.state().trial_depth == 0); CHECK(game.state().pending_trial_reward == 0);
        CHECK(game.state().room == 57);
    }
}
void trial_exit_reward_uses_survived_tier_and_can_be_skipped() {
    auto p = fixed_damage(.01); p.escape_chance = 1; p.trial_legendary_chances = {0, 0, 0, 1, 1};
    for (int tier = 1; tier <= 5; ++tier) {
        auto s = encounter(Encounter::trial_monster, 51); s.trial_seen = true;
        Game game(p, 7, s);
        for (int i = 1; i <= tier; ++i) {
            game.step({ActionKind::flee});
            if (i < tier) game.step({ActionKind::choose_door, 0});
        }
        game.step({ActionKind::choose_door, 1}); CHECK(game.state().pending_trial_reward == tier);
        Game skipped = game; skipped.step({ActionKind::skip});
        CHECK(skipped.state().pending_trial_reward == 0); CHECK(skipped.state().legendaries == 0);
        CHECK(skipped.state().epics == 0); CHECK(skipped.state().gold_rewards == 0);
        game.step({ActionKind::interact});
        CHECK(game.state().gold_rewards == (tier == 1 ? 1 : 0));
        CHECK(game.state().epics == (tier >= 2 && tier < 4 ? 1 : 0));
        CHECK(game.state().legendaries == (tier >= 4 ? 1 : 0));
    }
}
void trial_death_forfeits_reward_and_resumes_normal_rooms() {
    auto p = fixed_damage(.1); p.escape_chance = 0; p.trap_chance = 0;
    p.door_weights.fill(0); p.door_weights[ix(Door::trial)] = 1;
    for (int depth : {0, 2, 4}) for (auto action : {ActionKind::fight, ActionKind::flee}) {
        auto s = encounter(Encounter::trial_monster, 51 + depth); s.trial_seen = true;
        s.trial_depth = depth; s.hp = .01;
        Game game(p, 7, s); game.step({action});
        CHECK(game.state().phase == Phase::recovery); CHECK(game.state().room == 52 + depth);
        CHECK(game.state().trial_depth == 0); CHECK(game.state().pending_trial_reward == 0);
        CHECK(game.state().legendaries == 0); CHECK(game.state().epics == 0); CHECK(game.state().gold_rewards == 0);
        game.step({ActionKind::wait, 0, 4.8}); game.step({ActionKind::reenter});
        CHECK(game.state().phase == Phase::doors);
        for (auto door : game.state().doors) CHECK(door.kind != Door::trial);
        game.step({ActionKind::choose_door, game.state().doors[0].kind == Door::monster ? 0 : 1}); game.step({ActionKind::fight});
        CHECK(game.state().room == 53 + depth); CHECK(game.state().trial_depth == 0);
    }
    auto s = encounter(Encounter::trial_monster, 51); s.trial_seen = true; s.hp = .05;
    s.curses.give(effect(EffectKind::poison, .05, 2)); p.escape_chance = 1;
    Game poison(p, 7, s); poison.step({ActionKind::flee});
    CHECK(poison.state().phase == Phase::recovery); CHECK(poison.state().room == 52);
    CHECK(poison.state().trial_depth == 0); CHECK(poison.state().pending_trial_reward == 0);
    // An observed trapped exit must not preserve an unclaimed reward after death.
    s = encounter(Encounter::empty, 54); s.phase = Phase::doors; s.trial_seen = true;
    s.trial_depth = 3; s.hp = .01; s.doors = {{{Door::wall}, {Door::exit_trial, true}}};
    Game exit_death(p, 7, s); exit_death.step({ActionKind::choose_door, 1});
    CHECK(exit_death.state().phase == Phase::recovery); CHECK(exit_death.state().pending_trial_reward == 0);
    CHECK(exit_death.recovery_destination() == Phase::doors); CHECK(exit_death.state().legendaries == 0);
}
void flooded_room_enforces_timer() {
    auto p = fixed_damage(); auto s = encounter(Encounter::flooded, 30);
    Game slow(p, 1, s); slow.step({ActionKind::linger, 0, 10}); CHECK(slow.state().phase == Phase::recovery);
    Game fast(p, 1, s); fast.step({ActionKind::skip}); CHECK(fast.state().room == 31);
}
void all_encounters_have_a_transition() {
    auto p = fixed_damage(.01);
    for (std::size_t n = 0; n < ix(Encounter::count); ++n) {
        const auto e = static_cast<Encounter>(n);
        auto s = encounter(e, e == Encounter::boss ? 25 : 30); s.keys = 10; s.donated_resource = 1; s.pending_trial_reward = 4;
        Game game(p, 5, s);
        Action action{is_enemy(e) ? ActionKind::fight : ActionKind::interact};
        CHECK(game.legal(action)); game.step(action); CHECK(game.state().actions == 1);
        CHECK(game.state().hp >= 0 && game.state().hp <= 1); CHECK(game.state().keys >= 0);
    }
}
void final_boss_completes_without_an_extra_epic() {
    auto p = fixed_damage(); auto s = encounter(Encounter::boss, 100);
    Game game(p, 1, s); game.step({ActionKind::fight});
    CHECK(game.state().phase == Phase::complete); CHECK(game.state().legendaries == 1); CHECK(game.state().epics == 0);
    CHECK(!game.legal({ActionKind::fight}));
}
void boss_reentry_policy_waits_for_enough_health() {
    auto p = fixed_damage(); auto s = encounter(Encounter::boss, 100); s.phase = Phase::recovery; s.hp = 0;
    Game game(p, 1, s); Policy policy; auto a = policy.choose(game);
    CHECK(a.kind == ActionKind::wait); near(a.value, .401 * 24); game.step(a);
    CHECK(policy.choose(game).kind == ActionKind::reenter);
}
void login_grid_is_included_in_wait() {
    auto p = fixed_damage(); auto s = encounter(); s.phase = Phase::recovery; s.hp = 0; s.elapsed_hours = 1;
    Game game(p, 1, s); Policy policy; policy.login_interval_hours = 8;
    auto a = policy.choose(game); near(a.value, 7);
}
void threaded_runs_are_reproducible_and_budget_bounded() {
    auto p = profile(); Policy policy; policy.reroll_limit = 2;
    BatchOptions opts; opts.runs = 128; opts.seed = 99; opts.limits.budget = 25;
    const auto one = run_batch(p, policy, opts); opts.threads = 4;
    const auto four = run_batch(p, policy, opts);
    CHECK(report_json(p, policy, opts, one) == report_json(p, policy, opts, four));
    for (const auto& r : four) { CHECK(r.mushrooms <= 25); CHECK(r.mushrooms == r.recovery_mushrooms + r.reroll_mushrooms); CHECK(!r.action_limit); }
    opts.limits.budget = 0;
    for (const auto& r : run_batch(p, policy, opts)) CHECK(r.mushrooms == 0);
}
void censored_runs_are_not_dropped() {
    auto p = profile(); Policy policy; BatchOptions opts; opts.runs = 4; opts.limits.deadline_hours = 1e-7;
    const auto results = run_batch(p, policy, opts);
    for (const auto& r : results) { CHECK(!r.complete); near(r.hours, opts.limits.deadline_hours); }
    const auto json = report_json(p, policy, opts, results);
    CHECK(json.find("\"completed_only_mean_hours\": null") != std::string::npos);
    CHECK(json.find("\"unfinished\": 4") != std::string::npos);
    CHECK(json.find("\"total_mushrooms_divided_by_completions\": null") != std::string::npos);
}
void action_cap_invalidates_deadline_statistics() {
    auto p = profile(); Policy policy; BatchOptions opts; opts.runs = 2; opts.limits.max_actions = 1;
    const auto results = run_batch(p, policy, opts); const auto json = report_json(p, policy, opts, results);
    CHECK(results[0].action_limit); CHECK(json.find("\"valid_deadline_experiment\": false") != std::string::npos);
    CHECK(json.find("\"restricted_mean_hours_at_deadline\": null") != std::string::npos);
}
void trace_replays_and_rejects_tampering() {
    auto p = profile(); Policy policy; Temp trace;
    { std::ofstream out(trace.path); run_one(p, policy, 12, {}, &out); }
    CHECK(replay(p, trace.path.string()) > 100);
    auto other = p; other.fingerprint = "wrong";
    rejects([&] { replay(other, trace.path.string()); });
    std::ifstream input(trace.path); std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>()); input.close();
    const auto index = text.find("choose_door"); CHECK(index != std::string::npos); text.replace(index, 11, "skip");
    { std::ofstream out(trace.path); out << text; }
    rejects([&] { replay(p, trace.path.string()); });
}
void profile_validation_rejects_guesses_disguised_as_verified() {
    auto p = profile(); p.evidence = "verified"; rejects([&] { p.validate(); });
    p = profile(); p.barrel_blessing = 1.1; rejects([&] { p.validate(); });
    p = profile(); p.door_weights[0] = -1; rejects([&] { p.validate(); });
    p = profile(); p.gem_pool = {Gem::rabbit}; rejects([&] { p.validate(); });
    Temp file;
    std::ifstream input(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    { std::ofstream out(file.path); out << text << "\nbarrel_typo=0.5\n"; }
    rejects([&] { Profile::load(file.path.string()); });
    { std::ofstream out(file.path); out << text << "\nbarrel_blessing=0.9\n"; }
    rejects([&] { Profile::load(file.path.string()); });
}
StartState example(const char* file) {
    return StartState::load(std::string(SFLD_SOURCE_DIR) + "/examples/" + file);
}
void progress_preserves_observed_doors_and_walls() {
    auto p = profile(); auto start = example("progress.state");
    for (std::uint64_t seed = 0; seed < 20; ++seed) {
        Game game(p, seed, start); CHECK(game.state().room == 42); CHECK(game.state().run_number == 2);
        CHECK(game.state().doors[0].kind == Door::golden); CHECK(game.state().doors[1].kind == Door::wall);
        CHECK(game.legal({ActionKind::choose_door, 0})); CHECK(!game.legal({ActionKind::choose_door, 1}));
        near(game.state().hp, .63); CHECK(game.state().keys == 4); CHECK(game.state().has(Gem::rabbit));
    }
    start.state.doors = {{{Door::locked}, {Door::wall}}}; start.state.keys = 0;
    rejects([&] { Game invalid(p, 1, start); });
    start.state.doors = {{{Door::wall}, {Door::wall}}};
    rejects([&] { Game invalid(p, 1, start); });
}
void progress_effects_apply_with_remaining_counters() {
    const auto start = StartState::parse("schema=1\nroom=42\nrun_number=2\nphase=encounter\nhp_percent=50\nkeys=4\n"
        "gems=hero\nencounter=monster\nblessings=recovery:weak:2\ncurses=broken_armor:weak:2,poison:weak:1\n");
    auto p = fixed_damage(); Game game(p, 5, start); game.step({ActionKind::fight});
    near(game.state().hp, .29); CHECK(game.state().room == 43);
    CHECK(!game.state().effect(EffectKind::poison)); CHECK(game.state().effect(EffectKind::recovery)->remaining == 1);
    CHECK(game.state().effect(EffectKind::broken_armor)->remaining == 1);
}
void resumed_recovery_retains_heal_prices_and_future_budget() {
    auto p = fixed_damage(); auto start = example("recovery.state");
    Game game(p, 7, start, {20}); CHECK(game.step_price() == 20);
    game.step({ActionKind::heal_step}); near(game.state().hp, .3); CHECK(game.state().mushrooms == 20);
    game.step({ActionKind::reenter}); CHECK(game.state().phase == Phase::encounter); CHECK(game.state().encounter == Encounter::boss);
    Policy policy; const auto result = run_one(p, policy, 7, {20, 3}, nullptr, &start);
    CHECK(result.complete); CHECK(result.mushrooms == 20); CHECK(result.deaths == 0); CHECK(result.hours < 3);
    const auto free = run_one(p, policy, 7, {0, 1}, nullptr, &start);
    CHECK(!free.complete); CHECK(free.mushrooms == 0); near(free.hours, 1);
}
void resumed_recovery_restores_doors_without_repaying_an_encounter() {
    auto p = profile();
    auto start = StartState::parse("schema=1\nroom=42\nrun_number=1\nphase=recovery\nresume_phase=doors\n"
        "hp_percent=20\nkeys=1\ngems=rabbit\ndoors=locked,wall\n");
    Game doors(p, 1, start); doors.step({ActionKind::reenter});
    CHECK(doors.state().phase == Phase::doors); CHECK(doors.state().keys == 1);
    doors.step({ActionKind::choose_door, 0}); CHECK(doors.state().keys == 0);
    start = StartState::parse("schema=1\nroom=42\nrun_number=1\nphase=recovery\nresume_phase=encounter\n"
        "hp_percent=20\nkeys=0\ngems=rabbit\nencounter=barrel\n");
    Game encounter_game(p, 1, start); encounter_game.step({ActionKind::reenter});
    CHECK(encounter_game.state().phase == Phase::encounter); CHECK(encounter_game.state().keys == 0);
    encounter_game.step({ActionKind::skip}); CHECK(encounter_game.state().room == 43);
}
void observed_shop_offers_and_reroll_history_are_preserved() {
    auto p = profile(); const auto start = example("shop.state");
    Game game(p, 99, start); CHECK(game.state().shop_rerolls == 1);
    CHECK(game.state().offers[0].effect.kind == EffectKind::elixir); CHECK(game.state().offers[0].keys == 3);
    game.step({ActionKind::buy, 0}); near(game.state().hp, .9); CHECK(game.state().keys == 0); CHECK(game.state().room == 43);
    auto reroll_start = start;
    for (auto& offer : reroll_start.state.offers) {
        offer = {effect_template(EffectKind::raider, false), 99};
        offer.effect.starts_at = reroll_start.state.turn;
    }
    Game cap(p, 8, reroll_start, {10}); Policy policy; policy.reroll_limit = 1;
    CHECK(policy.choose(cap).kind == ActionKind::skip);
}
void progress_gem_offers_override_unknown_future_pool() {
    auto p = profile(); const auto start = example("gem-choice.state");
    p.exclude_offered_gems = false; // Small legacy pool is unrelated to the imported offer test.
    p.later_run_gem_pool = {Gem::hero, Gem::bull, Gem::blood, Gem::hick, Gem::devil};
    Game game(p, 2, start); CHECK(game.state().gem_offers == start.state.gem_offers);
    Policy policy; policy.preferred_gem = Gem::greasy; game.step(policy.choose(game));
    CHECK(game.state().has(Gem::greasy)); CHECK(game.state().has(Gem::rabbit)); CHECK(game.state().room == 51);
}
void run_specific_gem_pools_select_the_configured_choices() {
    auto p = profile();
    p.exclude_offered_gems = false; // Five choices support held-only exclusion.
    p.first_run_gem_pool = {Gem::rabbit, Gem::moonstone, Gem::spying, Gem::pendant, Gem::greasy};
    p.later_run_gem_pool = {Gem::hero, Gem::bull, Gem::blood, Gem::hick, Gem::devil}; p.validate();
    auto start = StartState::parse("schema=1\nroom=26\nrun_number=1\nphase=gems\nhp_percent=60\nkeys=3\ngem_offers=unknown\n");
    for (int run : {1, 2, 7}) for (std::uint64_t seed = 0; seed < 10; ++seed) {
        start.state.run_number = run; Game game(p, seed, start);
        const auto& pool = p.gems_for_run(run);
        for (auto gem : game.state().gem_offers) CHECK(std::find(pool.begin(), pool.end(), gem) != pool.end());
    }
    Temp file;
    std::ifstream input(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    for (const auto* key : {"gems_first_run=", "gems_later_runs=", "exclude_offered_gems="}) {
        const auto position = text.find(key);
        if (position != std::string::npos) {
            const auto end = text.find('\n', position);
            text.erase(position, end == std::string::npos ? end : end - position + 1);
        }
    }
    { std::ofstream out(file.path); out << text << "\ngems_first_run=rabbit,moonstone,spying,pendant,greasy\n"
        << "gems_later_runs=hero,bull,blood,hick,devil\nexclude_offered_gems=false\n"; }
    const auto loaded = Profile::load(file.path.string());
    CHECK(!loaded.exclude_offered_gems);
    CHECK(loaded.gems_for_run(1) == p.first_run_gem_pool); CHECK(loaded.gems_for_run(2) == p.later_run_gem_pool);
}
void stage_three_hero_damage_is_random_and_respects_its_fight_reduction() {
    for (const auto file : {"synthetic.profile", "mushroom-school-2024.profile"}) {
        auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/" + file);
        p.escape_chance = 0; p.combat_curse_chance = 0;
        auto s = encounter(Encounter::monster, 60);
        s.gems[ix(Gem::hero)] = true; s.gems[ix(Gem::gambler)] = true;
        for (const bool fleeing : {false, true}) {
            double first = -1; bool varied = false;
            const auto range = fleeing ? p.escape_damage[2] : p.monster_damage[2];
            const double factor = .8;
            for (int seed = 0; seed < 512; ++seed) {
                Game game(p, static_cast<std::uint64_t>(seed), s);
                game.step({fleeing ? ActionKind::flee : ActionKind::fight});
                const double damage = 1 - game.state().hp;
                CHECK(damage >= range.low * factor - 1e-12);
                CHECK(damage <= range.high * factor + 1e-12);
                CHECK(damage < .2);
                if (first < 0) first = damage;
                else if (std::abs(damage - first) > 1e-6) varied = true;
            }
            CHECK(varied);
        }
        // The curse stone itself never increases damage. Broken Armor does.
        p.damage_distribution = "high";
        s.curses.give(effect(EffectKind::broken_armor, .5, 4));
        Game cursed(p, 11, s); cursed.step({ActionKind::fight});
        near(1 - cursed.state().hp, p.monster_damage[2].high * 1.3);
        s.curses = {}; s.gems[ix(Gem::hero)] = false; s.gems[ix(Gem::rabbit)] = true;
        s.curses.give(effect(EffectKind::clumsy, .8, 5));
        Game rabbit(p, 11, s); rabbit.step({ActionKind::flee});
        near(1 - rabbit.state().hp, p.escape_damage[2].high * 1.25);
    }
}
void zero_health_rounding_and_independent_effect_slots() {
    auto p = fixed_damage(); p.trap_chance = 0;
    for (const double initial : {.20, .21}) {
        auto s = encounter(Encounter::empty, 10); s.hp = initial;
        s.curses.give(effect(EffectKind::poison, .05, 4));
        for (int tick = 0; tick < 4; ++tick) {
            s.phase = Phase::encounter; s.encounter = Encounter::empty;
            Game game(p, 1, s); game.step({ActionKind::interact}); s = game.state();
        }
        CHECK(s.deaths == (initial == .20 ? 1 : 0)); near(s.hp, initial == .20 ? 0 : .01);
        s = encounter(Encounter::empty, 10); s.hp = initial; s.phase = Phase::doors;
        s.doors = {{{Door::monster, true}, {Door::mystery}}};
        Game first(p, 1, s); first.step({ActionKind::choose_door, 0}); s = first.state();
        s.phase = Phase::doors; ++s.room; ++s.turn;
        s.doors = {{{Door::monster, true}, {Door::mystery}}};
        Game second(p, 1, s); second.step({ActionKind::choose_door, 0});
        CHECK(second.state().deaths == (initial == .20 ? 1 : 0)); near(second.state().hp, initial == .20 ? 0 : .01);
    }
    auto s = encounter(Encounter::empty, 10);
    for (const auto kind : {EffectKind::one_hit, EffectKind::recovery, EffectKind::escape}) s.blessings.give(effect_template(kind, true));
    for (const auto kind : {EffectKind::poison, EffectKind::broken_armor, EffectKind::clumsy}) s.curses.give(effect_template(kind, true));
    CHECK(s.blessings.size == 3); CHECK(s.curses.size == 3);
    s.blessings.give(effect_template(EffectKind::lockpick, true));
    CHECK(s.blessings.size == 3); CHECK(!s.effect(EffectKind::one_hit));
    CHECK(s.effect(EffectKind::poison)); CHECK(s.effect(EffectKind::broken_armor)); CHECK(s.effect(EffectKind::clumsy));
    s.curses.give(effect_template(EffectKind::hard_lock, true));
    CHECK(s.curses.size == 3); CHECK(!s.effect(EffectKind::poison));
    CHECK(s.effect(EffectKind::recovery)); CHECK(s.effect(EffectKind::escape)); CHECK(s.effect(EffectKind::lockpick));
    Game game(p, 1, s); game.step({ActionKind::interact});
    CHECK(game.state().blessings.size == 3); CHECK(game.state().curses.size == 3);
}
void gem_offer_history_excludes_every_displayed_stone_across_three_stages() {
    auto p = fixed_damage(0); p.boss_damage.fill({0, 0});
    CHECK(p.exclude_offered_gems);
    for (int run : {1, 2}) for (std::uint64_t seed = 0; seed < 32; ++seed) {
        State s; s.run_number = run;
        std::array<bool, ix(Gem::count)> seen{};
        for (int selection = 0; selection < 3; ++selection) {
            s.phase = Phase::encounter; s.encounter = Encounter::boss;
            s.room = 25 * (selection + 1); s.turn = s.room - 1; s.hp = 1;
            Game game(p, seed, s); game.step({ActionKind::fight});
            CHECK(game.state().phase == Phase::gems);
            for (Gem gem : game.state().gem_offers) {
                CHECK(!seen[ix(gem)]); seen[ix(gem)] = true;
                CHECK(game.state().offered_gems[ix(gem)]);
            }
            CHECK(std::count(game.state().offered_gems.begin(), game.state().offered_gems.end(), true) == 3 * (selection + 1));
            CHECK(game.state().offered_gems == seen);
            game.step({ActionKind::choose_gem, selection % 3}); s = game.state();
            CHECK(s.offered_gems == seen);
            CHECK(std::count(s.gems.begin(), s.gems.end(), true) == selection + 1);
        }
    }
}
void disabled_offer_exclusion_preserves_legacy_shuffle_and_records_history() {
    auto p = fixed_damage(0); p.boss_damage.fill({0, 0}); p.exclude_offered_gems = false;
    const std::vector<Gem> nine{Gem::rabbit, Gem::moonstone, Gem::spying, Gem::pendant, Gem::greasy,
        Gem::gambler, Gem::greed, Gem::hero, Gem::time_traveler};
    p.gem_pool = p.first_run_gem_pool = p.later_run_gem_pool = nine; p.validate();
    bool repeated_rejected_stone = false;
    for (std::uint64_t seed = 0; seed < 32; ++seed) {
        auto s = encounter(Encounter::boss, 75);
        s.gems[ix(nine[0])] = true; s.gems[ix(nine[3])] = true;
        for (std::size_t i = 0; i < 6; ++i) s.offered_gems[ix(nine[i])] = true;
        auto pool = nine;
        pool.erase(std::remove_if(pool.begin(), pool.end(), [&](Gem gem) { return s.has(gem); }), pool.end());
        Random reference(seed); reference.room(76);
        std::array<Gem, 3> expected{};
        for (std::size_t i = 0; i < expected.size(); ++i) {
            const auto j = i + static_cast<std::size_t>(reference.unit(Stream::gems) * static_cast<double>(pool.size() - i));
            std::swap(pool[i], pool[j]); expected[i] = pool[i];
        }
        Game game(p, seed, s); game.step({ActionKind::fight});
        CHECK(game.state().gem_offers == expected);
        auto history = s.offered_gems;
        for (Gem gem : expected) {
            CHECK(!s.has(gem)); repeated_rejected_stone |= history[ix(gem)]; history[ix(gem)] = true;
        }
        CHECK(game.state().offered_gems == history);
    }
    CHECK(repeated_rejected_stone);
}
void gem_offer_history_survives_death_and_clears_at_completion_and_restart() {
    auto p = fixed_damage(); p.boss_damage.fill({0, 0});
    auto s = encounter(Encounter::monster, 30); s.hp = .01;
    s.gems[ix(Gem::hero)] = true;
    for (Gem gem : {Gem::hero, Gem::rabbit, Gem::moonstone}) s.offered_gems[ix(gem)] = true;
    Game death(p, 7, s); death.step({ActionKind::fight});
    CHECK(death.state().phase == Phase::recovery); CHECK(death.state().room == 31);
    CHECK(death.state().offered_gems == s.offered_gems);
    death.step({ActionKind::wait, 0, p.recovery_hours * .2}); death.step({ActionKind::reenter});
    CHECK(death.state().offered_gems == s.offered_gems);
    auto last = death.state(); last.phase = Phase::encounter; last.encounter = Encounter::boss;
    last.room = 100; last.turn = 99; last.hp = 1;
    Limits limits; limits.repeat_runs = true;
    Game completed(p, 7, last, limits); completed.step({ActionKind::fight});
    CHECK(completed.state().phase == Phase::complete);
    CHECK(std::none_of(completed.state().offered_gems.begin(), completed.state().offered_gems.end(), [](bool seen) { return seen; }));
    // Restoring a completed state must not repopulate history from still-held stones.
    Game restored(p, 7, completed.state(), limits);
    CHECK(restored.state().offered_gems == completed.state().offered_gems);
    restored.step({ActionKind::restart});
    CHECK(restored.state().run_number == 2);
    CHECK(std::none_of(restored.state().offered_gems.begin(), restored.state().offered_gems.end(), [](bool seen) { return seen; }));
    CHECK(std::none_of(restored.state().gems.begin(), restored.state().gems.end(), [](bool held) { return held; }));
    auto changed = s; changed.offered_gems[ix(Gem::greed)] = true;
    CHECK(state_digest(s) != state_digest(changed));
}
void gem_pool_minimum_matches_the_offer_exclusion_rule() {
    auto p = profile(); p.first_run_gem_pool.clear(); p.later_run_gem_pool.clear();
    p.gem_pool.resize(9); p.exclude_offered_gems = true; p.validate();
    p.gem_pool.resize(8); rejects([&] { p.validate(); });
    p.exclude_offered_gems = false; p.gem_pool.resize(5); p.validate();
    p.gem_pool.resize(4); rejects([&] { p.validate(); });
    p = profile(); p.first_run_gem_pool.resize(8); rejects([&] { p.validate(); });
    p = profile(); p.later_run_gem_pool.resize(8); rejects([&] { p.validate(); });
}
void progress_can_continue_an_active_trial() {
    auto p = fixed_damage(.1); p.trial_legendary_chances.fill(0);
    auto start = StartState::parse("schema=1\nroom=42\nrun_number=2\nphase=doors\nhp_percent=100\nkeys=1\n"
        "gems=moonstone\ntrial_seen=true\ntrial_depth=3\ndoors=trial,exit_trial\n");
    Game game(p, 11, start); game.step({ActionKind::choose_door, 0}); game.step({ActionKind::fight});
    near(game.state().hp, .87); CHECK(game.state().trial_depth == 4); CHECK(game.state().room == 43);
    game.step({ActionKind::choose_door, 1}); game.step({ActionKind::interact});
    CHECK(game.state().epics == 1); CHECK(game.state().trial_depth == 0); CHECK(game.state().room == 44);
}
void progress_rejects_inconsistent_and_misspelled_states() {
    auto p = profile(); const std::string base = "schema=1\nroom=42\nrun_number=2\nphase=doors\nhp_percent=60\nkeys=1\ngems=rabbit\ndoors=golden,wall\n";
    StartState::parse(std::string("\xef\xbb\xbf") + base).validate(p);
    for (const auto* extra : {"keeys=3\n", "keys=2\n", "curses=recovery:weak:2\n", "blessings=elixir:weak:1\n",
        "blessings=one_hit:weak:2,one_hit:weak:3\n", "resume_phase=encounter\n", "trial_depth=3\n"})
        rejects([&] { const auto s = StartState::parse(base + extra); s.validate(p); });
    auto start = example("progress.state"); start.state.hp = 0; rejects([&] { start.validate(p); });
    start = example("progress.state"); start.state.gems.fill(false); rejects([&] { start.validate(p); });
    start = example("gem-choice.state"); start.state.gem_offers[2] = Gem::rabbit; rejects([&] { start.validate(p); });
    start = example("progress.state"); start.state.doors[0].kind = Door::boss; rejects([&] { start.validate(p); });
    start = example("recovery.state"); start.state.blessings.give(effect_template(EffectKind::recovery, false));
    rejects([&] { start.validate(p); });
    start = example("progress.state"); start.state.trial_seen = true; start.state.trial_depth = 5;
    start.state.doors = {{{Door::trial}, {Door::exit_trial}}}; rejects([&] { start.validate(p); });
    rejects([&] { StartState::parse("schema=1\nroom=1\nrun_number=1\nphase=doors\nhp_percent=nan\nkeys=0\n"); });
}
void progress_round_trips_without_hp_or_effect_rounding() {
    auto p = profile(); auto start = example("progress.state"); Random random(991);
    for (int i = 0; i < 1000; ++i) {
        start.state.hp = .01 + .99 * random.unit(Stream::special);
        const auto restored = StartState::parse(start.encode()); restored.validate(p);
        CHECK(state_digest(start.state) == state_digest(restored.state));
        CHECK(start.fingerprint() == restored.fingerprint());
    }
}
void progress_forecasts_and_traces_are_reproducible() {
    auto p = profile(); Policy policy; BatchOptions options; options.runs = 64; options.limits.budget = 25;
    options.start = example("gem-choice.state"); const auto one = run_batch(p, policy, options);
    options.threads = 4; const auto four = run_batch(p, policy, options);
    const auto report = report_json(p, policy, options, one);
    CHECK(report == report_json(p, policy, options, four)); CHECK(report.find("\"rabbit\": 0") != std::string::npos);
    CHECK(report.find("\"run_number_within_event\": 2") != std::string::npos);
    CHECK(report.find("additional time and spending") != std::string::npos);
    Temp trace;
    { std::ofstream out(trace.path); run_one(p, policy, 12, {}, &out, &*options.start); }
    CHECK(replay(p, trace.path.string()) > 50);
    std::ifstream input(trace.path); std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>()); input.close();
    const auto at = text.find("keys=5"); CHECK(at != std::string::npos); text.replace(at, 6, "keys=6");
    { std::ofstream out(trace.path); out << text; }
    rejects([&] { replay(p, trace.path.string()); });
}
void final_boss_forecast_reports_only_remaining_time() {
    auto p = fixed_damage(); auto start = example("recovery.state");
    start.state.phase = Phase::encounter; start.state.hp = 1;
    Policy policy; const auto result = run_one(p, policy, 15, {}, nullptr, &start);
    CHECK(result.complete); CHECK(result.actions == 1); near(result.hours, 2.0 / 3600);
    CHECK(result.mushrooms == 0); CHECK(result.deaths == 0);
}
void old_engine_traces_require_their_original_version() {
    auto p = profile(); Policy policy; std::ostringstream output;
    run_one(p, policy, 12, {}, &output);
    auto text = output.str();
    const auto current = text;
    text.replace(0, std::string("#sfld-trace-v2\t").size() + engine_version.size(), "#sfld-trace-v1");
    const auto start = text.find("#start\tfresh\t0\n#actions\n"); CHECK(start != std::string::npos);
    text.erase(start, std::string("#start\tfresh\t0\n#actions\n").size());
    Temp file; { std::ofstream out(file.path); out << text; }
    rejects([&] { replay(p, file.path.string()); });
    text = current; text.replace(text.find(engine_version), engine_version.size(), "0.2.0");
    { std::ofstream out(file.path); out << text; }
    rejects([&] { replay(p, file.path.string()); });
}
void fountain_variants_and_narrator_have_distinct_effects() {
    auto p = fixed_damage(); p.blessing_weights = {0,1,0,0,0,0,0,0}; p.strong_effect = 1;
    auto s = encounter(Encounter::fountain, 30); s.hp = .3;
    s.curses.give(effect_template(EffectKind::poison, false));
    s.curses.give(effect_template(EffectKind::broken_armor, false));
    s.blessings.give(effect_template(EffectKind::lockpick, false));
    Game plain(p, 3, s); plain.step({ActionKind::interact});
    near(plain.state().hp, .5); CHECK(plain.state().curses.size == 2);
    s.encounter = Encounter::cleansing_fountain;
    Game clean(p, 3, s); clean.step({ActionKind::interact});
    near(clean.state().hp, .55); CHECK(clean.state().curses.size == 0);
    CHECK(clean.state().effect(EffectKind::lockpick)->remaining == 2);
    s.encounter = Encounter::narrator;
    Game narrator(p, 3, s); narrator.step({ActionKind::interact});
    near(narrator.state().hp, .5); CHECK(narrator.state().curses.size == 2);
    CHECK(narrator.state().effect(EffectKind::one_hit)->remaining == 8);
    s.curses.clear(); Game skip(p, 3, s); skip.step({ActionKind::skip});
    near(skip.state().hp, .3); CHECK(!skip.state().effect(EffectKind::one_hit));
}
void eight_room_one_hit_expires_and_still_awards_keys() {
    auto p = fixed_damage(); p.key_chance = 1; p.door_weights.fill(0); p.door_weights[ix(Door::monster)] = 1;
    p.trap_chance = 0;
    auto s = encounter(Encounter::empty, 10); s.phase = Phase::shop; s.hp = .25; s.keys = 4;
    s.offers[0] = {effect_template(EffectKind::one_hit, true), 4};
    Game game(p, 5, s); game.step({ActionKind::buy, 0});
    CHECK(game.state().keys == 0); CHECK(game.state().shop_purchases == 1);
    CHECK(game.state().strong_one_hit_purchases == 1); CHECK(game.state().effect(EffectKind::one_hit)->remaining == 8);
    for (int i = 0; i < 8; ++i) {
        game.step({ActionKind::choose_door, game.state().doors[0].kind == Door::monster ? 0 : 1}); game.step({ActionKind::fight}); near(game.state().hp, .25);
    }
    CHECK(game.state().keys == 8); CHECK(!game.state().effect(EffectKind::one_hit));
    game.step({ActionKind::choose_door, game.state().doors[0].kind == Door::monster ? 0 : 1}); game.step({ActionKind::fight}); near(game.state().hp, .05);
}
void key_moment_rolls_two_keys_at_seventy_percent() {
    auto p = fixed_damage(0); p.key_chance = 0;
    auto s = encounter(); s.blessings.give(effect_template(EffectKind::key_moment, true));
    near(s.blessings.slots[0].magnitude, .7);
    int successes = 0;
    for (std::uint64_t seed = 0; seed < 10000; ++seed) {
        Game game(p, seed, s); game.step({ActionKind::fight});
        CHECK(game.state().keys == 0 || game.state().keys == 2);
        successes += game.state().keys == 2 ? 1 : 0;
        CHECK(game.state().effect(EffectKind::key_moment)->remaining == 7);
    }
    CHECK(successes > 6700 && successes < 7300);
    p.escape_chance = 1;
    Game flee(p, 2, s); flee.step({ActionKind::flee});
    CHECK(flee.state().keys == 0); CHECK(flee.state().effect(EffectKind::key_moment)->remaining == 8);
}
void gold_curse_reduces_chest_rewards_and_combines_with_raider() {
    auto p = fixed_damage(); p.container_weights = {0,0,1,0};
    for (auto e : {Encounter::crate, Encounter::silver, Encounter::bronze, Encounter::sarcophagus}) {
        auto s = encounter(e, 30);
        s.curses.give(effect_template(EffectKind::gold_hangover, false));
        Game cursed(p, 4, s); cursed.step({ActionKind::interact}); near(cursed.state().gold_units, .5);
        CHECK(cursed.state().gold_rewards == 1);
        s.blessings.give(effect_template(EffectKind::raider, false));
        Game both(p, 4, s); both.step({ActionKind::interact}); near(both.state().gold_units, 1);
        s.curses.clear(); Game blessed(p, 4, s); blessed.step({ActionKind::interact}); near(blessed.state().gold_units, 2);
    }
}
void generated_shop_offers_are_distinct_with_independent_strengths() {
    auto p = fixed_damage(); auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors;
    s.doors = {{{Door::shop}, {Door::wall}}};
    int strong = 0, both_strong = 0, both_weak = 0;
    for (std::uint64_t seed = 0; seed < 2000; ++seed) {
        Game game(p, seed, s); game.step({ActionKind::choose_door, 0});
        const auto& offers = game.state().offers;
        CHECK(offers[0].effect.kind != offers[1].effect.kind);
        int count = 0;
        for (const auto& o : offers) {
            const auto weak = effect_template(o.effect.kind, false);
            count += o.effect.remaining > weak.remaining || o.effect.magnitude > weak.magnitude ? 1 : 0;
        }
        strong += count; both_strong += count == 2 ? 1 : 0; both_weak += count == 0 ? 1 : 0;
    }
    CHECK(strong > 1800 && strong < 2200); CHECK(both_strong > 300); CHECK(both_weak > 300);
    p.shop_blessing_weights = {0,1,0,0,0,0,0,0}; rejects([&] { p.validate(); });
}
void rerolls_do_not_tick_effects_or_spend_keys_and_buy_ends_shop() {
    auto p = fixed_damage(); p.shop_blessing_weights = {0,1,0,0,0,0,1,0}; p.shop_strong_effect = 1;
    auto s = encounter(Encounter::empty, 30); s.phase = Phase::shop; s.keys = 4; s.hp = .4;
    s.blessings.give(effect_template(EffectKind::recovery, false));
    s.curses.give(effect_template(EffectKind::poison, false));
    Game game(p, 8, s, {2});
    for (int i = 0; i < 2; ++i) {
        game.step({ActionKind::reroll}); near(game.state().hp, .4); CHECK(game.state().room == 30);
        CHECK(game.state().turn == 29); CHECK(game.state().keys == 4);
        CHECK(game.state().effect(EffectKind::poison)->remaining == 5);
        CHECK(game.state().effect(EffectKind::recovery)->remaining == 3);
    }
    CHECK(game.state().mushrooms == 2); CHECK(game.state().reroll_mushrooms == 2);
    CHECK(!game.legal({ActionKind::reroll}));
    const int slot = game.state().offers[0].effect.kind == EffectKind::one_hit ? 0 : 1;
    game.step({ActionKind::buy, slot}); CHECK(game.state().room == 31); CHECK(game.state().keys == 0);
    CHECK(!game.legal({ActionKind::buy, 1 - slot})); CHECK(!game.legal({ActionKind::reroll}));
    CHECK(game.state().effect(EffectKind::one_hit)->remaining == 8); near(game.state().hp, .45);
}
void curse_shop_has_no_reroll_and_one_trade_grants_keys_then_leaves() {
    auto p = fixed_damage(0); auto s = encounter(Encounter::curse_shop, 42);
    s.phase = Phase::curse_shop; s.keys = 7; s.shops_seen.fill(true);
    // Native Key to Failure panel: Poisoned +1 key, Ponderous +4 keys,
    // with OK / NO THANKS and no reroll control.
    s.offers = {{{effect_template(EffectKind::poison, false), 1},
                 {effect_template(EffectKind::clumsy, true), 4}}};
    for (int side : {0, 1}) {
        Game game(p, 8, s, {10});
        CHECK(!game.legal({ActionKind::reroll}));
        rejects([&] { game.step({ActionKind::reroll}); });
        CHECK(game.state().actions == 0); CHECK(game.state().mushrooms == 0);
        CHECK(game.state().shop_rerolls == 0); CHECK(game.state().keys == 7);
        CHECK(game.legal({ActionKind::buy, side}));
        const auto offer = s.offers[static_cast<std::size_t>(side)];
        game.step({ActionKind::buy, side});
        CHECK(game.state().keys == 7 + offer.keys);
        CHECK(game.state().effect(offer.effect.kind)->remaining == offer.effect.remaining);
        CHECK(game.state().room == 43); CHECK(game.state().phase == Phase::doors);
        CHECK(game.state().mushrooms == 0); CHECK(game.state().shop_purchases == 0);
        CHECK(!game.legal({ActionKind::buy, 1 - side})); CHECK(!game.legal({ActionKind::reroll}));
    }
    Game declined(p, 8, s, {10}); declined.step({ActionKind::skip});
    CHECK(declined.state().room == 43); CHECK(declined.state().keys == 7);
    CHECK(declined.state().curses.size == 0);
}
void targeted_rerolls_reject_weak_one_hit_and_stop_at_strong() {
    auto p = fixed_damage(); p.shop_blessing_weights = {0,1,0,0,0,0,1,0}; p.shop_strong_effect = 1;
    auto s = encounter(Encounter::empty, 30); s.phase = Phase::shop; s.hp = .3; s.keys = 4;
    s.offers = {{{effect_template(EffectKind::one_hit, false), 2}, {effect_template(EffectKind::elixir, true), 3}}};
    Policy policy; policy.shop = ShopPolicy::one_hit_8; policy.reroll_limit = 5;
    Game game(p, 19, s, {10}); CHECK(policy.choose(game).kind == ActionKind::reroll);
    game.step(policy.choose(game)); const auto buy = policy.choose(game); CHECK(buy.kind == ActionKind::buy);
    CHECK(game.state().offers[static_cast<std::size_t>(buy.index)].effect.kind == EffectKind::one_hit);
    game.step(buy); CHECK(game.state().strong_one_hit_purchases == 1); CHECK(game.state().mushrooms == 1);
    CHECK(game.state().effect(EffectKind::one_hit)->remaining == 8);
    policy.shop = ShopPolicy::one_hit;
    Game either(p, 19, s, {10}); const auto weak = policy.choose(either); CHECK(weak.kind == ActionKind::buy && weak.index == 0);
}
void targeted_rerolls_stop_when_capped_unaffordable_or_unavailable() {
    auto p = fixed_damage(); auto s = encounter(Encounter::empty, 30); s.phase = Phase::shop; s.keys = 4; s.hp = .3;
    s.offers = {{{effect_template(EffectKind::one_hit, false), 2}, {effect_template(EffectKind::elixir, true), 3}}};
    Policy policy; policy.shop = ShopPolicy::one_hit_8; policy.reroll_limit = 2;
    Game no_budget(p, 5, s); CHECK(policy.choose(no_budget).kind == ActionKind::buy);
    s.shop_rerolls = 2; Game capped(p, 5, s, {10}); CHECK(policy.choose(capped).kind == ActionKind::buy);
    s.shop_rerolls = 0; s.keys = 3; Game poor(p, 5, s, {10}); CHECK(policy.choose(poor).kind == ActionKind::buy);
    s.keys = 4; p.shop_strong_effect = 0; Game impossible(p, 5, s, {10}); CHECK(policy.choose(impossible).kind == ActionKind::buy);
    p.shop_strong_effect = .5; s.blessings.give(effect_template(EffectKind::one_hit, true));
    Game covered(p, 5, s, {10}); CHECK(policy.choose(covered).kind != ActionKind::reroll);
    s.blessings.clear(); s.room = 99; s.turn = 98; Game last(p, 5, s, {10}); CHECK(policy.choose(last).kind != ActionKind::reroll);
    s.room = 30; s.turn = 29; s.keys = 0;
    for (auto shop : {ShopPolicy::adaptive, ShopPolicy::one_hit, ShopPolicy::one_hit_8}) {
        policy.shop = shop; Game no_keys(p, 5, s, {10}); CHECK(policy.choose(no_keys).kind == ActionKind::skip);
    }
}
void shop_policy_accounts_for_losing_oldest_blessing() {
    auto p = fixed_damage(); auto s = encounter(Encounter::empty, 30); s.phase = Phase::shop; s.keys = 4;
    s.blessings.give(effect_template(EffectKind::one_hit, true));
    s.blessings.give(effect_template(EffectKind::disarm, true));
    s.blessings.give(effect_template(EffectKind::lockpick, true));
    s.offers = {{{effect_template(EffectKind::escape, false), 3}, {effect_template(EffectKind::raider, false), 1}}};
    Game game(p, 1, s); Policy policy; CHECK(policy.choose(game).kind == ActionKind::skip);
}
void recovery_budget_can_reserve_mushrooms_for_rerolls() {
    auto p = fixed_damage(); auto s = encounter(); s.phase = Phase::recovery; s.hp = 0;
    Policy policy; policy.recovery_budget = 0; policy.revive_hp = 1;
    Game free(p, 1, s, {50}); CHECK(policy.choose(free).kind == ActionKind::wait);
    policy.recovery_budget = 10; Game capped(p, 1, s, {50});
    CHECK(policy.choose(capped).kind == ActionKind::heal_step); capped.step(policy.choose(capped));
    CHECK(policy.choose(capped).kind == ActionKind::wait); CHECK(capped.state().mushrooms == 10);
}
void lantern_zombie_and_flying_tube_have_half_damage() {
    auto p = fixed_damage();
    for (auto e : {Encounter::undead, Encounter::tube, Encounter::beta}) {
        auto s = encounter(e, 30); Game game(p, 1, s); game.step({ActionKind::fight}); near(game.state().hp, .9);
        CHECK(game.state().lucky_coins == (e == Encounter::tube ? 10 : 0));
        p.escape_chance = 1; Game flee(p, 1, s); flee.step({ActionKind::flee});
        near(flee.state().hp, 1); CHECK(flee.state().lucky_coins == 0);
    }
}
void pig_can_be_skipped_and_does_not_heal_a_lethal_fight() {
    auto p = fixed_damage(); auto s = encounter(Encounter::pig, 30); s.hp = .3;
    Game skip(p, 1, s); Policy policy; CHECK(policy.choose(skip).kind == ActionKind::skip);
    skip.step(policy.choose(skip)); near(skip.state().hp, .3);
    Game lethal(p, 1, s); lethal.step({ActionKind::fight}); CHECK(lethal.state().phase == Phase::recovery);
    near(lethal.state().hp, 0);
    s.hp = .5; Game win(p, 1, s); win.step({ActionKind::fight}); near(win.state().hp, .7);
}
void armory_requires_eligible_equipment_and_generates_only_at_90_to_98() {
    auto p = fixed_damage(); p.armory_legendary_chance = 1;
    auto s = encounter(Encounter::armory, 90);
    Game ineligible(p, 1, s); ineligible.step({ActionKind::interact}); CHECK(ineligible.state().legendaries == 0); CHECK(ineligible.state().epics == 1);
    p.armory_bonus_eligible = true;
    Game eligible(p, 1, s); eligible.step({ActionKind::interact}); CHECK(eligible.state().legendaries == 1);
    p.golden_weights.fill(0); p.golden_weights[ix(Encounter::armory)] = 1;
    s.phase = Phase::doors; s.doors = {{{Door::golden}, {Door::wall}}};
    for (int room : {89,90,98,99}) {
        s.room = room; s.turn = room - 1; Game game(p, 2, s); game.step({ActionKind::choose_door, 0});
        CHECK(game.state().encounter == (room >= 90 && room <= 98 ? Encounter::armory : Encounter::empty));
    }
}
void armory_once_per_run_schedule_offers_one_golden_wall_pair_in_90_to_98() {
    auto p = fixed_damage(); p.armory_schedule = "once_per_run";
    int at_90 = 0;
    for (std::uint64_t seed = 1; seed <= 2000; ++seed) {
        Game game(p, seed, encounter(Encounter::empty, 89)); game.step({ActionKind::interact});
        CHECK(game.state().room == 90); CHECK(game.state().armory_room == 0 || game.state().armory_room == 90);
        at_90 += game.state().armory_room == 90;
    }
    CHECK(at_90 > 160 && at_90 < 290); // 1/9 of 2000 is 222.
    // Not yet offered by room 98: it must come now; once offered, never again.
    auto s = encounter(Encounter::empty, 97);
    Game last(p, 3, s); last.step({ActionKind::interact});
    CHECK(last.state().armory_room == 98);
    const auto& d = last.state().doors;
    CHECK((d[0].kind == Door::golden && d[1].kind == Door::wall) || (d[0].kind == Door::wall && d[1].kind == Door::golden));
    last.step({ActionKind::choose_door, d[0].kind == Door::golden ? 0 : 1});
    CHECK(last.state().encounter == Encounter::armory);
    s.armory_room = 93;
    for (std::uint64_t seed = 1; seed <= 50; ++seed) {
        Game game(p, seed, s); game.step({ActionKind::interact}); CHECK(game.state().armory_room == 93);
    }
    for (int room : {60, 89, 99}) {
        Game game(p, 4, encounter(Encounter::empty, room - 1)); game.step({ActionKind::interact});
        CHECK(game.state().armory_room == 0);
    }
}
void auction_item_is_not_automatically_epic() {
    auto p = fixed_damage(); auto s = encounter(Encounter::auction, 30);
    p.auction_epic_chance = 0; Game ordinary(p, 2, s); ordinary.step({ActionKind::interact}); CHECK(ordinary.state().epics == 0);
    p.auction_epic_chance = 1; Game epic(p, 2, s); epic.step({ActionKind::interact}); CHECK(epic.state().epics == 1);
}
void duration_gems_extend_acquired_effects_and_death_clears_them() {
    auto p = fixed_damage(); auto s = encounter(Encounter::empty, 30); s.phase = Phase::shop; s.keys = 4;
    s.gems[ix(Gem::time_traveler)] = true; s.gems[ix(Gem::moonstone)] = true;
    s.offers[0] = {effect_template(EffectKind::one_hit, true), 4};
    Game blessing(p, 1, s); blessing.step({ActionKind::buy, 0});
    CHECK(blessing.state().effect(EffectKind::one_hit)->remaining == 9);
    s.phase = Phase::curse_shop; s.offers[0] = {effect_template(EffectKind::poison, true), 2};
    Game curse(p, 1, s); curse.step({ActionKind::buy, 0});
    CHECK(curse.state().effect(EffectKind::poison)->remaining == 11); CHECK(curse.state().keys == 6);
    auto next = curse.state(); next.phase = Phase::encounter; next.encounter = Encounter::monster; next.hp = .1;
    next.blessings.give(effect_template(EffectKind::lockpick, true));
    Game death(p, 2, next); death.step({ActionKind::fight});
    CHECK(death.state().curses.size == 0 && death.state().blessings.size == 0);
    CHECK(death.state().has(Gem::time_traveler) && death.state().has(Gem::moonstone));
}
void one_hit_does_not_prevent_trap_or_poison_damage() {
    auto p = fixed_damage(); auto s = encounter(Encounter::monster, 30); s.phase = Phase::doors;
    s.doors = {{{Door::monster, true}, {Door::wall}}};
    s.blessings.give(effect_template(EffectKind::one_hit, true)); s.curses.give(effect_template(EffectKind::poison, false));
    Game game(p, 2, s); game.step({ActionKind::choose_door, 0}); near(game.state().hp, .9);
    game.step({ActionKind::fight}); near(game.state().hp, .85);
}
void cursed_traps_apply_no_direct_damage_and_poison_is_separate() {
    auto p = fixed_damage(); p.curse_weights = {0, 1, 0, 0, 0}; p.strong_effect = 0;
    p.mystery_without_monster_weights.reset(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::empty)] = 1;
    p.golden_weights.fill(0); p.golden_weights[ix(Encounter::empty)] = 1;
    for (auto door : {Door::monster, Door::mystery}) {
        auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors;
        s.doors = {{{door, true, true}, {Door::wall}}};
        Game game(p, 2, s); game.step({ActionKind::choose_door, 0});
        near(game.state().hp, 1); CHECK(game.state().curses.size == 1);
        CHECK(game.state().effect(EffectKind::poison));
        game.step({door == Door::monster ? ActionKind::fight : ActionKind::interact});
        near(game.state().hp, door == Door::monster ? .75 : .95);
    }
    auto low = encounter(Encounter::empty, 30); low.phase = Phase::doors; low.hp = .03;
    low.doors = {{{Door::mystery, true, true}, {Door::wall}}};
    Game poisoned(p, 2, low); poisoned.step({ActionKind::choose_door, 0});
    near(poisoned.state().hp, .03); CHECK(poisoned.state().deaths == 0);
    poisoned.step({ActionKind::interact}); CHECK(poisoned.state().deaths == 1);
    CHECK(poisoned.state().room == 31);
    low.blessings.give(effect_template(EffectKind::disarm, false));
    Game disarmed(p, 2, low); disarmed.step({ActionKind::choose_door, 0});
    near(disarmed.state().hp, .03); CHECK(disarmed.state().curses.size == 0);
    CHECK(disarmed.state().effect(EffectKind::disarm)->remaining == 3);
}
void greed_unlocks_container_blessings_only_on_the_loot_branch() {
    auto p = fixed_damage(); p.container_weights = {1, 0, 0, 0}; p.skeleton_wakes = 0;
    p.blessing_weights = {0, 1, 0, 0, 0, 0, 0, 0}; p.strong_effect = 0;
    for (auto kind : {Encounter::crate, Encounter::silver, Encounter::bronze, Encounter::skeleton}) {
        auto s = encounter(kind, 30);
        Game ordinary(p, 2, s); ordinary.step({ActionKind::interact});
        CHECK(ordinary.state().blessings.size == 0);
        s.gems[ix(Gem::greed)] = true;
        Game greed(p, 2, s); greed.step({ActionKind::interact});
        CHECK(greed.state().effect(EffectKind::one_hit));
        CHECK(greed.state().effect(EffectKind::one_hit)->remaining == 4);
        near(greed.state().hp, 1);
    }
    // A sleeping soldier can yield loot; a waking one becomes a normal fight.
    p.skeleton_wakes = 1;
    auto s = encounter(Encounter::skeleton, 30); s.gems[ix(Gem::greed)] = true;
    Game awake(p, 2, s); awake.step({ActionKind::interact});
    CHECK(awake.state().encounter == Encounter::monster);
    CHECK(awake.state().blessings.size == 0); CHECK(awake.state().room == 30);
    awake.step({ActionKind::fight});
    CHECK(awake.state().blessings.size == 0); near(awake.state().hp, .8);
    for (auto kind : {Encounter::monster, Encounter::undead, Encounter::mimic}) {
        s.encounter = kind;
        Game fight(p, 2, s); fight.step({ActionKind::fight});
        CHECK(fight.state().blessings.size == 0);
    }
}
void greed_uses_profile_blessing_weight_without_an_additive_bonus() {
    auto p = fixed_damage(); p.container_weights = {0, 0, 1, 0};
    p.blessing_weights = {0, 1, 0, 0, 0, 0, 0, 0};
    auto s = encounter(Encounter::silver, 30); s.gems[ix(Gem::greed)] = true;
    for (std::uint64_t seed = 0; seed < 64; ++seed) {
        Game gold(p, seed, s); gold.step({ActionKind::interact});
        CHECK(gold.state().blessings.size == 0); CHECK(gold.state().gold_rewards == 1);
    }
    // Barrel and room-specific blessings do not need the stone.
    s.gems[ix(Gem::greed)] = false; s.encounter = Encounter::barrel; p.barrel_blessing = 1;
    Game barrel(p, 2, s); barrel.step({ActionKind::interact});
    CHECK(barrel.state().effect(EffectKind::one_hit));
    s.encounter = Encounter::narrator;
    Game narrator(p, 2, s); narrator.step({ActionKind::interact});
    CHECK(narrator.state().effect(EffectKind::one_hit));
}
void container_blessing_gate_can_be_disabled_for_sensitivity() {
    std::ifstream input(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    const std::string setting = "container_blessing_requires_greed=true";
    const auto at = text.find(setting); CHECK(at != std::string::npos);
    text.replace(at, setting.size(), "container_blessing_requires_greed=false");
    Temp file; { std::ofstream output(file.path); output << text; }
    auto p = legacy_tables(Profile::load(file.path.string())); CHECK(!p.container_blessing_requires_greed);
    p.container_weights = {1, 0, 0, 0}; p.blessing_weights = {0, 1, 0, 0, 0, 0, 0, 0};
    Game alternate(p, 2, encounter(Encounter::silver, 30)); alternate.step({ActionKind::interact});
    CHECK(alternate.state().effect(EffectKind::one_hit));
}
void generated_traps_are_converted_by_gambler_without_a_random_curse_roll() {
    auto p = fixed_damage(); p.trap_chance = 1; p.strong_effect = 0;
    p.shop_schedule = "weighted"; // This fixture isolates ordinary trapped-door generation.
    p.curse_weights = {0, 0, 0, 1, 0};
    p.mystery_without_monster_weights.reset(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::empty)] = 1;
    p.golden_weights = p.mystery_weights;
    for (auto kind : {Door::monster, Door::mystery, Door::golden}) {
        p.door_weights.fill(0); p.door_weights[ix(kind)] = 1;
        for (bool gambler : {false, true}) for (std::uint64_t seed = 0; seed < 64; ++seed) {
            auto s = encounter(Encounter::empty, 30); s.gems[ix(Gem::gambler)] = gambler;
            Game game(p, seed, s); game.step({ActionKind::interact});
            int trapped = -1, count = 0;
            for (int i = 0; i < 2; ++i) {
                const auto door = game.state().doors[static_cast<std::size_t>(i)];
                if (door.trap) { trapped = i; ++count; CHECK(door.kind != Door::golden); }
                CHECK(door.cursed_trap == (door.trap && gambler));
            }
            CHECK(count == (kind == Door::golden ? 0 : 1));
            game.step({ActionKind::choose_door, trapped < 0 ? 0 : trapped});
            near(game.state().hp, gambler || kind == Door::golden ? 1 : .9);
            CHECK(game.state().curses.size == (gambler && kind != Door::golden ? 1 : 0));
        }
    }
    p.trap_chance = 0; p.door_weights.fill(0); p.door_weights[ix(Door::monster)] = 1;
    auto s = encounter(Encounter::empty, 30);
    s.gems[ix(Gem::gambler)] = true; s.gems[ix(Gem::masochist)] = true;
    Game forced(p, 8, s); forced.step({ActionKind::interact});
    int traps = 0;
    for (const auto door : forced.state().doors) { traps += door.trap; CHECK(door.trap == door.cursed_trap); }
    CHECK(traps == 1);
}
void trial_legendary_probabilities_are_specific_to_the_collected_tier() {
    auto p = fixed_damage();
    for (int legendary_tier : {4, 5}) {
        p.trial_legendary_chances.fill(0);
        p.trial_legendary_chances[static_cast<std::size_t>(legendary_tier - 1)] = 1;
        for (int tier = 1; tier <= 5; ++tier) {
            auto s = encounter(Encounter::prize, 57); s.pending_trial_reward = tier;
            Game game(p, 5, s); game.step({ActionKind::interact});
            CHECK(game.state().legendaries == (tier == legendary_tier ? 1 : 0));
            CHECK(game.state().epics == (tier >= 2 && tier != legendary_tier ? 1 : 0));
            CHECK(game.state().gold_rewards == (tier == 1 ? 1 : 0));
            CHECK(game.state().pending_trial_reward == 0); CHECK(game.state().completed_runs == 0);
        }
    }
}
void destiny_spins_award_all_six_outcomes_before_separate_room_contents() {
    auto p = fixed_damage(); p.strong_effect = 0;
    p.blessing_weights = {0, 1, 0, 0, 0, 0, 0, 0}; p.curse_weights = {0, 0, 0, 1, 0};
    p.destiny_reward_amounts = {3, 17, 4, 2};
    // The Destiny contents table is independent of ordinary mystery contents.
    p.mystery_without_monster_weights.reset(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::mimic)] = 1;
    for (std::size_t reward = 0; reward < 6; ++reward) {
        p.destiny_reward_weights.fill(0); p.destiny_reward_weights[reward] = 1;
        for (auto kind : {Encounter::empty, Encounter::monster, Encounter::barrel, Encounter::crate,
                          Encounter::silver, Encounter::bronze, Encounter::skeleton}) {
            p.destiny_weights.fill(0); p.destiny_weights[ix(kind)] = 1;
            auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors; s.hp = .6;
            s.doors = {{{Door::destiny}, {Door::wall}}};
            Game game(p, 5, s); CHECK(game.door_cost(Door::destiny) == 0);
            CHECK(game.legal({ActionKind::choose_door, 0})); game.step({ActionKind::choose_door, 0});
            const auto& after = game.state();
            CHECK(after.keys == 0); CHECK(after.room == 30); CHECK(after.phase == Phase::encounter);
            CHECK(after.encounter == kind); near(after.hp, .6);
            CHECK(after.blessings.size == (reward == 0 ? 1 : 0));
            CHECK(after.curses.size == (reward == 5 ? 1 : 0));
            near(after.gold_units, reward == 1 ? 3 : 0);
            CHECK(after.lucky_coins == (reward == 2 ? 17 : 0));
            for (std::size_t i = 0; i < 6; ++i) CHECK(after.resources[i] == (reward == 3 && i == 5 ? 14 : 10));
            CHECK(after.mushrooms_found == (reward == 4 ? 2 : 0)); CHECK(after.mushrooms == 0);
            // Winning currency does not quietly change the experiment's hard spending cap.
            CHECK(!game.legal({ActionKind::heal_step})); CHECK(!game.legal({ActionKind::heal_full}));
            if (reward == 0 && kind == Encounter::monster) {
                CHECK(after.effect(EffectKind::one_hit)); game.step({ActionKind::fight}); near(game.state().hp, .6);
            }
        }
    }
}
void hungry_doors_enforce_payment_and_never_return_the_donated_resource() {
    auto p = fixed_damage();
    for (std::size_t resource = 0; resource < 6; ++resource) {
        const auto kind = static_cast<Door>(ix(Door::wood) + resource);
        auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors; s.resources.fill(0);
        s.doors = {{{kind}, {Door::monster}}}; s.curses.give(effect_template(EffectKind::hard_lock, false));
        Game blocked(p, 5, s); CHECK(!blocked.available(s.doors[0]));
        const auto before = state_digest(blocked.state()); rejects([&] { blocked.step({ActionKind::choose_door, 0}); });
        CHECK(state_digest(blocked.state()) == before);
        s.resources[resource] = 1;
        for (std::uint64_t seed = 0; seed < 64; ++seed) {
            Game game(p, seed, s); CHECK(game.door_cost(kind) == 0); CHECK(game.available(s.doors[0]));
            game.step({ActionKind::choose_door, 0});
            CHECK(game.state().resources[resource] == 0); CHECK(game.state().keys == 0);
            CHECK(game.state().encounter == Encounter::sated_chest); CHECK(game.state().room == 30);
            game.step({ActionKind::interact});
            CHECK(game.state().resources[resource] == 0); CHECK(game.state().room == 31);
            int received = 0;
            for (int balance : game.state().resources) { CHECK(balance == 0 || balance == 2); received += balance; }
            CHECK(received == 2); CHECK(game.state().keys == 0);
        }
    }
}
void blessing_door_is_free_and_its_effect_applies_to_the_waiting_enemy() {
    auto p = fixed_damage(); p.strong_effect = 0; p.blessing_weights = {0, 1, 0, 0, 0, 0, 0, 0};
    auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors; s.hp = .1;
    s.doors = {{{Door::blessing}, {Door::wall}}};
    Game game(p, 5, s); CHECK(game.legal({ActionKind::choose_door, 0}));
    game.step({ActionKind::choose_door, 0}); CHECK(game.state().keys == 0);
    CHECK(game.state().encounter == Encounter::monster); CHECK(game.state().room == 30);
    CHECK(game.state().effect(EffectKind::one_hit));
    game.step({ActionKind::fight}); near(game.state().hp, .1); CHECK(game.state().room == 31);
    CHECK(game.state().effect(EffectKind::one_hit)->remaining == 3);
}
void door_reward_profiles_reject_invalid_probabilities_and_retired_trap_odds() {
    auto p = profile(); p.destiny_reward_weights.fill(0); rejects([&] { p.validate(); });
    p = profile(); p.destiny_weights.fill(0); rejects([&] { p.validate(); });
    p = profile(); p.destiny_reward_amounts[3] = 1.5; rejects([&] { p.validate(); });
    p = profile(); p.trial_legendary_chances[4] = 1.1; rejects([&] { p.validate(); });
    std::ifstream input(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    const std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    Temp file;
    { std::ofstream output(file.path); output << text << "\ncursed_trap_chance=0.05\n"; }
    rejects([&] { Profile::load(file.path.string()); });
    { std::ofstream output(file.path); output << text << "\ncursed_trap_chance=0\n"; }
    Profile::load(file.path.string()).validate();
}
void opening_shop_has_fixed_free_offers_and_explicit_reroll_scenarios() {
    auto p = fixed_damage(); p.shop_strong_effect = 1;
    p.shop_blessing_weights = {1, 0, 0, 1, 0, 0, 0, 0}; // Ordinary shops must use other offers.
    auto s = encounter(Encounter::empty, 5); s.phase = Phase::doors;
    s.doors = {{{Door::shop}, {Door::wall}}};
    for (int selected = 0; selected < 2; ++selected) {
        Game game(p, 5, s, {10}); game.step({ActionKind::choose_door, 0});
        const auto& offers = game.state().offers;
        CHECK(offers[0].effect.kind == EffectKind::one_hit); CHECK(offers[0].effect.remaining == 4);
        CHECK(offers[1].effect.kind == EffectKind::escape); CHECK(offers[1].effect.remaining == 5);
        CHECK(offers[0].keys == 0 && offers[1].keys == 0); CHECK(!game.legal({ActionKind::reroll}));
        game.step({ActionKind::buy, selected}); CHECK(game.state().room == 6);
        CHECK(game.state().shop_purchases == 1); CHECK(!game.legal({ActionKind::buy, 1 - selected}));
        const auto* chosen = game.state().effect(selected == 0 ? EffectKind::one_hit : EffectKind::escape);
        CHECK(chosen); CHECK(chosen->remaining == (selected == 0 ? 4 : 5));
    }
    for (auto mode : {"fixed", "normal"}) {
        p.opening_shop_rerolls = mode;
        Game game(p, 5, s, {10}); game.step({ActionKind::choose_door, 0});
        CHECK(game.legal({ActionKind::reroll})); game.step({ActionKind::reroll});
        CHECK(game.state().room == 5); CHECK(game.state().mushrooms == 1);
        CHECK(game.state().offers[0].keys == 0 && game.state().offers[1].keys == 0);
        if (p.opening_shop_rerolls == "fixed") CHECK(game.state().offers[0].effect.kind == EffectKind::one_hit);
        else for (auto offer : game.state().offers) CHECK(offer.effect.kind == EffectKind::raider || offer.effect.kind == EffectKind::disarm);
    }
    p.opening_shop_rerolls = "disabled"; s.run_number = 2;
    Game later(p, 5, s, {10}); later.step({ActionKind::choose_door, 0}); CHECK(later.legal({ActionKind::reroll}));
    for (auto offer : later.state().offers) CHECK(offer.effect.kind == EffectKind::raider || offer.effect.kind == EffectKind::disarm);
    s.run_number = 1; s.room = 15;
    Game ordinary(p, 5, s, {10}); ordinary.step({ActionKind::choose_door, 0}); CHECK(ordinary.legal({ActionKind::reroll}));
    for (auto offer : ordinary.state().offers) CHECK(offer.effect.kind == EffectKind::raider || offer.effect.kind == EffectKind::disarm);
}
void mystery_beside_a_monster_excludes_enemies_but_allows_an_awakened_corpse() {
    auto p = fixed_damage(); p.skeleton_wakes = 1;
    for (int chosen : {0, 1}) {
        auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors;
        s.doors[static_cast<std::size_t>(chosen)] = {Door::mystery};
        s.doors[static_cast<std::size_t>(1 - chosen)] = {Door::monster};
        for (std::uint64_t seed = 0; seed < 128; ++seed) {
            Game game(p, seed, s); game.step({ActionKind::choose_door, chosen});
            CHECK(!is_enemy(game.state().encounter)); CHECK(game.state().room == 30);
        }
        auto forced = p; forced.mystery_without_monster_weights.reset(); forced.mystery_weights.fill(0); forced.mystery_weights[ix(Encounter::monster)] = 1;
        Game excluded(forced, 5, s); excluded.step({ActionKind::choose_door, chosen});
        CHECK(excluded.state().encounter == Encounter::empty);
        s.doors[static_cast<std::size_t>(1 - chosen)] = {Door::golden};
        Game allowed(forced, 5, s); allowed.step({ActionKind::choose_door, chosen});
        CHECK(allowed.state().encounter == Encounter::monster);
        allowed.step({ActionKind::fight}); CHECK(allowed.state().room == 31);
        CHECK(allowed.state().phase == Phase::doors); CHECK(allowed.state().encounter == Encounter::empty);
        s.doors[static_cast<std::size_t>(1 - chosen)] = {Door::monster};
        forced.mystery_weights.fill(0); forced.mystery_weights[ix(Encounter::skeleton)] = 1;
        Game corpse(forced, 5, s); corpse.step({ActionKind::choose_door, chosen});
        CHECK(corpse.state().encounter == Encounter::skeleton); corpse.step({ActionKind::interact});
        CHECK(corpse.state().encounter == Encounter::monster); CHECK(corpse.state().room == 30);
        CHECK(!corpse.legal({ActionKind::interact})); corpse.step({ActionKind::fight});
        CHECK(corpse.state().room == 31); CHECK(corpse.state().phase == Phase::doors);
    }
}
void empty_key_rooms_still_charge_the_key_and_advance_normally() {
    auto p = fixed_damage(); p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    for (auto kind : {Door::locked, Door::double_locked}) {
        auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors; s.keys = 2;
        s.doors = {{{kind}, {Door::wall}}};
        Game game(p, 5, s); game.step({ActionKind::choose_door, 0});
        CHECK(game.state().keys == (kind == Door::locked ? 1 : 0));
        CHECK(game.state().encounter == Encounter::empty); CHECK(game.state().room == 30);
        game.step({ActionKind::interact}); CHECK(game.state().room == 31);
        CHECK(game.state().gold_rewards == 0 && game.state().epics == 0); near(game.state().hp, 1);
    }
}
void mystery_override_depends_on_the_other_choice_and_preserves_other_pools() {
    auto p = fixed_damage(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::crate)] = 1;
    auto& unpaired = p.mystery_without_monster_weights.emplace(); unpaired[ix(Encounter::skeleton)] = 1;
    p.skeleton_wakes = 1;
    p.locked_weights.fill(0); p.locked_weights[ix(Encounter::silver)] = 1;
    p.destiny_weights.fill(0); p.destiny_weights[ix(Encounter::bronze)] = 1;
    for (int side : {0, 1}) for (auto other : {Door::monster, Door::golden, Door::locked, Door::wall}) {
        auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors; s.keys = 2;
        s.doors[static_cast<std::size_t>(side)] = {Door::mystery};
        s.doors[static_cast<std::size_t>(1 - side)] = {other};
        Game game(p, 8, s); game.step({ActionKind::choose_door, side});
        CHECK(game.state().encounter == (other == Door::monster ? Encounter::crate : Encounter::skeleton));
        if (other != Door::monster) {
            game.step({ActionKind::interact}); CHECK(game.state().encounter == Encounter::monster);
            CHECK(game.state().room == 30);
        }
        for (auto door : {Door::unlocked, Door::locked, Door::destiny}) {
            s.doors[static_cast<std::size_t>(side)] = {door};
            Game separate(p, 8, s); separate.step({ActionKind::choose_door, side});
            CHECK(separate.state().encounter == (door == Door::unlocked ? Encounter::crate :
                  door == Door::locked ? Encounter::silver : Encounter::bronze));
        }
    }
}
void mystery_override_is_optional_and_validated_in_profiles() {
    std::ifstream source(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile", std::ios::binary);
    std::string text((std::istreambuf_iterator<char>(source)), std::istreambuf_iterator<char>());
    const auto begin = text.find("\nmystery_without_monster="); CHECK(begin != std::string::npos);
    text.erase(begin + 1, text.find('\n', begin + 1) - begin);
    Temp file;
    { std::ofstream output(file.path); output << text; }
    auto legacy = legacy_tables(Profile::load(file.path.string())); CHECK(!legacy.mystery_without_monster_weights);
    legacy.mystery_weights.fill(0); legacy.mystery_weights[ix(Encounter::empty)] = 1;
    auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors; s.doors = {{{Door::mystery}, {Door::wall}}};
    Game old(legacy, 8, s); old.step({ActionKind::choose_door, 0}); CHECK(old.state().encounter == Encounter::empty);
    for (auto weights : {"monster:-1", "monster:0", "monster:1,monster:1", "unknown:1"}) {
        { std::ofstream output(file.path); output << text << "\nmystery_without_monster=" << weights << '\n'; }
        rejects([&] { Profile::load(file.path.string()); });
    }
}
void mystery_without_a_visible_monster_samples_the_revised_mix() {
    const auto p = profile(); std::array<int, ix(Encounter::count)> counts{};
    constexpr int samples = 12000;
    for (int i = 0; i < samples; ++i) {
        auto s = encounter(Encounter::empty, 30); s.phase = Phase::doors;
        const int side = i % 2;
        s.doors[static_cast<std::size_t>(side)] = {Door::mystery};
        s.doors[static_cast<std::size_t>(1 - side)] = {i % 3 == 0 ? Door::golden : Door::locked};
        Game game(p, static_cast<std::uint64_t>(i), s); game.step({ActionKind::choose_door, side});
        ++counts[ix(game.state().encounter)];
    }
    CHECK(std::abs(static_cast<double>(counts[ix(Encounter::monster)]) / samples - .80) < .03);
    CHECK(std::abs(static_cast<double>(counts[ix(Encounter::skeleton)]) / samples - .075) < .025);
    CHECK(std::abs(static_cast<double>(counts[ix(Encounter::crate)]) / samples - .025) < .015);
}
void strategic_policies_price_the_independent_mystery_risk() {
    auto p = fixed_damage(.8); p.escape_chance = 0;
    p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::empty)] = 1;
    p.locked_weights = p.mystery_weights;
    auto s = encounter(Encounter::empty, 80); s.phase = Phase::doors; s.keys = 1;
    s.doors = {{{Door::mystery}, {Door::locked}}};
    for (auto strategy : {Strategy::strategic, Strategy::key_stockpile}) {
        Policy policy; policy.strategy = strategy;
        auto& unpaired = p.mystery_without_monster_weights.emplace(); unpaired[ix(Encounter::empty)] = 1;
        Game safe(p, 8, s); CHECK(policy.choose(safe).index == 0);
        unpaired.fill(0); unpaired[ix(Encounter::monster)] = 1;
        Game risky(p, 8, s); const auto before = state_digest(risky.state());
        CHECK(policy.choose(risky).index == 1); CHECK(before == state_digest(risky.state()));
    }
}
void strategic_policy_uses_the_visible_pair_and_does_not_touch_rng_or_state() {
    auto p = fixed_damage(.2); Policy policy; policy.strategy = Strategy::strategic;
    auto s = encounter(Encounter::empty, 10); s.phase = Phase::doors;
    s.doors = {{{Door::monster}, {Door::mystery}}};
    Game game(p, 5, s), other_seed(p, 9183, s);
    const auto before = state_digest(game.state());
    CHECK(policy.choose(game).index == 1); CHECK(policy.choose(other_seed).index == 1);
    CHECK(before == state_digest(game.state()));
    Game untouched = game;
    game.step(policy.choose(game)); untouched.step({ActionKind::choose_door, 1});
    CHECK(state_digest(game.state()) == state_digest(untouched.state()));
    // A trap can make the otherwise safe mystery door worse than evasion.
    s.doors[1].trap = true; s.blessings.give(effect_template(EffectKind::escape, false));
    Game trapped(p, 5, s); CHECK(policy.choose(trapped).index == 0);
    s.doors[1].trap = false; s.blessings.clear(); s.blessings.give(effect_template(EffectKind::one_hit, false));
    p.key_chance = 1;
    Game free_keys(p, 5, s); CHECK(policy.choose(free_keys).index == 0);
}
void strategic_policy_compares_combat_and_heals_without_wasting_an_unusable_partial() {
    auto p = fixed_damage(.2); Policy policy; policy.strategy = Strategy::strategic;
    auto s = encounter(Encounter::monster, 30);
    s.blessings.give(effect_template(EffectKind::escape, false));
    Game escape(p, 5, s); CHECK(policy.choose(escape).kind == ActionKind::flee);
    s.blessings.clear(); s.blessings.give(effect_template(EffectKind::one_hit, false)); p.key_chance = 1;
    Game fight(p, 5, s); CHECK(policy.choose(fight).kind == ActionKind::fight);
    s.blessings.clear(); s.encounter = Encounter::boss; s.room = 50; s.hp = .05;
    Game funded(p, 5, s, {500}); CHECK(policy.choose(funded).kind == ActionKind::fight);
    Game insufficient(p, 5, s, {10}); CHECK(policy.choose(insufficient).kind == ActionKind::fight);
    Game free(p, 5, s); CHECK(policy.choose(free).kind == ActionKind::fight);
    s.phase = Phase::recovery; s.hp = 0;
    Game recovery(p, 5, s, {500}); CHECK(policy.choose(recovery).kind == ActionKind::heal_full);
    policy.recovery_budget = 0; CHECK(policy.choose(recovery).kind == ActionKind::wait);
}
void strategic_policy_preserves_useful_blessings_and_respects_closed_opening_rerolls() {
    auto p = fixed_damage(); Policy policy; policy.strategy = Strategy::strategic; policy.reroll_limit = 10;
    auto s = encounter(Encounter::empty, 30); s.phase = Phase::shop; s.keys = 10;
    s.blessings.give(effect_template(EffectKind::one_hit, true));
    s.blessings.give(effect_template(EffectKind::escape, true));
    s.blessings.give(effect_template(EffectKind::recovery, true));
    s.offers = {{{effect_template(EffectKind::raider, false), 1}, {effect_template(EffectKind::one_hit, false), 2}}};
    Game protected_effects(p, 5, s); CHECK(policy.choose(protected_effects).kind == ActionKind::skip);
    s = encounter(Encounter::empty, 5); s.phase = Phase::doors; s.doors = {{{Door::shop}, {Door::wall}}};
    Game opening(p, 5, s, {500}); opening.step({ActionKind::choose_door, 0});
    const auto action = policy.choose(opening); CHECK(action.kind == ActionKind::buy); CHECK(opening.legal(action));
}
void key_stockpile_collects_early_spends_late_and_keeps_free_choices() {
    auto p = fixed_damage(.13); p.key_chance = .5;
    Policy policy; policy.strategy = Strategy::key_stockpile;
    auto s = encounter(Encounter::monster, 10);
    Game early(p, 5, s); CHECK(policy.choose(early).kind == ActionKind::fight);
    s.keys = 10; Game stocked(p, 5, s); CHECK(policy.choose(stocked).kind == ActionKind::flee);
    s.keys = 0; s.room = 80; s.shops_seen[3] = true;
    Game late(p, 5, s); CHECK(policy.choose(late).kind == ActionKind::flee);
    s.blessings.give(effect_template(EffectKind::one_hit, false));
    Game free_fight(p, 5, s); CHECK(policy.choose(free_fight).kind == ActionKind::fight);
    s.blessings.clear(); s.phase = Phase::doors; s.keys = 1;
    s.doors = {{{Door::monster}, {Door::locked}}};
    p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    Game spend(p, 5, s); CHECK(policy.choose(spend).index == 1);
    s.room = 10;
    Game save(p, 5, s); CHECK(policy.choose(save).index == 0);
    s.doors[1] = {Door::mystery};
    p.mystery_without_monster_weights.reset(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::empty)] = 1;
    Game safe(p, 5, s); CHECK(policy.choose(safe).index == 1);
    s.phase = Phase::encounter; s.hp = .05;
    Game lethal(p, 5, s); CHECK(policy.choose(lethal).kind == ActionKind::flee);
}
void run_diagnostics_count_actual_reentries_waits_and_stage_key_use() {
    auto p = fixed_damage(); p.boss_damage.fill({.3, .3});
    p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    const auto start = StartState::parse("schema=1\nroom=99\nrun_number=1\nphase=doors\nhp_percent=10\nkeys=2\ngems=gambler,greed,blood\ndoors=locked,wall\n");
    Policy policy; policy.strategy = Strategy::strategic;
    BatchOptions options; options.runs = 1; options.start = start;
    const auto results = run_batch(p, policy, options);
    const auto& r = results.front();
    CHECK(r.complete && r.deaths == 1 && r.reentries == 1);
    near(r.recovery_wait_hours, .301 * p.recovery_hours);
    near(r.hours, r.recovery_wait_hours + r.active_hours);
    CHECK(!r.stages[0].completed && r.stages[3].completed);
    CHECK(r.stages[3].keys_spent_on_doors == 1 && r.stages[3].keys_at_exit == 1);
    CHECK(r.stages[3].nonboss_fights == 0 && r.stages[3].flee_actions == 0);
    const auto report = report_json(p, policy, options, results);
    CHECK(report.find("\"completed_run_death_histogram\": {\"1\":1}") != std::string::npos);
    CHECK(report.find("\"stage\":1,\"samples\":0,\"mean_nonboss_fights\":null") != std::string::npos);
}
void compulsory_shops_appear_once_per_stage_across_runs_and_arena() {
    auto p = fixed_damage(0); p.boss_damage.fill({0, 0}); p.trap_chance = 0;
    p.exclude_offered_gems = false; // Keep the five-stone shop fixture independent of offer history.
    p.door_weights.fill(0); p.door_weights[ix(Door::shop)] = 100;
    p.gem_pool = p.first_run_gem_pool = p.later_run_gem_pool = {Gem::rabbit, Gem::moonstone, Gem::hero, Gem::spying, Gem::greed};
    Policy policy;
    for (unsigned seed = 0; seed < 50; ++seed) {
        Limits limits; limits.repeat_runs = true;
        Game game(p, seed, limits); std::array<int, 4> shops{};
        while (game.state().completed_runs < 2) {
            const auto& s = game.state();
            auto action = policy.choose(game);
            if (s.phase == Phase::doors) {
                for (std::size_t i = 0; i < 2; ++i) if (s.doors[i].kind == Door::shop) {
                    ++shops[static_cast<std::size_t>((s.room - 1) / 25)];
                    CHECK(s.doors[1 - i].kind == Door::wall);
                    CHECK(!game.legal({ActionKind::choose_door, static_cast<int>(1-i)}));
                    if (s.run_number == 1 && s.room <= 25) CHECK(s.room == 5);
                    if (s.run_number == 2 && s.room <= 25) CHECK(s.room >= 6);
                }
                if (s.room == 51 && seed % 2) action = {ActionKind::choose_door, 1};
                else if (s.trial_depth > 0 && s.trial_depth < 5) action = {ActionKind::choose_door, 0};
            }
            if (s.phase == Phase::encounter && game.legal({ActionKind::fight})) action = {ActionKind::fight};
            if (s.phase == Phase::shop) action = {ActionKind::skip};
            if (s.phase == Phase::complete) {
                for (int count : shops) CHECK(count == 1);
                shops.fill(0); action = {ActionKind::restart};
            }
            CHECK(game.legal(action)); game.step(action);
            CHECK(!game.finished());
        }
        for (int count : shops) CHECK(count == 1);
    }
    p.shop_schedule = "sometimes"; rejects([&] { p.validate(); });
}
void shop_history_survives_resume_and_death_and_resets_on_restart() {
    auto p = fixed_damage(1); p.door_weights.fill(0); p.door_weights[ix(Door::monster)] = 1;
    const auto start = StartState::parse("schema=1\nroom=49\nrun_number=2\nphase=doors\nhp_percent=100\nkeys=0\ngems=rabbit\nshops_seen=1\ndoors=unknown\n");
    const auto roundtrip = StartState::parse(start.encode());
    Game due(p, 4, roundtrip); CHECK(due.state().doors[0].kind == Door::shop); CHECK(due.state().shops_seen[1]);
    auto seen = start; seen.state.shops_seen[1] = true;
    Game passed(p, 4, StartState::parse(seen.encode()));
    CHECK(passed.state().doors[0].kind != Door::shop && passed.state().doors[1].kind != Door::shop);
    passed.step({ActionKind::choose_door, passed.state().doors[0].kind == Door::monster ? 0 : 1}); passed.step({ActionKind::fight});
    CHECK(passed.state().phase == Phase::recovery); CHECK(passed.state().shops_seen[1]);
    auto s = encounter(Encounter::boss, 100); s.shops_seen.fill(true);
    p.boss_damage.fill({0, 0}); Limits limits; limits.repeat_runs = true;
    Game complete(p, 4, s, limits); complete.step({ActionKind::fight}); complete.step({ActionKind::restart});
    for (bool flag : complete.state().shops_seen) CHECK(!flag);
    rejects([&] { auto future = start; future.state.shops_seen[3] = true; future.validate(p); });
}
void official_escape_thresholds_apply_to_actual_combat() {
    const auto p = fixed_damage(.1); near(p.escape_chance, .4);
    for (unsigned seed = 0; seed < 200; ++seed) for (int combination = 0; combination < 4; ++combination) {
        auto s = encounter(); s.gems[ix(Gem::rabbit)] = (combination & 1) != 0;
        s.gems[ix(Gem::moonstone)] = (combination & 2) != 0;
        Random random(seed);
        const bool escaped = random.chance(Stream::escape, .4 + (combination & 1 ? .4 : 0) + (combination & 2 ? .2 : 0));
        const double hit = .1 * (s.has(Gem::rabbit) ? 1.25 : 1);
        Game game(p, seed, s); game.step({ActionKind::flee}); near(game.state().hp, escaped ? 1 : 1 - hit);
    }
}
void room_pairs_gate_stones_and_limit_traps() {
    auto p = fixed_damage(0); p.trap_chance = 1;
    bool two_mysteries = false, two_golden = false, unlocked = false, doubled = false, epic = false;
    for (unsigned mask = 0; mask < 8; ++mask) for (unsigned seed = 0; seed < 600; ++seed) {
        auto s = encounter(Encounter::empty, 30); s.run_number = 2; s.keys = 20; s.shops_seen.fill(true);
        s.gems[ix(Gem::spying)] = (mask & 1) != 0;
        s.gems[ix(Gem::lodestone)] = (mask & 2) != 0;
        s.gems[ix(Gem::devil)] = (mask & 4) != 0;
        Game game(p, seed, s); game.step({ActionKind::interact});
        int monsters = 0, mysteries = 0, types = 0, traps = 0, gold = 0;
        for (auto d : game.state().doors) {
            monsters += d.kind == Door::monster; mysteries += d.kind == Door::mystery;
            gold += d.kind == Door::golden; traps += d.trap;
            types += d.kind != Door::monster && d.kind != Door::mystery && d.kind != Door::golden;
            CHECK(d.kind != Door::golden || !d.trap);
            CHECK(d.kind != Door::unlocked || s.has(Gem::spying));
            CHECK(d.kind != Door::double_locked || s.has(Gem::lodestone));
            CHECK(d.kind != Door::epic || s.has(Gem::devil));
            unlocked |= d.kind == Door::unlocked; doubled |= d.kind == Door::double_locked; epic |= d.kind == Door::epic;
        }
        CHECK(monsters <= 1); CHECK(types <= 1); CHECK(traps <= 1);
        two_mysteries |= mysteries == 2;
        two_golden |= gold == 2;
    }
    CHECK(two_mysteries && two_golden && unlocked && doubled && epic);
    // Even a manually supplied old golden-trap observation cannot hurt or curse.
    auto s = encounter(Encounter::empty, 6); s.phase = Phase::doors; s.doors = {{{Door::golden, true, true}, {Door::wall}}};
    Game safe(p, 1, s); safe.step({ActionKind::choose_door, 0}); near(safe.state().hp, 1); CHECK(safe.state().curses.size == 0);
}
void double_mystery_generation_keeps_both_choices_and_the_unpaired_content_pool() {
    auto p = fixed_damage(0); p.trap_chance = 0;
    p.door_weights.fill(0); p.door_weights[ix(Door::mystery)] = 1;
    p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::empty)] = 1;
    auto& unpaired = p.mystery_without_monster_weights.emplace();
    unpaired.fill(0); unpaired[ix(Encounter::monster)] = 1;
    for (int side : {0, 1}) for (unsigned seed = 0; seed < 32; ++seed) {
        auto s = encounter(Encounter::empty, 30); s.run_number = 2; s.shops_seen.fill(true);
        Game game(p, seed, s); game.step({ActionKind::interact});
        CHECK(game.state().doors[0].kind == Door::mystery);
        CHECK(game.state().doors[1].kind == Door::mystery);
        CHECK(game.legal({ActionKind::choose_door, side}));
        game.step({ActionKind::choose_door, side});
        CHECK(game.state().encounter == Encounter::monster);
    }
}
void first_five_choices_are_fixed_and_first_stage_has_no_cursed_doors() {
    auto p = fixed_damage(0); p.trap_chance = 1;
    for (int run : {1, 2, 8}) for (int n = 1; n <= 5; ++n) for (unsigned seed = 0; seed < 100; ++seed) {
        StartState start; start.state = encounter(Encounter::empty, n); start.state.phase = Phase::doors;
        start.state.run_number = run; start.generate_current = true;
        Game g(p, seed, start);
        for (const auto door : g.state().doors) CHECK(!door.trap && !door.cursed_trap);
        if (run == 1 && n == 5) { CHECK(g.state().doors[0].kind == Door::shop); CHECK(g.state().doors[1].kind == Door::wall); }
        else {
            auto a = g.state().doors[0], b = g.state().doors[1];
            CHECK((a.kind == Door::monster && b.kind == Door::mystery) || (a.kind == Door::mystery && b.kind == Door::monster));
            g.step({ActionKind::choose_door, a.kind == Door::mystery ? 0 : 1}); CHECK(!is_enemy(g.state().encounter));
        }
    }
    for (unsigned seed = 0; seed < 1000; ++seed) {
        auto s = encounter(Encounter::empty, 10); s.shops_seen[0] = true;
        Game g(p, seed, s); g.step({ActionKind::interact});
        for (auto d : g.state().doors) CHECK(d.kind != Door::cursed && d.kind != Door::sacrifice);
    }
}
void refill_ladder_continues_through_repeated_deaths_and_bosses_require_victory() {
    auto p = fixed_damage(); auto s = encounter(Encounter::boss, 25); s.phase = Phase::recovery; s.hp = 0;
    Game g(p, 7, s, {1000}); int spent = 0;
    for (int n = 0; n < 8; ++n) {
        CHECK(g.step_price() == 10 + 5*n); spent += g.step_price();
        g.step({ActionKind::heal_step}); g.step({ActionKind::reenter}); g.step({ActionKind::fight});
        CHECK(g.state().phase == Phase::recovery); CHECK(g.state().room == 25);
        CHECK(g.state().mushrooms == spent); CHECK(g.state().paid_steps == n+1);
        CHECK(g.state().epics == 0); CHECK(g.state().completed_runs == 0);
    }
}
void mimic_and_rps_offer_only_their_two_encounter_actions() {
    auto p = fixed_damage(0);
    Game mimic(p, 1, encounter(Encounter::mimic, 30));
    CHECK(mimic.legal({ActionKind::fight})); CHECK(mimic.legal({ActionKind::skip}));
    CHECK(!mimic.legal({ActionKind::flee})); CHECK(!mimic.legal({ActionKind::interact}));
    for (auto strategy : {Strategy::baseline, Strategy::strategic, Strategy::key_stockpile}) {
        Policy policy; policy.strategy = strategy; CHECK(mimic.legal(policy.choose(mimic)));
    }
    Game rps(p, 1, encounter(Encounter::rps, 30));
    CHECK(rps.legal({ActionKind::interact})); CHECK(rps.legal({ActionKind::skip}));
    for (int i = 0; i < 3; ++i) CHECK(!rps.legal({ActionKind::rps, i}));
    CHECK(!rps.legal({ActionKind::fight})); CHECK(!rps.legal({ActionKind::flee}));
}
void rusty_chart_boosts_effect_healing_by_forty_percent() {
    auto p = fixed_damage(); auto s = encounter(Encounter::fountain, 30); s.hp = .1; s.gems[ix(Gem::rusty)] = true;
    Game fountain(p, 1, s); fountain.step({ActionKind::interact}); near(fountain.state().hp, .45);
    s.encounter = Encounter::empty; s.blessings.give(effect_template(EffectKind::recovery, false));
    Game recovery(p, 1, s); recovery.step({ActionKind::interact}); near(recovery.state().hp, .24);
}
void diamond_full_heals_only_instant_elixirs_when_enabled() {
    auto p = fixed_damage(); CHECK(p.time_traveler_elixir_full_heal);
    for (bool enabled : {false, true}) for (bool diamond : {false, true}) for (bool strong : {false, true}) {
        p.time_traveler_elixir_full_heal = enabled;
        auto s = encounter(Encounter::empty, 30); s.hp = .05; s.phase = Phase::shop;
        s.gems[ix(Gem::time_traveler)] = diamond;
        s.offers = {{{effect_template(EffectKind::elixir, strong), 0}, {effect_template(EffectKind::one_hit, false), 0}}};
        Game game(p, 1, s); game.step({ActionKind::buy, 0});
        near(game.state().hp, enabled && diamond ? 1.0 : .05 + (strong ? .5 : .25));
        CHECK(!game.state().effect(EffectKind::elixir));
    }
    p.time_traveler_elixir_full_heal = true;
    auto s = encounter(Encounter::empty, 30); s.hp = .05; s.gems[ix(Gem::time_traveler)] = true;
    s.blessings.give(effect_template(EffectKind::recovery, false));
    Game recovery(p, 1, s); recovery.step({ActionKind::interact}); near(recovery.state().hp, .15);
    s.blessings.clear(); s.encounter = Encounter::fountain;
    Game fountain(p, 1, s); fountain.step({ActionKind::interact}); near(fountain.state().hp, .30);
    s.encounter = Encounter::empty; s.phase = Phase::shop;
    s.offers = {{{effect_template(EffectKind::elixir, false), 0}, {effect_template(EffectKind::one_hit, false), 0}}};
    for (auto strategy : {Strategy::baseline, Strategy::strategic, Strategy::key_stockpile}) {
        Policy policy; policy.strategy = strategy; Game shop(p, 1, s);
        const auto chosen = policy.choose(shop); CHECK(chosen.kind == ActionKind::buy && chosen.index == 0);
    }
}
void opening_trap_boundary_and_import_validation() {
    auto p = fixed_damage(0); p.trap_chance = 1; p.shop_schedule = "weighted";
    p.door_weights.fill(0); p.door_weights[ix(Door::monster)] = 1;
    for (int run : {1, 2, 8}) {
        StartState start; start.state = encounter(Encounter::empty, 6);
        start.state.phase = Phase::doors; start.state.run_number = run; start.generate_current = true;
        Game boundary(p, 5, start);
        CHECK(int(boundary.state().doors[0].trap) + int(boundary.state().doors[1].trap) == 1);
        for (int room = 1; room <= 5; ++room) for (bool cursed : {false, true}) {
            start.generate_current = false; start.state.room = room; start.state.turn = room - 1;
            start.state.doors = {{{Door::monster, true, cursed}, {Door::mystery}}};
            rejects([&] { Game invalid(p, 5, start); });
            // Low-level fixture states cannot trigger a stale opening trap either.
            Game stale(p, 5, start.state); stale.step({ActionKind::choose_door, 0});
            near(stale.state().hp, 1); CHECK(stale.state().curses.size == 0);
            CHECK(stale.outcomes().empty());
        }
    }
}
void first_run_containers_never_return_empty_loot() {
    for (const auto* file : {"synthetic.profile", "mushroom-school-2024.profile"}) {
        auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/" + file);
        p.skeleton_wakes = 0;
        for (int run : {1, 2}) for (auto kind : {Encounter::crate, Encounter::silver, Encounter::bronze, Encounter::skeleton, Encounter::epic}) {
            bool empty_seen = false;
            for (unsigned seed = 0; seed < 256; ++seed) {
                auto s = encounter(kind, 30); s.run_number = run; s.gems[ix(Gem::hero)] = true;
                Game game(p, seed, s); game.step({ActionKind::interact});
                const bool rewarded = std::any_of(game.outcomes().begin(), game.outcomes().end(), [](const Outcome& e) {
                    return e.kind == "gold" || e.kind == "item" || e.kind == "epics" || e.kind == "blessing";
                });
                empty_seen |= !rewarded;
                if (run == 1) CHECK(rewarded);
            }
            CHECK(empty_seen == (run > 1));
        }
        // Even a profile containing only empty outcomes must honor protection.
        p.container_joint_weights = std::array<double, 8>{1, 0, 0, 0, 0, 0, 0, 0};
        p.container_weights = {0, 0, 0, 1};
        for (auto kind : {Encounter::crate, Encounter::skeleton}) {
            Game game(p, 1, encounter(kind, 3)); game.step({ActionKind::interact});
            CHECK(game.state().gold_rewards == 1); near(game.state().hp, 1);
        }
        p.skeleton_wakes = 1;
        Game awake(p, 1, encounter(Encounter::skeleton, 3)); awake.step({ActionKind::interact});
        CHECK(awake.state().encounter == Encounter::monster); CHECK(awake.state().gold_rewards == 0);
    }
}
}
int main() {
    const std::pair<const char*, std::function<void()>> cases[] = {
#define TEST(fn) {#fn, fn}
        TEST(mystery_override_depends_on_the_other_choice_and_preserves_other_pools),
        TEST(mystery_override_is_optional_and_validated_in_profiles),
        TEST(mystery_without_a_visible_monster_samples_the_revised_mix),
        TEST(strategic_policies_price_the_independent_mystery_risk),
        TEST(effects_replace_and_refresh), TEST(ordinary_damage_and_additive_modifiers), TEST(boss_ignores_combat_and_room_effects),
        TEST(one_hit_is_not_a_free_failed_escape), TEST(failed_escape_has_separate_modifiers), TEST(death_preserves_progress_and_clears_effects),
        TEST(calibrated_damage_scope_is_optional_and_keeps_escape_only_stones),
        TEST(key_calibration_respects_spying_lodestone_and_key_moment),
        TEST(recovery_precedes_poison_without_repeating_room_rewards), TEST(acquired_effect_starts_in_next_room),
        TEST(purchased_recovery_heals_before_shop_exit_poison),
        TEST(chest_recovery_heals_on_acquisition_exit_and_consumes_one_use),
        TEST(combat_curses_require_a_strike_without_one_hit),
        TEST(stage_three_hero_damage_is_random_and_respects_its_fight_reduction),
        TEST(zero_health_rounding_and_independent_effect_slots),
        TEST(losing_lockpick_on_exit_death_keeps_a_legal_path), TEST(greasy_removes_epic_chests_from_mystery_doors_too),
        TEST(greasy_recovery_requires_surviving_the_room), TEST(paid_steps_escalate_and_respect_hard_budget),
        TEST(paid_refills_are_available_only_during_recovery),
        TEST(full_refill_is_separate_from_step_schedule), TEST(locked_trap_does_not_charge_twice_after_death),
        TEST(lockpick_disarm_use_event_counters), TEST(diamond_overrides_gambler_for_barrels), TEST(only_offered_gems_can_be_selected),
        TEST(free_shops_are_first_run_only), TEST(trial_exit_and_death_reset), TEST(flooded_room_enforces_timer),
        TEST(trial_entrance_is_offered_at_51_and_can_be_declined), TEST(trial_fights_and_both_escape_outcomes_advance_to_five),
        TEST(trial_exit_reward_uses_survived_tier_and_can_be_skipped), TEST(trial_death_forfeits_reward_and_resumes_normal_rooms),
        TEST(all_encounters_have_a_transition), TEST(final_boss_completes_without_an_extra_epic),
        TEST(boss_reentry_policy_waits_for_enough_health), TEST(login_grid_is_included_in_wait),
        TEST(threaded_runs_are_reproducible_and_budget_bounded), TEST(censored_runs_are_not_dropped),
        TEST(action_cap_invalidates_deadline_statistics), TEST(trace_replays_and_rejects_tampering),
        TEST(profile_validation_rejects_guesses_disguised_as_verified),
        TEST(progress_preserves_observed_doors_and_walls), TEST(progress_effects_apply_with_remaining_counters),
        TEST(resumed_recovery_retains_heal_prices_and_future_budget), TEST(resumed_recovery_restores_doors_without_repaying_an_encounter),
        TEST(observed_shop_offers_and_reroll_history_are_preserved), TEST(progress_gem_offers_override_unknown_future_pool),
        TEST(run_specific_gem_pools_select_the_configured_choices), TEST(progress_can_continue_an_active_trial),
        TEST(gem_offer_history_excludes_every_displayed_stone_across_three_stages),
        TEST(disabled_offer_exclusion_preserves_legacy_shuffle_and_records_history),
        TEST(gem_offer_history_survives_death_and_clears_at_completion_and_restart),
        TEST(gem_pool_minimum_matches_the_offer_exclusion_rule),
        TEST(progress_rejects_inconsistent_and_misspelled_states), TEST(progress_round_trips_without_hp_or_effect_rounding),
        TEST(progress_forecasts_and_traces_are_reproducible), TEST(final_boss_forecast_reports_only_remaining_time),
        TEST(old_engine_traces_require_their_original_version),
        TEST(fountain_variants_and_narrator_have_distinct_effects), TEST(eight_room_one_hit_expires_and_still_awards_keys),
        TEST(key_moment_rolls_two_keys_at_seventy_percent), TEST(gold_curse_reduces_chest_rewards_and_combines_with_raider),
        TEST(generated_shop_offers_are_distinct_with_independent_strengths), TEST(rerolls_do_not_tick_effects_or_spend_keys_and_buy_ends_shop),
        TEST(curse_shop_has_no_reroll_and_one_trade_grants_keys_then_leaves),
        TEST(targeted_rerolls_reject_weak_one_hit_and_stop_at_strong), TEST(targeted_rerolls_stop_when_capped_unaffordable_or_unavailable),
        TEST(shop_policy_accounts_for_losing_oldest_blessing), TEST(recovery_budget_can_reserve_mushrooms_for_rerolls),
        TEST(lantern_zombie_and_flying_tube_have_half_damage), TEST(pig_can_be_skipped_and_does_not_heal_a_lethal_fight),
        TEST(armory_requires_eligible_equipment_and_generates_only_at_90_to_98),
        TEST(armory_once_per_run_schedule_offers_one_golden_wall_pair_in_90_to_98),TEST(auction_item_is_not_automatically_epic),
        TEST(duration_gems_extend_acquired_effects_and_death_clears_them), TEST(one_hit_does_not_prevent_trap_or_poison_damage),
        TEST(cursed_traps_apply_no_direct_damage_and_poison_is_separate),
        TEST(greed_unlocks_container_blessings_only_on_the_loot_branch),
        TEST(greed_uses_profile_blessing_weight_without_an_additive_bonus),
        TEST(container_blessing_gate_can_be_disabled_for_sensitivity),
        TEST(generated_traps_are_converted_by_gambler_without_a_random_curse_roll),
        TEST(trial_legendary_probabilities_are_specific_to_the_collected_tier),
        TEST(destiny_spins_award_all_six_outcomes_before_separate_room_contents),
        TEST(hungry_doors_enforce_payment_and_never_return_the_donated_resource),
        TEST(blessing_door_is_free_and_its_effect_applies_to_the_waiting_enemy),
        TEST(door_reward_profiles_reject_invalid_probabilities_and_retired_trap_odds),
        TEST(opening_shop_has_fixed_free_offers_and_explicit_reroll_scenarios),
        TEST(mystery_beside_a_monster_excludes_enemies_but_allows_an_awakened_corpse),
        TEST(empty_key_rooms_still_charge_the_key_and_advance_normally),
        TEST(strategic_policy_uses_the_visible_pair_and_does_not_touch_rng_or_state),
        TEST(strategic_policy_compares_combat_and_heals_without_wasting_an_unusable_partial),
        TEST(strategic_policy_preserves_useful_blessings_and_respects_closed_opening_rerolls),
        TEST(key_stockpile_collects_early_spends_late_and_keeps_free_choices),
        TEST(run_diagnostics_count_actual_reentries_waits_and_stage_key_use),
        TEST(compulsory_shops_appear_once_per_stage_across_runs_and_arena),
        TEST(shop_history_survives_resume_and_death_and_resets_on_restart),
        TEST(official_escape_thresholds_apply_to_actual_combat),
        TEST(room_pairs_gate_stones_and_limit_traps), TEST(double_mystery_generation_keeps_both_choices_and_the_unpaired_content_pool),
        TEST(first_five_choices_are_fixed_and_first_stage_has_no_cursed_doors),
        TEST(opening_trap_boundary_and_import_validation), TEST(first_run_containers_never_return_empty_loot),
        TEST(refill_ladder_continues_through_repeated_deaths_and_bosses_require_victory), TEST(mimic_and_rps_offer_only_their_two_encounter_actions),
        TEST(rusty_chart_boosts_effect_healing_by_forty_percent), TEST(diamond_full_heals_only_instant_elixirs_when_enabled)
#undef TEST
    };
    int failed = 0;
    for (const auto& [name, test] : cases) {
        try { test(); std::cout << "PASS " << name << '\n'; }
        catch (const std::exception& e) { ++failed; std::cerr << "FAIL " << name << ": " << e.what() << '\n'; }
    }
    std::cout << std::size(cases) - static_cast<std::size_t>(failed) << "/" << std::size(cases) << " tests passed.\n";
    return failed ? 1 : 0;
}
