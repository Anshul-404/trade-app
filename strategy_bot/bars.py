"""Completed-candle model, validation and 09:15 IST session-aligned aggregation."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
LAST_15M_START = time(15, 15)


@dataclass(frozen=True, slots=True)
class Bar:
    """One completed candle. `volume is None` means unknown, never zero."""

    start: datetime  # timezone-aware IST
    minutes: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    complete: bool = True

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


def session_offset_minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute - (SESSION_OPEN.hour * 60 + SESSION_OPEN.minute)


def invalid_reason(bar: Bar, previous: Bar | None = None) -> str | None:
    """Return why a candle must be rejected, or None when it is usable."""
    if bar.start.tzinfo is None:
        return "naive_timestamp"
    values = (bar.open, bar.high, bar.low, bar.close)
    if not all(isinstance(v, (int, float)) and isfinite(v) and v > 0 for v in values):
        return "non_finite_or_non_positive_price"
    if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
        return "ohlc_inconsistent"
    if bar.volume is not None and (not isfinite(bar.volume) or bar.volume < 0):
        return "invalid_volume"
    local = bar.start.astimezone(IST)
    if local.weekday() >= 5:
        return "weekend_bar"
    if not (SESSION_OPEN <= local.time() < SESSION_CLOSE):
        return "outside_session"
    if bar.end.astimezone(IST).time() > SESSION_CLOSE and bar.end.astimezone(IST).date() == local.date():
        return "crosses_session_close"
    if session_offset_minutes(local) % bar.minutes:
        return "not_session_aligned"
    if previous is not None:
        if bar.start == previous.start:
            return "duplicate_timestamp"
        if bar.start < previous.end:
            return "non_chronological_or_overlapping"
    return None


def _parse_volume(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "" or str(raw).strip().lower() in {"nan", "none", "null"}:
        return None
    return float(raw)


def load_csv(path: Path | str, minutes: int, *, volume_available: bool = True):
    """Read a downloader CSV (epoch/open/high/low/close[/volume]).

    Returns (bars, audit). Invalid rows are excluded and listed in the audit;
    price data is never rewritten.
    """
    bars: list[Bar] = []
    audit: list[dict] = []
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        needed = {"epoch", "open", "high", "low", "close"}
        if not needed.issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: missing columns {sorted(needed - set(reader.fieldnames or []))}")
        has_volume = "volume" in (reader.fieldnames or [])
        for line, row in enumerate(reader, start=2):
            try:
                start = datetime.fromtimestamp(int(float(row["epoch"])), IST)
                bar = Bar(start, minutes, float(row["open"]), float(row["high"]),
                          float(row["low"]), float(row["close"]),
                          _parse_volume(row.get("volume")) if (has_volume and volume_available) else None)
            except (TypeError, ValueError, OverflowError, OSError):
                audit.append({"line": line, "reason": "unparseable_row"})
                continue
            reason = invalid_reason(bar, bars[-1] if bars else None)
            if reason:
                audit.append({"line": line, "time": start.isoformat(), "reason": reason})
                continue
            bars.append(bar)
    return bars, audit


def aggregate(bars: list[Bar], minutes: int, *, min_parts: int):
    """Aggregate finer completed bars into session-aligned `minutes` bars.

    A bucket with fewer than `min_parts` source bars is excluded and audited.
    The final 15:15 hourly bucket is naturally shorter than 60 minutes.
    """
    out: list[Bar] = []
    audit: list[dict] = []
    group: list[Bar] = []
    key = None

    def flush():
        if not group:
            return
        start = key
        session_end = start.replace(hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute)
        span = min(minutes, int((session_end - start).total_seconds() // 60))
        expected = span // group[0].minutes
        needed = min(min_parts, expected)
        if len(group) < needed:
            audit.append({"time": start.isoformat(), "reason": "incomplete_bucket",
                          "parts": len(group), "expected": expected})
            return
        volumes = [b.volume for b in group]
        out.append(Bar(start, span, group[0].open, max(b.high for b in group),
                       min(b.low for b in group), group[-1].close,
                       None if any(v is None for v in volumes) else float(sum(volumes)),
                       complete=len(group) == expected))

    for bar in bars:
        local = bar.start.astimezone(IST)
        offset = session_offset_minutes(local)
        bucket = local.replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute,
                               second=0, microsecond=0) + timedelta(minutes=(offset // minutes) * minutes)
        if bucket != key:
            flush()
            group, key = [], bucket
        group.append(bar)
    flush()
    return out, audit
