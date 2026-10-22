#include "sfld/engine.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>

using namespace sfld;
namespace {
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Correction test line " + std::to_string(__LINE__) + ": " #x); } while (false)
Profile profile() { return Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile"); }
StartState start(int room, int run) {
    return StartState::parse("schema=1\nroom=" + std::to_string(room) + "\nrun_number=" + std::to_string(run) +
        "\nphase=doors\nhp_percent=100\nkeys=8\ndoors=unknown\n");
}
void opening_alternatives_never_have_traps() {
    auto p = profile(); p.trap_chance = 1;
    for (auto other : {Door::mystery, Door::destiny, Door::blessing, Door::wood}) {
        p.opening_door_weights->fill(0); (*p.opening_door_weights)[ix(other)] = 1;
        p.validate();
        for (int run : {1, 2, 3}) for (int room = 1; room <= 5; ++room) for (unsigned seed = 0; seed < 20; ++seed) {
            Game g(p, seed, start(room, run));
            const auto& s = g.state();
            CHECK(!s.doors[0].trap && !s.doors[1].trap && !s.doors[0].cursed_trap && !s.doors[1].cursed_trap);
            if (room == 5 && run == 1) {
                CHECK(s.doors[0].kind == Door::shop && s.doors[1].kind == Door::wall);
            } else CHECK((s.doors[0].kind == Door::monster && s.doors[1].kind == other) ||
                         (s.doors[1].kind == Door::monster && s.doors[0].kind == other));
        }
    }
}
void later_stage_one_never_gets_a_shop_even_with_overdue_import() {
    auto p = profile(); CHECK(!p.later_stage_one_shop);
    for (auto schedule : {"one_per_stage", "weighted"}) {
        p.shop_schedule = schedule;
        if (p.shop_schedule == "weighted") p.shop_windows.reset();
        p.door_pair_weights.reset(); p.door_weights.fill(0);
        p.door_weights[ix(Door::shop)] = 1000; p.door_weights[ix(Door::monster)] = 1;
        for (int room = 1; room < 25; ++room) for (unsigned seed = 0; seed < 20; ++seed) {
            Game g(p, seed, start(room, 2));
            CHECK(g.state().doors[0].kind != Door::shop && g.state().doors[1].kind != Door::shop);
        }
    }
}
void traveler_can_receive_blessings_and_legacy_scenario_is_explicit() {
    auto p = profile(); State s; s.phase = Phase::encounter; s.encounter = Encounter::barrel;
    s.room = 60; s.run_number = 2; s.gems[ix(Gem::time_traveler)] = true;
    p.blessing_weights.fill(0); p.blessing_weights[ix(EffectKind::one_hit)] = 1;
    CHECK(std::abs(barrel_blessing_chance(p, s) - .25) < 1e-12);
    int blessings = 0, curses = 0;
    for (unsigned seed = 0; seed < 400; ++seed) {
        Game g(p, seed, s); g.step({ActionKind::interact});
        blessings += g.state().blessings.size > 0; curses += g.state().curses.size > 0;
    }
    CHECK(blessings > 0 && curses > 0 && blessings + curses == 400);
    s.gems[ix(Gem::time_traveler)] = false;
    CHECK(barrel_blessing_chance(p, s) == .5);
    s.gems[ix(Gem::gambler)] = true; CHECK(barrel_blessing_chance(p, s) == 1);
    s.gems[ix(Gem::time_traveler)] = true; p.time_traveler_barrel_curse_multiplier = 0;
    CHECK(barrel_blessing_chance(p, s) == 0);
}
void weak_one_hit_replaces_eight_if_explicitly_bought() {
    auto p = profile();
    auto initial = start(5, 1); initial.state.phase = Phase::shop;
    auto active = effect_template(EffectKind::one_hit, true); active.remaining = 8; active.starts_at = initial.state.turn;
    initial.state.blessings.give(active);
    // Entering the shop generates its real, fixed free offers.
    initial.state.phase = Phase::doors; initial.state.doors = {{{Door::shop}, {Door::wall}}}; initial.generate_current = false;
    Game g(p, 9, initial); g.step({ActionKind::choose_door, 0});
    CHECK(g.state().offers[0].effect.kind == EffectKind::one_hit && g.state().offers[0].effect.remaining == 4);
    CHECK(g.state().offers[0].keys == 0 && g.state().offers[1].keys == 0);
    CHECK(!g.legal({ActionKind::reroll}));
    CHECK(g.state().blessings.slots[0].remaining == 8);
    g.step({ActionKind::buy, 0}); CHECK(g.state().blessings.slots[0].remaining == 4);
}
void pendant_preserves_special_physical_door_styles() {
    for (bool joint : {false, true}) {
        auto p = profile(); p.trap_chance = 0;
        p.door_weights.fill(0); p.door_weights[ix(Door::blessing)] = 1; p.door_weights[ix(Door::mystery)] = 1;
        if (joint) p.door_pair_weights = std::array<double, 8>{1, 0, 0, 0, 1, 0, 0, 0};
        else p.door_pair_weights.reset();
        auto initial = start(71, 2); initial.state.gems[ix(Gem::pendant)] = true;
        initial.state.gems[ix(Gem::thirsty)] = true; initial.state.shops_seen = {false, true, true, false};
        for (unsigned seed = 0; seed < 50; ++seed) {
            Game g(p, seed, initial); const auto& d = g.state().doors;
            CHECK((d[0].kind == Door::blessing && d[1].kind == Door::mystery) ||
                  (d[1].kind == Door::blessing && d[0].kind == Door::mystery));
        }
    }
}
}
int main() {
    try {
        opening_alternatives_never_have_traps();
        later_stage_one_never_gets_a_shop_even_with_overdue_import();
        traveler_can_receive_blessings_and_legacy_scenario_is_explicit();
        weak_one_hit_replaces_eight_if_explicitly_bought();
        pendant_preserves_special_physical_door_styles();
        std::cout << "Mechanics correction tests passed\n";
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
