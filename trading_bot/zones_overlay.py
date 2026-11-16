"""Read-only order block / FVG zones from the existing tested engine.

Analysis only receives complete bars. OB zones start at the *break* that
confirmed them (not their older source candle). FVG zones start after the
third candle closes. Data provenance is exposed for safety.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from time import time
from typing import Any

from .candles import SUPPORTED_MINUTES, time_bucket


def _epoch(value: Any) -> int:
    return int(value.timestamp())


def _direction(value: Any) -> str:
    direction = str(getattr(value, "value", value)).upper()
    if direction not in ("BULLISH", "BEARISH"):
        raise ValueError("Invalid direction")
    return direction


def _zone_prices(zone: Any) -> tuple[float, float]:
    upper, lower = float(zone.top), float(zone.bottom)
    if not all(isfinite(x) and x > 0 for x in (upper, lower)) or lower >= upper:
        raise ValueError("Invalid zone price range")
    return upper, lower


def _quality(bars: list[dict], first: int, last: int) -> str:
    sources = (bars[i].get("source") for i in range(first, last + 1))
    return ("historical_ohlc" if all(s == "historical" for s in sources)
            else "indicative_sampled_ltp")


def analyze_zones(
    bars: list[dict], *, timeframe: int, symbol: str,
    now_epoch: int | None = None, structure_detector: Any = None,
    ob_detector: Any = None, fvg_detector: Any = None,
    candle_type: Any = None, max_zones: int = 40,
) -> dict:
    if timeframe not in SUPPORTED_MINUTES:
        raise ValueError("Unsupported timeframe")
    if max_zones < 1 or max_zones > 200:
        raise ValueError("max_zones must be between 1 and 200")
    now_epoch = int(time()) if now_epoch is None else int(now_epoch)
    current_bucket = time_bucket(now_epoch, timeframe)
    complete = sorted((dict(b) for b in bars if int(b["time"]) < current_bucket),
                      key=lambda bar: int(bar["time"]))
    result = {
        "symbol": symbol, "timeframe": timeframe,
        "bars_analyzed": len(complete),
        "incomplete_bars_excluded": len(bars) - len(complete),
        "data_note": "Zones on sampled live LTP candles are indicative; no trading decisions or orders.",
        "status_note": "Mitigation/invalidation describes the latest closed bar, not historic zone lifetime.",
        "order_blocks": [], "fair_value_gaps": [],
        "total_order_blocks": 0, "total_fair_value_gaps": 0,
        "skipped_order_blocks": 0, "skipped_fair_value_gaps": 0,
    }
    if len(complete) < 6:
        return result

    if candle_type is None:
        from backend.models.market_data import Candle
        candle_type = Candle
    if structure_detector is None:
        from trading_engine.bos_choch.structure_detector import detect_market_structure
        structure_detector = detect_market_structure
    if ob_detector is None:
        from trading_engine.order_blocks.order_block_detector import detect_order_blocks
        ob_detector = detect_order_blocks
    if fvg_detector is None:
        from trading_engine.fair_value_gaps.fvg_detector import detect_fair_value_gaps
        fvg_detector = detect_fair_value_gaps

    candles = [candle_type(
        timestamp=datetime.fromtimestamp(int(b["time"]), timezone.utc),
        open=float(b["open"]), high=float(b["high"]),
        low=float(b["low"]), close=float(b["close"]),
        volume=float(b["volume"]) if b.get("volume") is not None else 0.0,
    ) for b in complete]
    breaks = structure_detector(candles, left_bars=2, right_bars=2, initial_trend="NEUTRAL")
    blocks = ob_detector(candles, breaks)
    gaps = fvg_detector(candles)
    end_time = int(complete[-1]["time"])

    # Accept a zone only if its source and confirmation indexes and timestamps
    # agree with the bars actually supplied to the model.
    for block in blocks:
        try:
            origin, confirm = int(block.index), int(block.break_index)
            if not 0 <= origin < confirm < len(complete):
                raise ValueError("Out-of-range order block indexes")
            if (_epoch(block.timestamp) != int(complete[origin]["time"]) or
                    _epoch(block.break_timestamp) != int(complete[confirm]["time"])):
                raise ValueError("Order block timestamp mismatch")
            high, low = _zone_prices(block)
            direction = _direction(block.block_type)
            result["order_blocks"].append({
                "type": "ORDER_BLOCK", "direction": direction,
                "start_time": int(complete[confirm]["time"]),
                "origin_time": int(complete[origin]["time"]),
                "end_time": end_time, "top": high, "bottom": low,
                "status": str(getattr(block, "mitigation_status", "UNKNOWN")),
                "invalidated": bool(getattr(block, "invalidated", False)),
                "strength": str(getattr(block, "strength", "UNKNOWN")),
                "quality": _quality(complete, origin, confirm),
            })
        except (ValueError, TypeError, AttributeError, OverflowError):
            result["skipped_order_blocks"] += 1

    for gap in gaps:
        try:
            confirm = int(gap.index)
            if not 2 <= confirm < len(complete):
                raise ValueError("Out-of-range FVG index")
            if (_epoch(gap.timestamp) != int(complete[confirm]["time"]) or
                    _epoch(gap.third_candle_timestamp) != int(complete[confirm]["time"]) or
                    _epoch(gap.first_candle_timestamp) != int(complete[confirm-2]["time"])):
                raise ValueError("FVG timestamp mismatch")
            high, low = _zone_prices(gap)
            direction = _direction(gap.fvg_type)
            result["fair_value_gaps"].append({
                "type": "FVG", "direction": direction,
                "start_time": int(complete[confirm]["time"]),
                "end_time": end_time, "top": high, "bottom": low,
                "status": str(getattr(gap, "mitigation_status", "UNKNOWN")),
                "strength": str(getattr(gap, "strength", "UNKNOWN")),
                "quality": _quality(complete, confirm-2, confirm),
            })
        except (ValueError, TypeError, AttributeError, OverflowError):
            result["skipped_fair_value_gaps"] += 1

    for key, total_key in (("order_blocks", "total_order_blocks"),
                           ("fair_value_gaps", "total_fair_value_gaps")):
        result[key].sort(key=lambda z: (z["start_time"], z["top"], z["bottom"]))
        result[total_key] = len(result[key])
        result[key] = result[key][-max_zones:]
    return result
