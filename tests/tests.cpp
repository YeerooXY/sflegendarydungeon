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
Profile profile() { return Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile"); }
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
    Game game(p, 1, s); game.step({ActionKind::flee}); near(game.state().hp, .82);
}
void death_preserves_progress_and_clears_effects() {
    auto p = fixed_damage(); auto s = encounter(Encounter::monster, 37); s.hp = .1; s.keys = 7;
    s.gems[ix(Gem::hero)] = true; s.blessings.give(effect(EffectKind::lockpick, 1, 2, Clock::door));
    s.curses.give(effect(EffectKind::hard_lock, 2, 4));
    Game game(p, 1, s); game.step({ActionKind::fight});
    CHECK(game.state().phase == Phase::recovery); CHECK(game.state().room == 37); CHECK(game.state().keys == 7);
    CHECK(game.state().has(Gem::hero)); CHECK(game.state().blessings.size == 0); CHECK(game.state().curses.size == 0);
    CHECK(game.state().resources[0] == 10); near(game.state().hp, 0);
    const auto before = state_digest(game.state()); rejects([&] { game.step({ActionKind::reenter}); });
    CHECK(before == state_digest(game.state()));
    const double time = game.state().elapsed_hours;
    game.step({ActionKind::wait, 0, 4.8}); near(game.state().hp, .2); near(game.state().elapsed_hours - time, 4.8);
    game.step({ActionKind::reenter}); CHECK(game.state().phase == Phase::encounter);
}
void poison_cannot_be_healed_after_death_or_repeat_room_reward() {
    auto p = fixed_damage(); auto s = encounter(Encounter::sarcophagus, 12); s.hp = .04;
    s.curses.give(effect(EffectKind::poison, .05, 5)); s.blessings.give(effect(EffectKind::recovery, .2, 3));
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
    auto p = fixed_damage(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::epic)] = 1;
    auto s = encounter(); s.phase = Phase::doors; s.doors = {{{Door::mystery}, {Door::wall}}};
    s.gems[ix(Gem::greasy)] = true;
    Game game(p, 1, s); game.step({ActionKind::choose_door, 0});
    CHECK(game.state().encounter == Encounter::empty);
}
void greasy_recovery_requires_surviving_the_room() {
    auto p = fixed_damage(.05); auto s = encounter(Encounter::monster, 30); s.hp = .04; s.gems[ix(Gem::greasy)] = true;
    Game game(p, 1, s); game.step({ActionKind::fight}); game.step({ActionKind::wait, 0, 4.8}); game.step({ActionKind::reenter});
    near(game.state().hp, .2); CHECK(game.state().effect(EffectKind::recovery)->remaining == 3);
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
void locked_trap_does_not_charge_twice_after_death() {
    auto p = fixed_damage(); p.locked_weights.fill(0); p.locked_weights[ix(Encounter::monster)] = 1;
    auto s = encounter(); s.phase = Phase::doors; s.hp = .05; s.keys = 1;
    s.doors = {{{Door::locked, true}, {Door::wall}}};
    Game game(p, 1, s); game.step({ActionKind::choose_door, 0});
    CHECK(game.state().keys == 0); CHECK(game.state().phase == Phase::recovery);
    game.step({ActionKind::wait, 0, 4.8}); game.step({ActionKind::reenter});
    CHECK(game.state().phase == Phase::encounter); CHECK(game.state().keys == 0); near(game.state().hp, .2);
}
void lockpick_disarm_use_event_counters() {
    auto p = fixed_damage(); p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    auto s = encounter(); s.phase = Phase::doors; s.doors = {{{Door::double_locked, true}, {Door::wall}}};
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
        Action action{is_enemy(e) ? ActionKind::fight : (e == Encounter::rps ? ActionKind::rps : ActionKind::interact)};
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
    p.later_run_gem_pool = {Gem::hero, Gem::bull, Gem::blood, Gem::hick, Gem::devil};
    Game game(p, 2, start); CHECK(game.state().gem_offers == start.state.gem_offers);
    Policy policy; policy.preferred_gem = Gem::greasy; game.step(policy.choose(game));
    CHECK(game.state().has(Gem::greasy)); CHECK(game.state().has(Gem::rabbit)); CHECK(game.state().room == 51);
}
void run_specific_gem_pools_select_the_configured_choices() {
    auto p = profile();
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
    { std::ofstream out(file.path); out << text << "\ngems_first_run=rabbit,moonstone,spying,pendant,greasy\n"
        << "gems_later_runs=hero,bull,blood,hick,devil\n"; }
    const auto loaded = Profile::load(file.path.string());
    CHECK(loaded.gems_for_run(1) == p.first_run_gem_pool); CHECK(loaded.gems_for_run(2) == p.later_run_gem_pool);
}
void progress_can_continue_an_active_trial() {
    auto p = fixed_damage(.1); p.trial_legendary_chance = 0;
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
    text.replace(0, std::string("#sfld-trace-v2\t0.3.0").size(), "#sfld-trace-v1");
    const auto start = text.find("#start\tfresh\t0\n#actions\n"); CHECK(start != std::string::npos);
    text.erase(start, std::string("#start\tfresh\t0\n#actions\n").size());
    Temp file; { std::ofstream out(file.path); out << text; }
    rejects([&] { replay(p, file.path.string()); });
    text = current; text.replace(text.find("0.3.0"), 5, "0.2.0");
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
        game.step({ActionKind::choose_door, 0}); game.step({ActionKind::fight}); near(game.state().hp, .25);
    }
    CHECK(game.state().keys == 8); CHECK(!game.state().effect(EffectKind::one_hit));
    game.step({ActionKind::choose_door, 0}); game.step({ActionKind::fight}); near(game.state().hp, .05);
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
}
int main() {
    const std::pair<const char*, std::function<void()>> cases[] = {
#define TEST(fn) {#fn, fn}
        TEST(effects_replace_and_refresh), TEST(ordinary_damage_and_additive_modifiers), TEST(boss_ignores_combat_and_room_effects),
        TEST(one_hit_is_not_a_free_failed_escape), TEST(failed_escape_has_separate_modifiers), TEST(death_preserves_progress_and_clears_effects),
        TEST(poison_cannot_be_healed_after_death_or_repeat_room_reward), TEST(acquired_effect_starts_in_next_room),
        TEST(losing_lockpick_on_exit_death_keeps_a_legal_path), TEST(greasy_removes_epic_chests_from_mystery_doors_too),
        TEST(greasy_recovery_requires_surviving_the_room), TEST(paid_steps_escalate_and_respect_hard_budget),
        TEST(full_refill_is_separate_from_step_schedule), TEST(locked_trap_does_not_charge_twice_after_death),
        TEST(lockpick_disarm_use_event_counters), TEST(diamond_overrides_gambler_for_barrels), TEST(only_offered_gems_can_be_selected),
        TEST(free_shops_are_first_run_only), TEST(trial_exit_and_death_reset), TEST(flooded_room_enforces_timer),
        TEST(all_encounters_have_a_transition), TEST(final_boss_completes_without_an_extra_epic),
        TEST(boss_reentry_policy_waits_for_enough_health), TEST(login_grid_is_included_in_wait),
        TEST(threaded_runs_are_reproducible_and_budget_bounded), TEST(censored_runs_are_not_dropped),
        TEST(action_cap_invalidates_deadline_statistics), TEST(trace_replays_and_rejects_tampering),
        TEST(profile_validation_rejects_guesses_disguised_as_verified),
        TEST(progress_preserves_observed_doors_and_walls), TEST(progress_effects_apply_with_remaining_counters),
        TEST(resumed_recovery_retains_heal_prices_and_future_budget), TEST(resumed_recovery_restores_doors_without_repaying_an_encounter),
        TEST(observed_shop_offers_and_reroll_history_are_preserved), TEST(progress_gem_offers_override_unknown_future_pool),
        TEST(run_specific_gem_pools_select_the_configured_choices), TEST(progress_can_continue_an_active_trial),
        TEST(progress_rejects_inconsistent_and_misspelled_states), TEST(progress_round_trips_without_hp_or_effect_rounding),
        TEST(progress_forecasts_and_traces_are_reproducible), TEST(final_boss_forecast_reports_only_remaining_time),
        TEST(old_engine_traces_require_their_original_version),
        TEST(fountain_variants_and_narrator_have_distinct_effects), TEST(eight_room_one_hit_expires_and_still_awards_keys),
        TEST(key_moment_rolls_two_keys_at_seventy_percent), TEST(gold_curse_reduces_chest_rewards_and_combines_with_raider),
        TEST(generated_shop_offers_are_distinct_with_independent_strengths), TEST(rerolls_do_not_tick_effects_or_spend_keys_and_buy_ends_shop),
        TEST(targeted_rerolls_reject_weak_one_hit_and_stop_at_strong), TEST(targeted_rerolls_stop_when_capped_unaffordable_or_unavailable),
        TEST(shop_policy_accounts_for_losing_oldest_blessing), TEST(recovery_budget_can_reserve_mushrooms_for_rerolls),
        TEST(lantern_zombie_and_flying_tube_have_half_damage), TEST(pig_can_be_skipped_and_does_not_heal_a_lethal_fight),
        TEST(armory_requires_eligible_equipment_and_generates_only_at_90_to_98), TEST(auction_item_is_not_automatically_epic),
        TEST(duration_gems_extend_acquired_effects_and_death_clears_them), TEST(one_hit_does_not_prevent_trap_or_poison_damage)
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
