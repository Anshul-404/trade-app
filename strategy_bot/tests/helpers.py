"""Synthetic candle builders for deterministic engine tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from strategy_bot import config as config_module
from strategy_bot.bars import IST, Bar

BASELINE = Path(__file__).resolve().parents[1] / "configs" / "experiment_baseline.toml"


def cfg(**overrides):
    import tomllib
    raw = tomllib.loads(BASELINE.read_text(encoding="utf-8"))
    for dotted, value in overrides.items():
        section, key = dotted.split("__")
        raw[section][key] = value
    return config_module.from_dict(raw)


def no_structure(bars, left, right):
    return []


def bar_at(day: str, hhmm: str, open_: float, close: float, volume: float | None = 100.0, wick: float = 2.0) -> Bar:
    start = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=IST)
    return Bar(start, 15, open_, max(open_, close) + wick, min(open_, close) - wick, close, volume)


def path_bars(day: str, start_price: float, closes: list[float], first: str = "09:15",
              volume: float | None = 100.0) -> list[Bar]:
    moment = datetime.fromisoformat(f"{day}T{first}:00")
    out, price = [], start_price
    for close in closes:
        out.append(bar_at(day, moment.strftime("%H:%M"), price, close, volume))
        price = close
        moment += timedelta(minutes=15)
    return out


def linear(start: float, end: float, n: int) -> list[float]:
    return [round(start + (end - start) * (i + 1) / n, 2) for i in range(n)]


def base_history() -> list[Bar]:
    """Seven sessions: a peak (resistance 24452), a two-day fall, four up days.

    After the 7th session the bias is BULLISH (3-day close break + 5-day trend).
    The only overhead structure is the day-1 hourly swing high / prior-week high.
    """
    bars = []
    bars += path_bars("2026-06-01", 24380, linear(24380, 24450, 12) + linear(24450, 24420, 13))
    bars += path_bars("2026-06-02", 24420, linear(24420, 24200, 25))
    bars += path_bars("2026-06-03", 24200, linear(24200, 24000, 25))
    price = 24000
    for day in ("2026-06-04", "2026-06-05", "2026-06-08", "2026-06-09"):
        bars += path_bars(day, price, linear(price, price + 40, 25))
        price += 40
    return bars  # last close 24160


def trigger_day(after: list[float]) -> list[Bar]:
    """Wed 2026-06-10: three quiet hours, a strong 12:15 1H breakout, then `after`."""
    day = "2026-06-10"
    bars = path_bars(day, 24160, linear(24160, 24170, 12))
    bars += path_bars(day, 24170, [24190, 24215, 24235, 24250], first="12:15", volume=500.0)
    bars += path_bars(day, 24250, after, first="13:15")
    return bars
