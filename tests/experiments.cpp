#include "sfld/experiment.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <sstream>
using namespace sfld;
namespace {
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Line " + std::to_string(__LINE__) + ": " #x); } while (false)
void near(double a, double b) { CHECK(std::abs(a - b) < 1e-9); }
Profile profile() { return Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile"); }
State room(Encounter e, int n = 1) { State s; s.phase = Phase::encounter; s.encounter = e; s.room = n; s.turn = n - 1; return s; }
void refills_preserve_active_decisions() {
    auto p = profile(); auto s = room(Encounter::barrel); s.hp = .3;
    s.blessings.give(effect_template(EffectKind::one_hit, true));
    Limits limits; limits.budget = 100;
    Game game(p, 1, s, limits); CHECK(game.legal({ActionKind::heal_step})); game.step({ActionKind::heal_step});
    near(game.state().hp, .5); CHECK(game.state().mushrooms == 10); CHECK(game.state().phase == Phase::encounter);
    CHECK(game.state().room == 1); CHECK(game.state().encounter == Encounter::barrel);
    CHECK(game.state().blessings.slots[0].remaining == 8);
    CHECK(game.full_price() == 24); game.step({ActionKind::heal_full}); near(game.state().hp, 1);
    CHECK(game.state().mushrooms == 34); CHECK(game.state().paid_steps == 2);
    CHECK(!game.legal({ActionKind::heal_step})); CHECK(!game.legal({ActionKind::wait, 0, 1}));
}
void cumulative_water_timer_and_progress() {
    auto p = profile(); auto s = room(Encounter::flooded); s.hp = .1; s.flooded_seconds = 7;
    Limits limits; limits.budget = 100;
    Game game(p, 1, s, limits); game.step({ActionKind::heal_step}); near(game.state().flooded_seconds, 9);
    game.step({ActionKind::interact}); CHECK(game.state().phase == Phase::recovery); CHECK(game.state().room == 1);
    const int spent = game.state().mushrooms; game.step({ActionKind::heal_full}); game.step({ActionKind::reenter});
    near(game.state().flooded_seconds, 0); game.step({ActionKind::skip}); CHECK(game.state().room == 2);
    CHECK(game.state().mushrooms > spent);
    const auto start = StartState::parse("schema=1\nroom=1\nrun_number=1\nphase=encounter\nhp_percent=50\nkeys=0\nencounter=flooded\nflooded_seconds=7\n");
    start.validate(p); near(StartState::parse(start.encode()).state.flooded_seconds, 7);
}
void restart_carries_budget_time_and_unlocks_pool() {
    auto p = profile(); p.boss_damage.fill({.1, .1});
    p.first_run_gem_pool = {Gem::rabbit, Gem::moonstone, Gem::spying, Gem::pendant, Gem::greasy};
    p.later_run_gem_pool = {Gem::gambler, Gem::greed, Gem::hero, Gem::thirsty, Gem::bull};
    auto s = room(Encounter::boss, 100); s.hp = .5; s.keys = 9; s.resources[0] = 8; s.paid_steps = 3;
    s.gems[ix(Gem::rabbit)] = true; s.elapsed_hours = 10; s.mushrooms = 40;
    s.recovery_mushrooms = 39; s.reroll_mushrooms = 1;
    Limits limits; limits.budget = 50; limits.repeat_runs = true;
    Game game(p, 99, s, limits); game.step({ActionKind::fight}); CHECK(game.state().completed_runs == 1);
    const auto time = game.state().elapsed_hours; near(game.state().first_completion_hours, time);
    CHECK(game.legal({ActionKind::restart})); game.step({ActionKind::restart});
    CHECK(game.state().run_number == 2); CHECK(game.state().room == 1); CHECK(game.state().keys == 0);
    CHECK(!game.state().has(Gem::rabbit)); CHECK(game.state().resources[0] == 8); CHECK(game.state().paid_steps == 0);
    CHECK(game.state().mushrooms == 40); CHECK(game.state().recovery_mushrooms == 39); CHECK(game.state().elapsed_hours > time);
    near(game.state().hp, 1); CHECK(!game.legal({ActionKind::restart}));
    auto next = game.state(); next.room = 25; next.phase = Phase::encounter; next.encounter = Encounter::boss;
    Game boss(p, 7, next, limits); boss.step({ActionKind::fight});
    for (auto gem : boss.state().gem_offers) CHECK(std::find(p.later_run_gem_pool.begin(), p.later_run_gem_pool.end(), gem) != p.later_run_gem_pool.end());
    for (const auto* mode : {"carry", "empty"}) {
        limits.restart_health = mode; Game alternative(p, 99, s, limits); alternative.step({ActionKind::fight}); alternative.step({ActionKind::restart});
        near(alternative.state().hp, std::string_view(mode) == "carry" ? .4 : 0);
        CHECK(alternative.state().deaths == 0);
    }
}
void deadline_is_enforced_inside_engine() {
    auto p = profile(); Limits limits; limits.deadline_hours = 1.0 / 3600;
    Game game(p, 1, limits); CHECK(game.finished()); CHECK(legal_actions(game).empty()); CHECK(!game.legal({ActionKind::choose_door, 0}));
    auto s = room(Encounter::monster); s.phase = Phase::recovery; s.hp = 0;
    Game recovery(p, 1, s, limits); auto actions = legal_actions(recovery); CHECK(actions.size() == 1);
    CHECK(!recovery.legal({ActionKind::wait, 0, 2})); recovery.step(actions[0]); CHECK(recovery.finished());
    near(recovery.state().elapsed_hours, limits.deadline_hours);
    limits.budget = 500;
    Game funded(p, 1, s, limits); Policy baseline;
    CHECK(baseline.choose(funded).kind == ActionKind::wait);
    funded.step(baseline.choose(funded)); CHECK(funded.finished()); CHECK(funded.state().mushrooms == 0);
}
void floor_curse_identities_and_damage_scenarios() {
    auto p = profile(); p.monster_damage.fill({.1, .2}); p.combat_curse_chance = 1;
    constexpr EffectKind expected[]{EffectKind::gold_hangover, EffectKind::poison, EffectKind::broken_armor};
    for (int n : {26, 51, 76}) {
        Game g(p, 3, room(Encounter::monster, n)); g.step({ActionKind::fight}); CHECK(g.state().effect(expected[(n - 26) / 25]));
    }
    p.strong_effect = .8;
    auto explorer = room(Encounter::monster, 51); explorer.gems[ix(Gem::explorer)] = true;
    for (unsigned seed = 0; seed < 20; ++seed) {
        Game g(p, seed, explorer); g.step({ActionKind::fight}); CHECK(g.state().effect(EffectKind::poison)->remaining == 10);
    }
    p.combat_curse_chance = 0;
    for (const auto* mode : {"low", "high", "midpoint"}) {
        p.damage_distribution = mode; Game g(p, 2, room(Encounter::monster)); g.step({ActionKind::fight});
        near(g.state().hp, std::string_view(mode) == "low" ? .9 : std::string_view(mode) == "high" ? .8 : .85);
    }
    p.damage_distribution = "triangular";
    for (unsigned seed = 0; seed < 100; ++seed) {
        Game g(p, seed, room(Encounter::monster)); g.step({ActionKind::fight}); CHECK(g.state().hp >= .8 && g.state().hp <= .9);
    }
}
void spider_variants_and_rps_rewards() {
    auto p = profile(); p.spider_success.fill(1);
    for (auto e : {Encounter::spider_legs, Encounter::spider_head, Encounter::spider_full}) {
        Game g(p, 5, room(e)); g.step({ActionKind::interact}); CHECK(g.state().keys == (e == Encounter::spider_legs ? 1 : e == Encounter::spider_head ? 2 : 5));
    }
    p.spider_success.fill(0); p.spider_bite_damage = .1;
    Game g(p, 5, room(Encounter::spider_head)); g.step({ActionKind::interact}); near(g.state().hp, .9); CHECK(g.state().effect(EffectKind::poison));
    bool won = false;
    for (unsigned seed = 0; seed < 20; ++seed) {
        Game rps(p, seed, room(Encounter::rps)); rps.step({ActionKind::rps, 0});
        if (rps.state().epics) { won = true; CHECK(rps.state().blessings.size > 0 || rps.state().hp == 1); }
    }
    CHECK(won);
}
void event_batches_and_learning_respect_limits() {
    const auto p = profile(); Policy baseline; BatchOptions options; options.runs = 30;
    options.limits.budget = 500; options.limits.deadline_hours = 168;
    const Controller choose = [&](const Game& g) { return baseline.choose(g); };
    const auto serial = event_batch(p, choose, options); options.threads = 3;
    const auto parallel = event_batch(p, choose, options);
    for (std::size_t i = 0; i < serial.size(); ++i) {
        CHECK(serial[i].completions == parallel[i].completions); CHECK(serial[i].actions == parallel[i].actions);
        CHECK(serial[i].mushrooms <= 500); CHECK(serial[i].elapsed_hours <= 168 + 1e-9); CHECK(!serial[i].action_limit);
        CHECK(serial[i].mushrooms == serial[i].recovery_mushrooms + serial[i].reroll_mushrooms);
    }
    options.limits.budget = 0; options.runs = 2;
    std::ostringstream log; const auto learned = train_policy(p, options, {1, 4, 2}, log);
    const auto result = event_batch(p, [&](const Game& g) { return learned.choose(g); }, options);
    for (const auto& r : result) CHECK(r.mushrooms == 0 && !r.action_limit);
    options.limits.max_actions = 1; const auto capped = event_batch(p, choose, options);
    CHECK(capped[0].action_limit); CHECK(event_report(p, options, capped, "test").find("\"valid\":false") != std::string::npos);
}
void observations_hide_unrevealed_encounters_and_seed() {
    auto p = profile(); Game g(p, 31415926);
    const auto json = observation_json(g); CHECK(json.find("31415926") == std::string::npos); CHECK(json.find("\"encounter\":") == std::string::npos);
    CHECK(json.find("\"gem_offers\"") == std::string::npos); CHECK(json.find("\"offers\"") == std::string::npos);
    for (const auto& a : legal_actions(g)) CHECK(g.legal(a));
}
}
int main() {
    try {
        refills_preserve_active_decisions(); cumulative_water_timer_and_progress(); restart_carries_budget_time_and_unlocks_pool();
        deadline_is_enforced_inside_engine(); floor_curse_identities_and_damage_scenarios(); spider_variants_and_rps_rewards();
        event_batches_and_learning_respect_limits(); observations_hide_unrevealed_encounters_and_seed();
        std::cout << "8 experiment test groups passed.\n"; return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
