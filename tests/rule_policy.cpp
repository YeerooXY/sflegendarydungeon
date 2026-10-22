#include "sfld/rule_policy.hpp"
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

using namespace sfld;
namespace {
void check(bool condition, int line) {
    if (!condition) throw std::runtime_error("Human rule test failed at line " + std::to_string(line));
}
#define CHECK(value) check(bool(value), __LINE__)
template<class F> void rejects(F action) {
    bool failed = false;
    try { action(); } catch (const std::exception&) { failed = true; }
    CHECK(failed);
}
void near(double actual, double expected) { CHECK(std::abs(actual - expected) < 1e-9); }
Profile profile() { return Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile"); }
State encounter(Encounter kind = Encounter::monster, int room = 10, double hp = 1) {
    State s; s.phase = Phase::encounter; s.encounter = kind; s.room = room; s.turn = room - 1; s.hp = hp;
    return s;
}
struct Temp {
    std::filesystem::path path = std::filesystem::temp_directory_path() / ("sfld-human-rule-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".rules");
    ~Temp() { std::error_code ignored; std::filesystem::remove(path, ignored); }
};
void serialization_and_validation() {
    RulePolicy rules; rules.id = "roundtrip"; rules.fight_keys = {9, 4, 2, 0}; rules.door_reserve = {4, 4, 3, 1};
    rules.gem_rules.scores[ix(Gem::rabbit)] = 12.5; rules.shop_mode = 2; rules.shop_reserve_fraction = .25;
    rules.shop_purchase_floor = 80; rules.guaranteed_escape_door_preference = true; rules.paid_refill_mode = 3;
    rules.fight_when_cheaper = true;
    rules.login_interval_hours = 6; rules.validate();
    Temp file; rules.save(file.path.string());
    CHECK(RulePolicy::load(file.path.string()).encode() == rules.encode());
    const auto invalid = [&](const std::string& text) {
        { std::ofstream out(file.path); out << text; }
        rejects([&] { RulePolicy::load(file.path.string()); });
    };
    invalid("schema=1\nshop_mode=3\n"); invalid("schema=1\nrevive_hp=.1\n");
    invalid("schema=1\nfight_keys=1,2,3\n"); invalid("schema=1\nfight_keys=1,2,3,4,\n");
    invalid("schema=1\nshop_allowance=2.5\n"); invalid("schema=1\nmax_fight_death_risk=nan\n");
    invalid("schema=1\nshop_allowance=5\nshop_allowance=2\n"); invalid("schema=1\nunknown=1\n");
    invalid("id=missing-schema\n"); invalid("schema=2\n");
    invalid("schema=1\nshop_purchase_floor=151\n"); invalid("schema=1\nshop_purchase_floor=-1\n");
    invalid("schema=1\nshop_purchase_floor=nan\n"); invalid("schema=1\npaid_refill_mode=4\n");
    invalid("schema=1\nguaranteed_escape_door_preference=2\n");
    invalid("schema=1\nfight_when_cheaper=2\n");
    { std::ofstream out(file.path); out << "schema=1\nid=old-file\n"; }
    const auto old = RulePolicy::load(file.path.string());
    CHECK(old.shop_purchase_floor == 0); CHECK(!old.guaranteed_escape_door_preference); CHECK(old.paid_refill_mode == 1);
    CHECK(!old.fight_when_cheaper);
}
void keys_health_and_one_hit_are_explicit_combat_rules() {
    auto p = profile(); p.damage_distribution = "uniform";
    p.monster_damage.fill({.1, .2, .15}); p.escape_damage = p.monster_damage;
    p.escape_chance = .4; RulePolicy rules;
    auto s = encounter(); Game farming(p, 1, s); CHECK(rules.choose(farming).kind == ActionKind::fight);
    s.keys = rules.fight_keys[0]; Game enough(p, 1, s); CHECK(rules.choose(enough).kind == ActionKind::flee);
    s.keys = 0; s.hp = .151; rules.min_fight_hp = 0;
    Game risky(p, 1, s); CHECK(rules.choose(risky).kind == ActionKind::flee);
    rules.max_fight_death_risk = 1; CHECK(rules.choose(risky).kind == ActionKind::fight);
    rules.max_fight_death_risk = .05; s.hp = .01; s.blessings.give(effect_template(EffectKind::one_hit, false));
    Game free_hit(p, 1, s); CHECK(rules.choose(free_hit).kind == ActionKind::fight);
    s = encounter(); s.gems[ix(Gem::rabbit)] = true;
    Game rabbit(p, 1, s); CHECK(rules.choose(rabbit).kind == ActionKind::flee);
}
void free_mystery_and_key_reserves_control_doors() {
    auto p = profile(); RulePolicy rules; auto s = encounter(); s.phase = Phase::doors;
    s.doors = {{{Door::monster}, {Door::mystery}}};
    Game safe(p, 1, s); CHECK(rules.choose(safe).index == 1);
    s.blessings.give(effect_template(EffectKind::one_hit, false));
    Game one_hit(p, 1, s); CHECK(rules.choose(one_hit).index == 0);
    s.blessings.clear(); s.keys = 4; s.doors = {{{Door::locked}, {Door::mystery}}};
    Game scarce(p, 1, s); CHECK(rules.choose(scarce).index == 1);
    s.keys = 10; Game abundant(p, 1, s); CHECK(rules.choose(abundant).index == 0);
    rules.door_reserve.fill(0); s.room = 60; s.turn = 59; s.keys = 4;
    Game before_shop(p, 1, s, {100}); CHECK(rules.choose(before_shop).index == 1);
    s.shops_seen[2] = true; Game after_shop(p, 1, s, {100}); CHECK(rules.choose(after_shop).index == 0);
}
void health_saving_fights_adapt_to_bull_without_ignoring_survival() {
    auto p = profile(); p.damage_distribution = "uniform";
    p.monster_damage.fill({.1, .1, .1}); p.escape_damage = p.monster_damage; p.escape_chance = .4;
    RulePolicy rules; rules.fight_keys.fill(0); rules.fight_escape_cutoff = 0;
    auto s = encounter(); s.keys = 20; s.gems[ix(Gem::bull)] = true;
    Game bull(p, 1, s); CHECK(rules.choose(bull).kind == ActionKind::flee);
    rules.fight_when_cheaper = true;
    // Bull reduces both hits to 8%; escaping still saves 10% of those hits.
    CHECK(rules.choose(bull).kind == ActionKind::flee);
    s.gems[ix(Gem::hick)] = true; Game both(p, 1, s);
    // The escape-only Hick penalty makes 90% * 11% worse than fighting for 8%.
    CHECK(rules.choose(both).kind == ActionKind::fight);
    s.gems[ix(Gem::bull)] = false; Game hick(p, 1, s);
    CHECK(rules.choose(hick).kind == ActionKind::flee); // .6 * .13 < .1.
    s.gems[ix(Gem::hick)] = false; Game ordinary(p, 1, s);
    CHECK(rules.choose(ordinary).kind == ActionKind::flee);
    s.gems[ix(Gem::rabbit)] = s.gems[ix(Gem::moonstone)] = true;
    Game guaranteed(p, 1, s); CHECK(rules.choose(guaranteed).kind == ActionKind::flee);
    s.blessings.give(effect_template(EffectKind::one_hit, false));
    Game one_hit(p, 1, s); CHECK(rules.choose(one_hit).kind == ActionKind::fight);
    // Compare actual end-room HP, not raw damage: both paths heal back to full.
    s = encounter(); s.keys = 20; s.gems[ix(Gem::bull)] = s.gems[ix(Gem::hick)] = true;
    s.blessings.give(effect_template(EffectKind::recovery, true));
    Game capped_healing(p, 1, s); CHECK(rules.choose(capped_healing).kind == ActionKind::flee);
    s.blessings.clear(); s.hp = .15;
    p.monster_damage.fill({.1, .2, .15}); p.escape_damage = p.monster_damage;
    rules.min_fight_hp = 0; Game risky(p, 1, s);
    CHECK(rules.choose(risky).kind == ActionKind::flee); // 12.5% fight death risk exceeds 5%.
    rules.max_fight_death_risk = .2;
    CHECK(rules.choose(risky).kind == ActionKind::fight);
    rules.min_fight_hp = .2;
    CHECK(rules.choose(risky).kind == ActionKind::flee); // Minimum HP still applies.
}
void traps_are_compared_with_the_other_door() {
    auto p = profile(); p.monster_damage.fill({.4, .4, .4}); p.escape_damage = p.monster_damage; p.escape_chance = .4;
    RulePolicy rules; rules.fight_keys.fill(0);
    auto s = encounter(Encounter::empty, 10, .5); s.phase = Phase::doors;
    s.doors = {{{Door::monster}, {Door::mystery, true}}};
    Game smaller_hit(p, 1, s); CHECK(rules.choose(smaller_hit).index == 1);
    s.hp = .08; Game lethal_trap(p, 1, s); CHECK(rules.choose(lethal_trap).index == 0);
    s.hp = .5; s.gems[ix(Gem::gambler)] = true;
    Game curse_only(p, 1, s); CHECK(rules.choose(curse_only).index == 1);
}
void guaranteed_escape_avoids_costly_doors_without_overriding_safe_rewards() {
    auto p = profile(); RulePolicy rules; rules.fight_keys.fill(0); rules.door_reserve.fill(0);
    auto s = encounter(); s.phase = Phase::doors; s.keys = 20;
    s.gems[ix(Gem::rabbit)] = s.gems[ix(Gem::moonstone)] = true;
    for (const auto alternative : {Door::locked, Door::double_locked, Door::epic, Door::cursed, Door::sacrifice, Door::destiny}) {
        s.doors = {{{Door::monster}, {alternative}}};
        Game game(p, 1, s);
        rules.guaranteed_escape_door_preference = false; rules.door_mode = 0;
        CHECK(rules.choose(game).index == 1);
        rules.guaranteed_escape_door_preference = true;
        for (int mode = 0; mode < 3; ++mode) { rules.door_mode = mode; CHECK(rules.choose(game).index == 0); }
    }
    for (const auto alternative : {Door::mystery, Door::golden}) {
        s.doors = {{{Door::monster}, {alternative}}};
        Game reward(p, 1, s); CHECK(rules.choose(reward).index == 1);
    }
    s.blessings.give(effect_template(EffectKind::one_hit, false));
    Game one_hit(p, 1, s); CHECK(rules.choose(one_hit).index == 0);
    s.blessings.clear(); rules.door_mode = 0; s.doors = {{{Door::monster, true}, {Door::cursed}}};
    Game trapped(p, 1, s); CHECK(rules.choose(trapped).index == 1);
    s.doors[0].trap = false; s.gems[ix(Gem::moonstone)] = false;
    Game imperfect(p, 1, s); CHECK(rules.choose(imperfect).index == 1);
    s.blessings.give(effect_template(EffectKind::escape, false));
    Game temporary_escape(p, 1, s); CHECK(rules.choose(temporary_escape).index == 0);
    s.curses.give(effect_template(EffectKind::clumsy, false));
    Game reduced_escape(p, 1, s); CHECK(rules.choose(reduced_escape).index == 1);
}
void shop_credit_gate_carryover_and_ranked_purchases() {
    auto p = profile(); p.shop_blessing_weights.fill(0);
    p.shop_blessing_weights[ix(EffectKind::raider)] = 1; p.shop_blessing_weights[ix(EffectKind::elixir)] = 1;
    auto s = encounter(Encounter::empty, 35); s.phase = Phase::shop; s.keys = 4;
    s.reroll_shop_visits = 2; s.reroll_bank_used = 2;
    s.offers = {{{effect_template(EffectKind::raider, false), 1}, {effect_template(EffectKind::elixir, false), 1}}};
    RulePolicy rules; Game rolling(p, 3, s, {100});
    int refreshes = 0;
    while (rules.choose(rolling).kind == ActionKind::reroll) {
        rolling.step(rules.choose(rolling)); ++refreshes; CHECK(refreshes <= 8);
    }
    CHECK(refreshes == 8); CHECK(rules.choose(rolling).kind == ActionKind::skip);
    s.keys = 3; Game no_keys(p, 3, s, {100}); CHECK(rules.choose(no_keys).kind != ActionKind::reroll);
    s.keys = 4; s.offers[0] = {effect_template(EffectKind::one_hit, true), 4};
    Game strong(p, 3, s, {100}); CHECK(rules.choose(strong).kind == ActionKind::buy);
    CHECK(rules.choose(strong).index == 0);
    s.room = 5; s.turn = 4; s.keys = 0; s.reroll_shop_visits = 0; s.reroll_bank_used = 0;
    s.offers = {{{effect_template(EffectKind::one_hit, false), 0}, {effect_template(EffectKind::escape, false), 0}}};
    Game opening(p, 3, s, {100}); CHECK(rules.choose(opening).kind == ActionKind::buy);
    CHECK(!opening.legal({ActionKind::reroll}));
}
void recovery_respects_cadence_bosses_and_mushroom_allocation() {
    auto p = profile(); RulePolicy rules; auto s = encounter(); s.phase = Phase::recovery; s.hp = 0;
    Game natural(p, 1, s); auto action = rules.choose(natural);
    CHECK(action.kind == ActionKind::wait); near(action.value, .2 * p.recovery_hours);
    rules.login_interval_hours = 6; action = rules.choose(natural); near(action.value, 6);
    rules.login_interval_hours = 0; Game funded(p, 1, s, {100});
    CHECK(rules.choose(funded).kind == ActionKind::heal_step);
    rules.shop_reserve_fraction = 1; CHECK(rules.choose(funded).kind == ActionKind::wait);
    rules.shop_reserve_fraction = 0; rules.paid_refill_mode = 0; CHECK(rules.choose(funded).kind == ActionKind::wait);
    s.room = 100; s.hp = .2; s.encounter = Encounter::boss; Game boss(p, 1, s);
    CHECK(rules.choose(boss).kind == ActionKind::wait);
    rules.paid_refill_mode = 1; s.room = 10; s.hp = .2; s.gems[ix(Gem::greasy)] = true;
    Game greasy(p, 1, s); CHECK(rules.choose(greasy).kind == ActionKind::reenter);
}
void shop_purchase_floor_is_a_nonrelaxing_paid_offer_gate() {
    auto p = profile(); RulePolicy rules;
    auto s = encounter(Encounter::empty, 35); s.phase = Phase::shop; s.keys = 8;
    s.offers = {{{effect_template(EffectKind::disarm, false), 2}, {effect_template(EffectKind::raider, false), 1}}};
    Game weak(p, 1, s); CHECK(rules.choose(weak).kind == ActionKind::buy);
    rules.shop_purchase_floor = 60; CHECK(rules.choose(weak).kind == ActionKind::skip);
    rules.shop_purchase_floor = 59; CHECK(rules.choose(weak).kind == ActionKind::buy);
    rules.shop_purchase_floor = 80; s.offers[0] = {effect_template(EffectKind::elixir, false), 1}; s.hp = .2;
    Game useful_potion(p, 1, s); CHECK(rules.choose(useful_potion).kind == ActionKind::buy);
    s.hp = .9; Game tiny_potion(p, 1, s); CHECK(rules.choose(tiny_potion).kind == ActionKind::skip);
    s.hp = .2; rules.shop_purchase_floor = 100;
    Game no_exception(p, 1, s); CHECK(rules.choose(no_exception).kind == ActionKind::skip);
    s.gems[ix(Gem::time_traveler)] = true;
    Game full_potion(p, 1, s); CHECK(rules.choose(full_potion).kind == ActionKind::buy);
    // A declining reroll target must not quietly discard the purchase floor.
    s.gems.fill(false); s.hp = 1; s.reroll_shop_visits = 1; s.reroll_bank_used = 4; s.shop_rerolls = 4;
    s.offers[0] = {effect_template(EffectKind::disarm, false), 2};
    p.shop_blessing_weights.fill(0); p.shop_blessing_weights[ix(EffectKind::disarm)] = 1;
    p.shop_blessing_weights[ix(EffectKind::raider)] = 1;
    Game last_refresh(p, 1, s, {10}); CHECK(rules.choose(last_refresh).kind == ActionKind::reroll);
    last_refresh.step(rules.choose(last_refresh)); CHECK(rules.choose(last_refresh).kind == ActionKind::skip);
    rules.shop_purchase_floor = 150; s.room = 5; s.turn = 4; s.keys = 0;
    s.reroll_shop_visits = s.reroll_bank_used = s.shop_rerolls = 0;
    s.offers = {{{effect_template(EffectKind::one_hit, false), 0}, {effect_template(EffectKind::escape, false), 0}}};
    Game opening(p, 1, s); CHECK(rules.choose(opening).kind == ActionKind::buy); CHECK(rules.choose(opening).index == 0);
}
void efficient_refills_compare_effective_hp_and_respect_finish_and_deadline() {
    auto p = profile(); RulePolicy rules; auto s = encounter(); s.phase = Phase::recovery; s.hp = 0;
    Game empty(p, 1, s, {100}); CHECK(empty.full_price() == 48); CHECK(empty.step_price() == 10);
    CHECK(rules.choose(empty).kind == ActionKind::heal_step); // Legacy mode remains unchanged.
    rules.paid_refill_mode = 3; CHECK(rules.choose(empty).kind == ActionKind::heal_full);
    Game limited(p, 1, s, {20}); CHECK(rules.choose(limited).kind == ActionKind::heal_step);
    rules.shop_reserve_fraction = .6; CHECK(rules.choose(empty).kind == ActionKind::heal_step);
    rules.shop_reserve_fraction = 1; CHECK(rules.choose(empty).kind == ActionKind::wait);
    rules.shop_reserve_fraction = 0; s.paid_steps = 1;
    Game rising_step(p, 1, s, {100}); CHECK(rules.choose(rising_step).kind == ActionKind::heal_full);
    // Compare the rounded full-refill price and the profile's actual HP rate.
    s.paid_steps = 0; s.hp = .195; Game rounded(p, 1, s, {100});
    CHECK(rounded.full_price() == 39); CHECK(rules.choose(rounded).kind == ActionKind::heal_full);
    p.full_heal_cost_per_fraction = 60; Game dear_full(p, 1, s, {100});
    CHECK(rules.choose(dear_full).kind == ActionKind::heal_step); p.full_heal_cost_per_fraction = 48;
    // Final-boss surplus is discarded on full/empty restarts: one safe step is enough.
    p.boss_damage[3] = {.5, .55, .525}; s = encounter(Encounter::boss, 100, .4); s.phase = Phase::recovery;
    rules.revive_hp = 1; Game final_boss(p, 1, s, {100});
    CHECK(rules.choose(final_boss).kind == ActionKind::heal_step); final_boss.step(rules.choose(final_boss));
    CHECK(rules.choose(final_boss).kind == ActionKind::reenter); final_boss.step(rules.choose(final_boss));
    CHECK(rules.choose(final_boss).kind == ActionKind::fight);
    Limits carry_limits; carry_limits.budget = 100; carry_limits.repeat_runs = true; carry_limits.restart_health = "carry";
    Game carry(p, 1, s, carry_limits); CHECK(rules.choose(carry).kind == ActionKind::heal_full);
    s.room = 75; Game earlier_boss(p, 1, s, {100}); CHECK(rules.choose(earlier_boss).kind == ActionKind::heal_full);
    // There must be time to use purchased HP, including reentry. No spending at the deadline tail.
    s = encounter(); s.phase = Phase::recovery; s.hp = 0; rules.revive_hp = .2;
    Limits late_limits; late_limits.budget = 100; late_limits.deadline_hours = 2.5 * p.action_seconds / 3600;
    Game too_late(p, 1, s, late_limits); CHECK(rules.choose(too_late).kind == ActionKind::wait);
    while (!too_late.finished()) { const auto action = rules.choose(too_late); CHECK(too_late.legal(action)); too_late.step(action); }
    CHECK(too_late.state().mushrooms == 0); near(too_late.state().elapsed_hours, late_limits.deadline_hours);
    late_limits.deadline_hours = 3 * p.action_seconds / 3600;
    Game enough_time(p, 1, s, late_limits); CHECK(rules.choose(enough_time).kind == ActionKind::heal_full);
}
void barrels_optional_monsters_and_cursetrader_have_rules() {
    auto p = profile(); RulePolicy rules;
    auto s = encounter(Encounter::barrel); Game healthy(p, 1, s); CHECK(rules.choose(healthy).kind == ActionKind::skip);
    s.gems[ix(Gem::gambler)] = true; Game gambler(p, 1, s); CHECK(rules.choose(gambler).kind == ActionKind::interact);
    s.gems[ix(Gem::time_traveler)] = true; Game traveler(p, 1, s); CHECK(rules.choose(traveler).kind == ActionKind::skip);
    s = encounter(Encounter::mimic); Game mimic(p, 1, s); CHECK(rules.choose(mimic).kind == ActionKind::skip);
    s.blessings.give(effect_template(EffectKind::one_hit, false));
    Game free_mimic(p, 1, s); CHECK(rules.choose(free_mimic).kind == ActionKind::fight);
    s = encounter(Encounter::pig, 10, .8); Game pig(p, 1, s); CHECK(rules.choose(pig).kind == ActionKind::fight);
    s.hp = 1; Game free_keys_pig(p, 1, s); CHECK(rules.choose(free_keys_pig).kind == ActionKind::fight);
    s.hp = .1; Game dangerous_pig(p, 1, s); CHECK(rules.choose(dangerous_pig).kind == ActionKind::skip);
    s = encounter(Encounter::empty, 35); s.phase = Phase::curse_shop;
    s.offers = {{{effect_template(EffectKind::poison, false), 2}, {effect_template(EffectKind::gold_hangover, false), 2}}};
    Game trader(p, 1, s); CHECK(rules.choose(trader).kind == ActionKind::buy); CHECK(rules.choose(trader).index == 1);
}
void choices_do_not_read_or_advance_rng() {
    auto p = profile(); RulePolicy rules; auto s = encounter();
    Game inspected(p, 12, s), reference(p, 12, s), different_seed(p, 33, s);
    const auto action = rules.choose(inspected);
    CHECK(rules.choose(different_seed).kind == action.kind);
    const auto digest = state_digest(inspected.state());
    for (int i = 0; i < 30; ++i) CHECK(rules.choose(inspected).kind == action.kind);
    CHECK(state_digest(inspected.state()) == digest);
    inspected.step(action); reference.step(action);
    CHECK(state_digest(inspected.state()) == state_digest(reference.state()));
}
void complete_event_actions_are_legal_across_modes() {
    auto p = profile();
    for (int mode = 0; mode < 4; ++mode) for (int budget : {0, 50}) {
        RulePolicy rules; rules.door_mode = rules.shop_mode = rules.barrel_mode = mode % 3; rules.paid_refill_mode = mode;
        if (mode == 3) { rules.guaranteed_escape_door_preference = true; rules.shop_purchase_floor = 80; rules.fight_when_cheaper = true; }
        Limits limits; limits.budget = budget; limits.deadline_hours = 48; limits.repeat_runs = true;
        Game game(p, static_cast<std::uint64_t>(100 + mode + budget), limits);
        while (!game.finished()) {
            const auto action = rules.choose(game); CHECK(game.legal(action)); game.step(action);
        }
        CHECK(game.state().mushrooms <= budget); CHECK(game.state().elapsed_hours <= 48 + 1e-9);
        CHECK(game.state().actions < limits.max_actions);
    }
}
}
int main() {
    try {
        serialization_and_validation(); keys_health_and_one_hit_are_explicit_combat_rules();
        health_saving_fights_adapt_to_bull_without_ignoring_survival();
        free_mystery_and_key_reserves_control_doors(); traps_are_compared_with_the_other_door();
        guaranteed_escape_avoids_costly_doors_without_overriding_safe_rewards();
        shop_credit_gate_carryover_and_ranked_purchases(); recovery_respects_cadence_bosses_and_mushroom_allocation();
        shop_purchase_floor_is_a_nonrelaxing_paid_offer_gate(); efficient_refills_compare_effective_hp_and_respect_finish_and_deadline();
        barrels_optional_monsters_and_cursetrader_have_rules(); choices_do_not_read_or_advance_rng();
        complete_event_actions_are_legal_across_modes();
        std::cout << "13 explicit human-rule policy tests passed\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
