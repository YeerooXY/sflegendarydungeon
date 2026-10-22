#pragma once
#include "sfld/progress.hpp"

namespace sfld {
// Visible planning state only: never includes an environment seed or RNG
// counters. An independent planning seed supplies every future random draw.
// Export with Game::planning_snapshot() to preserve normalized offered-stone
// history. Manually assembled snapshots must supply that history themselves;
// use StartState for progress imports that need legacy-history normalization.
struct PlanningSnapshot {
    State state;
    Limits limits;
    Phase recovery_destination = Phase::encounter;
    std::vector<Outcome> outcomes;
};
class Game {
public:
    Game(const Profile&, std::uint64_t seed, Limits = {});
    // Explicit fixture/research state; caller must supply a valid visible state.
    Game(const Profile&, std::uint64_t seed, State, Limits = {});
    Game(const Profile&, std::uint64_t seed, const StartState&, Limits = {});
    Game(const Profile&, std::uint64_t independent_seed, const PlanningSnapshot&);
    PlanningSnapshot planning_snapshot() const;
    const State& state() const { return state_; }
    const Profile& profile() const { return profile_; }
    const Limits& limits() const { return limits_; }
    bool legal(Action) const;
    void step(Action);
    int door_cost(Door) const;
    bool available(DoorView) const;
    int step_price() const;
    int full_price() const;
    double damage_ceiling(bool boss) const;
    double battle_multiplier(bool fleeing) const;
    double flee_probability() const;
    double action_hours(Action) const;
    double local_hour() const;
    bool finished() const;
    Phase recovery_destination() const { return resume_phase_; }
    const std::vector<Outcome>& outcomes() const { return outcomes_; }
private:
    const Profile& profile_;
    std::uint64_t seed_;
    Random random_;
    State state_;
    Limits limits_;
    Phase resume_phase_ = Phase::encounter;
    std::vector<Outcome> outcomes_;
    void apply(Action);
    void respect_sleep_schedule();
    void result(std::string_view kind, std::string_view detail = "", double amount = 0, int duration = 0);
    void generate_doors();
    void ensure_open_path();
    void generate_shop(bool curses);
    bool opening_shop() const;
    void generate_gems();
    void enter(DoorView);
    void fight(bool flee);
    void interact();
    void finish_room();
    void die();
    bool hurt(double);
    void heal(double);
    void give(Effect, bool immediately = false);
    Effect random_effect(bool curse, Stream = Stream::effects);
    void random_reward(bool ordinary_container = false);
    void destiny_reward();
    void award_keys();
    void award_gold(bool chest, double units = 1);
    double sample_damage(Range);
    void restart();
};
}
