#include "sfld/policy.hpp"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

using namespace sfld;
namespace {
void check(bool value, int line) {
    if (!value) throw std::runtime_error("Shop timing test failed at line " + std::to_string(line));
}
#define CHECK(value) check(bool(value), __LINE__)
template<class F> void rejects(F action) {
    bool failed = false;
    try { action(); } catch (const std::invalid_argument&) { failed = true; }
    CHECK(failed);
}
Profile profile() {
    return Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
}
Profile simple_profile() {
    auto p = profile(); p.shop_schedule = "one_per_stage";
    p.opening_door_weights.reset();
    p.exclude_offered_gems = false; // Five-stone fixture isolates shop placement.
    p.shop_windows.reset(); // Exercise optional legacy placement independently of shipped defaults.
    p.monster_damage.fill({0, 0, 0}); p.escape_damage = p.monster_damage; p.boss_damage = p.monster_damage;
    p.damage_distribution = "uniform"; p.trap_chance = 0; p.combat_curse_chance = 0;
    p.door_pair_weights.reset(); p.door_weights.fill(0);
    p.door_weights[ix(Door::monster)] = 1; p.door_weights[ix(Door::mystery)] = 1;
    p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::empty)] = 1;
    p.mystery_without_monster_weights.reset(); p.mystery_beside_mystery_weights.reset(); p.mystery_beside_wall_weights.reset();
    p.gem_pool = p.first_run_gem_pool = p.later_run_gem_pool =
        {Gem::rabbit, Gem::moonstone, Gem::hero, Gem::spying, Gem::greed};
    return p;
}
std::array<Range, 4> windows() { return {{{6, 24}, {30, 40}, {61, 70}, {80, 85}}}; }
std::array<int, 4> shop_rooms(const Profile& p, unsigned seed, int run_number) {
    Limits limits; limits.run_number = run_number;
    Game game(p, seed, limits); Policy policy;
    std::array<int, 4> rooms{};
    for (int i = 0; i < 1000 && game.state().phase != Phase::complete; ++i) {
        const auto& s = game.state();
        auto action = policy.choose(game);
        if (s.phase == Phase::doors) {
            for (int side = 0; side < 2; ++side) if (s.doors[static_cast<std::size_t>(side)].kind == Door::shop) {
                const auto stage = static_cast<std::size_t>((s.room - 1) / 25);
                CHECK(rooms[stage] == 0); rooms[stage] = s.room;
                CHECK(s.doors[static_cast<std::size_t>(1 - side)].kind == Door::wall);
            }
            // Exercise the arena on half of seeds; otherwise take its monster.
            if (s.room == p.trial_entry_room && s.trial_depth == 0) action = {ActionKind::choose_door, seed % 2 ? 1 : 0};
        }
        if (s.phase == Phase::shop) action = {ActionKind::skip};
        CHECK(game.legal(action)); game.step(action);
    }
    CHECK(game.state().phase == Phase::complete);
    CHECK(rooms[0] != 0 || (run_number > 1 && !p.later_stage_one_shop));
    for (std::size_t i = 1; i < rooms.size(); ++i) CHECK(rooms[i] != 0);
    return rooms;
}
void windows_bound_shops_and_preserve_the_fixed_opening() {
    auto p = simple_profile(); p.shop_windows = windows(); p.validate();
    for (unsigned seed = 0; seed < 150; ++seed) for (int run_number : {1, 2}) {
        const auto rooms = shop_rooms(p, seed, run_number);
        for (std::size_t i = 0; i < rooms.size(); ++i) {
            if (i == 0 && run_number == 1) CHECK(rooms[i] == 5);
            else if (i == 0 && !p.later_stage_one_shop) CHECK(rooms[i] == 0);
            else CHECK(rooms[i] >= (*p.shop_windows)[i].low && rooms[i] <= (*p.shop_windows)[i].high);
        }
    }
}
void absent_windows_retain_the_full_stage_uniform_schedule() {
    auto legacy = simple_profile(); CHECK(!legacy.shop_windows);
    legacy.later_stage_one_shop = true;
    auto explicit_full = legacy;
    explicit_full.shop_windows = std::array<Range, 4>{{{6, 24}, {26, 49}, {51, 74}, {76, 99}}};
    explicit_full.validate();
    for (unsigned seed = 0; seed < 50; ++seed) for (int run_number : {1, 2})
        CHECK(shop_rooms(legacy, seed, run_number) == shop_rooms(explicit_full, seed, run_number));
}
void a_late_import_keeps_its_unseen_shop_quota() {
    auto p = profile(); p.shop_schedule = "one_per_stage"; p.shop_windows = windows();
    auto start = StartState::parse("schema=1\nroom=45\nrun_number=2\nphase=doors\nhp_percent=100\nkeys=0\ngems=rabbit\nshops_seen=1\ndoors=unknown\n");
    for (unsigned seed = 0; seed < 20; ++seed) {
        Game game(p, seed, start);
        CHECK(game.state().doors[0].kind == Door::shop);
        CHECK(game.state().doors[1].kind == Door::wall);
        CHECK(game.state().shops_seen[1]);
    }
    start.state.shops_seen[1] = true;
    Game already_seen(p, 5, start);
    CHECK(already_seen.state().doors[0].kind != Door::shop && already_seen.state().doors[1].kind != Door::shop);
}
void profile_parser_and_validation_reject_invalid_windows() {
    std::ifstream source(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    std::string base, line;
    // Supply exactly one test window below, even when the shipped profile has
    // adopted windows as its default. Rejections must test bounds, not duplicates.
    while (std::getline(source, line)) if (!line.starts_with("shop_windows=")) base += line + '\n';
    const auto path = std::filesystem::temp_directory_path() / ("sfld-shop-timing-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".profile");
    struct Remove { std::filesystem::path path; ~Remove() { std::error_code ignored; std::filesystem::remove(path, ignored); } } remove{path};
    const auto load = [&](const std::string& value) {
        { std::ofstream out(path); out << base << "\nshop_windows=" << value << '\n'; }
        return Profile::load(path.string());
    };
    auto parsed = load("6:24,30:40,61:70,80:85");
    CHECK(parsed.shop_windows.has_value()); CHECK((*parsed.shop_windows)[2].low == 61);
    rejects([&] { load("6:24,30:40,61:70"); });
    rejects([&] { load("6:24,30:40,61:70,80:85:83"); });
    rejects([&] { load("6.5:24,30:40,61:70,80:85"); });
    rejects([&] { load("5:24,30:40,61:70,80:85"); });
    rejects([&] { load("6:25,30:40,61:70,80:85"); });
    rejects([&] { load("6:24,40:30,61:70,80:85"); });
    rejects([&] { load("6:24,30:40,51:51,80:85"); });
    parsed.shop_schedule = "weighted"; rejects([&] { parsed.validate(); });
}
}
int main() {
    try {
        windows_bound_shops_and_preserve_the_fixed_opening();
        absent_windows_retain_the_full_stage_uniform_schedule();
        a_late_import_keeps_its_unseen_shop_quota();
        profile_parser_and_validation_reject_invalid_windows();
        std::cout << "Shop timing tests passed\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
