#pragma once
#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define SFLD_RL_API __declspec(dllexport)
#else
#define SFLD_RL_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif
typedef struct sfld_rl_env sfld_rl_env;
typedef struct sfld_rl_snapshot sfld_rl_snapshot;

/* All array buffers are caller-owned, contiguous, row-major. Calls return 0 on
 * success and -1 on error (create returns NULL). Read last_error immediately.
 * A handle is single-threaded; different handles may be used independently.
 * No function exposes a Game seed, random stream, or unrevealed encounter.
 */
SFLD_RL_API const char* sfld_rl_last_error(void);
SFLD_RL_API int32_t sfld_rl_observation_count(void);
SFLD_RL_API int32_t sfld_rl_action_count(void);
SFLD_RL_API int32_t sfld_rl_metric_count(void);
SFLD_RL_API const char* sfld_rl_schema_json(void);
SFLD_RL_API sfld_rl_env* sfld_rl_create(const char* const* profiles, int32_t profile_count,
    const int32_t* budgets, int32_t budget_count, int32_t env_count,
    double deadline_hours, uint64_t max_actions, uint8_t seed_namespace, int32_t auto_reset);
SFLD_RL_API void sfld_rl_destroy(sfld_rl_env*);
/* Optional daily availability, configured before the first reset. Local hours
 * in [0,24); sleep_until_hour=0 disables sleep. Pauses preserve living HP;
 * recovery continues passively. Active actions cannot cross midnight. */
SFLD_RL_API int32_t sfld_rl_set_daily_schedule(sfld_rl_env*, double sleep_until_hour, double event_start_hour);
/* Creation loads configuration only. Reset assigns consecutive episode indices.
 * Namespaces partition seeds into disjoint domains; indices must be <2^24.
 */
SFLD_RL_API int32_t sfld_rl_reset(sfld_rl_env*, uint64_t seed, uint64_t initial_episode_index,
    float* observations, uint8_t* masks);
SFLD_RL_API int32_t sfld_rl_reset_done(sfld_rl_env*, const int32_t* indices, int32_t count,
    float* observations, uint8_t* masks);
SFLD_RL_API uint64_t sfld_rl_next_episode_index(const sfld_rl_env*);
SFLD_RL_API int32_t sfld_rl_observe(const sfld_rl_env*, float* observations, uint8_t* masks);
SFLD_RL_API int32_t sfld_rl_episode_info(const sfld_rl_env*, double* metrics);
/* Optional reporting-only totals, three doubles per slot: relative gold units,
 * gold reward count, epic count. These do not alter policy inputs or rewards. */
SFLD_RL_API int32_t sfld_rl_reward_totals(const sfld_rl_env*, double* totals);
/* Illegal actions reject the entire batch before any Game is stepped. Rewards
 * are completion increments. A safety action cap is truncated, never terminated.
 * Only genuine terminals auto-reset. Terminal buffers contain the pre-reset
 * observation/mask for finished rows, and zeros for other rows. Metrics describe
 * the just-stepped episode even when its row subsequently auto-resets.
 */
SFLD_RL_API int32_t sfld_rl_step(sfld_rl_env*, const int32_t* actions,
    float* observations, uint8_t* masks, float* rewards, uint8_t* terminated,
    uint8_t* truncated, double* metrics, float* terminal_observations, uint8_t* terminal_masks);
/* Explicit experiment-only custom recovery waits. A target of -1 preserves
 * the ordinary menu action. Otherwise actions[i] must be a wait action and
 * targets[i] an HP fraction in [.2,1] above current HP. The native engine waits
 * exactly to that target, clipped to the deadline. This does not change the
 * 23-action model schema. Full-batch validation is still atomic.
 */
SFLD_RL_API int32_t sfld_rl_step_wait_targets(sfld_rl_env*, const int32_t* actions, const double* targets,
    float* observations, uint8_t* masks, float* rewards, uint8_t* terminated,
    uint8_t* truncated, double* metrics, float* terminal_observations, uint8_t* terminal_masks);
/* kind: 0=legacy scorer, 1=survival scorer, 2=human rules (requires rules_path).
 * Optional rules_path replaces stone preferences for scorer teachers. Returns
 * -1 per row for terminals or custom waits outside the finite action menu.
 */
SFLD_RL_API int32_t sfld_rl_teacher_actions(sfld_rl_env*, int32_t kind,
    const char* rules_path, int32_t* actions);
/* Visible JSON for adviser parity checks; no future outcomes or RNG seeds.
 * Valid until the next call on this thread. Returns nullptr on an error. */
SFLD_RL_API const char* sfld_rl_visible_json(const sfld_rl_env*, int32_t slot);
/* Research snapshots contain visible state only. Restoring supplies independent
 * future randomness; it never copies the source game's random streams. Snapshot
 * lifetime is independent of either environment. Restore preserves the target
 * episode index and requires matching profile, budget, deadline, action cap and daily schedule.
 */
SFLD_RL_API sfld_rl_snapshot* sfld_rl_capture(const sfld_rl_env*, int32_t slot);
SFLD_RL_API sfld_rl_snapshot* sfld_rl_fixture(const sfld_rl_env*, int32_t profile,
    int32_t budget, const char* progress, uint64_t seed);
/* Exact visible state from the agent's planning_snapshot command. No source
 * RNG is included; profile and all environment limits must match on restore. */
SFLD_RL_API sfld_rl_snapshot* sfld_rl_planning_fixture(const sfld_rl_env*, int32_t profile,
    const char* planning_state);
SFLD_RL_API void sfld_rl_snapshot_destroy(sfld_rl_snapshot*);
SFLD_RL_API int32_t sfld_rl_restore(sfld_rl_env*, int32_t slot,
    const sfld_rl_snapshot*, uint64_t independent_seed);
/* CLAIRVOYANT research only: copies the complete game including its random
 * streams, so the target replays the source's exact future. Use it for
 * hindsight bounds, never for an adviser or a fair comparison. */
SFLD_RL_API int32_t sfld_rl_clone_slot(sfld_rl_env*, int32_t source, int32_t target);
/* Research opt-in: afterwards, action -1 leaves that slot unchanged for one step. */
SFLD_RL_API int32_t sfld_rl_enable_hold(sfld_rl_env*);
#ifdef __cplusplus
}
#endif
