"""Thread-safe candles with data provenance and integrity diagnostics.

Historical Dhan OHLC is authoritative for historical bars; a Ticker feed contains
LTP only, so its OHLC candles are sampled for visualization, not backtesting.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from math import isfinite
from threading import RLock
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SUPPORTED_MINUTES = (1, 2, 5, 10, 15, 60)


def time_bucket(timestamp: int, minutes: int) -> int:
    """Return candle start, aligned with the NSE 09:15 IST session."""
    if minutes not in SUPPORTED_MINUTES:
        raise ValueError(f"Unsupported timeframe: {minutes}")
    local = datetime.fromtimestamp(timestamp, IST)
    session_start = local.replace(hour=9, minute=15, second=0, microsecond=0)
    offset = int((local - session_start).total_seconds())
    return int((session_start + timedelta(
        seconds=(offset // (minutes * 60)) * minutes * 60
    )).timestamp())


def _known_volume(item: dict) -> float | None:
    """Zero is real only when returned by the historical data provider."""
    value = item.get("volume")
    if value is None:
        return None
    parsed = float(value)
    return parsed if isfinite(parsed) and parsed >= 0 else None


class CandleStore:
    def __init__(self, max_bars: int = 6000, volume_supported: bool = True):
        self._bars: dict[int, dict] = {}
        self._lock = RLock()
        self.max_bars = max_bars
        # Spot indexes do not have their own traded volume.
        self.volume_supported = volume_supported
        self.version = 0
        self.last_price: float | None = None
        self.last_tick_epoch: int | None = None

    def seed(self, history: list[dict]) -> int:
        """Load provider historical OHLC; don't overwrite already-sampled live bars."""
        with self._lock:
            count = 0
            for item in sorted(history, key=lambda c: int(c["time"])):
                try:
                    ts = int(item["time"])
                    o, h, l, c = (float(item[key]) for key in ("open", "high", "low", "close"))
                    if not all(map(isfinite, (o, h, l, c))) or l > min(o, c) or h < max(o, c):
                        continue
                    minute = time_bucket(ts, 1)
                    if minute in self._bars:
                        continue
                    self._bars[minute] = {
                        "time": minute, "open": o, "high": h, "low": l,
                        "close": c,
                        "volume": _known_volume(item) if self.volume_supported else None,
                        "source": "historical",
                    }
                    count += 1
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
            self._trim()
            if count:
                self.version += 1
            return count

    def on_tick(self, price: float, timestamp: int) -> bool:
        """Apply last traded price by local receipt time (NOT exchange event time)."""
        if not isfinite(price) or price <= 0:
            return False
        minute = time_bucket(timestamp, 1)
        with self._lock:
            if self.last_tick_epoch is not None and timestamp < self.last_tick_epoch:
                return False
            if self._bars and minute < max(self._bars):
                return False
            bar = self._bars.get(minute)
            if bar:
                bar["high"] = max(bar["high"], price)
                bar["low"] = min(bar["low"], price)
                bar["close"] = price
                if bar["source"] == "historical":
                    bar["source"] = "mixed"
                # Ticker packets don't supply volume. Even historical volume for
                # a still-updating candle is an incomplete observation.
                bar["volume"] = None
            else:
                self._bars[minute] = {
                    "time": minute, "open": price, "high": price,
                    "low": price, "close": price, "volume": None,
                    "source": "live_sampled",
                }
            self.last_price = price
            self.last_tick_epoch = timestamp
            self.version += 1
            self._trim()
            return True

    def _trim(self) -> None:
        if len(self._bars) > self.max_bars:
            for ts in sorted(self._bars)[:len(self._bars) - self.max_bars]:
                del self._bars[ts]

    def snapshot(self, minutes: int, limit: int = 500) -> list[dict]:
        if minutes not in SUPPORTED_MINUTES:
            raise ValueError("Invalid timeframe")
        with self._lock:
            source = [dict(self._bars[t]) for t in sorted(self._bars)]
        result: list[dict] = []
        for bar in source:
            start = time_bucket(bar["time"], minutes)
            if result and result[-1]["time"] == start:
                agg = result[-1]
                agg["high"] = max(agg["high"], bar["high"])
                agg["low"] = min(agg["low"], bar["low"])
                agg["close"] = bar["close"]
                # Unknown volume in any input -> unknown aggregate volume.
                agg["volume"] = (
                    agg["volume"] + bar["volume"]
                    if agg["volume"] is not None and bar["volume"] is not None
                    else None
                )
                if agg["source"] != bar["source"]:
                    agg["source"] = "mixed"
            else:
                result.append({**bar, "time": start})
        return result[-limit:]

    def quality_summary(self) -> dict:
        """Useful warnings rather than a claim of exchange-grade completeness."""
        with self._lock:
            bars = [dict(self._bars[t]) for t in sorted(self._bars)]
        counts = Counter(bar["source"] for bar in bars)
        unknown_volume = sum(bar["volume"] is None for bar in bars)
        gaps = []
        large_ranges = []
        for previous, bar in zip(bars, bars[1:]):
            previous_local = datetime.fromtimestamp(previous["time"], IST)
            current_local = datetime.fromtimestamp(bar["time"], IST)
            interval_mins = (bar["time"] - previous["time"]) // 60
            if (
                previous_local.date() == current_local.date()
                and (9, 15) <= (previous_local.hour, previous_local.minute) <= (15, 29)
                and (9, 15) <= (current_local.hour, current_local.minute) <= (15, 29)
                and interval_mins > 1
            ):
                gaps.append({
                    "after_ist": previous_local.isoformat(),
                    "before_ist": current_local.isoformat(),
                    "missing_minutes": interval_mins - 1,
                })
        for bar in bars:
            if bar["open"] > 0 and (bar["high"] - bar["low"]) / bar["open"] >= 0.01:
                large_ranges.append({
                    "time_ist": datetime.fromtimestamp(bar["time"], IST).isoformat(),
                    "range_pct": round((bar["high"] - bar["low"]) / bar["open"] * 100, 3),
                    "source": bar["source"],
                })
        return {
            "symbol_data": "historical provider OHLC + sampled live LTP",
            "live_timestamp_source": "computer_receipt_time_not_exchange_timestamp",
            "live_candles_suitable_for_backtesting": False,
            "volume_status": "not_applicable_index" if not self.volume_supported else "unavailable_for_live_ticker_candles",
            "one_minute_candles": len(bars),
            "historical_bars": counts["historical"],
            "sampled_live_bars": counts["live_sampled"],
            "mixed_bars": counts["mixed"],
            "unknown_volume_bars": unknown_volume,
            "observed_intraday_missing_minutes": sum(g["missing_minutes"] for g in gaps),
            "gap_examples": gaps[:10],
            "large_1m_ranges_at_least_1pct": large_ranges[:10],
            "notes": [
                "Observed gaps are diagnostic only, not proof of missing provider data.",
                "Large ranges are review candidates, not necessarily bad candles.",
                "Use verified historical OHLC before signals/backtests; live LTP sampling can miss highs/lows.",
            ],
        }
