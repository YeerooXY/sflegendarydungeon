#pragma once
#include "sfld/model.hpp"

namespace sfld {
class Game {
public:
    Game(const Profile&, std::uint64_t seed, Limits = {}, int run_number = 1);
    // Explicit fixture/research state; caller must supply a valid visible state.
    Game(const Profile&, std::uint64_t seed, State, Limits = {});
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
private:
    const Profile& profile_;
    Random random_;
    State state_;
    Limits limits_;
    Phase resume_phase_ = Phase::encounter;
    void generate_doors();
    void ensure_open_path();
    void generate_shop(bool curses);
    void generate_gems();
    void enter(DoorView);
    void fight(bool flee);
    void interact(int choice = 0);
    void finish_room();
    void die();
    bool hurt(double);
    void heal(double);
    void give(Effect, bool immediately = false);
    Effect random_effect(bool curse, Stream = Stream::effects);
    void random_reward();
    void award_keys();
    double battle_multiplier(bool fleeing) const;
    double flee_probability() const;
};
}
