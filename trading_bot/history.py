"""Historical one-minute warm-up from the official DhanHQ API."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def fetch_recent_1m(client_id: str, access_token: str, exchange: str,
                    security_id: str, instrument_type: str, days: int = 5) -> list[dict]:
    from dhanhq import DhanContext, dhanhq

    dhan = dhanhq(DhanContext(client_id, access_token))
    now = datetime.now(IST)
    start = now - timedelta(days=days)
    response = dhan.intraday_minute_data(
        security_id=security_id,
        exchange_segment=exchange,
        instrument_type=instrument_type,
        from_date=start.strftime("%Y-%m-%d 09:15:00"),
        to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        interval=1,
    )
    if not isinstance(response, dict) or response.get("status") != "success":
        raise RuntimeError("Historical Data API request failed; verify Data API access.")
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Historical Data API returned no candle arrays.")
    keys = ("timestamp", "open", "high", "low", "close")
    if not all(isinstance(data.get(k), list) for k in keys):
        raise RuntimeError("Historical Data API returned unexpected candle format.")
    volumes = data.get("volume") or [0] * len(data["timestamp"])
    return [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(l),
         "close": float(c), "volume": float(v or 0)}
        for t, o, h, l, c, v in zip(
            data["timestamp"], data["open"], data["high"], data["low"],
            data["close"], volumes
        )
    ]
