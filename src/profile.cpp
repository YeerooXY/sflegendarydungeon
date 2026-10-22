#include "sfld/model.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>

namespace sfld {
namespace {
std::string trim(std::string_view s) {
    const auto first = s.find_first_not_of(" \r\n\t");
    if (first == std::string_view::npos) return {};
    return std::string(s.substr(first, s.find_last_not_of(" \r\n\t") - first + 1));
}
std::vector<std::string> split(std::string_view text, char delimiter) {
    std::vector<std::string> result;
    std::size_t begin = 0;
    while (begin <= text.size()) {
        auto end = text.find(delimiter, begin);
        if (end == std::string_view::npos) end = text.size();
        result.push_back(trim(text.substr(begin, end - begin)));
        if (end == text.size()) break;
        begin = end + 1;
    }
    return result;
}
double number(const std::string& text) {
    double value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size() || !std::isfinite(value))
        throw std::invalid_argument("Expected a finite number: " + text);
    return value;
}
template<std::size_t N> void numbers(std::array<double, N>& out, const std::string& text) {
    const auto parts = split(text, ',');
    if (parts.size() != N) throw std::invalid_argument("Wrong number of values: " + text);
    for (std::size_t i = 0; i < N; ++i) out[i] = number(parts[i]);
}
template<std::size_t N, class Parser>
void weights(std::array<double, N>& out, const std::string& text, Parser parser) {
    std::set<std::size_t> seen;
    for (const auto& part : split(text, ',')) {
        auto pair = split(part, ':');
        if (pair.size() != 2) throw std::invalid_argument("Expected identifier:weight: " + part);
        const auto index = ix(parser(pair[0]));
        if (!seen.insert(index).second) throw std::invalid_argument("Duplicate outcome: " + pair[0]);
        out.at(index) = number(pair[1]);
    }
}
void ranges(std::array<Range, 4>& out, const std::string& text) {
    auto parts = split(text, ',');
    if (parts.size() != 4) throw std::invalid_argument("Four floor-band ranges required");
    for (std::size_t i = 0; i < 4; ++i) {
        auto pair = split(parts[i], ':');
        if (pair.size() != 2 && pair.size() != 3) throw std::invalid_argument("Expected minimum:maximum[:mean]");
        out[i] = {number(pair[0]), number(pair[1]), pair.size() == 3 ? number(pair[2]) : -1};
    }
}
template<class T> void check_weights(const T& weights) {
    double sum = 0;
    for (double w : weights) {
        if (!std::isfinite(w) || w < 0) throw std::invalid_argument("Weights must be finite and nonnegative");
        sum += w;
    }
    if (!(sum > 0) || !std::isfinite(sum)) throw std::invalid_argument("Distribution must have positive finite weight");
}
}
Profile Profile::load(const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("Cannot read profile: " + path);
    const std::string raw((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    Profile p;
    p.fingerprint = text_fingerprint(raw);
    std::map<std::string, std::string> fields;
    std::istringstream input(raw);
    std::string line;
    while (std::getline(input, line)) {
        line = trim(line.substr(0, line.find('#')));
        if (line.empty()) continue;
        const auto equals = line.find('=');
        if (equals == std::string::npos) throw std::invalid_argument("Expected key=value: " + line);
        auto key = trim(line.substr(0, equals));
        if (!fields.emplace(key, trim(line.substr(equals + 1))).second)
            throw std::invalid_argument("Duplicate profile key: " + key);
    }
    const auto take = [&fields](const std::string& key) {
        auto entry = fields.find(key);
        if (entry == fields.end()) throw std::invalid_argument("Missing profile key: " + key);
        auto value = entry->second; fields.erase(entry); return value;
    };
    if (take("schema") != "1") throw std::invalid_argument("Unsupported profile schema");
    p.id = take("id"); p.evidence = take("evidence");
    weights(p.door_weights, take("doors"), door_from);
    if (fields.contains("opening_doors")) weights(p.opening_door_weights.emplace(), take("opening_doors"), door_from);
    if (fields.contains("door_pairs")) numbers(p.door_pair_weights.emplace(), take("door_pairs"));
    weights(p.mystery_weights, take("mystery"), encounter_from);
    if (fields.contains("mystery_without_monster"))
        weights(p.mystery_without_monster_weights.emplace(), take("mystery_without_monster"), encounter_from);
    if (fields.contains("mystery_beside_mystery"))
        weights(p.mystery_beside_mystery_weights.emplace(), take("mystery_beside_mystery"), encounter_from);
    if (fields.contains("mystery_beside_wall"))
        weights(p.mystery_beside_wall_weights.emplace(), take("mystery_beside_wall"), encounter_from);
    if (fields.contains("destiny")) weights(p.destiny_weights, take("destiny"), encounter_from);
    else p.destiny_weights = p.mystery_weights;
    if (fields.contains("destiny_rewards")) numbers(p.destiny_reward_weights, take("destiny_rewards"));
    if (fields.contains("destiny_reward_amounts")) numbers(p.destiny_reward_amounts, take("destiny_reward_amounts"));
    weights(p.locked_weights, take("locked"), encounter_from);
    weights(p.golden_weights, take("golden"), encounter_from);
    for (const auto& g : split(take("gems"), ',')) p.gem_pool.push_back(gem_from(g));
    for (auto [key, pool] : {std::pair{"gems_first_run", &p.first_run_gem_pool},
                            std::pair{"gems_later_runs", &p.later_run_gem_pool}}) {
        if (fields.contains(key)) for (const auto& g : split(take(key), ',')) pool->push_back(gem_from(g));
    }
    ranges(p.monster_damage, take("monster_damage"));
    ranges(p.escape_damage, take("escape_damage"));
    ranges(p.boss_damage, take("boss_damage"));
    numbers(p.trial_multipliers, take("trial_multipliers"));
    numbers(p.blessing_weights, take("blessings"));
    numbers(p.curse_weights, take("curses"));
    numbers(p.container_weights, take("containers"));
    if (fields.contains("container_joint")) numbers(p.container_joint_weights.emplace(), take("container_joint"));
    if (fields.contains("container_joint_greed")) numbers(p.container_joint_greed_weights.emplace(), take("container_joint_greed"));
    const std::pair<const char*, double*> scalars[] = {
        {"trap_chance", &p.trap_chance},
        {"barrel_blessing", &p.barrel_blessing}, {"strong_effect", &p.strong_effect},
        {"escape_chance", &p.escape_chance}, {"key_chance", &p.key_chance},
        {"combat_curse_chance", &p.combat_curse_chance}, {"skeleton_wakes", &p.skeleton_wakes},
        {"hidden_monster_chance", &p.hidden_monster_chance}, {"recovery_hours", &p.recovery_hours},
        {"action_seconds", &p.action_seconds}, {"full_heal_cost_per_fraction", &p.full_heal_cost_per_fraction}
    };
    for (auto [key, destination] : scalars) *destination = number(take(key));
    if (fields.contains("spying_key_penalty")) p.spying_key_penalty = number(take("spying_key_penalty"));
    if (fields.contains("spying_unlock_fraction")) p.spying_unlock_fraction = number(take("spying_unlock_fraction"));
    if (fields.contains("time_traveler_barrel_curse_multiplier"))
        p.time_traveler_barrel_curse_multiplier = number(take("time_traveler_barrel_curse_multiplier"));
    // Old zero-valued profiles remain readable; never silently retain the
    // invented random cursed-trap roll after the maintainer's correction.
    if (fields.contains("cursed_trap_chance") && number(take("cursed_trap_chance")) != 0)
        throw std::invalid_argument("Remove cursed_trap_chance: Gambler converts traps deterministically; use the original engine for historical profiles");
    if (fields.contains("trial_legendary_chances")) numbers(p.trial_legendary_chances, take("trial_legendary_chances"));
    else if (fields.contains("trial_legendary_chance")) {
        const double chance = number(take("trial_legendary_chance"));
        p.trial_legendary_chances = {0, 0, 0, chance, chance};
    }
    if (fields.contains("trial_rewards_5")) numbers(p.trial_reward_weights_5.emplace(), take("trial_rewards_5"));
    if (fields.contains("trial_entry_room")) {
        const double room = number(take("trial_entry_room"));
        if (room < 0 || room > 93 || std::floor(room) != room)
            throw std::invalid_argument("trial_entry_room must be an integer in [0,93]");
        p.trial_entry_room = static_cast<int>(room);
    }
    const double resources = number(take("initial_resources"));
    if (resources < 0 || resources > 1000000 || std::floor(resources) != resources)
        throw std::invalid_argument("initial_resources must be an integer in [0,1000000]");
    p.initial_resources = static_cast<int>(resources);
    const auto free = take("free_first_level_shops");
    if (free != "true" && free != "false") throw std::invalid_argument("Expected true/false for free_first_level_shops");
    p.free_first_level_shops = free == "true";
    if (fields.contains("shop_schedule")) p.shop_schedule = take("shop_schedule");
    if (fields.contains("shop_windows")) {
        const auto parts = split(take("shop_windows"), ',');
        if (parts.size() != 4) throw std::invalid_argument("shop_windows requires four minimum:maximum room windows");
        auto& windows = p.shop_windows.emplace();
        for (std::size_t i = 0; i < windows.size(); ++i) {
            const auto bounds = split(parts[i], ':');
            if (bounds.size() != 2) throw std::invalid_argument("shop_windows expects minimum:maximum");
            windows[i] = {number(bounds[0]), number(bounds[1])};
        }
    }
    p.shop_blessing_weights = p.blessing_weights;
    p.shop_curse_weights = p.curse_weights;
    if (fields.contains("shop_blessings")) numbers(p.shop_blessing_weights, take("shop_blessings"));
    if (fields.contains("shop_curses")) numbers(p.shop_curse_weights, take("shop_curses"));
    if (fields.contains("shop_strong_effect")) p.shop_strong_effect = number(take("shop_strong_effect"));
    if (fields.contains("opening_shop_rerolls")) p.opening_shop_rerolls = take("opening_shop_rerolls");
    if (fields.contains("armory_legendary_chance")) p.armory_legendary_chance = number(take("armory_legendary_chance"));
    if (fields.contains("auction_epic_chance")) p.auction_epic_chance = number(take("auction_epic_chance"));
    if (fields.contains("damage_distribution")) p.damage_distribution = take("damage_distribution");
    if (fields.contains("spider_success")) numbers(p.spider_success, take("spider_success"));
    if (fields.contains("spider_bite_damage")) p.spider_bite_damage = number(take("spider_bite_damage"));
    for (auto [key, destination] : {std::pair{"rps_epic_reward", &p.rps_epic_reward},
                                   std::pair{"fight_only_damage_stones", &p.fight_only_damage_stones},
                                   std::pair{"later_stage_one_shop", &p.later_stage_one_shop},
                                   std::pair{"exclude_offered_gems", &p.exclude_offered_gems},
                                   std::pair{"time_traveler_elixir_full_heal", &p.time_traveler_elixir_full_heal},
                                   std::pair{"floor_specific_curses", &p.floor_specific_curses},
                                   std::pair{"container_blessing_requires_greed", &p.container_blessing_requires_greed}}) {
        if (!fields.contains(key)) continue;
        const auto value = take(key);
        if (value != "true" && value != "false") throw std::invalid_argument("Expected true/false for " + std::string(key));
        *destination = value == "true";
    }
    if (fields.contains("armory_bonus_eligible")) {
        const auto value = take("armory_bonus_eligible");
        if (value != "true" && value != "false") throw std::invalid_argument("Expected true/false for armory_bonus_eligible");
        p.armory_bonus_eligible = value == "true";
    }
    if (!fields.empty()) throw std::invalid_argument("Unknown profile key: " + fields.begin()->first);
    p.validate();
    return p;
}
void Profile::validate() const {
    if (!std::isfinite(time_traveler_barrel_curse_multiplier) || time_traveler_barrel_curse_multiplier < 0)
        throw std::invalid_argument("time_traveler_barrel_curse_multiplier must be finite and nonnegative");
    if (opening_door_weights) {
        check_weights(*opening_door_weights);
        for (std::size_t i = 0; i < opening_door_weights->size(); ++i) {
            const auto d = static_cast<Door>(i);
            if ((*opening_door_weights)[i] > 0 && d != Door::mystery && d != Door::destiny &&
                d != Door::blessing && !(d >= Door::wood && d <= Door::hourglasses))
                throw std::invalid_argument("opening_doors supports mystery, destiny, blessing and hungry resource doors");
        }
    }
    if (shop_schedule != "weighted" && shop_schedule != "one_per_stage")
        throw std::invalid_argument("shop_schedule must be weighted or one_per_stage");
    if (shop_windows) {
        if (shop_schedule != "one_per_stage")
            throw std::invalid_argument("shop_windows requires shop_schedule=one_per_stage");
        for (std::size_t i = 0; i < shop_windows->size(); ++i) {
            const auto window = (*shop_windows)[i];
            const int first = i == 0 ? 6 : static_cast<int>(i * 25) + 1;
            const int last = static_cast<int>((i + 1) * 25) - 1;
            if (!std::isfinite(window.low) || !std::isfinite(window.high) ||
                std::floor(window.low) != window.low || std::floor(window.high) != window.high ||
                window.low < first || window.high > last || window.low > window.high)
                throw std::invalid_argument("shop_windows must contain integer rooms within each stage, after the opening and before its boss");
            if (window.low == trial_entry_room && window.high == trial_entry_room)
                throw std::invalid_argument("A shop window must contain an ordinary room besides the fixed trial entrance");
        }
    }
    if (opening_shop_rerolls != "disabled" && opening_shop_rerolls != "normal" && opening_shop_rerolls != "fixed")
        throw std::invalid_argument("opening_shop_rerolls must be disabled, normal or fixed");
    if (trial_entry_room != 0 && (trial_entry_room < 26 || trial_entry_room > 93 ||
        trial_entry_room % 25 == 0 || trial_entry_room % 25 > 18))
        throw std::invalid_argument("Trial entrance must leave room for five tiers and the prize before a boss, or be 0 for weighted generation");
    if (damage_distribution != "uniform" && damage_distribution != "triangular" && damage_distribution != "triangular_mean" && damage_distribution != "low" &&
        damage_distribution != "high" && damage_distribution != "midpoint")
        throw std::invalid_argument("damage_distribution must be uniform, triangular, triangular_mean, low, high or midpoint");
    for (double p : spider_success) if (!std::isfinite(p) || p < 0 || p > 1)
        throw std::invalid_argument("Invalid spider success probability");
    if (!std::isfinite(spider_bite_damage) || spider_bite_damage < 0 || spider_bite_damage > 1)
        throw std::invalid_argument("spider_bite_damage must be in [0,1]");
    if (id.empty()) throw std::invalid_argument("Profile id is required");
    if (evidence != "synthetic")
        throw std::invalid_argument("Only synthetic evidence is supported; live calibration is not implemented");
    check_weights(door_weights); check_weights(mystery_weights); check_weights(locked_weights);
    if (door_pair_weights) check_weights(*door_pair_weights);
    if (mystery_without_monster_weights) check_weights(*mystery_without_monster_weights);
    if (mystery_beside_mystery_weights) check_weights(*mystery_beside_mystery_weights);
    if (mystery_beside_wall_weights) check_weights(*mystery_beside_wall_weights);
    check_weights(destiny_weights); check_weights(destiny_reward_weights);
    for (double amount : destiny_reward_amounts)
        if (!std::isfinite(amount) || amount < 1 || amount > 1000 || std::floor(amount) != amount)
            throw std::invalid_argument("Destiny reward amounts must be integer units in [1,1000]");
    check_weights(golden_weights); check_weights(blessing_weights); check_weights(curse_weights);
    check_weights(container_weights);
    if (container_joint_weights) check_weights(*container_joint_weights);
    if (container_joint_greed_weights) {
        check_weights(*container_joint_greed_weights);
        if (!container_joint_weights) throw std::invalid_argument("container_joint_greed requires container_joint");
    }
    if (trial_reward_weights_5) check_weights(*trial_reward_weights_5);
    check_weights(shop_blessing_weights); check_weights(shop_curse_weights);
    const auto positive = [](double w) { return w > 0; };
    if (std::count_if(shop_blessing_weights.begin(), shop_blessing_weights.end(), positive) < 2 ||
        std::count_if(shop_curse_weights.begin(), shop_curse_weights.end(), positive) < 2)
        throw std::invalid_argument("Shops need at least two distinct effect types with positive weight");
    if (door_weights[ix(Door::boss)] != 0 || door_weights[ix(Door::exit_trial)] != 0)
        throw std::invalid_argument("Boss and trial-exit doors are generated by progression, not weights");
    for (const auto* pool : {&gem_pool, &first_run_gem_pool, &later_run_gem_pool}) {
        if (pool != &gem_pool && pool->empty()) continue;
        const std::set<Gem> distinct(pool->begin(), pool->end());
        const std::size_t minimum = exclude_offered_gems ? 9 : 5;
        if (distinct.size() != pool->size() || pool->size() < minimum)
            throw std::invalid_argument(exclude_offered_gems ?
                "Each gem pool needs at least nine distinct choices when offered gems are excluded" :
                "Each gem pool needs at least five distinct choices");
        for (auto g : *pool) if (ix(g) >= ix(Gem::count)) throw std::invalid_argument("Invalid gem");
    }
    for (const auto* table : {&monster_damage, &escape_damage, &boss_damage})
        for (auto r : *table) {
            if (!std::isfinite(r.low) || !std::isfinite(r.high) || r.low < 0 || r.high < r.low || r.high > 10)
                throw std::invalid_argument("Damage ranges must satisfy 0 <= low <= high <= 10");
            if (!std::isfinite(r.mean) || (r.mean != -1 && (r.mean < r.low || r.mean > r.high)))
                throw std::invalid_argument("A reported damage mean must lie inside its range");
            if (damage_distribution == "triangular_mean" &&
                (r.mean < 0 || 3*r.mean < 2*r.low+r.high-1e-12 || 3*r.mean > r.low+2*r.high+1e-12))
                throw std::invalid_argument("triangular_mean requires all range means and a mode inside each range");
        }
    for (double v : trial_legendary_chances)
        if (!std::isfinite(v) || v < 0 || v > 1) throw std::invalid_argument("Trial legendary probability outside [0,1]");
    for (double v : {trap_chance, barrel_blessing, strong_effect, escape_chance,
        key_chance, spying_key_penalty, combat_curse_chance, skeleton_wakes, hidden_monster_chance,
        spying_unlock_fraction, shop_strong_effect, armory_legendary_chance, auction_epic_chance})
        if (!std::isfinite(v) || v < 0 || v > 1) throw std::invalid_argument("Probability outside [0,1]");
    for (double v : trial_multipliers)
        if (!std::isfinite(v) || v <= 0 || v > 10) throw std::invalid_argument("Invalid trial multiplier");
    if (!std::isfinite(recovery_hours) || recovery_hours <= 0 || recovery_hours > 10000 ||
        !std::isfinite(action_seconds) || action_seconds < 0 || action_seconds > 3600 ||
        !std::isfinite(full_heal_cost_per_fraction) || full_heal_cost_per_fraction <= 0 || full_heal_cost_per_fraction > 1000000)
        throw std::invalid_argument("Invalid recovery, action time or full-refill parameter");
}
const std::vector<Gem>& Profile::gems_for_run(int run_number) const {
    const auto& pool = run_number == 1 ? first_run_gem_pool : later_run_gem_pool;
    return pool.empty() ? gem_pool : pool;
}
const std::array<double, ix(Encounter::count)>& Profile::mystery_for_pair(bool beside_monster) const {
    return !beside_monster && mystery_without_monster_weights ? *mystery_without_monster_weights : mystery_weights;
}
const std::array<double, ix(Encounter::count)>& Profile::mystery_for_alternative(Door other) const {
    if (other == Door::mystery && mystery_beside_mystery_weights) return *mystery_beside_mystery_weights;
    if (other == Door::wall && mystery_beside_wall_weights) return *mystery_beside_wall_weights;
    return mystery_for_pair(other == Door::monster);
}
}
