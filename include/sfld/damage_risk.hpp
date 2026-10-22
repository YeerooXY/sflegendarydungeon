#pragma once
#include "sfld/model.hpp"
#include <algorithm>
#include <string_view>

namespace sfld {
// Analytical counterpart of Game::sample_damage. No random draws or hidden state.
// The lower moments use strict D < threshold, matching death at exactly zero HP.
class DamageRisk {
public:
    DamageRisk(Range range, std::string_view distribution) : range_(range), distribution_(distribution) {}
    double mean() const {
        if (point_mass()) return point();
        return distribution_ == "triangular_mean" ? range_.mean : (range_.low + range_.high) / 2;
    }
    double maximum() const { return point_mass() ? point() : range_.high; }
    double below(double threshold) const {
        if (point_mass()) return point() < threshold ? 1 : 0;
        const double x = std::clamp((threshold - range_.low) / width(), 0.0, 1.0);
        if (!triangular()) return x;
        const double c = mode();
        if (x <= c && c > 0) return x * x / c;
        if (c == 1) return 1;
        return 1 - (1 - x) * (1 - x) / (1 - c);
    }
    double moment_below(double threshold) const {
        if (point_mass()) return point() < threshold ? point() : 0;
        const double x = std::clamp((threshold - range_.low) / width(), 0.0, 1.0);
        double first = x * x / 2;
        if (triangular()) {
            const double c = mode();
            if (x <= c && c > 0) first = 2 * x * x * x / (3 * c);
            else if (c == 1) first = 2.0 / 3;
            else {
                const double tail = 1 - x;
                first = (1 + c) / 3 - tail * tail * (1 - 2 * tail / 3) / (1 - c);
            }
        }
        return range_.low * below(threshold) + width() * first;
    }
    struct RoomResult { double hit_survival, room_survival, expected_hp; };
    RoomResult after_room(double hp, double poison = 0, double recovery = 0,
                          double healing_before_tick = 0) const {
        // Game::hurt and room-exit poison clamp numerical residue at zero HP.
        constexpr double hp_epsilon = 1e-12;
        const double hit_survival = below(hp - hp_epsilon);
        // Survive the hit first, then receive healing (capped at full HP), then
        // take poison. Neither heal can rescue a character killed by the hit.
        if (poison >= 1 - hp_epsilon) return {hit_survival, 0, 0};
        const double threshold = std::min(hp, hp + healing_before_tick + recovery - poison) - hp_epsilon;
        const double survival = below(threshold);
        const double intercept = hp + healing_before_tick - poison + recovery;
        const double cap = 1 - poison;
        const double capped_at = std::min(threshold, intercept - cap);
        const double expected_hp = intercept * survival - moment_below(threshold) -
            (intercept - cap) * below(capped_at) + moment_below(capped_at);
        return {hit_survival, survival, std::clamp(expected_hp, 0.0, 1.0)};
    }
private:
    Range range_;
    std::string_view distribution_;
    bool point_mass() const {
        return range_.low == range_.high || distribution_ == "low" ||
            distribution_ == "high" || distribution_ == "midpoint";
    }
    double point() const {
        if (distribution_ == "high") return range_.high;
        if (distribution_ == "midpoint") return (range_.low + range_.high) / 2;
        return range_.low;
    }
    double width() const { return range_.high - range_.low; }
    bool triangular() const { return distribution_ == "triangular" || distribution_ == "triangular_mean"; }
    double mode() const {
        return distribution_ == "triangular_mean" ?
            std::clamp((3 * range_.mean - 2 * range_.low - range_.high) / width(), 0.0, 1.0) : .5;
    }
};
}
