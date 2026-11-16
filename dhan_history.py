#!/usr/bin/env python3
"""Trading Bot: Dhan v2 3-year OHLCV downloader (CSV + one-sheet Excel).

Fetch 1-minute candles in <=85-day chunks, fetch daily candles separately,
then derive 2m/5m/15m/60m and weekly bars without crossing IST trading days.
For active instruments only; it does not stitch expired futures intraday series.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv
from tqdm import tqdm

from merge_history_to_xlsx import export_excel

IST = ZoneInfo("Asia/Kolkata")
API_BASE = "https://api.dhan.co/v2/charts"
COLUMNS = ["open", "high", "low", "close", "volume", "open_interest"]
OHLC = ["open", "high", "low", "close"]
CHUNK_DAYS = 85  # safely below Dhan's 90-day maximum per intraday request


def empty_candles() -> pd.DataFrame:
    index = pd.DatetimeIndex([], tz=IST, name="timestamp")
    return pd.DataFrame(columns=COLUMNS, index=index).astype(float)


def from_dhan_response(response: dict[str, Any]) -> pd.DataFrame:
    """Validate Dhan's parallel arrays and convert epoch timestamps to IST."""
    if not isinstance(response, dict):
        raise ValueError("Dhan returned a non-object response")
    if response.get("status") in {"failure", "error"} or response.get("errorCode"):
        raise ValueError(f"Dhan API returned an error: {response}")
    payload = response.get("data", response)
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected Dhan data layout: {str(response)[:300]}")
    timestamps = payload.get("timestamp")
    if timestamps is None:
        raise ValueError(f"Dhan response is missing 'timestamp': {str(response)[:300]}")
    size = len(timestamps)
    for column in OHLC:
        if column not in payload or len(payload[column]) != size:
            raise ValueError(f"Malformed Dhan {column} array")
    if size == 0:
        return empty_candles()

    content = {col: payload[col] for col in OHLC}
    for key in ("volume", "open_interest"):
        values = payload.get(key)
        if values is not None and len(values) != size:
            raise ValueError(f"Malformed Dhan {key} array")
        content[key] = values if values is not None else [None] * size

    df = pd.DataFrame(content)
    df.index = pd.DatetimeIndex(
        pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(IST),
        name="timestamp",
    )
    for col in COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[OHLC].isna().any(axis=None):
        raise ValueError("Dhan returned missing/non-numeric OHLC prices")
    return df.sort_index()[~df.index.duplicated(keep="last")]


def api_post(session: requests.Session, endpoint: str, payload: dict, retries: int = 5) -> dict:
    """Retry transient network/API failures; immediately report auth/parameter errors."""
    for attempt in range(retries):
        try:
            reply = session.post(f"{API_BASE}/{endpoint}", json=payload, timeout=(10, 90))
            if reply.status_code in {401, 403}:
                raise RuntimeError(
                    f"Dhan authorization failed ({reply.status_code}). "
                    "Verify access token and Data API subscription. "
                    f"Response: {reply.text[:250]}"
                )
            if reply.status_code == 400:
                raise RuntimeError(f"Dhan rejected request: {reply.text[:500]}")
            if reply.status_code == 429 or 500 <= reply.status_code < 600:
                raise requests.HTTPError(f"HTTP {reply.status_code}: {reply.text[:250]}")
            reply.raise_for_status()
            data = reply.json()
            if not isinstance(data, dict):
                raise ValueError(f"Unexpected JSON type: {type(data).__name__}")
            if data.get("status") in ("failure", "error") or data.get("errorCode"):
                raise RuntimeError(f"Dhan API failed: {str(data)[:500]}")
            return data
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"Dhan request failed after {retries} attempts: {exc}") from exc
            pause = min(2 ** attempt * 2, 30)
            tqdm.write(f"Request problem: {exc}; retrying in {pause}s...")
            time.sleep(pause)
    raise AssertionError("unreachable")


