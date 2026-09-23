#include "sfld/batch.hpp"
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
        TEST(profile_validation_rejects_guesses_disguised_as_verified)
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
