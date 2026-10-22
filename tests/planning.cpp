#include "sfld/rollout.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <set>
#include <stdexcept>

using namespace sfld;
namespace {
void check(bool value, int line) {
    if (!value) throw std::runtime_error("Planning test failed at line " + std::to_string(line));
}
#define CHECK(value) check(bool(value), __LINE__)
void near(double a, double b) { CHECK(std::abs(a - b) < 1e-10); }
template<class F> void rejects(F operation) {
    bool failed = false; try { operation(); } catch (const std::exception&) { failed = true; } CHECK(failed);
}
Profile profile() {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    // The deliberately minimal fight-only continuation requires the fixed
    // monster/mystery opening; special-door legality has dedicated tests.
    p.opening_door_weights.reset(); return p;
}
State encounter(int room = 10, double hp = 1) {
    State s; s.phase = Phase::encounter; s.encounter = room % 25 == 0 ? Encounter::boss : Encounter::monster;
    s.room = room; s.turn = room - 1; s.hp = hp; return s;
}
State shop() {
    auto s = encounter(37, .45); s.phase = Phase::shop; s.run_number = 2; s.keys = 7;
    s.offers = {{{effect_template(EffectKind::one_hit, true), 4}, {effect_template(EffectKind::escape, false), 2}}};
    for (auto& offer : s.offers) offer.effect.starts_at = s.turn;
    return s;
}
const RolloutActionEstimate& estimate(const RolloutReport& report, ActionKind kind) {
    const auto found = std::find_if(report.actions.begin(), report.actions.end(), [&](const auto& e) { return e.action.kind == kind; });
    CHECK(found != report.actions.end()); return *found;
}
Controller fight_or_expire() {
    return [](const Game& game) -> Action {
        if (game.state().phase == Phase::recovery && game.legal({ActionKind::reenter})) return {ActionKind::reenter};
        if (game.state().phase == Phase::recovery)
            return {ActionKind::wait, 0, game.limits().deadline_hours - game.state().elapsed_hours};
        if (game.state().phase == Phase::complete) return {ActionKind::restart};
        if (game.state().phase == Phase::doors) return {ActionKind::choose_door, 0};
        return {ActionKind::fight};
    };
}
void snapshots_preserve_visible_state_and_prices_exactly() {
    auto p = profile(); auto s = shop();
    s.resources = {1, 2, 3, 4, 5, 6}; s.gems[ix(Gem::greasy)] = true;
    auto effect = effect_template(EffectKind::one_hit, false); effect.starts_at = 34; s.blessings.give(effect);
    effect = effect_template(EffectKind::poison, false); effect.starts_at = 36; s.curses.give(effect);
    s.paid_steps = 2; s.mushrooms = 13; s.recovery_mushrooms = 10; s.reroll_mushrooms = 3;
    s.shop_rerolls = 2; s.reroll_shop_visits = 3; s.reroll_bank_used = 10;
    s.elapsed_hours = 12.5; s.active_hours = .3; s.actions = 123;
    s.run_start_hours = 10; s.run_start_mushrooms = 5; s.completed_runs = 1; s.first_completion_hours = 5;
    s.trial_seen = true; s.donated_resource = 1; s.shop_purchases = 2; s.strong_one_hit_purchases = 1;
    s.epics = 4; s.legendaries = 1; s.gold_rewards = 2; s.gold_units = 3.5; s.lucky_coins = 7;
    Limits limits; limits.budget = 100; limits.deadline_hours = 168; limits.repeat_runs = true; limits.restart_health = "carry";
    Game game(p, 19, s, limits); game.step({ActionKind::reroll});
    const auto snapshot = game.planning_snapshot();
    Game branch(p, 872, snapshot);
    CHECK(state_digest(game.state()) == state_digest(branch.state()));
    CHECK(observation_json(game) == observation_json(branch));
    CHECK(branch.limits().budget == limits.budget); CHECK(branch.limits().deadline_hours == limits.deadline_hours);
    CHECK(branch.limits().max_actions == limits.max_actions); CHECK(branch.limits().repeat_runs);
    CHECK(branch.limits().run_number == limits.run_number); CHECK(branch.limits().restart_health == "carry");
    CHECK(branch.recovery_destination() == game.recovery_destination());
    CHECK(branch.step_price() == 20); CHECK(branch.full_price() == game.full_price());
    // A planning snapshot is copied exactly, even when an imported visible
    // ledger differs from fixture-constructor normalization conventions.
    auto imported = snapshot; imported.state.reroll_shop_visits = 0; imported.state.shops_seen.fill(false);
    Game exact(p, 4, imported); CHECK(exact.state().reroll_shop_visits == 0);
    CHECK(!exact.state().shops_seen[1]); CHECK(state_digest(exact.state()) == state_digest(imported.state));
    // Inactive shop offers remain in State after restart, with previous-run
    // activation turns. Preserve them instead of treating them as active buffs.
    auto completed = shop(); completed.phase = Phase::complete; completed.room = 101; completed.turn = 100;
    Game restart(p, 4, completed, limits); restart.step({ActionKind::restart});
    CHECK(restart.state().offers[0].effect.starts_at > restart.state().turn);
    Game restarted_copy(p, 3, restart.planning_snapshot());
    CHECK(state_digest(restart.state()) == state_digest(restarted_copy.state()));
}
void future_samples_do_not_depend_on_live_rng_history() {
    auto p = profile(); Limits limits; limits.budget = 100;
    Game consumed(p, 11, shop(), limits); consumed.step({ActionKind::reroll});
    // The same seed and visible state, but a different position in the live shop stream.
    Game reset_stream(p, 11, consumed.state(), limits);
    Game other_seed(p, 999999, consumed.state(), limits);
    CHECK(state_digest(consumed.state()) == state_digest(reset_stream.state()));
    Game a(p, 9321, consumed.planning_snapshot()), b(p, 9321, reset_stream.planning_snapshot()), c(p, 9321, other_seed.planning_snapshot());
    for (int i = 0; i < 3; ++i) {
        a.step({ActionKind::reroll}); b.step({ActionKind::reroll}); c.step({ActionKind::reroll});
        CHECK(state_digest(a.state()) == state_digest(b.state())); CHECK(state_digest(a.state()) == state_digest(c.state()));
        CHECK(observation_json(a) == observation_json(b));
    }
    std::set<std::string> outcomes;
    for (std::uint64_t seed = 1; seed <= 16; ++seed) {
        Game sampled(p, seed, consumed.planning_snapshot()); sampled.step({ActionKind::reroll});
        outcomes.insert(state_digest(sampled.state()));
    }
    CHECK(outcomes.size() > 1);
}
void recovery_destinations_survive_the_snapshot() {
    auto p = profile(); auto s = encounter(10, .05); s.phase = Phase::doors;
    s.doors = {{{Door::monster, true}, {Door::golden}}};
    Game trap(p, 8, s); trap.step({ActionKind::choose_door, 0});
    CHECK(trap.state().phase == Phase::recovery); CHECK(trap.state().room == 11);
    CHECK(trap.recovery_destination() == Phase::doors);
    Game after_trap(p, 47, trap.planning_snapshot());
    CHECK(state_digest(trap.state()) == state_digest(after_trap.state()));
    after_trap.step({ActionKind::wait, 0, .2 * p.recovery_hours}); after_trap.step({ActionKind::reenter});
    CHECK(after_trap.state().phase == Phase::doors); CHECK(after_trap.state().room == 11);
    Game boss(p, 8, encounter(25, .01)); boss.step({ActionKind::fight});
    CHECK(boss.state().phase == Phase::recovery); CHECK(boss.recovery_destination() == Phase::encounter);
    Game retry(p, 47, boss.planning_snapshot());
    retry.step({ActionKind::wait, 0, .2 * p.recovery_hours}); retry.step({ActionKind::reenter});
    CHECK(retry.state().phase == Phase::encounter); CHECK(retry.state().room == 25); CHECK(retry.state().encounter == Encounter::boss);
}
void invalid_snapshots_are_rejected_without_normalizing_them() {
    auto p = profile(); Game game(p, 1, encounter()); const auto original = game.planning_snapshot();
    const auto invalid = [&](auto alter) { auto snapshot = original; alter(snapshot); rejects([&] { Game branch(p, 2, snapshot); }); };
    invalid([](auto& s) { s.state.hp = std::numeric_limits<double>::quiet_NaN(); });
    invalid([](auto& s) { s.state.mushrooms = s.limits.budget + 1; });
    invalid([](auto& s) { s.state.elapsed_hours = s.limits.deadline_hours + 1; });
    invalid([](auto& s) { s.state.blessings.size = 4; });
    invalid([](auto& s) { s.state.phase = static_cast<Phase>(100); });
    invalid([](auto& s) { s.state.trial_depth = 6; });
    invalid([](auto& s) { s.state.offers[0].keys = -1; });
    invalid([](auto& s) { s.state.phase = Phase::recovery; s.recovery_destination = Phase::recovery; });
    invalid([](auto& s) { s.limits.restart_health = "unknown"; });
}
void rollouts_prefer_a_useful_final_boss_heal_and_leave_live_game_untouched() {
    auto p = profile(); p.boss_damage[3] = {.5, .5, .5};
    auto s = encounter(100, .4); s.completed_runs = 3; s.phase = Phase::recovery;
    Limits limits; limits.budget = 10; limits.deadline_hours = .002; limits.repeat_runs = true;
    Game game(p, 7, s, limits), untouched = game;
    const auto before = observation_json(game);
    RolloutOptions options; options.samples = 8; options.seed = 71666;
    const auto result = evaluate_rollouts(game, fight_or_expire(), options);
    CHECK(result.actions.size() == legal_actions(game).size());
    for (const auto& e : result.actions) CHECK(game.legal(e.action));
    CHECK(result.actions[result.reference_index].action.kind == ActionKind::reenter);
    CHECK(result.actions[result.best_index].action.kind == ActionKind::heal_step);
    near(estimate(result, ActionKind::reenter).mean, 0);
    const auto& heal = estimate(result, ActionKind::heal_step);
    near(heal.mean, 1); near(heal.standard_error, 0); near(heal.paired_advantage, 1); near(heal.paired_standard_error, 0);
    CHECK(before == observation_json(game));
    game.step({ActionKind::reenter}); untouched.step({ActionKind::reenter});
    CHECK(!game.legal({ActionKind::heal_step})); CHECK(!game.legal({ActionKind::heal_full}));
    game.step({ActionKind::fight}); untouched.step({ActionKind::fight});
    CHECK(observation_json(game) == observation_json(untouched));
}
void paired_estimates_use_future_completions_and_reproduce_with_fresh_planning_seed() {
    auto p = profile(); p.damage_distribution = "uniform"; p.boss_damage[3] = {.3, .6, .45};
    auto s = encounter(100, .4); s.completed_runs = 3; s.phase = Phase::recovery;
    Limits limits; limits.budget = 10; limits.deadline_hours = .002; limits.repeat_runs = true;
    Game game(p, 7, s, limits), another_live_seed(p, 9999, s, limits);
    RolloutOptions options; options.samples = 64; options.seed = 83211;
    const auto result = evaluate_rollouts(game, fight_or_expire(), options);
    const auto repeated = evaluate_rollouts(another_live_seed, fight_or_expire(), options);
    const auto& fight = estimate(result, ActionKind::reenter); const auto& heal = estimate(result, ActionKind::heal_step);
    CHECK(fight.mean > 0 && fight.mean < 1); CHECK(fight.standard_error > 0);
    near(heal.mean, 1); near(heal.paired_advantage, 1 - fight.mean); near(heal.paired_standard_error, fight.standard_error);
    near(fight.paired_advantage, 0); near(fight.paired_standard_error, 0);
    CHECK(result.best_index == repeated.best_index); CHECK(result.simulated_actions == repeated.simulated_actions);
    for (std::size_t i = 0; i < result.actions.size(); ++i) {
        near(result.actions[i].mean, repeated.actions[i].mean);
        near(result.actions[i].paired_standard_error, repeated.actions[i].paired_standard_error);
    }
}
void legal_nonmenu_reference_actions_and_failure_guards_are_explicit() {
    auto p = profile(); auto s = encounter(10, .1); s.phase = Phase::recovery;
    Limits limits; limits.deadline_hours = 3;
    Game recovery(p, 1, s, limits);
    const Controller wait = [](const Game& game) { return Action{ActionKind::wait, 0, std::min(.777, game.limits().deadline_hours - game.state().elapsed_hours)}; };
    RolloutOptions options; options.samples = 2;
    const auto report = evaluate_rollouts(recovery, wait, options);
    near(report.actions[report.reference_index].action.value, .777);
    CHECK(report.best_index == report.reference_index); CHECK(report.actions.size() > legal_actions(recovery).size());
    options.samples = 1; rejects([&] { evaluate_rollouts(recovery, wait, options); });
    options.samples = 4097; rejects([&] { evaluate_rollouts(recovery, wait, options); });
    options.samples = 2; options.max_total_actions = 1; rejects([&] { evaluate_rollouts(recovery, wait, options); });
    options.max_total_actions = 10000;
    rejects([&] { evaluate_rollouts(recovery, Controller{}, options); });
    rejects([&] { evaluate_rollouts(recovery, [](const Game&) { return Action{ActionKind::fight}; }, options); });
    s = encounter(100, .4); limits.budget = 10; limits.deadline_hours = .002; limits.max_actions = 1;
    Game capped(p, 1, s, limits); rejects([&] { evaluate_rollouts(capped, fight_or_expire(), options); });
    limits.max_actions = 100; Game invalid_continuation(p, 1, s, limits);
    rejects([&] { evaluate_rollouts(invalid_continuation, [](const Game&) { return Action{ActionKind::fight}; }, options); });
    auto terminal = invalid_continuation.planning_snapshot(); terminal.state.elapsed_hours = limits.deadline_hours;
    Game finished(p, 1, terminal); rejects([&] { evaluate_rollouts(finished, fight_or_expire(), options); });
}
void snapshots_accept_full_event_trajectories_and_active_trials() {
    const auto p = profile(); Policy policy; policy.strategy = Strategy::key_stockpile;
    const auto inspect = [&](const Game& live) {
        Game sampled(p, Random::mix(live.state().actions + 848112), live.planning_snapshot());
        CHECK(observation_json(live) == observation_json(sampled));
        const auto original = policy.choose(live), from_visible = policy.choose(sampled);
        CHECK(original.kind == from_visible.kind && original.index == from_visible.index && original.value == from_visible.value);
        CHECK(live.legal(original)); return original;
    };
    for (int budget : {0, 75, 500}) {
        Limits limits; limits.budget = budget; limits.deadline_hours = 168; limits.repeat_runs = true;
        Game live(p, static_cast<std::uint64_t>(99217 + budget), limits);
        while (!live.finished()) live.step(inspect(live));
        Game terminal(p, 145, live.planning_snapshot()); CHECK(terminal.finished());
        CHECK(live.state().completed_runs >= 1); CHECK(live.state().deaths > 0);
        CHECK(live.state().mushrooms <= budget); CHECK(live.state().elapsed_hours <= 168 + 1e-9);
    }
    auto s = encounter(53, .8); s.encounter = Encounter::trial_monster; s.trial_seen = true; s.trial_depth = 2;
    Limits limits; limits.budget = 50; limits.deadline_hours = 12;
    Game trial(p, 876, s, limits);
    while (!trial.finished()) trial.step(inspect(trial));
}
}
int main() {
    try {
        snapshots_preserve_visible_state_and_prices_exactly(); future_samples_do_not_depend_on_live_rng_history();
        recovery_destinations_survive_the_snapshot(); invalid_snapshots_are_rejected_without_normalizing_them();
        rollouts_prefer_a_useful_final_boss_heal_and_leave_live_game_untouched();
        paired_estimates_use_future_completions_and_reproduce_with_fresh_planning_seed();
        legal_nonmenu_reference_actions_and_failure_guards_are_explicit();
        snapshots_accept_full_event_trajectories_and_active_trials();
        std::cout << "8 planning snapshot and rollout tests passed\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
