"""Paper-mode runner on live Dhan candles. It cannot place orders.

    python -m strategy_bot.live --config strategy_bot/configs/experiment_baseline.toml

Reads DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN and the TB_* instrument settings from
trading_bot/.env (select the futures contract first with
`python -m trading_bot.contracts --nearest`). Every poll it downloads completed
1-minute candles from Dhan's historical-intraday API (exchange OHLCV, not
sampled ticks), builds completed 15m candles and feeds the strategy engine.
Signals, simulated fills and skip reasons go to a JSONL journal.

There is deliberately no order-placement code in this package. Turning a paper
signal into a real NIFTY option order needs the separately approved execution
model described in the strategy rules (strike/expiry, premium stop, sizing).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time as time_module
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config as config_module
from .bars import Bar, IST, SESSION_CLOSE, SESSION_OPEN, aggregate, invalid_reason
from .engine import StrategyEngine

LOGGER = logging.getLogger("strategy_bot.live")
API_URL = "https://api.dhan.co/v2/charts/intraday"
ROOT = Path(__file__).resolve().parent.parent


class DhanError(RuntimeError):
    pass


class DhanCandles:
    """Completed 1-minute OHLCV from Dhan, with retry and rate-limit backoff."""

    def __init__(self, client_id: str, access_token: str, security_id: str, segment: str,
                 instrument: str, *, transport=None, sleep=time_module.sleep, retries: int = 4):
        self._headers = {"Content-Type": "application/json", "Accept": "application/json",
                         "access-token": access_token, "client-id": client_id}
        self.security_id, self.segment, self.instrument = str(security_id), segment, instrument
        self._transport = transport or self._post
        self._sleep, self._retries = sleep, retries

    def _post(self, payload: dict) -> dict:
        request = Request(API_URL, data=json.dumps(payload).encode(), headers=self._headers, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise DhanError(f"Dhan authorization failed (HTTP {exc.code}); check token / Data API plan") from None
            if exc.code == 400:
                raise DhanError("Dhan rejected the request (HTTP 400); check security id / instrument") from None
            raise ConnectionError(f"Dhan HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ConnectionError(f"Dhan request failed: {type(exc).__name__}") from None

    def fetch_1m(self, start: datetime, end: datetime, *, volume: bool = True) -> list[Bar]:
        payload = {"securityId": self.security_id, "exchangeSegment": self.segment,
                   "instrument": self.instrument, "interval": "1",
                   "oi": self.instrument.startswith(("FUT", "OPT")),
                   "fromDate": start.strftime("%Y-%m-%d %H:%M:%S"), "toDate": end.strftime("%Y-%m-%d %H:%M:%S")}
        for attempt in range(self._retries):
            try:
                data = self._transport(payload)
                break
            except ConnectionError as exc:
                if attempt == self._retries - 1:
                    raise DhanError(f"{exc} after {self._retries} attempts") from None
                self._sleep(min(2 ** attempt * 2, 30))
        if not isinstance(data, dict) or data.get("status") in {"failure", "error"} or data.get("errorCode"):
            raise DhanError("Dhan returned an error payload for intraday candles")
        body = data.get("data", data)
        stamps = body.get("timestamp") if isinstance(body, dict) else None
        if stamps is None:
            raise DhanError("Dhan intraday response has no timestamp array")
        columns = [body.get(k) for k in ("open", "high", "low", "close")]
        if any(not isinstance(c, list) or len(c) != len(stamps) for c in columns):
            raise DhanError("Dhan intraday arrays are malformed")
        volumes = body.get("volume") if volume else None
        if volumes is not None and len(volumes) != len(stamps):
            volumes = None
        bars: list[Bar] = []
        for i, stamp in sorted(enumerate(stamps), key=lambda item: item[1]):
            try:
                bar = Bar(datetime.fromtimestamp(int(stamp), IST), 1, float(columns[0][i]), float(columns[1][i]),
                          float(columns[2][i]), float(columns[3][i]),
                          None if volumes is None or volumes[i] is None else float(volumes[i]))
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            if invalid_reason(bar, bars[-1] if bars else None) is None:
                bars.append(bar)
        return bars


class Journal:
    """Append-only JSONL with duplicate suppression across restarts."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    self._seen.add(self._key(json.loads(line)))
                except (ValueError, KeyError):
                    continue

    @staticmethod
    def _key(event: dict) -> str:
        return f"{event['type']}|{event['time']}|{event.get('id', '')}|{event.get('reason', '')}"

    def write(self, event: dict) -> bool:
        key = self._key(event)
        if key in self._seen:
            return False
        self._seen.add(key)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
        return True


