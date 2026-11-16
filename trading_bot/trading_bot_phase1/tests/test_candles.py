from datetime import datetime
from zoneinfo import ZoneInfo

from trading_bot.candles import CandleStore, time_bucket

IST = ZoneInfo("Asia/Kolkata")


def stamp(hour, minute, day=13):
    return int(datetime(2026, 11, day, hour, minute, tzinfo=IST).timestamp())


def test_intraminute_ohlc():
    s = CandleStore()
    assert s.on_tick(100, stamp(9, 15))
    assert s.on_tick(105, stamp(9, 15) + 5)
    assert s.on_tick(98, stamp(9, 15) + 10)
    assert s.snapshot(1) == [dict(time=stamp(9, 15), open=100, high=105, low=98,
                                  close=98, volume=None, source="live_sampled")]


def test_two_minute_bars_use_session_anchor():
    s = CandleStore()
    s.on_tick(100, stamp(9, 15))
    s.on_tick(102, stamp(9, 16))
    s.on_tick(99, stamp(9, 17))
    bars = s.snapshot(2)
    assert len(bars) == 2
    assert bars[0]['time'] == stamp(9, 15) and bars[0]['high'] == 102
    assert bars[1]['time'] == stamp(9, 17)


def test_hour_bars_start_at_0915():
    assert time_bucket(stamp(10, 10), 60) == stamp(9, 15)
    assert time_bucket(stamp(10, 15), 60) == stamp(10, 15)


def test_delayed_tick_is_rejected():
    s = CandleStore()
    s.on_tick(100, stamp(9, 16))
    assert not s.on_tick(98, stamp(9, 15))
    assert s.snapshot(1)[0]['close'] == 100


def test_seed_aggregation_and_history_volume():
    s = CandleStore()
    n = s.seed([
        dict(time=stamp(9, 15), open=100, high=103, low=99, close=101, volume=20),
        dict(time=stamp(9, 16), open=101, high=104, low=100, close=102, volume=30),
    ])
    assert n == 2
    assert s.snapshot(2)[0]['volume'] == 50
    assert s.snapshot(2)[0]['close'] == 102
    assert s.snapshot(2)[0]['source'] == 'historical'
    assert s.last_price is None


def test_live_volume_is_unknown_not_zero():
    s = CandleStore()
    s.on_tick(100, stamp(9, 15))
    s.on_tick(101, stamp(9, 16))
    assert s.snapshot(2)[0]['volume'] is None
    assert s.quality_summary()['sampled_live_bars'] == 2


def test_combining_historical_and_live_nulls_aggregate_volume():
    s = CandleStore()
    s.seed([dict(time=stamp(9, 15), open=100, high=101, low=99, close=100, volume=500)])
    s.on_tick(102, stamp(9, 16))
    agg = s.snapshot(2)[0]
    assert agg['volume'] is None
    assert agg['source'] == 'mixed'
    assert agg['high'] == 102


def test_live_update_to_historical_candle_becomes_mixed():
    s = CandleStore()
    s.seed([dict(time=stamp(9, 15), open=100, high=101, low=99, close=100, volume=500)])
    s.on_tick(100.5, stamp(9, 15) + 30)
    bar = s.snapshot(1)[0]
    assert bar['source'] == 'mixed'
    assert bar['volume'] is None


def test_index_volume_is_always_not_applicable():
    s = CandleStore(volume_supported=False)
    s.seed([dict(time=stamp(9, 15), open=100, high=101, low=99, close=100, volume=500)])
    assert s.snapshot(1)[0]['volume'] is None
    assert s.quality_summary()['volume_status'] == 'not_applicable_index'


def test_missing_minutes_within_session_but_not_overnight():
    s = CandleStore()
    s.on_tick(100, stamp(9, 15, day=12))
    s.on_tick(101, stamp(9, 17, day=12))
    s.on_tick(102, stamp(9, 15, day=13))
    summary = s.quality_summary()
    assert summary['observed_intraday_missing_minutes'] == 1
    assert len(summary['gap_examples']) == 1


def test_no_fake_spike_detection_on_normal_range():
    s = CandleStore()
    s.seed([dict(time=stamp(9, 15), open=25000, high=25040,
                 low=24980, close=25005, volume=1)])
    assert not s.quality_summary()['large_1m_ranges_at_least_1pct']


def test_large_range_flagged_for_manual_review():
    s = CandleStore()
    s.seed([dict(time=stamp(9, 15), open=25000, high=25300,
                 low=24980, close=25005, volume=1)])
    assert len(s.quality_summary()['large_1m_ranges_at_least_1pct']) == 1


def test_nan_volume_not_counted_as_reliable_volume():
    s = CandleStore()
    s.seed([dict(time=stamp(9, 15), open=100, high=101,
                 low=99, close=100, volume=float('nan'))])
    assert s.snapshot(1)[0]['volume'] is None
