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
void refills_preserve_recovery_destination() {
    auto p = profile(); auto s = room(Encounter::boss, 25); s.hp = .3; s.phase = Phase::recovery;
    s.blessings.give(effect_template(EffectKind::one_hit, true));
    Limits limits; limits.budget = 100;
    Game game(p, 1, s, limits); CHECK(game.legal({ActionKind::heal_step})); game.step({ActionKind::heal_step});
    near(game.state().hp, .5); CHECK(game.state().mushrooms == 10); CHECK(game.state().phase == Phase::recovery);
    CHECK(game.state().room == 25); CHECK(game.state().encounter == Encounter::boss);
    CHECK(game.state().blessings.slots[0].remaining == 8);
    CHECK(game.full_price() == 24); game.step({ActionKind::heal_full}); near(game.state().hp, 1);
    CHECK(game.state().mushrooms == 34); CHECK(game.state().paid_steps == 2);
    CHECK(!game.legal({ActionKind::heal_step}));
    game.step({ActionKind::reenter}); CHECK(game.state().phase == Phase::encounter);
    CHECK(game.state().room == 25); CHECK(game.state().encounter == Encounter::boss);
    CHECK(!game.legal({ActionKind::wait, 0, 1}));
}
void cumulative_water_timer_and_progress() {
    auto p = profile(); auto s = room(Encounter::flooded); s.hp = .1; s.flooded_seconds = 7;
    Limits limits; limits.budget = 100;
    Game game(p, 1, s, limits); CHECK(!game.legal({ActionKind::heal_step}));
    game.step({ActionKind::linger, 0, 3});
    CHECK(game.state().phase == Phase::recovery); CHECK(game.state().room == 2);
    const int spent = game.state().mushrooms; game.step({ActionKind::heal_full}); game.step({ActionKind::reenter});
    near(game.state().flooded_seconds, 0); CHECK(game.state().phase == Phase::doors); CHECK(game.state().room == 2);
    CHECK(game.state().mushrooms > spent);
    const auto start = StartState::parse("schema=1\nroom=1\nrun_number=1\nphase=encounter\nhp_percent=50\nkeys=0\nencounter=flooded\nflooded_seconds=7\n");
    start.validate(p); near(StartState::parse(start.encode()).state.flooded_seconds, 7);
}
void restart_carries_budget_time_and_unlocks_pool() {
    auto p = profile(); p.boss_damage.fill({.1, .1});
    p.exclude_offered_gems = false; // Five-stone pools exercise legacy held-only exclusion.
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
        Game rps(p, seed, room(Encounter::rps)); rps.step({ActionKind::interact});
        if (rps.state().epics) { won = true; CHECK(rps.state().blessings.size > 0 || rps.state().hp == 1); }
    }
    CHECK(won);
}
void reported_damage_means_are_preserved_by_sampling() {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/mushroom-school-2024.profile");
    p.combat_curse_chance = 0; p.key_chance = 0; p.escape_chance = 0;
    for (bool boss : {false, true}) for (int stage = 0; stage < 4; ++stage) {
        const Range range = (boss ? p.boss_damage : p.monster_damage)[static_cast<std::size_t>(stage)];
        const double mode = 3*range.mean - range.low - range.high;
        const double variance = (range.low*range.low+range.high*range.high+mode*mode-
            range.low*range.high-range.low*mode-range.high*mode)/18;
        double total = 0; constexpr unsigned count = 20000;
        for (unsigned seed = 0; seed < count; ++seed) {
            const auto state = room(boss ? Encounter::boss : Encounter::monster, boss ? (stage+1)*25 : stage*25+1);
            Game game(p, seed, state); game.step({ActionKind::fight});
            const double damage = 1-game.state().hp;
            CHECK(damage >= range.low-1e-12 && damage <= range.high+1e-12); total += damage;
            if (!boss && seed < 100) {
                Game fleeing(p, seed, state); fleeing.step({ActionKind::flee}); near(fleeing.state().hp,game.state().hp);
            }
        }
        CHECK(std::abs(total/count-range.mean) < 6*std::sqrt(variance/count));
    }
    const auto invalid = [&](Range r) {
        auto changed = p; changed.monster_damage[0] = r;
        try { changed.validate(); return false; } catch (const std::invalid_argument&) { return true; }
    };
    CHECK(invalid({.1,.2})); CHECK(invalid({.1,.2,.11})); CHECK(invalid({.1,.2,.25}));
    p.monster_damage.fill({.1,.1,.1}); p.validate();
    Game constant(p, 1, room(Encounter::monster)); constant.step({ActionKind::fight}); near(constant.state().hp,.9);
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
    CHECK(json.find("\"suggestions\"") == std::string::npos);
    Game control(p, 31415926); const auto before = state_digest(g.state());
    const auto ui = ui_observation_json(g);
    CHECK(ui.find("\"strategy\":\"key-stockpile\"") != std::string::npos);
    CHECK(ui.find("\"shop_schedule\":\"one_per_stage\"") != std::string::npos);
    CHECK(state_digest(g.state()) == before);
    Policy policy;
    for (int i = 0; i < 200 && !g.finished(); ++i) {
        const auto action = policy.choose(g); ui_observation_json(g);
        g.step(action); control.step(action); CHECK(state_digest(g.state()) == state_digest(control.state()));
    }
    for (const auto& a : legal_actions(g)) CHECK(g.legal(a));
}
void combat_forecasts_match_visible_hit_risk() {
    auto p = profile(); p.damage_distribution = "uniform"; p.escape_chance = .4;
    p.monster_damage.fill({.1, .3, .2}); p.escape_damage.fill({.2, .4, .3});
    p.boss_damage.fill({.3, .5, .4}); p.trial_multipliers = {1, 1.5, 2, 2.5, 3};
    const auto value = [&](const State& s, std::string_view action, std::string_view field) {
        Game game(p, 11, s); const auto json = ui_observation_json(game);
        auto at = json.find("\"combat_forecast\":"); CHECK(at != std::string::npos);
        at = json.find("\"" + std::string(action) + "\":{", at); CHECK(at != std::string::npos);
        at = json.find("\"" + std::string(field) + "\":", at); CHECK(at != std::string::npos);
        return std::stod(json.substr(json.find(':', at) + 1));
    };
    auto s = room(Encounter::monster, 12); s.hp = .2;
    near(value(s,"fight","max_damage"),.3); near(value(s,"fight","survival"),.5);
    near(value(s,"flee","max_damage"),.4); near(value(s,"flee","survival"),.4);
    s.gems[ix(Gem::hero)] = true;
    near(value(s,"fight","max_damage"),.24); near(value(s,"fight","survival"),.75);
    s.gems[ix(Gem::pearl)] = true;
    near(value(s,"fight","max_damage"),.24); near(value(s,"flee","max_damage"),.16);
    near(value(s,"flee","survival"),1);
    s.blessings.give(effect_template(EffectKind::one_hit, true));
    near(value(s,"fight","max_damage"),0); near(value(s,"fight","survival"),1);
    s = room(Encounter::boss,100); s.hp=.4;
    s.gems[ix(Gem::hero)]=s.gems[ix(Gem::rabbit)]=true;
    s.blessings.give(effect_template(EffectKind::one_hit,true));
    near(value(s,"fight","max_damage"),.5); near(value(s,"fight","survival"),.5);
    s = room(Encounter::pig,60); s.hp=.4;
    near(value(s,"fight","max_damage"),.4); near(value(s,"fight","survival"),0);
    s = room(Encounter::trial_monster,54); s.trial_depth=3;
    near(value(s,"fight","max_damage"),.75);
    s = room(Encounter::undead,60);
    near(value(s,"fight","max_damage"),.15);
    s.gems[ix(Gem::rabbit)]=s.gems[ix(Gem::moonstone)]=true;
    near(value(s,"flee","max_damage"),0); near(value(s,"flee","survival"),1);
    s = room(Encounter::monster,12); s.hp=.2; p.damage_distribution="high";
    near(value(s,"fight","survival"),0); p.damage_distribution="low";
    near(value(s,"fight","max_damage"),.1); near(value(s,"fight","survival"),1);
}
void destiny_mushrooms_are_visible_reported_and_survive_restart_without_raising_caps() {
    auto p = profile(); p.destiny_reward_weights = {0, 0, 0, 0, 1, 0};
    p.destiny_reward_amounts[3] = 3;
    p.destiny_weights.fill(0); p.destiny_weights[ix(Encounter::empty)] = 1;
    auto start = StartState::parse("schema=1\nroom=10\nrun_number=1\nphase=doors\nhp_percent=100\nkeys=0\ndoors=destiny,wall\n");
    Game game(p, 5, start); game.step({ActionKind::choose_door, 0});
    CHECK(game.state().mushrooms_found == 3 && game.state().mushrooms == 0);
    const auto observation = observation_json(game);
    CHECK(observation.find("\"mushrooms_found\":3") != std::string::npos);
    CHECK(observation.find("\"mushrooms_left\":0") != std::string::npos);
    auto without_reward = game.state(); without_reward.mushrooms_found = 0;
    CHECK(state_digest(game.state()) != state_digest(without_reward));
    BatchOptions options; options.runs = 1; options.limits.max_actions = 1; options.start = start;
    Policy policy;
    const auto results = run_batch(p, policy, options);
    CHECK(results[0].mushrooms_found == 3);
    CHECK(report_json(p, policy, options, results).find("\"mean_mushrooms_found\": 3") != std::string::npos);
    const auto events = event_batch(p, [&](const Game& g) { return policy.choose(g); }, options);
    CHECK(events[0].mushrooms_found == 3 && events[0].mushrooms == 0);
    CHECK(event_report(p, options, events, "test").find("\"mean_mushrooms_found\":3") != std::string::npos);
    auto s = room(Encounter::boss, 100); s.mushrooms_found = 3;
    Limits limits; limits.repeat_runs = true;
    Game restarting(p, 5, s, limits); restarting.step({ActionKind::fight}); restarting.step({ActionKind::restart});
    CHECK(restarting.state().mushrooms_found == 3); CHECK(restarting.limits().budget == 0);
}
void strategic_events_are_thread_reproducible_and_obey_both_budgets() {
    auto p = profile(); Policy policy; policy.strategy = Strategy::strategic; policy.reroll_limit = 3;
    BatchOptions options; options.runs = 24; options.seed = 61331; options.limits.deadline_hours = 168;
    const Controller choose = [&](const Game& g) {
        const auto action = policy.choose(g); CHECK(g.legal(action)); return action;
    };
    for (auto strategy : {Strategy::strategic, Strategy::key_stockpile}) for (int budget : {0, 500}) {
        policy.strategy = strategy;
        options.limits.budget = budget; options.threads = 1;
        const auto serial = event_batch(p, choose, options); options.threads = 3;
        const auto parallel = event_batch(p, choose, options);
        CHECK(event_report(p, options, serial, name(strategy)) == event_report(p, options, parallel, name(strategy)));
        for (const auto& r : serial) {
            CHECK(!r.action_limit); CHECK(r.mushrooms <= budget);
            CHECK(r.mushrooms == r.recovery_mushrooms + r.reroll_mushrooms);
            CHECK(r.elapsed_hours <= 168 + 1e-9);
        }
    }
}
}
int main() {
    try {
        refills_preserve_recovery_destination(); cumulative_water_timer_and_progress(); restart_carries_budget_time_and_unlocks_pool();
        deadline_is_enforced_inside_engine(); floor_curse_identities_and_damage_scenarios(); spider_variants_and_rps_rewards();
        event_batches_and_learning_respect_limits(); observations_hide_unrevealed_encounters_and_seed();
        combat_forecasts_match_visible_hit_risk();
        destiny_mushrooms_are_visible_reported_and_survive_restart_without_raising_caps();
        strategic_events_are_thread_reproducible_and_obey_both_budgets();
        reported_damage_means_are_preserved_by_sampling();
        std::cout << "12 experiment test groups passed.\n"; return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
