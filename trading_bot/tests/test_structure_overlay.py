"""Isolated integration tests; independent of the user's original engine files."""
from datetime import datetime, timezone
from types import SimpleNamespace

from trading_bot.structure_overlay import analyze_structure


def candle(time, o=100, h=105, l=95, c=101, source="historical"):
    return {"time": time, "open": o, "high": h, "low": l, "close": c,
            "volume": None, "source": source}


class FakeCandle:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_mapping_uses_exact_breakout_candle_and_skips_current_bar():
    # These timestamps align to 09:15 IST session start for one-minute candles.
    start = int(datetime(2026, 11, 13, 3, 45, tzinfo=timezone.utc).timestamp())
    bars = [candle(start + i * 60) for i in range(9)]
    captured = {}

    def detector(cs, *, left_bars, right_bars, initial_trend):
        captured["count"] = len(cs)
        captured["trend"] = initial_trend
        assert all(x.volume == 0 for x in cs)  # analysis-only placeholder
        return [SimpleNamespace(index=7, timestamp=cs[7].timestamp, break_type="BOS",
                                direction="BULLISH", broken_level=105.,
                                previous_trend="NEUTRAL", swing_index=2,
                                swing_timestamp=cs[2].timestamp)]

    result = analyze_structure(bars, timeframe=1, symbol="NIFTY NOV FUT",
                               now_epoch=start + 8 * 60 + 10,
                               detector=detector, candle_type=FakeCandle)
    assert captured == {"count": 8, "trend": "NEUTRAL"}
    assert result["incomplete_bars_excluded"] == 1
    assert result["events"][0]["time"] == start + 7 * 60
    assert result["events"][0]["swing_time"] == start + 2 * 60
    assert result["events"][0]["quality"] == "historical_ohlc"


def test_sampled_event_is_indicative_and_choch_works():
    start = int(datetime(2026, 11, 13, 3, 45, tzinfo=timezone.utc).timestamp())
    bars = [candle(start + i * 60, source="live_sampled") for i in range(8)]
    def detector(cs, **kwargs):
        return [SimpleNamespace(index=6, timestamp=cs[6].timestamp,
                                break_type="CHoCH", direction="BEARISH",
                                broken_level=95., swing_index=2,
                                swing_timestamp=cs[2].timestamp)]
    result = analyze_structure(bars, timeframe=1, symbol="NIFTY NOV FUT",
                               now_epoch=start + 8 * 60,
                               detector=detector, candle_type=FakeCandle)
    assert result["events"][0]["break_type"] == "CHoCH"
    assert result["events"][0]["quality"] == "indicative_sampled_ltp"


def test_mismatched_timestamp_fails_closed():
    start = int(datetime(2026, 11, 13, 3, 45, tzinfo=timezone.utc).timestamp())
    bars = [candle(start + i * 60) for i in range(8)]
    def detector(cs, **kwargs):
        return [SimpleNamespace(index=6, timestamp=cs[5].timestamp,
                                break_type="BOS", direction="BULLISH",
                                broken_level=100.)]
    result = analyze_structure(bars, timeframe=1, symbol="NIFTY NOV FUT",
                               now_epoch=start + 8 * 60,
                               detector=detector, candle_type=FakeCandle)
    assert result["events"] == []
    assert result["skipped_engine_events"] == 1


def test_too_few_closed_candles_does_not_call_engine():
    start = int(datetime(2026, 11, 13, 3, 45, tzinfo=timezone.utc).timestamp())
    def should_not_call(*args, **kwargs):
        raise AssertionError("called detector with insufficient candles")
    result = analyze_structure([candle(start)], timeframe=1, symbol="NIFTY",
                               now_epoch=start + 50, detector=should_not_call)
    assert result["events"] == []
    assert result["bars_analyzed"] == 0
