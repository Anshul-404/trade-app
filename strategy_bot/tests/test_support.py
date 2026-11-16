import json
import tomllib
from datetime import datetime, timedelta

import pytest

from strategy_bot import config as config_module
from strategy_bot.bars import IST, Bar, aggregate, load_csv
from strategy_bot.levels import Level, LevelBook, SwingTracker
from strategy_bot.live import DhanCandles, DhanError, Journal, PaperRunner

from .helpers import BASELINE, base_history, bar_at, cfg, no_structure, trigger_day


def raw_config():
    return tomllib.loads(BASELINE.read_text(encoding="utf-8"))


def test_config_has_no_hidden_defaults():
    raw = raw_config()
    del raw["retest"]["expiry_bars"]
    with pytest.raises(config_module.ConfigError, match="missing keys"):
        config_module.from_dict(raw)
    raw = raw_config()
    raw["hourly"]["surprise"] = 1
    with pytest.raises(config_module.ConfigError, match="unknown keys"):
        config_module.from_dict(raw)


@pytest.mark.parametrize("section,key,value", [
    ("strategy", "min_rr", 2.0), ("execution", "mode", "live"),
    ("daily", "flip_lookback_days", 2), ("entry", "fill", "limit"),
])
def test_config_rejects_rule_violations(section, key, value):
    raw = raw_config()
    raw[section][key] = value
    with pytest.raises(config_module.ConfigError):
        config_module.from_dict(raw)


def test_volume_required_conflicts_with_volume_free_data():
    with pytest.raises(config_module.ConfigError):
        cfg(data__volume_available=False)
    assert cfg(data__volume_available=False, volume__required=False).volume.required is False


def minute_bars(day="2026-06-01", count=375, volume=10.0):
    start = datetime.fromisoformat(f"{day}T09:15:00").replace(tzinfo=IST)
    return [Bar(start + timedelta(minutes=i), 1, 100 + i, 101 + i, 99 + i, 100.5 + i, volume) for i in range(count)]


def test_aggregation_is_session_aligned_and_drops_thin_buckets():
    ones = minute_bars()
    del ones[20:25]                                   # 09:35-09:39 missing -> 09:30 bucket has 10 bars
    candles, audit = aggregate(ones, 15, min_parts=13)
    assert candles[0].start.strftime("%H:%M") == "09:15" and candles[0].volume == 150
    assert [a["reason"] for a in audit] == ["incomplete_bucket"]
    assert candles[1].start.strftime("%H:%M") == "09:45"
    hours, _ = aggregate(minute_bars(), 60, min_parts=50)
    assert [h.start.strftime("%H:%M") for h in hours][-2:] == ["14:15", "15:15"] and hours[-1].minutes == 15


def test_unknown_volume_is_not_zero():
    ones = minute_bars(count=15)
    ones[3] = Bar(ones[3].start, 1, ones[3].open, ones[3].high, ones[3].low, ones[3].close, None)
    assert aggregate(ones, 15, min_parts=13)[0][0].volume is None


def test_csv_loader_audits_bad_rows(tmp_path):
    epoch = int(datetime(2026, 6, 1, 9, 15, tzinfo=IST).timestamp())
    rows = ["timestamp,epoch,open,high,low,close,volume",
            f"x,{epoch},100,101,99,100.5,5",
            f"x,{epoch},100,101,99,100.5,5",                  # duplicate
            f"x,{epoch + 900},100,99,101,100,5",              # high < low
            f"x,{epoch + 1800},100,101,99,100.5,",            # unknown volume
            f"x,{epoch + 86400 * 5},100,101,99,100.5,5"]      # Saturday
    path = tmp_path / "15m.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    bars, audit = load_csv(path, 15)
    assert len(bars) == 2 and bars[1].volume is None
    assert sorted(a["reason"] for a in audit) == ["duplicate_timestamp", "ohlc_inconsistent", "weekend_bar"]


def test_swing_is_only_known_after_right_side_bars():
    tracker = SwingTracker(2, 2)
    highs = [10, 11, 15, 12, 11, 10]
    seen = []
    for i, high in enumerate(highs):
        bar = bar_at("2026-06-01", f"{9 + (15 + 15 * i) // 60:02d}:{(15 + 15 * i) % 60:02d}", high - 3, high - 2)
        seen.append([s for s in tracker.add(bar) if s[0] == "R"])
    assert [bool(s) for s in seen] == [False, False, False, False, True, False]


