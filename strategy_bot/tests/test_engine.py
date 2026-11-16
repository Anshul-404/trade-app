import pytest

from strategy_bot.backtest import metrics, run
from strategy_bot.engine import StrategyEngine
from strategy_bot.levels import Level

from .helpers import base_history, bar_at, cfg, no_structure, trigger_day

RETEST_CONFIRM = [24228, 24256]          # 13:15 pullback into the zone, 13:30 bullish confirmation
RALLY = [24300, 24360, 24420, 24460]


def events_of(events, kind):
    return [e for e in events if e["type"] == kind]


def test_bias_needs_three_day_break_and_trend():
    engine, events = run(base_history(), cfg(), no_structure)
    daily = events_of(events, "daily")
    assert [d["bias"] for d in daily[:6]] == ["NEUTRAL"] * 6
    assert daily[6]["bias"] == "BULLISH" and daily[6]["three_day_flip"] == "BULLISH"
    assert engine.trades == []


def test_long_trade_reaches_structural_target():
    engine, events = run(base_history() + trigger_day(RETEST_CONFIRM + RALLY), cfg(), no_structure)
    trigger = events_of(events, "trigger")[0]
    assert trigger["midpoint"] == pytest.approx((24252 + 24168) / 2)
    signal = events_of(events, "signal")[0]
    assert signal["mode"] == "PAPER_SIGNAL_NOT_A_LIVE_ORDER"
    assert signal["stop"] == trigger["midpoint"] and signal["target"] == 24452
    assert signal["planned_rr"] >= 3.0
    (trade,) = engine.trades
    assert trade["exit_reason"] == "target"
    assert trade["entry"] == 24257 and trade["exit"] == 24452      # next open + 1 point slippage
    assert trade["net_points"] == 194 and trade["net_r"] == pytest.approx(194 / 47, abs=1e-3)


def test_nearer_obstacle_makes_setup_a_skip_not_a_stretched_target():
    engine = StrategyEngine(cfg(), no_structure)
    for bar in base_history():
        engine.on_bar(bar)
    blocker = base_history()[0]
    engine.book.add(Level(24330.0, "R", "swing_60m", blocker.start, blocker.end))
    events = []
    for bar in trigger_day(RETEST_CONFIRM + RALLY):
        events += engine.on_bar(bar)
    skip = [e for e in events_of(events, "skip") if e["reason"] == "rr_below_minimum"][0]
    assert skip["target"] == 24330 and skip["next_level_beyond"] == 24452 and skip["planned_rr"] < 3
    assert engine.trades == [] and not events_of(events, "signal")


def test_no_target_means_skip():
    engine = StrategyEngine(cfg(), no_structure)
    for bar in base_history():
        engine.on_bar(bar)
    engine.book.levels = [lv for lv in engine.book.levels if lv.side != "R" or lv.price < 24200]
    events = []
    for bar in trigger_day(RETEST_CONFIRM + RALLY):
        events += engine.on_bar(bar)
    assert [e["reason"] for e in events_of(events, "skip")] == ["no_causal_structural_target"]


def test_midpoint_close_exit_fills_next_open():
    engine, _ = run(base_history() + trigger_day(RETEST_CONFIRM + [24215, 24200, 24195]), cfg(), no_structure)
    trade = engine.trades[0]
    assert trade["exit_reason"] == "midpoint_close_exit"
    assert trade["exit"] == 24199 and trade["net_r"] < 0       # 14:15 open 24200 minus slippage


def test_hard_stop_and_gap():
    engine, _ = run(base_history() + trigger_day(RETEST_CONFIRM + [24180]), cfg(), no_structure)
    assert engine.trades[0]["exit_reason"] == "hard_stop" and engine.trades[0]["exit"] == 24189
    gap = base_history() + trigger_day(RETEST_CONFIRM + [24250] * 8)   # hold into the close
    gap.append(bar_at("2026-06-11", "09:15", 24100, 24110))            # opens far below the hard stop
    engine, _ = run(gap, cfg(), no_structure)
    assert engine.trades[0]["exit_reason"] == "hard_stop_gap" and engine.trades[0]["exit"] == 24099


def test_setup_invalidated_by_close_through_midpoint_before_entry():
    _, events = run(base_history() + trigger_day([24205, 24256]), cfg(), no_structure)
    assert events_of(events, "skip")[0]["reason"] == "midpoint_closed_before_entry"
    assert not events_of(events, "signal")


