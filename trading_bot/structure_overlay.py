"""Read-only BOS / CHoCH overlay for the existing OrderBlock detector.

Only finished chart bars reach the detector. The Dhan ticker feed provides LTP
rather than exchange-verified OHLC, so sampled-live events are indicative.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from time import time
from typing import Any

from .candles import SUPPORTED_MINUTES, time_bucket


def analyze_structure(
    bars: list[dict],
    *,
    timeframe: int,
    symbol: str,
    now_epoch: int | None = None,
    detector: Any = None,
    candle_type: Any = None,
    left_bars: int = 2,
    right_bars: int = 2,
) -> dict:
    """Convert chart bars into confirmed events; never use a forming candle.

    detector/candle_type are injectable for isolated tests. Production defaults
    import the user's existing, tested market structure module and Candle model.
    """
    if timeframe not in SUPPORTED_MINUTES:
        raise ValueError("Unsupported timeframe")
    if left_bars < 1 or right_bars < 1:
        raise ValueError("Swing confirmation bars must be positive")

    if now_epoch is None:
        now_epoch = int(time())
    current_bucket = time_bucket(int(now_epoch), timeframe)
    complete = [dict(b) for b in bars if int(b["time"]) < current_bucket]
    complete.sort(key=lambda b: int(b["time"]))

    response = {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_analyzed": len(complete),
        "incomplete_bars_excluded": len(bars) - len(complete),
        "engine": "trading_engine.bos_choch.structure_detector.detect_market_structure",
        "data_note": "Live LTP-sampled OHLC is indicative, not exchange-verified; do not trade from these markers alone.",
        "events": [],
        "skipped_engine_events": 0,
    }
    if len(complete) < left_bars + right_bars + 2:
        return response

    if detector is None:
        from trading_engine.bos_choch.structure_detector import detect_market_structure
        detector = detect_market_structure
    if candle_type is None:
        from backend.models.market_data import Candle
        candle_type = Candle

    candles = []
    for bar in complete:
        # Structure detection uses only OHLC, not volume. The legacy Candle
        # model expects a numeric volume. Unknown volume is 0 internally only;
        # the public chart/API retains null for volume when unavailable.
        volume = bar.get("volume")
        candles.append(candle_type(
            timestamp=datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc),
            open=float(bar["open"]), high=float(bar["high"]),
            low=float(bar["low"]), close=float(bar["close"]),
            volume=float(volume) if volume is not None else 0.0,
        ))

    events = detector(candles, left_bars=left_bars,
                      right_bars=right_bars, initial_trend="NEUTRAL")
    for event in events:
        try:
            index = int(event.index)
            if not 0 <= index < len(complete):
                raise ValueError("out-of-range event index")
            bar_time = int(complete[index]["time"])
            if int(event.timestamp.timestamp()) != bar_time:
                raise ValueError("event timestamp does not match breakout candle")
            swing_index = getattr(event, "swing_index", None)
            if swing_index is not None:
                swing_index = int(swing_index)
                if not 0 <= swing_index < index:
                    raise ValueError("out-of-range swing index")
                swing_timestamp = getattr(event, "swing_timestamp", None)
                if (swing_timestamp is not None and
                        int(swing_timestamp.timestamp()) != int(complete[swing_index]["time"])):
                    raise ValueError("swing timestamp does not match source candle")
            break_type = str(getattr(event.break_type, "value", event.break_type))
            direction = str(getattr(event.direction, "value", event.direction))
            if break_type.upper() not in {"BOS", "CHOCH"} or direction not in {"BULLISH", "BEARISH"}:
                raise ValueError("unsupported event kind")
            level = float(event.broken_level)
            if not isfinite(level):
                raise ValueError("invalid broken level")
            source = complete[index].get("source", "unknown")
            response["events"].append({
                "time": bar_time,
                "index": index,
                "break_type": "CHoCH" if break_type.upper() == "CHOCH" else "BOS",
                "direction": direction,
                "broken_level": level,
                "swing_time": int(complete[swing_index]["time"]) if swing_index is not None else None,
                "previous_trend": str(getattr(event, "previous_trend", "NEUTRAL")),
                "source": source,
                "quality": "historical_ohlc" if source == "historical" else "indicative_sampled_ltp",
            })
        except (AttributeError, TypeError, ValueError, OverflowError):
            # Fail closed: never place an event on a guessed time/bar.
            response["skipped_engine_events"] += 1
    response["events"].sort(key=lambda e: (e["time"], e["direction"], e["break_type"]))
    return response
