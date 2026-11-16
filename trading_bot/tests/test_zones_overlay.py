"""Tests for Phase 4 API data mapping using fakes rather than Dhan credentials."""
from datetime import datetime, timezone
from types import SimpleNamespace

from trading_bot.zones_overlay import analyze_zones

START = int(datetime(2026, 11, 13, 3, 45, tzinfo=timezone.utc).timestamp())


class Candle:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def bars(n=10, source='historical'):
    return [dict(time=START + i * 60, open=100., high=105., low=95., close=101.,
                 source=source, volume=None) for i in range(n)]


def block(cs, origin=2, confirm=6, **overrides):
    values = dict(index=origin, timestamp=cs[origin].timestamp,
                  break_index=confirm, break_timestamp=cs[confirm].timestamp,
                  block_type='BULLISH', top=103., bottom=98.,
                  mitigation_status='UNTOUCHED', invalidated=False, strength='STRONG')
    values.update(overrides)
    return SimpleNamespace(**values)


def gap(cs, confirm=7, **overrides):
    values = dict(index=confirm, timestamp=cs[confirm].timestamp,
                  first_candle_timestamp=cs[confirm-2].timestamp,
                  third_candle_timestamp=cs[confirm].timestamp,
                  fvg_type='BEARISH', top=104., bottom=102.,
                  mitigation_status='PARTIALLY_MITIGATED', strength='STRONG')
    values.update(overrides)
    return SimpleNamespace(**values)


def analyze(data=None, now=None, ob=None, fvg=None, breaks=None, **kwargs):
    data = bars() if data is None else data
    def fake_structure(cs, *, left_bars, right_bars, initial_trend):
        assert (left_bars, right_bars, initial_trend) == (2, 2, 'NEUTRAL')
        return ['a-break'] if breaks is None else breaks
    def fake_ob(cs, events):
        assert events == (['a-break'] if breaks is None else breaks)
        return [block(cs)] if ob is None else ob(cs)
    def fake_fvg(cs):
        return [gap(cs)] if fvg is None else fvg(cs)
    return analyze_zones(data, timeframe=1, symbol='NIFTY NOV FUT',
                         now_epoch=now or START + 10*60,
                         candle_type=Candle, structure_detector=fake_structure,
                         ob_detector=fake_ob, fvg_detector=fake_fvg, **kwargs)


def test_order_block_starts_at_confirmation_not_earlier_source():
    result = analyze()
    assert result['total_order_blocks'] == 1
    zone = result['order_blocks'][0]
    assert zone['origin_time'] == START + 2*60
    assert zone['start_time'] == START + 6*60
    assert zone['end_time'] == START + 9*60
    assert zone['quality'] == 'historical_ohlc'


def test_fvg_starts_at_third_candle_not_first():
    result = analyze()
    assert result['total_fair_value_gaps'] == 1
    zone = result['fair_value_gaps'][0]
    assert zone['start_time'] == START + 7*60
    assert zone['direction'] == 'BEARISH'
    assert zone['status'] == 'PARTIALLY_MITIGATED'


def test_still_forming_bar_excluded_from_detectors():
    data = bars()
    seen = {}
    def fake_fvg(cs):
        seen['last'] = cs[-1].timestamp
        return []
    result = analyze(data, now=START + 9*60 + 30, fvg=fake_fvg,
                     ob=lambda cs: [])
    assert result['bars_analyzed'] == 9
    assert result['incomplete_bars_excluded'] == 1
    assert seen['last'] == datetime.fromtimestamp(START + 8*60, timezone.utc)


def test_live_ltp_sourced_zone_is_flagged_indicative():
    data = bars(source='live_sampled')
    result = analyze(data)
    assert result['order_blocks'][0]['quality'] == 'indicative_sampled_ltp'
    assert result['fair_value_gaps'][0]['quality'] == 'indicative_sampled_ltp'


def test_wrong_origin_index_or_timestamp_is_skipped():
    result = analyze(ob=lambda cs: [
        block(cs, origin=8, confirm=6),
        block(cs, timestamp=cs[3].timestamp),
    ], fvg=lambda cs: [])
    assert result['total_order_blocks'] == 0
    assert result['skipped_order_blocks'] == 2


def test_mismatched_fvg_third_and_first_times_are_skipped():
    result = analyze(ob=lambda cs: [], fvg=lambda cs: [
        gap(cs, third_candle_timestamp=cs[6].timestamp),
        gap(cs, first_candle_timestamp=cs[4].timestamp),
    ])
    assert result['total_fair_value_gaps'] == 0
    assert result['skipped_fair_value_gaps'] == 2


def test_bad_zone_prices_and_directions_are_rejected():
    result = analyze(ob=lambda cs: [block(cs, bottom=106), block(cs, block_type='OTHER')],
                     fvg=lambda cs: [gap(cs, top=float('nan'))])
    assert result['skipped_order_blocks'] == 2
    assert result['skipped_fair_value_gaps'] == 1


def test_results_are_limited_without_changing_counts():
    result = analyze(ob=lambda cs: [block(cs, origin=i) for i in range(6)],
                     fvg=lambda cs: [], max_zones=2)
    assert result['total_order_blocks'] == 6
    assert len(result['order_blocks']) == 2


def test_too_few_closed_bars_does_not_call_models():
    def fail(*args, **kwargs):
        raise AssertionError('Too few candles: should not call detectors')
    result = analyze_zones(bars(5), timeframe=1, symbol='NIFTY',
                           now_epoch=START + 5*60,
                           candle_type=Candle, structure_detector=fail,
                           ob_detector=fail, fvg_detector=fail)
    assert result['order_blocks'] == []
    assert result['fair_value_gaps'] == []


def test_reject_unsupported_timeframe():
    import pytest
    with pytest.raises(ValueError, match='Unsupported timeframe'):
        analyze_zones([], timeframe=3, symbol='NIFTY')