def test_weak_volume_or_missing_volume_blocks_trigger():
    quiet = base_history() + [b.__class__(b.start, 15, b.open, b.high, b.low, b.close, 100.0)
                              for b in trigger_day(RETEST_CONFIRM)]
    engine, events = run(quiet, cfg(), no_structure)
    assert not events_of(events, "trigger") and engine.counters["h1_reject:low_relative_volume"] == 1
    unknown = [b.__class__(b.start, 15, b.open, b.high, b.low, b.close, None)
               for b in base_history() + trigger_day(RETEST_CONFIRM)]
    engine, events = run(unknown, cfg(), no_structure)
    assert not events_of(events, "trigger") and engine.counters["h1_reject:volume_unavailable"] == 1


def test_contradicting_structure_blocks_trigger():
    def bearish(bars, left, right):
        return [("BOS", "BEARISH", 1.0, bars[-1].start)]
    engine, events = run(base_history() + trigger_day(RETEST_CONFIRM), cfg(), bearish)
    assert not events_of(events, "trigger") and engine.counters["h1_reject:structure_contradicts"] >= 1


def test_runs_with_the_projects_real_bos_choch_detector():
    engine, events = run(base_history() + trigger_day(RETEST_CONFIRM + RALLY), cfg())
    assert "structure" in events_of(events, "trigger")[0]
    assert len(engine.trades) == 1


def test_no_lookahead_prefix_invariance():
    bars = base_history() + trigger_day(RETEST_CONFIRM + RALLY)
    _, full = run(bars, cfg(), no_structure)
    for cut in (len(bars) - 1, len(bars) - 4, len(bars) - 7, 180):
        engine = StrategyEngine(cfg(), no_structure)
        partial = [e for bar in bars[:cut] for e in engine.on_bar(bar)]
        assert partial == full[:len(partial)]


def test_rejects_bad_and_duplicate_candles():
    engine = StrategyEngine(cfg(), no_structure)
    first = bar_at("2026-06-01", "09:15", 24380, 24385)
    assert engine.on_bar(first) == []
    assert engine.on_bar(first)[0]["reason"] == "duplicate_timestamp"
    assert engine.on_bar(bar_at("2026-06-01", "09:20", 24385, 24390))[0]["reason"] == "not_session_aligned"
    broken = first.__class__(first.end, 15, 24385, 24380, 24390, 24388, 10.0)
    assert engine.on_bar(broken)[0]["reason"] == "ohlc_inconsistent"
    assert engine.on_bar(bar_at("2026-06-01", "09:45", 24385, 24390))[0]["type"] == "data_gap"


def test_metrics_shape():
    engine, _ = run(base_history() + trigger_day(RETEST_CONFIRM + RALLY), cfg(), no_structure)
    summary = metrics(engine.trades)
    assert summary["trades"] == 1 and summary["win_rate"] == 1.0 and summary["by_year"]["2026"]["trades"] == 1
    assert metrics([]) == {"trades": 0}


def test_short_side_mirrors_long():
    def flip(b):
        m = 49000.0
        return b.__class__(b.start, 15, m - b.open, m - b.low, m - b.high, m - b.close, b.volume)
    engine, events = run([flip(b) for b in base_history() + trigger_day(RETEST_CONFIRM + RALLY)], cfg(), no_structure)
    signal = events_of(events, "signal")[0]
    assert signal["direction"] == "BEARISH" and signal["stop"] > signal["planned_entry"] > signal["target"]
    (trade,) = engine.trades
    assert trade["exit_reason"] == "target" and trade["entry"] == 49000 - 24257 and trade["net_points"] == 194


def test_friday_position_is_closed_at_cutoff_when_carry_not_allowed():
    from datetime import timedelta
    shifted = [b.__class__(b.start + timedelta(days=2 if b.start.day >= 8 else 0), 15, b.open, b.high, b.low,
                           b.close, b.volume) for b in base_history()[25:] + trigger_day(RETEST_CONFIRM + [24262] * 8)]
    # sessions now run Tue 2 Jun .. Fri 12 Jun; the trigger day is the Friday
    lead = base_history()[:25]
    engine, _ = run(lead + shifted, cfg(), no_structure)
    assert shifted[-1].start.weekday() == 4
    assert [t["exit_reason"] for t in engine.trades] == ["friday_exit"]
