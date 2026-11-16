"""Strict strategy configuration.

Every parameter the strategy specification leaves "unfinalized" (section 12)
must be written explicitly in the TOML file. There are no hidden defaults in
code: a missing or unknown key is an error, so each backtest documents exactly
what it assumed.
"""
from __future__ import annotations

import tomllib
from datetime import time
from pathlib import Path
from types import SimpleNamespace

SPEC_MIN_RR = 3.0  # fixed by the strategy specification; config may only raise it

SCHEMA: dict[str, dict[str, type | tuple]] = {
    "meta": {"name": str, "status": str},
    "strategy": {"min_rr": float},
    "daily": {"trend_lookback_days": int, "min_directional_closes": int,
              "flip_lookback_days": int, "initial_bias": str},
    "hourly": {"min_body_ratio": float, "min_close_location": float,
               "breakout_lookback_bars": int, "allow_short_session_bar": bool},
    "volume": {"required": bool, "window_bars": int, "min_relative_multiple": float},
    "structure": {"swing_left": int, "swing_right": int, "mode": str, "window_bars": int},
    "retest": {"tolerance_points": float, "pullback_fraction_of_trigger_range": float, "expiry_bars": int, "min_confirm_body_ratio": float},
    "levels": {"swing_left": int, "swing_right": int, "target_sources": list,
               "cluster_tolerance_points": float, "zone_edge": str},
    "entry": {"fill": str},
    "hard_stop": {"buffer_points": float, "same_bar_collision": str},
    "risk": {"max_daily_loss_r": float},
    "reentry": {"max_attempts": int, "require_new_trigger": bool},
    "holding": {"max_sessions": int, "forced_exit_time": str, "friday_carry_allowed": bool,
                "friday_cutoff_time": str, "daily_flip_action": str},
    "costs": {"slippage_points_per_side": float, "round_trip_cost_points": float},
    "data": {"min_1m_bars_per_15m": int, "volume_available": bool},
    "execution": {"mode": str},
    "backtest": {"out_of_sample_start": str},
}

CHOICES = {
    ("daily", "initial_bias"): {"NEUTRAL"},
    ("structure", "mode"): {"mandatory", "non_contradictory", "record_only"},
    ("levels", "zone_edge"): {"near", "mid", "far"},
    ("entry", "fill"): {"next_open"},
    ("hard_stop", "same_bar_collision"): {"stop_first", "target_first"},
    ("holding", "daily_flip_action"): {"exit_next_open", "hold"},
    ("execution", "mode"): {"paper"},
}
SOURCES = {"swing_60m", "swing_1d", "prior_day", "prior_week"}


class ConfigError(ValueError):
    pass


def _clock(text: str, where: str) -> time:
    try:
        hour, minute = text.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        raise ConfigError(f"{where}: expected HH:MM, got {text!r}") from None


def from_dict(raw: dict) -> SimpleNamespace:
    unknown = set(raw) - set(SCHEMA)
    if unknown:
        raise ConfigError(f"unknown sections: {sorted(unknown)}")
    cfg = SimpleNamespace()
    for section, keys in SCHEMA.items():
        if section not in raw:
            raise ConfigError(f"missing section [{section}] - it must be set explicitly")
        body = raw[section]
        extra = set(body) - set(keys)
        missing = set(keys) - set(body)
        if extra:
            raise ConfigError(f"[{section}] unknown keys: {sorted(extra)}")
        if missing:
            raise ConfigError(f"[{section}] missing keys (no defaults exist): {sorted(missing)}")
        values = {}
        for key, kind in keys.items():
            value = body[key]
            if kind is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
                raise ConfigError(f"[{section}] {key}: expected {kind.__name__}, got {value!r}")
            allowed = CHOICES.get((section, key))
            if allowed and value not in allowed:
                raise ConfigError(f"[{section}] {key}: {value!r} not supported; choose from {sorted(allowed)}")
            values[key] = value
        setattr(cfg, section, SimpleNamespace(**values))

    if cfg.strategy.min_rr < SPEC_MIN_RR:
        raise ConfigError("strategy.min_rr may not be below the specified 3.0")
    if cfg.daily.flip_lookback_days != 3:
        raise ConfigError("daily.flip_lookback_days is fixed at 3 by the strategy rules")
    if not 4 <= cfg.daily.trend_lookback_days <= 5:
        raise ConfigError("daily.trend_lookback_days must be 4 or 5 (rule: recent 4-5 sessions)")
    if not 1 <= cfg.daily.min_directional_closes <= cfg.daily.trend_lookback_days - 1:
        raise ConfigError("daily.min_directional_closes out of range for the lookback")
    for section, key in (("hourly", "min_body_ratio"), ("hourly", "min_close_location"),
                         ("retest", "min_confirm_body_ratio")):
        if not 0.0 <= getattr(getattr(cfg, section), key) <= 1.0:
            raise ConfigError(f"[{section}] {key} must be within 0..1")
    for section, key in (("hourly", "breakout_lookback_bars"), ("volume", "window_bars"),
                         ("structure", "swing_left"), ("structure", "swing_right"),
                         ("levels", "swing_left"), ("levels", "swing_right"),
                         ("retest", "expiry_bars"), ("reentry", "max_attempts"),
                         ("holding", "max_sessions"), ("data", "min_1m_bars_per_15m")):
        if getattr(getattr(cfg, section), key) < 1:
            raise ConfigError(f"[{section}] {key} must be >= 1")
    for section, key in (("retest", "tolerance_points"), ("levels", "cluster_tolerance_points"),
                         ("hard_stop", "buffer_points"), ("costs", "slippage_points_per_side"),
                         ("costs", "round_trip_cost_points"), ("volume", "min_relative_multiple")):
        if getattr(getattr(cfg, section), key) < 0:
            raise ConfigError(f"[{section}] {key} must be >= 0")
    if not 0.0 <= cfg.retest.pullback_fraction_of_trigger_range <= 0.5:
        raise ConfigError("retest.pullback_fraction_of_trigger_range must be within 0..0.5")
    if cfg.risk.max_daily_loss_r <= 0:
        raise ConfigError("risk.max_daily_loss_r must be > 0")
    if not cfg.levels.target_sources or set(cfg.levels.target_sources) - SOURCES:
        raise ConfigError(f"levels.target_sources must be a non-empty subset of {sorted(SOURCES)}")
    if cfg.volume.required and not cfg.data.volume_available:
        raise ConfigError("volume.required=true needs data.volume_available=true (index data has no volume)")
    cfg.holding.forced_exit = _clock(cfg.holding.forced_exit_time, "holding.forced_exit_time")
    cfg.holding.friday_cutoff = _clock(cfg.holding.friday_cutoff_time, "holding.friday_cutoff_time")
    return cfg


def load(path: Path | str) -> SimpleNamespace:
    with Path(path).open("rb") as handle:
        return from_dict(tomllib.load(handle))
