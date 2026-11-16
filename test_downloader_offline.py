"""Offline regression checks, no Dhan credentials or network requests."""
import tempfile
from pathlib import Path
import pandas as pd
from dhan_history import (
    IST, from_dhan_response, resample_intraday, resample_weekly,
    read_candles, write_candles,
)


def payload_from_rows(rows):
    # rows: list[(ISO_IST, open, high, low, close, volume, oi)]
    return {"status": "success", "data": {
        "timestamp": [int(pd.Timestamp(r[0]).timestamp()) for r in rows],
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows],
        "open_interest": [r[6] for r in rows],
    }}


def test_bucket_alignment_and_no_overnight_merge():
    raw = payload_from_rows([
        ("2026-11-09 09:15:00+05:30", 100, 101, 99, 100, 10, 50),
        ("2026-11-09 09:16:00+05:30", 100, 102, 98, 102, 11, 52),
        ("2026-11-09 09:17:00+05:30", 102, 103, 101, 103, 12, 53),
        ("2026-11-10 09:15:00+05:30", 110, 111, 109, 110, 7, 55),
    ])
    data = from_dhan_response(raw)
    result = resample_intraday(data, 2)
    assert len(result) == 3
    assert list(result["open"]) == [100, 102, 110]
    assert list(result["close"]) == [102, 103, 110]
    assert list(result["high"]) == [102, 103, 111]
    assert result.iloc[0]["volume"] == 21
    assert result.iloc[0]["open_interest"] == 52
    assert result.index[0].strftime("%H:%M") == "09:15"
    hour = resample_intraday(data, 60)
    assert len(hour) == 2, "Must not merge separate trading days"
    with tempfile.TemporaryDirectory() as folder:
        csv_path = Path(folder) / "test.csv"
        write_candles(result, csv_path)
        reread = read_candles(csv_path)
        pd.testing.assert_frame_equal(reread, result, check_dtype=False, check_freq=False)


def test_weekly_ohlcv_and_friday_boundary():
    raw = payload_from_rows([
        ("2026-11-09 09:15:00+05:30", 100, 109, 98, 107, 15, 10),
        ("2026-11-13 09:15:00+05:30", 107, 111, 103, 109, 20, 12),
        ("2026-11-16 09:15:00+05:30", 115, 120, 110, 118, 30, 15),
    ])
    weekly = resample_weekly(from_dhan_response(raw))
    assert len(weekly) == 2
    assert list(weekly["open"]) == [100, 115]
    assert list(weekly["close"]) == [109, 118]
    assert list(weekly["volume"]) == [35, 30]
    assert list(weekly["open_interest"]) == [12, 15]
    assert weekly.index[0].day == 9


def test_empty_api_payload():
    data = from_dhan_response({"timestamp": [], "open": [], "high": [], "low": [], "close": []})
    assert data.empty


if __name__ == "__main__":
    test_bucket_alignment_and_no_overnight_merge()
    test_weekly_ohlcv_and_friday_boundary()
    test_empty_api_payload()
    print("3 offline tests passed")
