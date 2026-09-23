#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace sfld {
inline constexpr std::string_view engine_version = "0.3.0";
enum class Phase { doors, encounter, shop, curse_shop, gems, recovery, complete };
enum class Door { monster, mystery, locked, double_locked, unlocked, epic, golden,
    shop, cursed, sacrifice, blessing, destiny, wood, stone, souls, metal, arcane,
    hourglasses, trial, exit_trial, boss, wall, count };
enum class Encounter { monster, boss, barrel, crate, silver, bronze, epic, skeleton,
    mimic, cursed_chest, sacrifice_chest, sated_chest, prize, empty, fountain,
    cleansing_fountain, rocks, lava, narrator, flooded, wishing_well, rps, sewers,
    undead, sarcophagus, locked_sarcophagus, wood, wheel, spider_legs, spider_head,
    spider_full, souls, arcane, curse_shop, armory, locker, tube, beta, shakes,
    valaraukar, auction, rainbow, pig, trial_monster, count };
enum class Gem { rabbit, moonstone, spying, pendant, greasy, gambler, greed, hero,
    time_traveler, thirsty, pearl, blood, explorer, misadventurer, devil, deceit,
    lodestone, bull, hick, rusty, masochist, old_sacrifice, kidney, count };
enum class EffectKind { raider, one_hit, escape, disarm, lockpick, key_moment,
    elixir, recovery, broken_armor, poison, clumsy, gold_hangover, hard_lock, count };
enum class Clock { room, trap, door, fight };
enum class ActionKind { choose_door, fight, flee, interact, skip, buy, reroll,
    choose_gem, wait, heal_step, heal_full, reenter, rps, linger };
enum class Stream { doors, contents, damage, escape, keys, effects, shop, gems, special, count };

template<class E> constexpr std::size_t ix(E e) { return static_cast<std::size_t>(e); }
std::string_view name(Phase);
std::string_view name(Door);
std::string_view name(Encounter);
std::string_view name(Gem);
std::string_view name(EffectKind);
std::string_view name(ActionKind);
Phase phase_from(std::string_view);
EffectKind effect_from(std::string_view);
Door door_from(std::string_view);
Encounter encounter_from(std::string_view);
Gem gem_from(std::string_view);
ActionKind action_from(std::string_view);
bool is_curse(EffectKind);
bool is_enemy(Encounter);
bool is_optional(Encounter);

struct Effect {
    EffectKind kind = EffectKind::raider;
    double magnitude = 0;
    int remaining = 0;
    Clock clock = Clock::room;
    int starts_at = 0;
};
Effect effect_template(EffectKind, bool strong);
class Effects {
public:
    std::array<Effect, 3> slots{};
    int size = 0;
    const Effect* find(EffectKind, int turn) const;
    void give(Effect);
    void consume(Clock, int turn);
    void clear() { size = 0; }
};
struct Offer { Effect effect; int keys = 0; };
struct DoorView { Door kind = Door::wall; bool trap = false; bool cursed_trap = false; };
struct Action { ActionKind kind; int index = 0; double value = 0; };

// This is the player-visible state. It contains no future random draws or hidden rooms.
struct State {
    Phase phase = Phase::doors;
    int room = 1;
    int turn = 0;
    int run_number = 1;
    double hp = 1;
    int keys = 0;
    std::array<int, 6> resources{};
    std::array<bool, ix(Gem::count)> gems{};
    Effects blessings, curses;
    std::array<DoorView, 2> doors{};
    Encounter encounter = Encounter::empty;
    std::array<Offer, 2> offers{};
    std::array<Gem, 3> gem_offers{};
    int trial_depth = 0;
    bool trial_seen = false;
    int donated_resource = -1;
    int pending_trial_reward = 0;
    int deaths = 0;
    int paid_steps = 0;
    int mushrooms = 0;
    int recovery_mushrooms = 0;
    int reroll_mushrooms = 0;
    int shop_rerolls = 0;
    int shop_purchases = 0;
    int strong_one_hit_purchases = 0;
    int epics = 0;
    int legendaries = 0;
    int gold_rewards = 0;
    double gold_units = 0; // Relative reward units, not account gold.
    int lucky_coins = 0;
    int barrels_opened = 0;
    int barrels_skipped = 0;
    std::uint64_t actions = 0;
    double elapsed_hours = 0;
    double active_hours = 0;
    bool has(Gem g) const { return gems[ix(g)]; }
    const Effect* effect(EffectKind k) const;
};

struct Range { double low = 0; double high = 0; };
struct Profile {
    std::string id;
    std::string evidence;
    std::string fingerprint;
    std::array<double, ix(Door::count)> door_weights{};
    std::array<double, ix(Encounter::count)> mystery_weights{};
    std::array<double, ix(Encounter::count)> locked_weights{};
    std::array<double, ix(Encounter::count)> golden_weights{};
    std::vector<Gem> gem_pool;
    // Optional overrides; empty means use gem_pool. Pool contents still need evidence.
    std::vector<Gem> first_run_gem_pool, later_run_gem_pool;
    const std::vector<Gem>& gems_for_run(int run_number) const;
    std::array<Range, 4> monster_damage{};
    std::array<Range, 4> escape_damage{};
    std::array<Range, 4> boss_damage{};
    std::array<double, 5> trial_multipliers{};
    std::array<double, 8> blessing_weights{};
    std::array<double, 5> curse_weights{};
    // Container outcomes: blessing, normal item, gold, empty.
    std::array<double, 4> container_weights{};
    double trap_chance = 0;
    double cursed_trap_chance = 0;
    double barrel_blessing = 0;
    double strong_effect = 0;
    double escape_chance = 0;
    double key_chance = 0;
    double combat_curse_chance = 0;
    double skeleton_wakes = 0;
    double hidden_monster_chance = 0;
    double recovery_hours = 24;
    double action_seconds = 2;
    double full_heal_cost_per_fraction = 48;
    double trial_legendary_chance = 0;
    int initial_resources = 10;
    bool free_first_level_shops = true;
    // Unknown shop distributions are independently configurable from barrels.
    std::array<double, 8> shop_blessing_weights{};
    std::array<double, 5> shop_curse_weights{};
    double shop_strong_effect = .50;
    bool armory_bonus_eligible = false;
    double armory_legendary_chance = .10;
    double auction_epic_chance = .50;
    static Profile load(const std::string&);
    void validate() const;
};

struct Limits { int budget = 0; double deadline_hours = 240; std::uint64_t max_actions = 100000; int run_number = 1; };
// Counter-based, specified integer arithmetic, independent streams, reset per room.
class Random {
public:
    explicit Random(std::uint64_t seed) : seed_(seed) {}
    double unit(Stream);
    bool chance(Stream s, double p);
    double uniform(Stream, Range);
    std::size_t weighted(Stream, const double* weights, std::size_t count);
    void room(int room) { room_ = room; counters_.fill(0); }
    static std::uint64_t mix(std::uint64_t);
private:
    std::uint64_t seed_;
    int room_ = 1;
    std::array<std::uint64_t, ix(Stream::count)> counters_{};
};
std::string state_digest(const State&);
std::string json_string(std::string_view);
std::string text_fingerprint(std::string_view);
}
