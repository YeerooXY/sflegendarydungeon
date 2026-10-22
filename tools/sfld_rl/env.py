"""NumPy/ctypes bridge to the offline C++ Legendary Dungeon vector environment.

Observations and masks contain no RNG state, episode seed, or hidden room.
The metadata/episode counters are for experiment bookkeeping, never model input.
"""
from __future__ import annotations

import ctypes as ct
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class VectorEnv:
    def __init__(self, library, profiles, n_envs, budgets=(0, 20, 50, 100, 500),
                 seed=42, seed_namespace=0xA1, deadline_hours=168, max_actions=100000,
                 auto_reset=True, initial_episode_index=0, sleep_until_hour=0, event_start_hour=0):
        self.library_path = Path(library).resolve(strict=True)
        if isinstance(profiles, (str, os.PathLike)):
            profiles = [profiles]
        self.profile_paths = [Path(path).resolve(strict=True) for path in profiles]
        if not 0 <= int(seed_namespace) <= 255 or int(seed_namespace) != seed_namespace:
            raise ValueError("seed_namespace must be an integer in 0..255")
        if not 0 <= int(seed) < 2**64 or int(seed) != seed:
            raise ValueError("seed must be an unsigned 64-bit integer")
        if int(initial_episode_index) != initial_episode_index or not 0 <= int(initial_episode_index) < 2**24:
            raise ValueError("initial_episode_index must be in 0..2^24-1")
        if int(n_envs) != n_envs or not 1 <= int(n_envs) <= 4096:
            raise ValueError("n_envs must be an integer in 1..4096")
        if int(max_actions) != max_actions or not 1 <= int(max_actions) < 2**64:
            raise ValueError("max_actions must be a positive unsigned 64-bit integer")
        budget_values = list(budgets)
        if any(int(x) != x or not 0 <= x <= 1000000 for x in budget_values):
            raise ValueError("budgets must be integers in 0..1000000")
        for value in (sleep_until_hour, event_start_hour):
            if not math.isfinite(value) or not 0 <= value < 24:
                raise ValueError("Daily schedule hours must be finite and in [0,24)")
        self.n_envs = int(n_envs)
        self.seed = int(seed)
        self.initial_episode_index = int(initial_episode_index)
        self.auto_reset = bool(auto_reset)
        self._initialized = False
        self._handle = None
        self._dll_directory = os.add_dll_directory(str(self.library_path.parent)) if os.name == "nt" else None
        self._lib = ct.CDLL(str(self.library_path))
        self._bind()
        self.schema = json.loads(self._lib.sfld_rl_schema_json().decode("utf-8"))
        self.observation_names = self.schema["observation_names"]
        self.action_names = self.schema["action_names"]
        self.metric_names = self.schema["metric_names"]
        self.observation_dim = self.obs_dim = int(self._lib.sfld_rl_observation_count())
        self.action_dim = self.n_actions = int(self._lib.sfld_rl_action_count())
        self.metric_dim = int(self._lib.sfld_rl_metric_count())
        assert len(self.observation_names) == self.observation_dim
        assert len(self.action_names) == self.action_dim
        assert len(self.metric_names) == self.metric_dim
        profile_array = (ct.c_char_p * len(self.profile_paths))(*(os.fsencode(path) for path in self.profile_paths))
        budget_array = np.ascontiguousarray(budget_values, dtype=np.int32)
        self._handle = self._lib.sfld_rl_create(profile_array, len(self.profile_paths),
            self._ptr(budget_array, ct.c_int32), len(budget_array), self.n_envs,
            float(deadline_hours), int(max_actions), int(seed_namespace), int(self.auto_reset))
        if not self._handle:
            raise RuntimeError(self._error())
        if sleep_until_hour or event_start_hour:
            try:
                if not hasattr(self._lib, "sfld_rl_set_daily_schedule"):
                    raise RuntimeError("Daily schedules require engine 0.6.12 or newer")
                self._check(self._lib.sfld_rl_set_daily_schedule(self._handle, sleep_until_hour, event_start_hour))
            except Exception:
                self.close()
                raise
        self.metadata = {
            "library": str(self.library_path), "library_sha256": _sha256(self.library_path),
            "profiles": [{"path": str(path), "sha256": _sha256(path)} for path in self.profile_paths],
            "schema": self.schema,
            "schema_sha256": hashlib.sha256(json.dumps(self.schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "observation_dim": self.observation_dim, "action_dim": self.action_dim,
            "n_envs": self.n_envs, "budgets": budget_values, "seed_namespace": int(seed_namespace),
            "deadline_hours": float(deadline_hours), "max_actions": int(max_actions),
            "auto_reset": self.auto_reset, "initial_episode_index": self.initial_episode_index,
            "daily_schedule": {"sleep_until_hour": float(sleep_until_hour), "event_start_hour": float(event_start_hour)},
        }
        self._episode_info = None

    def _bind(self):
        lib = self._lib
        void, i32, u64, u8 = ct.c_void_p, ct.c_int32, ct.c_uint64, ct.c_uint8
        fp, bp, ip, dp = ct.POINTER(ct.c_float), ct.POINTER(u8), ct.POINTER(i32), ct.POINTER(ct.c_double)
        for name in ("sfld_rl_last_error", "sfld_rl_schema_json"):
            getattr(lib, name).restype = ct.c_char_p
            getattr(lib, name).argtypes = []
        for name in ("sfld_rl_observation_count", "sfld_rl_action_count", "sfld_rl_metric_count"):
            getattr(lib, name).restype = i32
            getattr(lib, name).argtypes = []
        lib.sfld_rl_create.restype = void
        lib.sfld_rl_create.argtypes = [ct.POINTER(ct.c_char_p), i32, ip, i32, i32, ct.c_double, u64, u8, i32]
        lib.sfld_rl_destroy.restype = None
        lib.sfld_rl_destroy.argtypes = [void]
        signatures = {
            "sfld_rl_reset": [void, u64, u64, fp, bp],
            "sfld_rl_reset_done": [void, ip, i32, fp, bp],
            "sfld_rl_observe": [void, fp, bp],
            "sfld_rl_episode_info": [void, dp],
            "sfld_rl_step": [void, ip, fp, bp, fp, bp, bp, dp, fp, bp],
            "sfld_rl_teacher_actions": [void, i32, ct.c_char_p, ip],
        }
        for name, parameters in signatures.items():
            getattr(lib, name).argtypes = parameters
            getattr(lib, name).restype = i32
        if hasattr(lib, "sfld_rl_step_wait_targets"):
            lib.sfld_rl_step_wait_targets.argtypes = [void, ip, dp, fp, bp, fp, bp, bp, dp, fp, bp]
            lib.sfld_rl_step_wait_targets.restype = i32
        if hasattr(lib, "sfld_rl_reward_totals"):
            lib.sfld_rl_reward_totals.argtypes = [void, dp]
            lib.sfld_rl_reward_totals.restype = i32
        if hasattr(lib, "sfld_rl_set_daily_schedule"):
            lib.sfld_rl_set_daily_schedule.argtypes = [void, ct.c_double, ct.c_double]
            lib.sfld_rl_set_daily_schedule.restype = i32
        lib.sfld_rl_next_episode_index.argtypes = [void]
        lib.sfld_rl_next_episode_index.restype = u64

    @staticmethod
    def _ptr(array, ctype):
        return array.ctypes.data_as(ct.POINTER(ctype))

    def _error(self):
        return self._lib.sfld_rl_last_error().decode("utf-8", errors="replace")

    def _check(self, status):
        if status != 0:
            raise RuntimeError(self._error())

    def _outputs(self):
        return (np.empty((self.n_envs, self.observation_dim), dtype=np.float32),
                np.empty((self.n_envs, self.action_dim), dtype=np.uint8))

    def _refresh_info(self):
        values = np.empty((self.n_envs, self.metric_dim), dtype=np.float64)
        self._check(self._lib.sfld_rl_episode_info(self._handle, self._ptr(values, ct.c_double)))
        self._episode_info = {name: values[:, i].copy() for i, name in enumerate(self.metric_names)}

    @property
    def next_episode_index(self):
        if not self._handle:
            raise RuntimeError("Environment is closed")
        if not self._initialized:
            return self.initial_episode_index
        return int(self._lib.sfld_rl_next_episode_index(self._handle))

    @property
    def episode_budgets(self):
        if self._episode_info is None:
            raise RuntimeError("Call reset first")
        return self._episode_info["budget"].astype(np.int64)

    @property
    def episode_indices(self):
        if self._episode_info is None:
            raise RuntimeError("Call reset first")
        return self._episode_info["episode_index"].astype(np.int64)

    @property
    def profile_indices(self):
        if self._episode_info is None:
            raise RuntimeError("Call reset first")
        return self._episode_info["profile_index"].astype(np.int64)

    def reset(self, seed=None, initial_episode_index=None):
        if seed is not None:
            if int(seed) != seed or not 0 <= int(seed) < 2**64:
                raise ValueError("seed must be an unsigned 64-bit integer")
            self.seed = int(seed)
        if initial_episode_index is None:
            initial_episode_index = self.initial_episode_index if seed is not None or not self._initialized else self.next_episode_index
        if int(initial_episode_index) != initial_episode_index or not 0 <= int(initial_episode_index) < 2**24:
            raise ValueError("Invalid initial_episode_index")
        obs, masks = self._outputs()
        self._check(self._lib.sfld_rl_reset(self._handle, self.seed, int(initial_episode_index),
            self._ptr(obs, ct.c_float), self._ptr(masks, ct.c_uint8)))
        self._initialized = True
        self._refresh_info()
        return obs, masks.astype(bool)

    def reset_done(self, indices):
        original = np.asarray(indices)
        if original.ndim != 1 or not np.issubdtype(original.dtype, np.integer):
            if original.size != 0:
                raise ValueError("indices must be a one-dimensional integer array")
        if np.any(original < 0) or np.any(original >= self.n_envs):
            raise ValueError("reset index out of range")
        indices = np.ascontiguousarray(original, dtype=np.int32)
        obs, masks = self._outputs()
        self._check(self._lib.sfld_rl_reset_done(self._handle, self._ptr(indices, ct.c_int32), len(indices),
            self._ptr(obs, ct.c_float), self._ptr(masks, ct.c_uint8)))
        self._refresh_info()
        return obs, masks.astype(bool)

    def observe(self):
        obs, masks = self._outputs()
        self._check(self._lib.sfld_rl_observe(self._handle, self._ptr(obs, ct.c_float), self._ptr(masks, ct.c_uint8)))
        return obs, masks.astype(bool)

    def step(self, actions, *, wait_targets=None):
        original = np.asarray(actions)
        if original.shape != (self.n_envs,) or not np.issubdtype(original.dtype, np.integer):
            raise ValueError("actions must be an integer vector of length n_envs")
        if np.any(original < 0) or np.any(original >= self.action_dim):
            raise ValueError("action index out of range")
        actions = np.ascontiguousarray(original, dtype=np.int32)
        obs, masks = self._outputs()
        terminal_obs, terminal_masks = self._outputs()
        rewards = np.empty(self.n_envs, dtype=np.float32)
        terminated = np.empty(self.n_envs, dtype=np.uint8)
        truncated = np.empty(self.n_envs, dtype=np.uint8)
        metrics = np.empty((self.n_envs, self.metric_dim), dtype=np.float64)
        function = self._lib.sfld_rl_step
        arguments = [self._handle, self._ptr(actions, ct.c_int32)]
        if wait_targets is not None:
            targets = np.ascontiguousarray(wait_targets, dtype=np.float64)
            if targets.shape != (self.n_envs,) or not np.all(np.isfinite(targets) & ((targets == -1) | ((targets >= .2) & (targets <= 1)))):
                raise ValueError("wait_targets must contain -1 or HP fractions in [.2,1]")
            if not hasattr(self._lib, "sfld_rl_step_wait_targets"):
                raise ValueError("This native library does not support custom recovery waits")
            function = self._lib.sfld_rl_step_wait_targets
            arguments.append(self._ptr(targets, ct.c_double))
        self._check(function(*arguments,
            self._ptr(obs, ct.c_float), self._ptr(masks, ct.c_uint8), self._ptr(rewards, ct.c_float),
            self._ptr(terminated, ct.c_uint8), self._ptr(truncated, ct.c_uint8), self._ptr(metrics, ct.c_double),
            self._ptr(terminal_obs, ct.c_float), self._ptr(terminal_masks, ct.c_uint8)))
        infos = {name: metrics[:, i].copy() for i, name in enumerate(self.metric_names)}
        infos["terminal_observations"] = terminal_obs
        infos["terminal_action_masks"] = terminal_masks.astype(bool)
        if self.auto_reset and terminated.any():
            # Step metrics describe the finished episode; these slots have
            # already reset and need their new episode identities.
            self._refresh_info()
        else:
            # The native step already returned the current metrics. Keep
            # private views: callers can mutate infos without changing these.
            self._episode_info = {name: metrics[:, i] for i, name in enumerate(self.metric_names)}
        return obs, masks.astype(bool), rewards, terminated.astype(bool), truncated.astype(bool), infos

    def reward_totals(self):
        values = np.empty((self.n_envs, 3), dtype=np.float64)
        self._check(self._lib.sfld_rl_reward_totals(self._handle, self._ptr(values, ct.c_double)))
        return {name: values[:, i].copy() for i, name in enumerate(("gold_units", "gold_rewards", "epics"))}

    def teacher_actions(self, kind="survival", rules_path=None):
        choices = {"legacy": 0, "baseline": 0, "survival": 1, "rules": 2}
        if kind not in choices:
            raise ValueError("Unknown teacher kind")
        path = os.fsencode(Path(rules_path).resolve(strict=True)) if rules_path is not None else None
        actions = np.empty(self.n_envs, dtype=np.int32)
        self._check(self._lib.sfld_rl_teacher_actions(self._handle, choices[kind], path, self._ptr(actions, ct.c_int32)))
        return actions.astype(np.int64)

    def close(self):
        if getattr(self, "_handle", None):
            self._lib.sfld_rl_destroy(self._handle)
            self._handle = None
        if getattr(self, "_dll_directory", None) is not None:
            self._dll_directory.close()
            self._dll_directory = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()
