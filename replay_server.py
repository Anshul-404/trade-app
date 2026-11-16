#!/usr/bin/env python3
"""Trading Bot offline historical bar replay server (no Dhan API / trading orders).

Extract beside dhan_history.py in your trading_bot folder and run:
    python replay_server.py
Open http://127.0.0.1:8787/ in a browser.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.parse
import urllib.request
import webbrowser
from bisect import bisect_right
from datetime import datetime, timedelta
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
TIMEFRAMES = ("1m", "2m", "5m", "15m", "60m", "1d", "1w")
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "replay_static"
VENDOR = STATIC / "vendor" / "lightweight-charts.standalone.production.js"
CHART_CDN = "https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js"
MAX_BATCH = 50
LOOKBACK = 180


class HistoryStore:
    """Loads a selected timeframe from an existing Trading Bot OHLCV CSV.

    No historical candle after the selected replay start index is returned by
    start_session(). The browser requests *next* bars explicitly.
    """

    def __init__(self, folder: Path, *, strict_data: bool = False):
        self.folder = Path(folder).resolve()
        self.strict_data = strict_data
        self._quality = {}
        self._meta = {}
        for timeframe in TIMEFRAMES:
            path = self.folder / f"{timeframe}.csv"
            if not path.is_file():
                continue
            with path.open("r", newline="", encoding="utf-8-sig") as file:
                reader = csv.DictReader(file)
                needed = {"epoch", "open", "high", "low", "close"}
                if not needed.issubset(reader.fieldnames or []):
                    raise ValueError(f"{path}: missing columns {sorted(needed)}")
                first = last = None
                count = 0
                for row in reader:
                    t = int(float(row["epoch"]))
                    if first is None:
                        first = t
                    last = t
                    count += 1
            if count:
                self._meta[timeframe] = {"timeframe": timeframe, "count": count, "first": first, "last": last}

    def catalog(self):
        return {"symbol": self.folder.name, "timeframes": [self._meta[tf] for tf in TIMEFRAMES if tf in self._meta],
                "timezone": "Asia/Kolkata", "max_batch": MAX_BATCH}

    @staticmethod
    def available_at(tf: str, epoch: int) -> int:
        """The earliest safe clock when a bar's complete OHLC is known in IST.

        Downloader timestamps identify the START of each bucket (even weekly).
        Weekly buckets use the first trading day in the week; they are only
        revealed at the week's Friday market close (conservative on holidays).
        """
        stamp = datetime.fromtimestamp(epoch, IST)
        session_close = stamp.replace(hour=15, minute=30, second=0, microsecond=0)
        if tf == '1d':
            return int(session_close.timestamp())
        if tf == '1w':
            friday = session_close + timedelta(days=(4 - stamp.weekday()) % 7)
            return int(friday.timestamp())
        bucket_end = stamp + timedelta(minutes=int(tf[:-1]))
        return int(min(bucket_end, session_close).timestamp())

    @lru_cache(maxsize=2)
    def candles(self, tf: str):
        """Load candles without manufacturing market prices.

        Repairs only sub-micro-point floating round-off in high/low. Larger
        violations and malformed rows are excluded, reported in an audit CSV,
        and exposed to the UI as data-quality warnings.

        The original timeframe CSV is NEVER modified.
        Use --strict-data to fail on the first anomaly instead.
        """
        if tf not in self._meta:
            raise ValueError(f"No candles available for {tf!r}. Check the files in {self.folder}")
        path = self.folder / f"{tf}.csv"
        report_path = self.folder / f"replay_data_issues_{tf}.csv"
        audit_columns = (
            "source_file", "line", "timestamp", "epoch", "open", "high",
            "low", "close", "action", "reason"
        )
        bars = []
        issues = []
        normalized = 0
        skipped = 0
        # Index values normally have 2 decimal places, while float serialization
        # can produce differences around 1e-12. No meaningful price changes.
        rounding_tolerance = 0.000001

        with path.open("r", newline="", encoding="utf-8-sig") as file:
            for row_num, row in enumerate(csv.DictReader(file), start=2):
                action = None
                reason = None
                try:
                    epoch = int(float(row["epoch"]))
                    o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
                    if not all(math.isfinite(x) for x in (o, h, l, c)):
                        action, reason = "SKIPPED", "Non-finite OHLC (NaN or infinity)"
                    else:
                        max_oc, min_oc = max(o, c), min(o, c)
                        violation = max(0.0, max_oc - h, l - min_oc, l - h)
                        if violation > rounding_tolerance:
                            action = "SKIPPED"
                            reason = (
                                "Invalid OHLC (high must >= open/close; "
                                "low must <= open/close; high must >= low); "
                                f"largest violation: {violation:.9g}"
                            )
                        elif violation > 0:
                            h = max(h, max_oc)
                            l = min(l, min_oc)
                            action, reason = "ROUNDING_NORMALIZED", "OHLC float round-off <= 0.000001"
                    if action != "SKIPPED" and bars and epoch <= bars[-1]["time"]:
                        action, reason = "SKIPPED", "Duplicate or unsorted timestamp"
                    if action != "SKIPPED":
                        raw_volume = row.get("volume", "")
                        try:
                            volume = float(raw_volume) if raw_volume not in ("", None, "nan") else 0.0
                            if not math.isfinite(volume):
                                volume = 0.0
                        except (TypeError, ValueError):
                            volume = 0.0
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    action, reason = "SKIPPED", f"Cannot parse candle: {exc}"

                if action is not None:
                    issues.append({
                        "source_file": path.name,
                        "line": row_num,
                        **{k: row.get(k, "") for k in ("timestamp", "epoch", "open", "high", "low", "close")},
                        "action": action,
                        "reason": reason,
                    })
                    if self.strict_data:
                        raise ValueError(f"Invalid candle {path.name}:{row_num}: {reason}")
                    if action == "SKIPPED":
                        skipped += 1
                        continue
                    normalized += 1

                bars.append({
                    "time": epoch, "available": self.available_at(tf, epoch),
                    "open": o, "high": h, "low": l, "close": c, "volume": volume,
                })

        if issues:
            # Audit first; don't alter the original Dhan CSV file or XLSX.
            tmp = report_path.with_suffix(".csv.tmp")
            with tmp.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=audit_columns)
                writer.writeheader()
                writer.writerows(issues)
            tmp.replace(report_path)
            print(f"WARNING: {path.name}: skipped {skipped} invalid candles; "
                  f"normalized {normalized} tiny rounding differences. "
                  f"Audit report: {report_path}", flush=True)
        elif report_path.exists():
            # Do not leave a stale report from an older version of the CSV.
            report_path.unlink()

        if not bars:
            raise ValueError(f"No valid candles in {path.name}; inspect {report_path.name}")

        self._quality[tf] = {
            "skipped": skipped,
            "normalized": normalized,
            "report_file": report_path.name if issues else None,
            "warning": (f"{skipped} invalid candle(s) excluded; {normalized} floating-point "
                        "rounding correction(s). Replay and trade results around missing "
                        "candles may be unreliable.") if issues else None,
        }
        return bars, [b["available"] for b in bars]

    def start_session(self, tf: str, *, date: str | None = None, index: int | None = None):
        bars, available_times = self.candles(tf)
        if index is not None:
            cursor = index
        elif date:
            try:
                parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=IST)
                cursor = bisect_right(available_times, int(parsed.timestamp()))
            except ValueError as exc:
                raise ValueError("Invalid date. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM in IST") from exc
        else:
            cursor = min(LOOKBACK, len(bars) - 1)
        if cursor < 0 or cursor > len(bars):
            raise ValueError("Date/index outside available candle history")
        history_start = max(0, cursor - LOOKBACK)
        return {"timeframe": tf, "cursor": cursor, "total": len(bars), "history": bars[history_start:cursor],
                "history_start": history_start, "first": bars[0]["time"], "last": bars[-1]["time"],
                "quality": self._quality.get(tf, {})}

    def step(self, tf: str, cursor: int, count: int = 1):
        if count < 1 or count > MAX_BATCH:
            raise ValueError(f"count must be between 1 and {MAX_BATCH}")
        bars, _ = self.candles(tf)
        if cursor < 0 or cursor > len(bars):
            raise ValueError("cursor outside available history")
        end = min(cursor + count, len(bars))
        return {"bars": bars[cursor:end], "cursor": end, "total": len(bars), "done": end == len(bars)}


def make_handler(store: HistoryStore, static: Path = STATIC):
    allowed = {"/": ("index.html", "text/html; charset=utf-8"),
               "/replay.js": ("replay.js", "text/javascript; charset=utf-8"),
               "/replay.css": ("replay.css", "text/css; charset=utf-8"),
               "/vendor/lightweight-charts.standalone.production.js": ("vendor/lightweight-charts.standalone.production.js", "text/javascript; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        def respond(self, payload, status=200, mime="application/json; charset=utf-8"):
            data = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8") if not isinstance(payload, bytes) else payload
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
            path = parts.path
            try:
                if path == "/api/catalog":
                    return self.respond(store.catalog())
                if path == "/api/start":
                    tf = params.get("tf", ["1m"])[0]
                    index = params.get("index", [None])[0]
                    result = store.start_session(tf, date=params.get("date", [None])[0],
                                                 index=int(index) if index is not None else None)
                    return self.respond(result)
                if path == "/api/step":
                    tf = params.get("tf", ["1m"])[0]
                    cursor = int(params.get("cursor", ["-1"])[0])
                    count = int(params.get("count", ["1"])[0])
                    return self.respond(store.step(tf, cursor, count))
                if path == "/chart-library.js":
                    chart_path = static / "vendor" / "lightweight-charts.standalone.production.js"
                    if chart_path.is_file():
                        return self.respond(chart_path.read_bytes(), mime="text/javascript; charset=utf-8")
                    self.send_response(302)
                    self.send_header("Location", CHART_CDN)
                    self.end_headers()
                    return
                if path in allowed:
                    filename, mime = allowed[path]
                    file = static / filename
                    if file.is_file():
                        return self.respond(file.read_bytes(), mime=mime)
                    return self.respond({"error": f"Missing {file.name}"}, 404)
                return self.respond({"error": "Not found"}, 404)
            except (ValueError, KeyError) as exc:
                return self.respond({"error": str(exc)}, 400)
            except (OSError, RuntimeError) as exc:
                return self.respond({"error": str(exc)}, 500)

        def log_message(self, fmt, *args):
            if "/api/step" not in self.path:  # avoid logging a line for every replay candle
                super().log_message(fmt, *args)

    return Handler


def download_chart_library(dest=VENDOR):
    """Optional: install chart JS once for fully offline replay (no market-data requests)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(CHART_CDN, headers={"User-Agent": "TradingBotReplay/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()
        if len(data) < 100_000 or data.lstrip().startswith(b"<"):
            raise RuntimeError("Unexpected chart download; no file was installed")
        temp = dest.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(dest)
        return len(data)
    except Exception as exc:
        raise RuntimeError(f"Could not download chart library: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description="Trading Bot: offline OHLCV bar-replay + manual backtesting")
    parser.add_argument("--data-dir", default=str(ROOT / "data" / "historical" / "NIFTY50_13"))
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--install-chart-library", action="store_true", help="Download chart library for offline use and exit")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parser.add_argument("--strict-data", action="store_true", help="Abort on the first invalid or rounded OHLC candle")
    args = parser.parse_args()
    if args.install_chart_library:
        try:
            size = download_chart_library()
        except RuntimeError as exc:
            parser.exit(1, f"ERROR: {exc}\n")
        print(f"Saved offline chart library ({size / 1024:.0f} KB) to {VENDOR}")
        return
    try:
        store = HistoryStore(Path(args.data_dir), strict_data=args.strict_data)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"ERROR loading CSV metadata: {exc}\n")
    if not store.catalog()["timeframes"]:
        parser.exit(1, f"ERROR: No timeframe CSVs found in {store.folder}.\n"
                  "Run dhan_history.py first, or pass --data-dir PATH.\n")
    if not 1 <= args.port <= 65535:
        parser.exit(1, "ERROR: port must be 1..65535\n")
    print("\nTrading Bot | HISTORICAL REPLAY | SIMULATED TRADES ONLY")
    print("Source: ", store.folder)
    print("Frames: ", ", ".join(item["timeframe"] for item in store.catalog()["timeframes"]))
    print(f"Open:    http://127.0.0.1:{args.port}")
    print("Tip: python replay_server.py --install-chart-library  (optional offline chart setup)")
    print("Stop: Ctrl+C\n")
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store)) as server:
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{args.port}/")
            server.serve_forever()
    except KeyboardInterrupt:
        print("\nReplay server stopped.")
    except OSError as exc:
        parser.exit(1, f"ERROR starting replay server: {exc}\n")


if __name__ == "__main__":
    main()