def write_candles(df: pd.DataFrame, filename: Path) -> None:
    """Write atomically, keeping IST wall-time and Unix seconds for backtesting."""
    filename.parent.mkdir(parents=True, exist_ok=True)
    frame = df.sort_index().copy()
    out = frame.reset_index()
    out.insert(1, "epoch", frame.index.asi8 // 1_000_000_000)
    out["timestamp"] = frame.index.strftime("%Y-%m-%d %H:%M:%S%z")
    out = out[["timestamp", "epoch", *COLUMNS]]
    temp = filename.with_suffix(filename.suffix + ".tmp")
    out.to_csv(temp, index=False)
    temp.replace(filename)


def read_candles(filename: Path) -> pd.DataFrame:
    data = pd.read_csv(filename, dtype={"timestamp": "string"})
    if data.empty:
        return empty_candles()
    idx = pd.DatetimeIndex(pd.to_datetime(data.pop("timestamp"), utc=True).dt.tz_convert(IST))
    data = data.drop(columns=["epoch"], errors="ignore")
    data.index = idx.rename("timestamp")
    for col in COLUMNS:
        if col not in data.columns:
            data[col] = pd.NA
    return data[COLUMNS].sort_index()


def merged(frames: list[pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if not frames:
        return empty_candles()
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.loc[(df.index >= start) & (df.index < end)]


def intraday_windows(start: pd.Timestamp, end: pd.Timestamp):
    cursor = start
    while cursor < end:
        stop = min(cursor + pd.Timedelta(days=CHUNK_DAYS), end)
        yield cursor, stop
        cursor = stop


def resample_intraday(df: pd.DataFrame, minutes: int, session_start: str = "09:15") -> pd.DataFrame:
    """Aggregate per IST trading day, anchored to exchange session open."""
    if df.empty:
        return empty_candles()
    hour, minute = map(int, session_start.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Invalid --session-start HH:MM")
    anchor_minutes = hour * 60 + minute
    i = df.index
    elapsed_minutes = i.hour * 60 + i.minute - anchor_minutes
    # Always anchor 2m/5m/15m/60m candles to 09:15 for NSE, not midnight.
    starts = i.normalize() + pd.to_timedelta(
        (elapsed_minutes // minutes) * minutes + anchor_minutes, unit="min"
    )
    starts = pd.DatetimeIndex(starts, name="timestamp")
    bars = df.groupby(starts, sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", lambda values: values.sum(min_count=1)),
        open_interest=("open_interest", "last"),
    )
    return bars[COLUMNS].dropna(subset=OHLC)


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Weeks ending Friday; timestamp is first trading day in each week."""
    if daily.empty:
        return empty_candles()
    weeks = daily.index.tz_localize(None).to_period("W-FRI")
    groups = daily.groupby(weeks, sort=True)
    result = groups.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", lambda values: values.sum(min_count=1)),
        open_interest=("open_interest", "last"),
    )
    result.index = pd.DatetimeIndex(groups.apply(lambda g: g.index[0]), name="timestamp")
    return result[COLUMNS]


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading Bot — Dhan downloader (7 CSV timeframes + single-sheet XLSX)")
    parser.add_argument("--symbol", default="NIFTY50", help="Local filename prefix")
    parser.add_argument("--security-id", default="13", help="Dhan numeric security ID")
    parser.add_argument("--segment", default="IDX_I", help="Dhan exchange segment")
    parser.add_argument("--instrument", default="INDEX", help="Dhan instrument type")
    parser.add_argument("--years", type=int, default=3, help="Number of calendar years to retrieve")
    parser.add_argument("--days", type=int, help="Optional quick test: download last N calendar days")
    parser.add_argument("--session-start", default="09:15", help="Exchange session start in IST, HH:MM")
    parser.add_argument("--out", default="data/historical", help="Output directory")
    parser.add_argument("--force", action="store_true", help="Refetch even cached chunks")
    parser.add_argument("--excel-only", action="store_true", help="Combine existing CSV files into a single XLSX without calling Dhan API")
    args = parser.parse_args()

    if args.years < 1 or (args.days is not None and args.days < 1):
        parser.error("--years and --days must be positive")
    if args.instrument.upper() in {"FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR"}:
        print(
            "WARNING: Dhan does NOT provide 3 years of continuous intraday futures "
            "history via a single active contract ID. Expired futures are not "
            "automatically stitched. Old windows may be empty.", file=sys.stderr
        )

    symbol = re.sub(r"[^\w-]+", "_", args.symbol.upper()).strip("_")
    if not symbol:
        parser.error("--symbol must contain a letter or digit")
    out_dir = Path(args.out).expanduser().resolve() / f"{symbol}_{args.security_id}"
    if args.excel_only:
        try:
            export_excel(out_dir)
            return 0
        except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    load_dotenv()
    token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
    if not token:
        parser.error("Missing DHAN_ACCESS_TOKEN. Add it to .env or set it as an environment variable.")
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    now = pd.Timestamp.now(tz=IST).floor("min")
    start = (now.normalize() - (pd.Timedelta(days=args.days) if args.days else pd.DateOffset(years=args.years)))
    # End is exclusive; exclude the current minute, which may still be forming.
    end = now
    today = now.normalize()
    # Daily candles are only requested after their market session completes.
    market_close = pd.Timestamp(f"{today.date()} 15:45:00", tz=IST)
    daily_end = today + pd.Timedelta(days=1) if now >= market_close else today

    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": token,
    })
    if os.getenv("DHAN_CLIENT_ID"):
        session.headers["client-id"] = os.getenv("DHAN_CLIENT_ID", "").strip()

    common = {
        "securityId": str(args.security_id),
        "exchangeSegment": args.segment.upper(),
        "instrument": args.instrument.upper(),
        "oi": args.instrument.upper().startswith(("FUT", "OPT")),
    }
    windows = list(intraday_windows(start, end))
    print(f"\nTrading Bot | {symbol} | {args.segment}/{args.instrument} | {args.security_id}")
    print(f"History: {start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d %H:%M')} IST")
    print(f"Output:  {out_dir}\n")

    try:
        with tqdm(total=len(windows) + 7, desc="Downloading / processing", unit="step", dynamic_ncols=True) as bar:
            parts = []
            for win_start, win_end in windows:
                label = f"{win_start:%Y%m%d_%H%M}_{win_end:%Y%m%d_%H%M}"
                chunk_path = cache_dir / f"1m_{label}.csv"
                if chunk_path.is_file() and not args.force:
                    df = read_candles(chunk_path)
                else:
                    body = {
                        **common,
                        "interval": "1",
                        "fromDate": win_start.strftime("%Y-%m-%d %H:%M:%S"),
                        "toDate": win_end.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    response = api_post(session, "intraday", body)
                    df = from_dhan_response(response)
                    df = df.loc[(df.index >= win_start) & (df.index < win_end)]
                    write_candles(df, chunk_path)
                parts.append(df)
                bar.set_postfix_str(f"1m {win_start:%Y-%m-%d} .. {win_end:%Y-%m-%d}")
                bar.update()

            minute_df = merged(parts, start, end)
            if minute_df.empty:
                raise RuntimeError("No 1-minute data returned. Verify security ID, instrument, and data access.")
            write_candles(minute_df, out_dir / "1m.csv")
            bar.set_postfix_str(f"1m saved ({len(minute_df):,} bars)")
            bar.update()

            for tf in (2, 5, 15, 60):
                result = resample_intraday(minute_df, tf, args.session_start)
                # Drop only an unfinished current-session bar; historical session
                # closing bars may be shorter than 60 minutes and remain valid.
                result = result.loc[
                    (result.index.normalize() < today)
                    | (result.index + pd.Timedelta(minutes=tf) <= end)
                ]
                write_candles(result, out_dir / f"{tf}m.csv")
                bar.set_postfix_str(f"{tf}m saved ({len(result):,} bars)")
                bar.update()

            if start < daily_end:
                daily_body = {
                    **common,
                    "expiryCode": 0,
                    "fromDate": start.strftime("%Y-%m-%d"),
                    "toDate": daily_end.strftime("%Y-%m-%d"),
                }
                daily_df = from_dhan_response(api_post(session, "historical", daily_body))
                # Daily response time can be midnight; compare IST trading dates.
                daily_df = daily_df.loc[
                    (daily_df.index.date >= start.date())
                    & (daily_df.index.date < daily_end.date())
                ]
            else:
                daily_df = empty_candles()
            write_candles(daily_df, out_dir / "1d.csv")
            bar.set_postfix_str(f"1d saved ({len(daily_df):,} bars)")
            bar.update()

            weekly_df = resample_weekly(daily_df)
            # Keep only completed NSE trading weeks (Friday at/after 15:45).
            if not weekly_df.empty:
                weekly_fridays = weekly_df.index.tz_localize(None).to_period("W-FRI").end_time.normalize()
                week_close = pd.DatetimeIndex(weekly_fridays).tz_localize(IST) + pd.Timedelta(hours=15, minutes=45)
                weekly_df = weekly_df.loc[week_close <= now]
            write_candles(weekly_df, out_dir / "1w.csv")
            bar.set_postfix_str(f"1w saved ({len(weekly_df):,} bars)")
            bar.update()

        # Export all seven CSVs together into ONE worksheet (with its own progress bar).
        export_excel(out_dir)

        print("\nFILES CREATED:")
        for tf in ("1m", "2m", "5m", "15m", "60m", "1d", "1w"):
            path = out_dir / f"{tf}.csv"
            print(f"  {path.name:<8} {path.stat().st_size / (1024 * 1024):7.2f} MB")
        print(f"\nAll files: {out_dir}")
        print(f"Coverage (1m): {minute_df.index.min()} to {minute_df.index.max()}")
        if minute_df.index.min() > start + pd.Timedelta(days=14):
            print("WARNING: 1m coverage starts much later than requested. Inspect instrument history and gaps.")
        if daily_df.empty:
            print("WARNING: Daily API returned no data; daily/weekly CSV files are empty.")
        print("Note: Non-trading days have no candles. Inspect missing-minute gaps before serious backtests.")
        return 0
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print(f"Downloaded 1m chunks are cached in {cache_dir}; rerun to resume.", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
