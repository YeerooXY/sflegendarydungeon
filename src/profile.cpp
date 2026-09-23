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
        if (pair.size() != 2) throw std::invalid_argument("Expected minimum:maximum");
        out[i] = {number(pair[0]), number(pair[1])};
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
    std::uint64_t hash = UINT64_C(14695981039346656037);
    for (char c : raw) { hash ^= static_cast<unsigned char>(c); hash *= UINT64_C(1099511628211); }
    std::ostringstream hex; hex << std::hex << hash; p.fingerprint = hex.str();
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
    weights(p.mystery_weights, take("mystery"), encounter_from);
    weights(p.locked_weights, take("locked"), encounter_from);
    weights(p.golden_weights, take("golden"), encounter_from);
    for (const auto& g : split(take("gems"), ',')) p.gem_pool.push_back(gem_from(g));
    ranges(p.monster_damage, take("monster_damage"));
    ranges(p.escape_damage, take("escape_damage"));
    ranges(p.boss_damage, take("boss_damage"));
    numbers(p.trial_multipliers, take("trial_multipliers"));
    numbers(p.blessing_weights, take("blessings"));
    numbers(p.curse_weights, take("curses"));
    numbers(p.container_weights, take("containers"));
    const std::pair<const char*, double*> scalars[] = {
        {"trap_chance", &p.trap_chance}, {"cursed_trap_chance", &p.cursed_trap_chance},
        {"barrel_blessing", &p.barrel_blessing}, {"strong_effect", &p.strong_effect},
        {"escape_chance", &p.escape_chance}, {"key_chance", &p.key_chance},
        {"combat_curse_chance", &p.combat_curse_chance}, {"skeleton_wakes", &p.skeleton_wakes},
        {"hidden_monster_chance", &p.hidden_monster_chance}, {"recovery_hours", &p.recovery_hours},
        {"action_seconds", &p.action_seconds}, {"full_heal_cost_per_fraction", &p.full_heal_cost_per_fraction},
        {"trial_legendary_chance", &p.trial_legendary_chance}
    };
    for (auto [key, destination] : scalars) *destination = number(take(key));
    const double resources = number(take("initial_resources"));
    if (resources < 0 || resources > 1000000 || std::floor(resources) != resources)
        throw std::invalid_argument("initial_resources must be an integer in [0,1000000]");
    p.initial_resources = static_cast<int>(resources);
    const auto free = take("free_first_level_shops");
    if (free != "true" && free != "false") throw std::invalid_argument("Expected true/false for free_first_level_shops");
    p.free_first_level_shops = free == "true";
    if (!fields.empty()) throw std::invalid_argument("Unknown profile key: " + fields.begin()->first);
    p.validate();
    return p;
}
void Profile::validate() const {
    if (id.empty()) throw std::invalid_argument("Profile id is required");
    if (evidence != "synthetic")
        throw std::invalid_argument("Version 0.1 supports synthetic evidence only; live calibration is not implemented");
    check_weights(door_weights); check_weights(mystery_weights); check_weights(locked_weights);
    check_weights(golden_weights); check_weights(blessing_weights); check_weights(curse_weights);
    check_weights(container_weights);
    if (door_weights[ix(Door::boss)] != 0 || door_weights[ix(Door::exit_trial)] != 0)
        throw std::invalid_argument("Boss and trial-exit doors are generated by progression, not weights");
    const std::set<Gem> distinct(gem_pool.begin(), gem_pool.end());
    if (distinct.size() != gem_pool.size() || gem_pool.size() < 5)
        throw std::invalid_argument("Gem pool needs at least five distinct choices");
    for (auto g : gem_pool) if (ix(g) >= ix(Gem::count)) throw std::invalid_argument("Invalid gem");
    for (const auto* table : {&monster_damage, &escape_damage, &boss_damage})
        for (auto r : *table)
            if (!std::isfinite(r.low) || !std::isfinite(r.high) || r.low < 0 || r.high < r.low || r.high > 10)
                throw std::invalid_argument("Damage ranges must satisfy 0 <= low <= high <= 10");
    for (double v : {trap_chance, cursed_trap_chance, barrel_blessing, strong_effect, escape_chance,
        key_chance, combat_curse_chance, skeleton_wakes, hidden_monster_chance, trial_legendary_chance})
        if (!std::isfinite(v) || v < 0 || v > 1) throw std::invalid_argument("Probability outside [0,1]");
    for (double v : trial_multipliers)
        if (!std::isfinite(v) || v <= 0 || v > 10) throw std::invalid_argument("Invalid trial multiplier");
    if (!std::isfinite(recovery_hours) || recovery_hours <= 0 || recovery_hours > 10000 ||
        !std::isfinite(action_seconds) || action_seconds < 0 || action_seconds > 3600 ||
        !std::isfinite(full_heal_cost_per_fraction) || full_heal_cost_per_fraction <= 0 || full_heal_cost_per_fraction > 1000000)
        throw std::invalid_argument("Invalid recovery, action time or full-refill parameter");
}
}