class PaperRunner:
    def __init__(self, cfg, source, journal: Journal, *, now=lambda: datetime.now(IST),
                 warmup_days: int = 30, grace_seconds: int = 20, detector=None):
        self.cfg, self.source, self.journal, self.now = cfg, source, journal, now
        self.engine = StrategyEngine(cfg, structure_detector=detector)
        self.warmup_days, self.grace = warmup_days, grace_seconds
        self.started = now()
        self._minute_bars: list[Bar] = []
        self._fed_until: datetime | None = None
        self.last_error: str | None = None

    def poll(self) -> list[dict]:
        """Fetch, build completed 15m candles, feed new ones. Returns new journal events."""
        now = self.now()
        if self._minute_bars:
            start = self._minute_bars[-1].start.replace(hour=9, minute=15)
        else:
            start = (now - timedelta(days=self.warmup_days)).replace(hour=9, minute=15, second=0, microsecond=0)
        fresh = self.source.fetch_1m(start, now, volume=self.cfg.data.volume_available)
        self.last_error = None
        self._minute_bars = [b for b in self._minute_bars if b.start < start] + fresh
        candles, _ = aggregate(self._minute_bars, 15, min_parts=self.cfg.data.min_1m_bars_per_15m)
        cutoff = now - timedelta(seconds=self.grace)
        written: list[dict] = []
        for candle in candles:
            if candle.end > cutoff:          # still forming or only just closed
                break
            if self._fed_until is not None and candle.start < self._fed_until:
                continue
            self._fed_until = candle.end
            for event in self.engine.on_bar(candle):
                if event["type"] == "daily" and candle.end < self.started:
                    continue
                event["replayed_history"] = candle.end < self.started
                if self.journal.write(event):
                    written.append(event)
        # keep roughly the warm-up window of minute bars in memory
        floor = now - timedelta(days=self.warmup_days + 2)
        self._minute_bars = [b for b in self._minute_bars if b.start >= floor]
        return written

    def is_stale(self) -> bool:
        now = self.now()
        if now.weekday() >= 5 or not (SESSION_OPEN <= now.time() <= SESSION_CLOSE):
            return False
        if self._fed_until is None:
            return True
        return (now - self._fed_until) > timedelta(minutes=35)


def _check_contract(security_id: str, warn_days: int) -> None:
    from trading_bot.contracts import download_contracts
    match = next((c for c in download_contracts() if c.security_id == security_id), None)
    if match is None:
        raise DhanError(f"Security id {security_id} is not an active NIFTY future in Dhan's instrument master "
                        "(expired or wrong). Run: python -m trading_bot.contracts --nearest")
    today = datetime.now(IST).date()
    days = (match.expiry - today).days
    LOGGER.info("Contract %s expiry %s lot %s", match.display_name, match.expiry, match.lot_size)
    if days <= warn_days:
        LOGGER.warning("Contract expires in %s day(s): roll over before expiry; candles of the next contract "
                       "must not be mixed into the same run.", days)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper-mode strategy runner on Dhan candles (no orders)")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--journal", type=Path, default=ROOT / "data" / "strategy" / "paper_journal.jsonl")
    parser.add_argument("--warmup-days", type=int, default=30, choices=range(10, 86), metavar="10..85")
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--rollover-warn-days", type=int, default=3)
    parser.add_argument("--once", action="store_true", help="Single poll, print engine state, exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = config_module.load(args.config)
    from dotenv import load_dotenv
    load_dotenv(ROOT / "trading_bot" / ".env")
    client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
    token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
    segment = os.getenv("TB_EXCHANGE", "").strip().upper()
    instrument = os.getenv("TB_INSTRUMENT", "").strip().upper()
    security_id = os.getenv("TB_SECURITY_ID", "").strip()
    if not client_id or not token:
        parser.error("Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in trading_bot/.env")
    if instrument == "FUTIDX" and segment == "NSE_FNO":
        _check_contract(security_id, args.rollover_warn_days)
    elif cfg.data.volume_available:
        parser.error("trading_bot/.env points at a non-futures instrument but the config expects volume. "
                     "Select a NIFTY future (python -m trading_bot.contracts --nearest); spot index data may "
                     "only be used with an explicitly labelled volume-free experiment config.")
    else:
        LOGGER.warning("Running on %s/%s without volume: an experiment, not the specified futures setup.",
                       segment, instrument)

    LOGGER.warning("PAPER MODE. Config '%s' status: %s. No orders can be placed by this program.",
                   cfg.meta.name, cfg.meta.status)
    runner = PaperRunner(cfg, DhanCandles(client_id, token, security_id, segment, instrument),
                         Journal(args.journal), warmup_days=args.warmup_days)
    failures = 0
    while True:
        try:
            for event in runner.poll():
                if event["type"] in {"signal", "entry", "exit", "trigger", "skip", "data_gap"}:
                    LOGGER.info("%s%s %s", "[replay] " if event.get("replayed_history") else "",
                                event["type"].upper(), json.dumps(event, default=str))
            failures = 0
            if runner.is_stale():
                LOGGER.warning("No completed 15m candle for over 35 minutes during market hours: data is stale; "
                               "no signal can be generated until candles resume.")
        except DhanError as exc:
            failures += 1
            runner.last_error = str(exc)
            LOGGER.error("Dhan data problem (%s in a row): %s", failures, exc)
            if "authorization" in str(exc):
                return 2
        if args.once:
            print(json.dumps(runner.engine.state(), indent=2, default=str))
            return 0 if failures == 0 else 1
        time_module.sleep(min(args.poll_seconds * (2 ** min(failures, 4)), 300))


if __name__ == "__main__":
    raise SystemExit(main())
