from datetime import datetime, timezone
from trading_bot.candles import CandleStore, SUPPORTED_MINUTES

START = int(datetime(2026,11,13,3,45,tzinfo=timezone.utc).timestamp())


def make_bar(i, source="historical"):
    return dict(time=START + i * 60, open=100 + i*.05, high=101 + i*.05,
                low=99 + i*.05, close=100.5 + i*.05,
                volume=12. if source == "historical" else None,
                source=source)


def test_latest_bars_empty():
    s=CandleStore()
    assert s.count_1m() == 0
    assert set(s.latest_bars()) == {str(tf) for tf in SUPPORTED_MINUTES}
    assert all(v is None for v in s.latest_bars().values())


def test_latest_bars_agree_with_snapshot_during_session():
    s=CandleStore()
    for i in range(375):
        b=make_bar(i)
        s.seed([b])
        if i % 59 == 0:
            s.on_tick(b["close"]+0.2, b["time"]+10)
        for tf in SUPPORTED_MINUTES:
            assert s.latest_bars()[str(tf)] == s.snapshot(tf, limit=1)[-1]
    assert s.count_1m() == 375


def test_latest_bars_across_sessions_and_missing_minutes():
    s=CandleStore()
    s.seed([{k:v for k,v in make_bar(i).items() if k!="source"}
            for i in [0,1,5,16,60,62,200,201]])
    s.seed([{**make_bar(240), "time": START + 24*3600}])
    for tf in SUPPORTED_MINUTES:
        assert s.latest_bars()[str(tf)] == s.snapshot(tf,limit=1)[-1]


def test_latest_bars_does_not_modify_original_data():
    s=CandleStore()
    s.seed([{k:v for k,v in make_bar(i).items() if k!="source"} for i in range(61)])
    before=s.snapshot(1,limit=100)
    s.latest_bars()
    assert s.snapshot(1,limit=100)==before
