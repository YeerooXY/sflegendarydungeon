#include "sfld/damage_risk.hpp"
#include "sfld/policy.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>

using namespace sfld;
namespace {
void check(bool value, int line) {
    if (!value) throw std::runtime_error("Survival policy test failed at line " + std::to_string(line));
}
#define CHECK(value) check(bool(value), __LINE__)
void near(double actual, double expected, double tolerance = 1e-10) {
    if (std::abs(actual - expected) > tolerance)
        throw std::runtime_error("Expected " + std::to_string(expected) + ", got " + std::to_string(actual));
}
Profile profile() {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    p.damage_distribution = "uniform";
    p.monster_damage.fill({.0998, .1590, .128});
    p.escape_damage = p.monster_damage;
    p.escape_chance = .4; p.key_chance = .7; p.combat_curse_chance = 0;
    return p;
}
State encounter(double hp = 1, Encounter kind = Encounter::monster) {
    State s; s.phase = Phase::encounter; s.encounter = kind;
    s.room = 10; s.turn = 9; s.hp = hp; return s;
}
Policy policy(bool aware = true) {
    Policy p; p.strategy = Strategy::key_stockpile; p.survival_aware = aware; return p;
}
void distribution_probabilities_and_truncated_moments() {
    const DamageRisk uniform{{.1, .3, .18}, "uniform"};
    near(uniform.below(.1), 0); near(uniform.below(.2), .5); near(uniform.below(.3), 1);
    near(uniform.mean(), .2); near(uniform.moment_below(.2), .075);
    const DamageRisk triangle{{.1, .3, .18}, "triangular"};
    near(triangle.below(.15), .125); near(triangle.below(.25), .875);
    near(triangle.mean(), .2); near(triangle.moment_below(.2), 1.0 / 12);
    const DamageRisk fitted{{.1, .3, .18}, "triangular_mean"};
    near(fitted.mean(), .18); near(fitted.below(.14), .2);
    near(fitted.below(.2), .6875); near(fitted.moment_below(1), .18);
    // End-point modes must work without a zero denominator.
    const DamageRisk descending{{0, 1, 1.0 / 3}, "triangular_mean"};
    const DamageRisk ascending{{0, 1, 2.0 / 3}, "triangular_mean"};
    near(descending.below(.5), .75); near(descending.moment_below(1), 1.0 / 3);
    near(ascending.below(.5), .25); near(ascending.moment_below(1), 2.0 / 3);
    for (const auto shape : {"low", "midpoint", "high"}) {
        const DamageRisk law{{.1, .3, .18}, shape};
        const double value = std::string_view(shape) == "low" ? .1 : std::string_view(shape) == "high" ? .3 : .2;
        near(law.below(value), 0); near(law.below(value + 1e-9), 1);
        near(law.moment_below(value), 0); near(law.moment_below(1), value);
        near(law.after_room(value).room_survival, 0);
    }
}
void recovery_precedes_poison_and_health_is_capped() {
    const DamageRisk hit{{.1, .1, .1}, "uniform"};
    auto risk = hit.after_room(.15, .05, .9);
    near(risk.hit_survival, 1); near(risk.room_survival, 1); near(risk.expected_hp, .9);
    risk = hit.after_room(.2, .05, .1);
    near(risk.room_survival, 1); near(risk.expected_hp, .15);
    risk = hit.after_room(.8, .05, .2, .6);
    near(risk.expected_hp, .95);
    // The pig's heal occurs only after the incoming hit has been survived.
    risk = hit.after_room(.1, 0, 0, .6);
    near(risk.room_survival, 0); near(risk.expected_hp, 0);
    const DamageRisk uniform{{0, .2, .1}, "uniform"};
    risk = uniform.after_room(.15, .05, .1);
    near(risk.hit_survival, .75); near(risk.room_survival, .75);
    near(risk.expected_hp, .09375);
    // The adviser must not treat floating-point residue as surviving health.
    risk = hit.after_room(.1 + 1e-16);
    near(risk.hit_survival, 0); near(risk.room_survival, 0);
    const DamageRisk no_hit{{0, 0, 0}, "uniform"};
    risk = no_hit.after_room(.05 + 2e-17, .05);
    near(risk.room_survival, 0); near(risk.expected_hp, 0);
    risk = no_hit.after_room(.06, .05);
    near(risk.room_survival, 1); near(risk.expected_hp, .01);
}
void near_mean_does_not_mean_a_safe_fight() {
    auto p = profile(); const auto s = encounter(.131); Game game(p, 19, s);
    CHECK(policy(false).choose(game).kind == ActionKind::fight);
    CHECK(policy().choose(game).kind == ActionKind::flee);
    p.damage_distribution = "low"; Game low(p, 19, s);
    CHECK(policy().choose(low).kind == ActionKind::fight);
    p.damage_distribution = "high"; Game high(p, 19, s);
    CHECK(policy().choose(high).kind == ActionKind::flee);
    p.damage_distribution = "midpoint"; Game middle(p, 19, s);
    CHECK(policy().choose(middle).kind == ActionKind::fight);
    p.damage_distribution = "triangular_mean"; Game fitted(p, 19, s);
    CHECK(policy().choose(fitted).kind == ActionKind::flee);
}
void encounter_modifiers_and_one_hit_change_the_choice() {
    auto p = profile(); p.monster_damage.fill({.2, .2, .2}); p.escape_damage = p.monster_damage;
    auto s = encounter(.12); Game ordinary(p, 19, s);
    CHECK(policy().choose(ordinary).kind == ActionKind::flee);
    for (const auto kind : {Encounter::undead, Encounter::tube, Encounter::beta}) {
        s.encounter = kind; Game half(p, 19, s);
        CHECK(policy().choose(half).kind == ActionKind::fight);
    }
    s = encounter(.01); s.blessings.give(effect_template(EffectKind::one_hit, false));
    Game one_hit(p, 19, s); CHECK(policy().choose(one_hit).kind == ActionKind::fight);
    s = encounter(.12); s.gems[ix(Gem::hero)] = true; s.gems[ix(Gem::bull)] = true; s.gems[ix(Gem::deceit)] = true;
    Game protected_fight(p, 19, s); CHECK(policy().choose(protected_fight).kind == ActionKind::fight);
    s.curses.give(effect_template(EffectKind::broken_armor, false));
    Game broken_armor(p, 19, s); CHECK(policy().choose(broken_armor).kind == ActionKind::flee);
    p.monster_damage.fill({.1, .1, .1}); p.escape_damage = p.monster_damage;
    p.trial_multipliers.fill(1); p.trial_multipliers[4] = 3;
    s = encounter(.21, Encounter::trial_monster); s.trial_depth = 0;
    Game early_trial(p, 19, s); CHECK(policy().choose(early_trial).kind == ActionKind::fight);
    s.trial_depth = 4; Game deep_trial(p, 19, s); CHECK(policy().choose(deep_trial).kind == ActionKind::flee);
    s = encounter(.2, Encounter::shakes); Game fixed(p, 19, s);
    CHECK(policy().choose(fixed).kind == ActionKind::flee);
    s = encounter(.5, Encounter::pig); Game pig(p, 19, s);
    CHECK(policy().choose(pig).kind == ActionKind::fight);
}
void poison_risk_changes_fighting_without_mutating_game() {
    auto p = profile(); p.monster_damage.fill({.1, .1, .1}); p.escape_damage = p.monster_damage;
    auto s = encounter(.14); Game safe(p, 71, s);
    CHECK(policy().choose(safe).kind == ActionKind::fight);
    s.curses.give(effect_template(EffectKind::poison, false));
    Game poisoned(p, 71, s);
    CHECK(policy().choose(poisoned).kind == ActionKind::flee);
    s.blessings.give(effect_template(EffectKind::recovery, true));
    Game risky(p, 71, s), untouched(p, 71, s);
    for (int i = 0; i < 20; ++i) CHECK(policy().choose(risky).kind == ActionKind::fight);
    near(risky.state().hp, s.hp); CHECK(risky.state().actions == 0);
    risky.step({ActionKind::flee}); untouched.step({ActionKind::flee});
    near(risky.state().hp, untouched.state().hp);
    CHECK(risky.state().deaths == untouched.state().deaths);
    CHECK(risky.state().doors[0].kind == untouched.state().doors[0].kind);
    CHECK(risky.state().doors[1].kind == untouched.state().doors[1].kind);
}
void analytical_exit_risk_matches_engine_sampled_damage() {
    auto p = profile(); p.monster_damage.fill({.1, .3, .18}); p.escape_damage = p.monster_damage;
    p.key_chance = 0;
    auto s = encounter(.24); s.curses.give(effect_template(EffectKind::poison, false));
    s.blessings.give(effect_template(EffectKind::recovery, false));
    for (const auto shape : {"uniform", "triangular", "triangular_mean", "low", "high", "midpoint"}) {
        p.damage_distribution = shape;
        const auto risk = DamageRisk{p.monster_damage[0], shape}.after_room(s.hp, .05, .1);
        double alive = 0, hp = 0;
        constexpr int count = 5000;
        for (int seed = 0; seed < count; ++seed) {
            Game game(p, static_cast<std::uint64_t>(seed), s);
            game.step({ActionKind::fight});
            alive += game.state().deaths == 0 ? 1 : 0;
            hp += game.state().hp;
        }
        near(alive / count, risk.room_survival, .025);
        near(hp / count, risk.expected_hp, .005);
    }
}
void useful_blessings_save_keys_without_relying_on_healing_after_a_lethal_hit() {
    auto p = profile();
    p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    const auto adviser = policy(false);
    for (const int room : {10, 35, 60, 85}) for (const int keys : {2, 10, 30}) {
        auto s = encounter(.6); s.room = room; s.turn = room - 1; s.keys = keys;
        s.blessings.give(effect_template(EffectKind::escape, true));
        Game escaped(p, 11, s);
        CHECK(adviser.choose(escaped).kind == ActionKind::flee);
        s.phase = Phase::doors;
        for (const auto door : {Door::locked, Door::double_locked}) {
            s.doors = {{{door}, {Door::monster}}};
            Game choice(p, 11, s); CHECK(adviser.choose(choice).index == 1);
        }
        s.blessings = {}; s.blessings.give(effect_template(EffectKind::recovery, true));
        for (const auto door : {Door::locked, Door::double_locked, Door::mystery}) {
            s.doors = {{{door}, {Door::monster}}};
            Game choice(p, 11, s); CHECK(adviser.choose(choice).index == 1);
        }
        s.phase = Phase::encounter; Game covered(p, 11, s);
        CHECK(adviser.choose(covered).kind == ActionKind::fight);
        s.hp = .09; Game lethal(p, 11, s);
        CHECK(adviser.choose(lethal).kind == ActionKind::flee);
        s.phase = Phase::doors; s.doors = {{{Door::locked}, {Door::monster}}};
        Game low(p, 11, s); CHECK(adviser.choose(low).index == 0);
    }
    auto s = encounter(.05); s.keys = 30; s.phase = Phase::doors;
    s.blessings.give(effect_template(EffectKind::escape, true));
    s.doors = {{{Door::monster, true}, {Door::locked}}};
    Game trapped(p, 11, s); CHECK(adviser.choose(trapped).index == 1);
    s.doors[0].trap = false;
    s.curses.give(effect_template(EffectKind::clumsy, true));
    Game clumsy(p, 11, s); CHECK(adviser.choose(clumsy).index == 1);
    s.curses = {}; s.blessings = {};
    s.blessings.give(effect_template(EffectKind::recovery, true));
    s.doors = {{{Door::monster, true}, {Door::mystery}}};
    Game lethal_trap(p, 11, s); CHECK(adviser.choose(lethal_trap).index == 1);
}
void stockpile_keeps_early_keys_but_spends_them_to_survive() {
    auto p = profile(); p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    auto s = encounter(.9); s.phase = Phase::doors;
    s.doors = {{{Door::locked}, {Door::monster}}};
    for (int keys : {4, 7, 9}) {
        s.keys = keys; Game early(p, 31, s);
        CHECK(policy().choose(early).index == 1);
        s.hp = .05; Game unsafe(p, 31, s);
        CHECK(policy().choose(unsafe).index == 0);
        s.hp = .9;
    }
    s.hp = .9; s.keys = 3; s.room = 35; s.turn = 34;
    Game before_shop(p, 31, s); CHECK(policy().choose(before_shop).index == 1);
    s.hp = .05; Game urgent(p, 31, s); CHECK(policy().choose(urgent).index == 0);
    s.hp = .9; s.room = 10; s.turn = 9;
    s.doors = {{{Door::mystery, true}, {Door::monster}}};
    Game trapped_mystery(p, 31, s); CHECK(policy().choose(trapped_mystery).index == 1);
    s.room = 35; s.turn = 34; s.phase = Phase::shop; s.keys = 4;
    s.offers = {{{effect_template(EffectKind::one_hit, true), 4}, {effect_template(EffectKind::raider, true), 2}}};
    Game useful_purchase(p, 31, s);
    CHECK(policy().choose(useful_purchase).kind == ActionKind::buy);
    CHECK(policy().choose(useful_purchase).index == 0);
}
void baseline_modifiers_apply_to_combat_and_doors() {
    auto p = profile(); Policy baseline;
    auto s = encounter(.131); Game unsafe(p, 51, s);
    CHECK(baseline.choose(unsafe).kind == ActionKind::fight);
    baseline.survival_aware = true;
    CHECK(baseline.choose(unsafe).kind == ActionKind::flee);

    p.locked_weights.fill(0); p.locked_weights[ix(Encounter::empty)] = 1;
    s.phase = Phase::doors; s.keys = 1;
    s.doors = {{{Door::monster}, {Door::locked}}};
    Game unsafe_choice(p, 51, s);
    CHECK(baseline.choose(unsafe_choice).index == 1);
    baseline.survival_aware = false;
    CHECK(baseline.choose(unsafe_choice).index == 0);

    s = encounter(1); s.keys = 10; Game enough_for_legacy(p, 52, s);
    CHECK(baseline.choose(enough_for_legacy).kind == ActionKind::flee);
    baseline.early_key_target = 12;
    CHECK(baseline.choose(enough_for_legacy).kind == ActionKind::fight);
    s.phase = Phase::doors; s.doors = {{{Door::monster}, {Door::locked}}};
    Game save_keys(p, 52, s); CHECK(baseline.choose(save_keys).index == 0);
    baseline.early_key_target = 0;
    CHECK(baseline.choose(save_keys).index == 1);
}
void safe_soldier_search_and_final_approach_recovery() {
    auto p = profile(); p.skeleton_wakes = 1;
    auto s = encounter(.21, Encounter::skeleton);
    s.gems[ix(Gem::rabbit)] = true; s.gems[ix(Gem::moonstone)] = true;
    Game guaranteed(p, 11, s); CHECK(policy().choose(guaranteed).kind == ActionKind::interact);
    s.gems.fill(false); s.blessings.give(effect_template(EffectKind::escape, false));
    Game blessed(p, 11, s); CHECK(policy().choose(blessed).kind == ActionKind::interact);
    s = encounter(0); s.phase = Phase::recovery;
    auto adviser = policy(); adviser.revive_hp = .21;
    Game ordinary(p, 11, s); auto action = adviser.choose(ordinary);
    CHECK(action.kind == ActionKind::wait); near(action.value, .21 * p.recovery_hours);
    s.room = 98; s.turn = 97;
    Game approach(p, 11, s); action = adviser.choose(approach);
    CHECK(action.kind == ActionKind::wait);
    const double target = std::min(1.0, p.boss_damage[3].high + 2 * p.monster_damage[3].high + .001);
    near(action.value, target * p.recovery_hours);
    approach.step(action); CHECK(adviser.choose(approach).kind == ActionKind::reenter);
}
void explicit_barrel_policy_fallback_preserves_controls_without_recursion() {
    auto p = profile(); auto s = encounter(.1, Encounter::barrel); Game barrel(p, 61, s);
    for (auto strategy : {Strategy::baseline, Strategy::strategic, Strategy::key_stockpile}) {
        Policy candidate; candidate.strategy = strategy;
        candidate.survival_aware = true; candidate.early_key_target = 12;
        candidate.barrels = BarrelPolicy::always;
        CHECK(candidate.choose(barrel).kind == ActionKind::interact);
        CHECK(candidate.choose_strategic(barrel).kind == ActionKind::interact);
        candidate.barrels = BarrelPolicy::skip;
        CHECK(candidate.choose(barrel).kind == ActionKind::skip);
        CHECK(candidate.choose_strategic(barrel).kind == ActionKind::skip);
        candidate.barrels = BarrelPolicy::low_hp; candidate.barrel_hp = .15;
        CHECK(candidate.choose(barrel).kind == ActionKind::interact);
        candidate.barrel_hp = .05;
        CHECK(candidate.choose(barrel).kind == ActionKind::skip);
    }
}
}
int main() {
    try {
        distribution_probabilities_and_truncated_moments();
        recovery_precedes_poison_and_health_is_capped();
        near_mean_does_not_mean_a_safe_fight();
        encounter_modifiers_and_one_hit_change_the_choice();
        poison_risk_changes_fighting_without_mutating_game();
        analytical_exit_risk_matches_engine_sampled_damage();
        useful_blessings_save_keys_without_relying_on_healing_after_a_lethal_hit();
        stockpile_keeps_early_keys_but_spends_them_to_survive();
        baseline_modifiers_apply_to_combat_and_doors();
        safe_soldier_search_and_final_approach_recovery();
        explicit_barrel_policy_fallback_preserves_controls_without_recursion();
        std::cout << "Survival policy tests passed\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