def test_level_book_nearest_first_and_close_through():
    book = LevelBook()
    now = datetime(2026, 6, 1, 10, tzinfo=IST)
    for price in (24330, 24340, 24452):
        book.add(Level(price, "R", "swing_60m", now, now))
    book.add(Level(24300, "R", "prior_day", now, now))
    found = book.nearest_target(1, 24250, ["swing_60m"], 15.0, "near")
    assert found["target"] == 24330 and found["zone_touches"] == 2 and found["next_level_beyond"] == 24452
    assert book.nearest_target(1, 24250, ["swing_60m", "prior_day"], 15.0, "near")["target"] == 24300
    book.on_close(24335)                               # closes through 24300 and 24330 only
    assert book.nearest_target(1, 24250, ["swing_60m", "prior_day"], 15.0, "near")["target"] == 24340
    assert sorted(lv.price for lv in book.levels) == [24340, 24452]
    assert book.nearest_target(-1, 24250, ["swing_60m"], 15.0, "near") is None


class FakeSource:
    def __init__(self, candles):
        self.minutes = []
        for c in candles:
            for i in range(15):
                price = c.open + (c.close - c.open) * (i + 1) / 15
                previous = c.open + (c.close - c.open) * i / 15
                self.minutes.append(Bar(c.start + timedelta(minutes=i), 1, previous, max(previous, price) + 2,
                                        min(previous, price) - 2, price, None if c.volume is None else c.volume / 15))
        self.calls = 0

    def fetch_1m(self, start, end, volume=True):
        self.calls += 1
        return [b for b in self.minutes if start <= b.start and b.end <= end]


def test_paper_runner_feeds_only_completed_candles_and_never_duplicates(tmp_path):
    candles = base_history() + trigger_day([24228, 24256, 24300, 24360, 24420, 24460])
    source = FakeSource(candles)
    clock = {"now": datetime(2026, 6, 10, 13, 20, tzinfo=IST)}
    journal_path = tmp_path / "journal.jsonl"
    runner = PaperRunner(cfg(), source, Journal(journal_path), now=lambda: clock["now"], detector=no_structure)
    first = runner.poll()
    assert [e["type"] for e in first if e["type"] == "trigger"] == ["trigger"]      # 12:15 hour closed at 13:15
    assert not any(e["type"] == "signal" for e in first)
    assert runner.poll() == []                                                       # same minute: nothing new
    clock["now"] = datetime(2026, 6, 10, 13, 44, tzinfo=IST)                         # 13:30 candle still forming
    assert not any(e["type"] == "signal" for e in runner.poll())
    clock["now"] = datetime(2026, 6, 10, 13, 46, tzinfo=IST)
    signals = [e for e in runner.poll() if e["type"] == "signal"]
    assert len(signals) == 1 and signals[0]["replayed_history"] is False
    clock["now"] = datetime(2026, 6, 10, 15, 0, tzinfo=IST)
    assert [e["exit_reason"] for e in runner.poll() if e["type"] == "exit"] == ["target"]

    # restart: history is replayed to rebuild state, but nothing is journalled twice
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    restarted = PaperRunner(cfg(), source, Journal(journal_path), now=lambda: clock["now"], detector=no_structure)
    assert restarted.poll() == []
    assert journal_path.read_text(encoding="utf-8").splitlines() == lines
    assert sum(json.loads(line)["type"] == "signal" for line in lines) == 1


def test_dhan_candles_retries_then_parses_and_reports_errors():
    epoch = int(datetime(2026, 6, 1, 9, 15, tzinfo=IST).timestamp())
    good = {"open": [100, 101], "high": [101, 102], "low": [99, 100], "close": [100.5, 101.5],
            "volume": [5, None], "timestamp": [epoch, epoch + 60]}
    replies = [ConnectionError("HTTP 429"), good]
    waits = []

    def transport(payload):
        assert payload["interval"] == "1" and payload["securityId"] == "42"
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    source = DhanCandles("c", "t", "42", "NSE_FNO", "FUTIDX", transport=transport, sleep=waits.append)
    start = datetime(2026, 6, 1, 9, 15, tzinfo=IST)
    bars = source.fetch_1m(start, start + timedelta(hours=1))
    assert waits == [2] and [b.volume for b in bars] == [5.0, None]
    failing = DhanCandles("c", "t", "42", "NSE_FNO", "FUTIDX", sleep=lambda _: None,
                          transport=lambda payload: {"status": "failure", "errorCode": "DH-905"})
    with pytest.raises(DhanError):
        failing.fetch_1m(start, start + timedelta(hours=1))


def test_package_contains_no_order_placement_calls():
    from pathlib import Path
    source = "".join(p.read_text(encoding="utf-8") for p in Path(config_module.__file__).parent.glob("*.py"))
    for forbidden in ("place_order", "/v2/orders", "modify_order", "/super/orders", "/forever/orders"):
        assert forbidden not in source
