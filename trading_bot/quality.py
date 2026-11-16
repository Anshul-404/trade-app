"""Read-only market data quality checks for Trading Bot."""

from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def analyze_quality(candles: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    timestamps = []
    invalid_ohlc = 0
    invalid_timestamps = 0

    for candle in candles:
        try:
            ts = int(candle["time"])
            o, h, l, c = (
                float(candle[key])
                for key in ("open", "high", "low", "close")
            )
            if not all(isfinite(v) and v > 0 for v in (o, h, l, c)):
                invalid_ohlc += 1
            elif l > min(o, c) or h < max(o, c) or l > h:
                invalid_ohlc += 1

            timestamps.append(ts)
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid_timestamps += 1

    unique = sorted(set(timestamps))
    duplicates = len(timestamps) - len(unique)

    # Check missing minutes only within the regular NSE session.
    # Exchange holidays and special sessions are not known here.
    present = set(unique)
    missing = 0

    if unique:
        first = datetime.fromtimestamp(unique[0], IST).date()
        last = datetime.fromtimestamp(unique[-1], IST).date()
        day = first

        while day <= last:
            if day.weekday() < 5:
                start = datetime(
                    day.year, day.month, day.day, 9, 15,
                    tzinfo=IST
                )
                for minute in range(375):
                    ts = int((start + timedelta(minutes=minute)).timestamp())
                    if unique[0] <= ts <= unique[-1] and ts not in present:
                        missing += 1
            day += timedelta(days=1)

    latest = unique[-1] if unique else None
    age = max(0, int(now.timestamp()) - latest) if latest else None

    return {
        "total_candles": len(candles),
        "unique_timestamps": len(unique),
        "duplicate_timestamps": duplicates,
        "invalid_ohlc": invalid_ohlc,
        "invalid_timestamps": invalid_timestamps,
        "missing_session_minutes_estimate": missing,
        "latest_candle_utc": (
            datetime.fromtimestamp(latest, timezone.utc).isoformat()
            if latest else None
        ),
        "latest_candle_age_seconds": age,
        "quality_status": (
            "no_data" if not candles
            else "issues_detected" if (
                duplicates or invalid_ohlc or invalid_timestamps or missing
            )
            else "ok"
        ),
        "notes": [
            "Missing-minute estimate assumes regular NSE weekday sessions.",
            "Exchange holidays and special trading sessions are not excluded.",
            "A stale latest candle may be normal outside market hours.",
            "Zero duplicates in CandleStore does not prove source data had none."
        ]
    }
