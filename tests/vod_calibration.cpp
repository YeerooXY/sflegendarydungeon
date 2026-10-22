#include "sfld/batch.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <numeric>
#include <sstream>
#include <stdexcept>

using namespace sfld;
namespace {
void require(bool ok, const char* expression, int line) {
    if (!ok) throw std::runtime_error("Line " + std::to_string(line) + ": " + expression);
}
#define CHECK(expression) require(bool(expression), #expression, __LINE__)
template<class F> void rejects(F&& operation) {
    bool rejected = false;
    try { operation(); } catch (const std::exception&) { rejected = true; }
    CHECK(rejected);
}
Profile fixture() {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    p.door_pair_weights.reset();
    p.mystery_beside_mystery_weights.reset(); p.mystery_beside_wall_weights.reset();
    p.container_joint_weights.reset(); p.container_joint_greed_weights.reset();
    p.trial_reward_weights_5.reset();
    p.shop_windows.reset();
    p.shop_schedule = "weighted"; p.trap_chance = 0;
    p.door_weights.fill(0);
    for (auto kind : {Door::monster, Door::mystery, Door::golden, Door::locked}) p.door_weights[ix(kind)] = 1;
    p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::empty)] = 1;
    p.mystery_without_monster_weights.reset();
    p.locked_weights = p.mystery_weights; p.golden_weights = p.mystery_weights;
    p.blessing_weights = {0, 1, 0, 0, 0, 0, 0, 0}; p.strong_effect = 0;
    p.skeleton_wakes = 0; p.container_blessing_requires_greed = true;
    return p;
}
State encounter(Encounter kind = Encounter::empty, int room = 30) {
    State s; s.phase = Phase::encounter; s.encounter = kind; s.room = room; s.turn = room - 1;
    s.run_number = 2; s.keys = 100; s.resources.fill(10); s.shops_seen.fill(true);
    return s;
}
std::size_t family(const State& state) {
    int monster = 0, mystery = 0, gold = 0, physical = 0;
    for (const auto d : state.doors) {
        if (d.kind == Door::monster) ++monster;
        else if (d.kind == Door::mystery) ++mystery;
        else if (d.kind == Door::golden) ++gold;
        else ++physical;
    }
    if (physical == 1 && mystery == 1) return 0;
    if (physical == 1 && monster == 1) return 1;
    if (mystery == 2) return 2;
    if (physical == 1 && gold == 1) return 3;
    if (monster == 1 && mystery == 1) return 4;
    if (gold == 1 && mystery == 1) return 5;
    if (gold == 1 && monster == 1) return 6;
    if (gold == 2) return 7;
    throw std::runtime_error("Generated an unsupported ordinary pair");
}
bool emitted(const Game& game, std::string_view kind) {
    return std::any_of(game.outcomes().begin(), game.outcomes().end(),
        [&](const auto& out) { return out.kind == kind; });
}
void joint_pair_weights_control_frequencies_without_order_bias() {
    auto p = fixture();
    p.door_pair_weights = std::array<double, 8>{35, 20, 15, 10, 8, 6, 4, 2};
    constexpr int samples = 30000;
    std::array<int, 8> counts{}; int physical_left = 0, physical_total = 0;
    for (int seed = 0; seed < samples; ++seed) {
        Game game(p, static_cast<std::uint64_t>(seed), encounter()); game.step({ActionKind::interact});
        const auto f = family(game.state()); ++counts[f];
        if (f == 0 || f == 1 || f == 3) {
            ++physical_total; physical_left += game.state().doors[0].kind == Door::locked;
        }
    }
    for (std::size_t i = 0; i < counts.size(); ++i)
        CHECK(std::abs(static_cast<double>(counts[i]) / samples - (*p.door_pair_weights)[i] / 100) < .01);
    CHECK(std::abs(static_cast<double>(physical_left) / physical_total - .5) < .02);
}
void joint_pairs_keep_stone_gates_and_legal_routes() {
    auto p = fixture(); p.door_pair_weights = std::array<double, 8>{1, 0, 0, 0, 0, 0, 0, 0};
    // Positive legacy variant weights must not bypass the enabling stones.
    p.door_weights[ix(Door::unlocked)] = 1000;
    p.door_weights[ix(Door::double_locked)] = 1000; p.door_weights[ix(Door::epic)] = 1000;
    bool unlocked = false, doubled = false, epic = false;
    for (unsigned mask = 0; mask < 8; ++mask) for (unsigned seed = 0; seed < 400; ++seed) {
        auto s = encounter(); s.gems[ix(Gem::spying)] = (mask & 1) != 0;
        s.gems[ix(Gem::lodestone)] = (mask & 2) != 0; s.gems[ix(Gem::devil)] = (mask & 4) != 0;
        Game game(p, seed, s); game.step({ActionKind::interact});
        CHECK(family(game.state()) == 0);
        CHECK(game.legal({ActionKind::choose_door, 0}) || game.legal({ActionKind::choose_door, 1}));
        for (const auto door : game.state().doors) {
            CHECK(door.kind != Door::unlocked || s.has(Gem::spying));
            CHECK(door.kind != Door::double_locked || s.has(Gem::lodestone));
            CHECK(door.kind != Door::epic || s.has(Gem::devil));
            unlocked |= door.kind == Door::unlocked; doubled |= door.kind == Door::double_locked;
            epic |= door.kind == Door::epic;
        }
    }
    CHECK(unlocked && doubled && epic);
    auto s = encounter(); s.gems[ix(Gem::devil)] = s.gems[ix(Gem::greasy)] = true;
    for (unsigned seed = 0; seed < 100; ++seed) {
        Game game(p, seed, s); game.step({ActionKind::interact});
        for (const auto door : game.state().doors) CHECK(door.kind != Door::epic);
    }
    // Pendant still requires a locked choice even when the empirical table has
    // no compatible positive family; it must not leave an unusable pair.
    p.door_pair_weights = std::array<double, 8>{0, 0, 1, 0, 0, 0, 0, 0};
    s = encounter(); s.keys = 0; s.gems[ix(Gem::pendant)] = true;
    for (unsigned seed = 0; seed < 100; ++seed) {
        Game game(p, seed, s); game.step({ActionKind::interact});
        CHECK(game.state().doors[0].kind == Door::locked || game.state().doors[1].kind == Door::locked);
        CHECK(game.legal({ActionKind::choose_door, 0}) || game.legal({ActionKind::choose_door, 1}));
    }
}
void compulsory_special_rooms_override_empirical_pairs() {
    auto p = fixture(); p.door_pair_weights = std::array<double, 8>{0, 0, 0, 0, 0, 0, 0, 1};
    for (int run : {1, 2}) {
        auto s = encounter(Encounter::empty, 4); s.run_number = run;
        Game game(p, 1, s); game.step({ActionKind::interact});
        if (run == 1) {
            CHECK(game.state().doors[0].kind == Door::shop); CHECK(game.state().doors[1].kind == Door::wall);
        } else CHECK(family(game.state()) == 4);
    }
    Game boss(p, 1, encounter(Encounter::empty, 24)); boss.step({ActionKind::interact});
    CHECK(boss.state().doors[0].kind == Door::boss); CHECK(boss.state().doors[1].kind == Door::wall);
    Game arena(p, 1, encounter(Encounter::boss, 50)); arena.step({ActionKind::fight});
    arena.step({ActionKind::choose_gem, 0});
    CHECK(arena.state().doors[0].kind == Door::monster); CHECK(arena.state().doors[1].kind == Door::trial);
    p.shop_schedule = "one_per_stage";
    auto s = encounter(Encounter::empty, 73); s.shops_seen[2] = false;
    Game shop(p, 1, s); shop.step({ActionKind::interact});
    CHECK(shop.state().doors[0].kind == Door::shop); CHECK(shop.state().doors[1].kind == Door::wall);
}
void spying_fraction_changes_only_stone_enabled_locked_doors() {
    auto p = fixture(); p.door_pair_weights = std::array<double, 8>{1, 0, 0, 0, 0, 0, 0, 0};
    for (double fraction : {0., 1.}) for (bool has_spying : {false, true}) {
        p.spying_unlock_fraction = fraction;
        auto s = encounter(); s.gems[ix(Gem::spying)] = has_spying;
        for (unsigned seed = 0; seed < 32; ++seed) {
            Game game(p, seed, s); game.step({ActionKind::interact});
            const auto expected = has_spying && fraction == 1 ? Door::unlocked : Door::locked;
            CHECK(game.state().doors[0].kind == expected || game.state().doors[1].kind == expected);
            CHECK(family(game.state()) == 0);
        }
    }
}
void empirical_trap_hazard_is_per_eligible_side_and_gold_is_safe() {
    auto p = fixture(); p.trap_chance = .30;
    p.door_pair_weights = std::array<double, 8>{0, 0, 1, 0, 0, 0, 0, 0};
    int positives = 0; constexpr int samples = 15000;
    for (int seed = 0; seed < samples; ++seed) {
        Game game(p, static_cast<std::uint64_t>(seed), encounter()); game.step({ActionKind::interact});
        const int n = int(game.state().doors[0].trap) + int(game.state().doors[1].trap);
        CHECK(n <= 1); positives += n;
    }
    // Two eligible sides yield one observed trap with probability 1 - (1-p)^2.
    CHECK(std::abs(static_cast<double>(positives) / samples - .51) < .015);
    p.trap_chance = 1;
    for (std::size_t f = 0; f < 8; ++f) {
        p.door_pair_weights->fill(0); (*p.door_pair_weights)[f] = 1;
        for (unsigned seed = 0; seed < 64; ++seed) {
            auto s = encounter(); s.gems[ix(Gem::gambler)] = true;
            Game game(p, seed, s); game.step({ActionKind::interact});
            int traps = 0;
            for (const auto door : game.state().doors) {
                traps += door.trap;
                CHECK(door.kind != Door::golden || !door.trap);
                CHECK(door.cursed_trap == door.trap);
            }
            CHECK(traps == (f == 3 || f == 7 ? 0 : 1));
        }
    }
}
void mystery_contents_use_the_exact_visible_alternative() {
    auto p = fixture(); p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::crate)] = 1;
    auto& broad = p.mystery_without_monster_weights.emplace(); broad.fill(0); broad[ix(Encounter::skeleton)] = 1;
    auto& paired = p.mystery_beside_mystery_weights.emplace(); paired.fill(0); paired[ix(Encounter::silver)] = 1;
    auto& wall = p.mystery_beside_wall_weights.emplace(); wall.fill(0); wall[ix(Encounter::bronze)] = 1;
    for (int chosen : {0, 1}) for (auto other : {Door::monster, Door::mystery, Door::wall, Door::locked, Door::golden}) {
        auto s = encounter(); s.phase = Phase::doors;
        s.doors[static_cast<std::size_t>(chosen)] = {Door::mystery};
        s.doors[static_cast<std::size_t>(1 - chosen)] = {other};
        const auto expected = other == Door::monster ? Encounter::crate : other == Door::mystery ? Encounter::silver :
            other == Door::wall ? Encounter::bronze : Encounter::skeleton;
        Game game(p, 5, s); game.step({ActionKind::choose_door, chosen}); CHECK(game.state().encounter == expected);
        s.doors[static_cast<std::size_t>(chosen)] = {Door::unlocked};
        Game unlocked(p, 5, s); unlocked.step({ActionKind::choose_door, chosen});
        CHECK(unlocked.state().encounter == Encounter::crate);
    }
    // The observation-based context cannot introduce a live monster beside
    // visible monster eyes, including when all surviving baseline weights vanish.
    p.mystery_weights.fill(0); p.mystery_weights[ix(Encounter::monster)] = 1;
    auto s = encounter(); s.phase = Phase::doors; s.doors = {{{Door::mystery}, {Door::monster}}};
    Game no_enemy(p, 6, s); no_enemy.step({ActionKind::choose_door, 0}); CHECK(!is_enemy(no_enemy.state().encounter));
    p.mystery_beside_mystery_weights.reset(); p.mystery_beside_wall_weights.reset();
    for (auto alternative : {Door::wall, Door::mystery}) {
        s.doors[1] = {alternative}; Game fallback(p, 6, s); fallback.step({ActionKind::choose_door, 0});
        CHECK(fallback.state().encounter == Encounter::skeleton);
    }
}
void joint_loot_can_award_all_three_outcomes_on_one_opening() {
    auto p = fixture(); p.container_joint_weights.emplace();
    for (auto kind : {Encounter::crate, Encounter::bronze, Encounter::silver}) for (unsigned mask = 0; mask < 8; ++mask) {
        p.container_joint_weights->fill(0); (*p.container_joint_weights)[mask] = 1;
        Game game(p, 7, encounter(kind)); game.step({ActionKind::interact});
        CHECK(bool(game.state().effect(EffectKind::one_hit)) == ((mask & 1) != 0));
        CHECK(emitted(game, "item") == ((mask & 2) != 0));
        CHECK(game.state().gold_rewards == ((mask & 4) != 0 ? 1 : 0));
        CHECK(emitted(game, "nothing") == (mask == 0));
        CHECK(game.state().room == 31); CHECK(game.state().epics == 0); CHECK(game.state().legendaries == 0);
    }
}
void greed_joint_pool_and_corpse_legacy_pool_stay_separate() {
    auto p = fixture(); p.container_joint_weights = std::array<double, 8>{0, 0, 0, 0, 1, 0, 0, 0};
    p.container_joint_greed_weights = std::array<double, 8>{0, 0, 0, 1, 0, 0, 0, 0};
    auto s = encounter(Encounter::crate);
    Game ordinary(p, 1, s); ordinary.step({ActionKind::interact});
    CHECK(ordinary.state().gold_rewards == 1); CHECK(ordinary.state().blessings.size == 0); CHECK(!emitted(ordinary, "item"));
    s.gems[ix(Gem::greed)] = true;
    Game greed(p, 1, s); greed.step({ActionKind::interact});
    CHECK(greed.state().gold_rewards == 0); CHECK(greed.state().effect(EffectKind::one_hit)); CHECK(emitted(greed, "item"));
    p.container_joint_greed_weights.reset();
    Game shared(p, 1, s); shared.step({ActionKind::interact}); CHECK(shared.state().gold_rewards == 1);
    p.container_weights = {1, 0, 0, 0}; s.encounter = Encounter::skeleton; s.gems[ix(Gem::greed)] = false;
    Game corpse(p, 1, s); corpse.step({ActionKind::interact});
    CHECK(corpse.state().gold_rewards == 0); CHECK(corpse.state().blessings.size == 0);
    s.gems[ix(Gem::greed)] = true;
    Game corpse_greed(p, 1, s); corpse_greed.step({ActionKind::interact});
    CHECK(corpse_greed.state().gold_rewards == 0); CHECK(corpse_greed.state().effect(EffectKind::one_hit));
    p.skeleton_wakes = 1;
    Game awakened(p, 1, s); awakened.step({ActionKind::interact});
    CHECK(awakened.state().encounter == Encounter::monster); CHECK(awakened.state().room == 30);
    CHECK(awakened.state().blessings.size == 0); CHECK(awakened.state().gold_rewards == 0);
}
void five_gate_chest_can_award_resources_without_an_item() {
    auto p = fixture(); p.trial_reward_weights_5.emplace(); p.trial_legendary_chances.fill(1);
    for (std::size_t outcome = 0; outcome < 4; ++outcome) {
        p.trial_reward_weights_5->fill(0); (*p.trial_reward_weights_5)[outcome] = 1;
        auto s = encounter(Encounter::prize, 56); s.pending_trial_reward = 5;
        Game game(p, 1, s); game.step({ActionKind::interact});
        CHECK(game.state().gold_rewards == (outcome == 0 ? 1 : 0));
        CHECK((game.state().resources[4] > s.resources[4]) == (outcome == 1));
        CHECK(game.state().epics == (outcome == 2 ? 1 : 0));
        CHECK(game.state().legendaries == (outcome == 3 ? 1 : 0));
        CHECK(game.state().pending_trial_reward == 0); CHECK(game.state().room == 57);
        s.pending_trial_reward = 4;
        Game lower(p, 1, s); lower.step({ActionKind::interact}); CHECK(lower.state().legendaries == 1);
    }
    p.trial_reward_weights_5.reset();
    auto s = encounter(Encounter::prize, 56); s.pending_trial_reward = 5;
    Game legacy(p, 1, s); legacy.step({ActionKind::interact}); CHECK(legacy.state().legendaries == 1);
}
struct Temp {
    std::filesystem::path path = std::filesystem::temp_directory_path() /
        ("sfld-calibration-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + ".profile");
    ~Temp() { std::error_code ignored; std::filesystem::remove(path, ignored); }
};
void optional_calibration_fields_are_parsed_and_reject_bad_distributions() {
    std::ifstream source(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    std::ostringstream legacy; std::string line;
    const std::array<std::string, 7> keys{"door_pairs", "mystery_beside_mystery", "mystery_beside_wall",
        "container_joint", "container_joint_greed", "trial_rewards_5", "spying_unlock_fraction"};
    while (std::getline(source, line)) {
        const bool calibration = std::any_of(keys.begin(), keys.end(), [&](const auto& key) { return line.starts_with(key + "="); });
        if (!calibration) legacy << line << '\n';
    }
    Temp file;
    auto load = [&](const std::string& extra) {
        { std::ofstream output(file.path); output << legacy.str() << extra; }
        return Profile::load(file.path.string());
    };
    const auto old = load(""); CHECK(!old.door_pair_weights); CHECK(!old.container_joint_weights); CHECK(!old.trial_reward_weights_5);
    CHECK(old.spying_unlock_fraction == .5);
    auto calibrated = load("door_pairs=1,2,3,4,5,6,7,8\nmystery_beside_mystery=monster:1\n"
        "mystery_beside_wall=empty:1\ncontainer_joint=1,0,0,0,0,0,0,1\n"
        "container_joint_greed=0,0,0,0,0,0,0,1\ntrial_rewards_5=19,20,6,3\n");
    CHECK(calibrated.door_pair_weights && calibrated.mystery_beside_mystery_weights && calibrated.mystery_beside_wall_weights);
    CHECK(calibrated.container_joint_weights && calibrated.container_joint_greed_weights && calibrated.trial_reward_weights_5);
    for (const auto& key : {"door_pairs", "container_joint"}) for (const auto& invalid : {
        "0,0,0,0,0,0,0,0", "1,1,1,1,1,1,1,-1", "1,1,1,1,1,1,1,nan", "1,2,3", "1,1,1,1,1,1,1,1,1"})
        rejects([&] { load(std::string(key) + "=" + invalid + "\n"); });
    for (const auto& key : {"mystery_beside_mystery", "mystery_beside_wall"}) for (const auto& invalid : {
        "monster:0", "monster:-1", "monster:1,monster:1", "unknown:1"})
        rejects([&] { load(std::string(key) + "=" + invalid + "\n"); });
    for (const auto& invalid : {"0,0,0,0", "1,-1,1,1", "1,1,1,nan", "1,1,1", "1,1,1,1,1"})
        rejects([&] { load(std::string("trial_rewards_5=") + invalid + "\n"); });
    rejects([&] { load("container_joint_greed=0,0,0,0,0,0,0,1\n"); });
    rejects([&] { load("container_joint=1,0,0,0,0,0,0,0\ncontainer_joint_greed=0,0,0,0,0,0,0,0\n"); });
    for (const auto& invalid : {"-0.1", "1.1", "nan", "inf"})
        rejects([&] { load(std::string("spying_unlock_fraction=") + invalid + "\n"); });
}
}
int main() {
    const std::array<std::pair<const char*, std::function<void()>>, 10> tests{{
        {"joint pair frequencies", joint_pair_weights_control_frequencies_without_order_bias},
        {"stone gates and legal routes", joint_pairs_keep_stone_gates_and_legal_routes},
        {"special rooms", compulsory_special_rooms_override_empirical_pairs},
        {"Spying Gem conversion", spying_fraction_changes_only_stone_enabled_locked_doors},
        {"per-eligible-side trap hazard", empirical_trap_hazard_is_per_eligible_side_and_gold_is_safe},
        {"exact mystery context", mystery_contents_use_the_exact_visible_alternative},
        {"overlapping container rewards", joint_loot_can_award_all_three_outcomes_on_one_opening},
        {"Greed and corpse separation", greed_joint_pool_and_corpse_legacy_pool_stay_separate},
        {"five-gate arena rewards", five_gate_chest_can_award_resources_without_an_item},
        {"profile validation", optional_calibration_fields_are_parsed_and_reject_bad_distributions},
    }};
    int failed = 0;
    for (const auto& [name, test] : tests) {
        try { test(); std::cout << "PASS " << name << '\n'; }
        catch (const std::exception& error) { ++failed; std::cerr << "FAIL " << name << ": " << error.what() << '\n'; }
    }
    return failed == 0 ? 0 : 1;
}
