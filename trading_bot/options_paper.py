"""DhanHQ NIFTY option-chain viewer and local-only paper options ledger.

No broker order/position endpoints are imported or called in this module.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

API = "https://api.dhan.co/v2"
IST = ZoneInfo("Asia/Kolkata")
EXPIRY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ONE_RUPEE = Decimal("0.01")


def money(value: float | str | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(ONE_RUPEE, rounding=ROUND_HALF_UP)


def finite_price(value: object) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_chain(raw: dict, expiry: str, fetched_at: float) -> dict:
    """Return consistent chain rows and an internal security-id map."""
    if raw.get("status") != "success" or not isinstance(raw.get("data"), dict):
        raise ValueError("Dhan did not return a successful option chain response")
    data = raw["data"]
    source = data.get("oc")
    if not isinstance(source, dict):
        raise ValueError("Option-chain strikes missing from Dhan response")
    quotes = {}
    rows = []
    for strike_text, legs in source.items():
        try:
            strike = float(strike_text)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(strike) or strike <= 0 or not isinstance(legs, dict):
            continue
        row = {"strike": strike}
        for kind in ("ce", "pe"):
            leg = legs.get(kind)
            if not isinstance(leg, dict):
                row[kind] = None
                continue
            try:
                sid = int(leg.get("security_id"))
            except (TypeError, ValueError):
                sid = 0
            if sid <= 0:
                row[kind] = None
                continue
            quote = {
                "security_id": sid,
                "expiry": expiry,
                "strike": strike,
                "kind": kind.upper(),
                "symbol": f"NIFTY {expiry} {strike:g} {kind.upper()}",
                "last_price": finite_price(leg.get("last_price")),
                "bid": finite_price(leg.get("top_bid_price")),
                "ask": finite_price(leg.get("top_ask_price")),
                "oi": leg.get("oi"),
                "volume": leg.get("volume"),
                "iv": leg.get("implied_volatility"),
                "greeks": leg.get("greeks") if isinstance(leg.get("greeks"), dict) else {},
            }
            row[kind] = quote
            quotes[sid] = quote
        if row.get("ce") is not None or row.get("pe") is not None:
            rows.append(row)
    rows.sort(key=lambda item: item["strike"])
    return {
        "expiry": expiry, "underlying": "NIFTY 50 SPOT", "underlying_price": finite_price(data.get("last_price")),
        "fetched_at_utc": datetime.fromtimestamp(fetched_at, timezone.utc).isoformat(),
        "fetched_at": fetched_at, "rows": rows, "quotes": quotes,
    }


class DhanOptions:
    """Blocking REST calls stay in FastAPI's synchronous worker thread, not chart event loop."""

    def __init__(self, client_id: str, access_token: str, *, ttl: float = 7.0,
                 transport=None, now=None, sleep=None):
        self.client_id = client_id
        self._access_token = access_token
        self.ttl = ttl
        self.transport = transport or self._post
        self.now = now or time.time
        self.sleep = sleep or time.sleep
        self._lock = threading.RLock()
        self._expiry_cache: tuple[float, list[str]] = (0, [])
        self._chains: dict[str, dict] = {}
        self._last_remote_chain = 0.0
        self._failed_refresh: set[str] = set()

    def _post(self, endpoint: str, payload: dict) -> dict:
        if not self.client_id or not self._access_token:
            raise RuntimeError("Configure DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in trading_bot/.env")
        request = Request(
            API + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "access-token": self._access_token,
                     "client-id": self.client_id},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Never print token, raw request headers, or untrusted response bodies.
            raise RuntimeError(f"Dhan API HTTP {exc.code}; check token/data subscription") from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("Dhan option-chain request failed; check network/subscription") from None

    def expiries(self) -> list[str]:
        with self._lock:
            if self._expiry_cache[1] and self.now() - self._expiry_cache[0] < 3600:
                return list(self._expiry_cache[1])
            data = self.transport("/optionchain/expirylist", {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"})
            if data.get("status") != "success" or not isinstance(data.get("data"), list):
                raise RuntimeError("Dhan expiry list was not available")
            today = datetime.fromtimestamp(self.now(), IST).date()
            expiries = set()
            for value in data["data"]:
                if not isinstance(value, str) or not EXPIRY_PATTERN.fullmatch(value):
                    continue
                try:
                    if date.fromisoformat(value) >= today:
                        expiries.add(value)
                except ValueError:
                    continue
            expiries = sorted(expiries)
            self._expiry_cache = (self.now(), expiries)
            return list(expiries)

    def chain(self, expiry: str, *, window: int = 13) -> dict:
        if not EXPIRY_PATTERN.fullmatch(expiry):
            raise ValueError("Expiry must be YYYY-MM-DD")
        if expiry not in self.expiries():
            raise ValueError("Choose an active expiry from Dhan's expiry list")
        with self._lock:
            cached = self._chains.get(expiry)
            age = self.now() - cached["fetched_at"] if cached else float("inf")
            if age >= self.ttl:
                # Respect 1 option-chain fetch per >=3 seconds across expiries.
                elapsed = self.now() - self._last_remote_chain
                if self._last_remote_chain and elapsed < 3.2:
                    self.sleep(3.2 - elapsed)
                self._last_remote_chain = self.now()
                try:
                    raw = self.transport("/optionchain", {"UnderlyingScrip": 13,
                                                           "UnderlyingSeg": "IDX_I", "Expiry": expiry})
                    cached = normalize_chain(raw, expiry, self.now())
                    self._chains[expiry] = cached
                except (RuntimeError, ValueError) as exc:
                    if not cached:
                        raise
                    # Old snapshot can be displayed with a warning, but cannot execute paper orders.
                    stale_error = str(exc)
                    self._failed_refresh.add(expiry)
                else:
                    stale_error = None
                    self._failed_refresh.discard(expiry)
            else:
                stale_error = None
            age = max(0, self.now() - cached["fetched_at"])
            rows = cached["rows"]
            spot = cached["underlying_price"]
            if spot and len(rows) > window:
                nearest = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - spot))
                start = max(0, min(nearest - window // 2, len(rows) - window))
                rows = rows[start:start + window]
            elif len(rows) > window:
                rows = rows[:window]
            return {
                "expiry": expiry, "underlying": cached["underlying"], "spot": spot,
                "rows": rows, "total_strikes": len(cached["rows"]),
                "age_seconds": round(age, 1), "stale": age > 20 or bool(stale_error),
                "warning": stale_error,
                "fetched_at_utc": cached["fetched_at_utc"],
            }

    def quote(self, expiry: str, security_id: int, max_age: float = 20) -> dict | None:
        """Only return a quote when there is a recent chain snapshot for this expiry."""
        with self._lock:
            cached = self._chains.get(expiry)
            if not cached or expiry in self._failed_refresh or self.now() - cached["fetched_at"] > max_age:
                return None
            return cached["quotes"].get(security_id)


class PaperLedger:
    """SQLite journal of virtual, long-only option trades. All money is simulated."""

    def __init__(self, path: Path | str, *, initial_cash: int = 100_000, lot_size: int = 65):
        if initial_cash <= 0 or lot_size <= 0:
            raise ValueError("Paper cash and option lot size must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lot_size = int(lot_size)
        self._lock = threading.RLock()
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS paper_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_positions (
                    expiry TEXT NOT NULL, security_id INTEGER NOT NULL, symbol TEXT NOT NULL,
                    kind TEXT NOT NULL, strike REAL NOT NULL, qty INTEGER NOT NULL,
                    avg_price TEXT NOT NULL, PRIMARY KEY(expiry, security_id));
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_utc TEXT NOT NULL,
                    action TEXT NOT NULL, expiry TEXT NOT NULL, security_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL, lots INTEGER NOT NULL, qty INTEGER NOT NULL,
                    fill_price TEXT NOT NULL, gross_value TEXT NOT NULL, cash_after TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO paper_meta VALUES (?, ?)", ("initial_cash", str(money(initial_cash))))
            db.execute("INSERT OR IGNORE INTO paper_meta VALUES (?, ?)", ("cash", str(money(initial_cash))))
            db.execute("INSERT OR IGNORE INTO paper_meta VALUES (?, ?)", ("realized_pnl", "0.00"))
            db.commit()

    @staticmethod
    def _meta(db, key: str) -> Decimal:
        return Decimal(db.execute("SELECT value FROM paper_meta WHERE key=?", (key,)).fetchone()[0])

    def order(self, *, action: str, lots: int, quote: dict) -> dict:
        if action not in ("BUY", "SELL") or not isinstance(lots, int) or isinstance(lots, bool) or not 1 <= lots <= 20:
            raise ValueError("Choose BUY/SELL and 1–20 whole lots")
        if quote.get("kind") not in ("CE", "PE"):
            raise ValueError("Only NIFTY CE/PE options are supported")
        quote_price = finite_price(quote.get("ask" if action == "BUY" else "bid"))
        if not quote_price:
            raise ValueError("No executable bid/ask price available: paper trade rejected")
        qty = lots * self.lot_size
        fill = money(quote_price)
        gross = fill * qty
        expiry, security_id = quote["expiry"], int(quote["security_id"])
        with self._lock, sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            cash = self._meta(db, "cash")
            realized = Decimal("0.00")
            row = db.execute("SELECT qty,avg_price FROM paper_positions WHERE expiry=? AND security_id=?",
                             (expiry, security_id)).fetchone()
            if action == "BUY":
                if gross > cash:
                    raise ValueError(f"Insufficient virtual balance: need ₹{gross}, available ₹{cash}")
                current_qty, current_avg = (int(row[0]), Decimal(row[1])) if row else (0, Decimal(0))
                new_qty = current_qty + qty
                avg = money((current_avg * current_qty + gross) / new_qty)
                db.execute("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?) "
                           "ON CONFLICT(expiry,security_id) DO UPDATE SET qty=excluded.qty,avg_price=excluded.avg_price",
                           (expiry, security_id, quote["symbol"], quote["kind"], quote["strike"], new_qty, str(avg)))
                cash -= gross
            else:
                if not row or int(row[0]) < qty:
                    raise ValueError("Not enough open quantity to sell; short selling is disabled")
                current_qty, avg = int(row[0]), Decimal(row[1])
                realized = money((fill - avg) * qty)
                remaining = current_qty - qty
                if remaining:
                    db.execute("UPDATE paper_positions SET qty=? WHERE expiry=? AND security_id=?",
                               (remaining, expiry, security_id))
                else:
                    db.execute("DELETE FROM paper_positions WHERE expiry=? AND security_id=?",
                               (expiry, security_id))
                cash += gross
            db.execute("UPDATE paper_meta SET value=? WHERE key='cash'", (str(cash),))
            total_realized = self._meta(db, "realized_pnl") + realized
            db.execute("UPDATE paper_meta SET value=? WHERE key='realized_pnl'", (str(total_realized),))
            stamped = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO paper_trades(timestamp_utc,action,expiry,security_id,symbol,lots,qty,fill_price,gross_value,cash_after,realized_pnl) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (stamped, action, expiry, security_id, quote["symbol"], lots, qty,
                        str(fill), str(gross), str(cash), str(realized)))
            trade_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
        return {"id": trade_id, "paper_only": True, "action": action, "symbol": quote["symbol"],
                "qty": qty, "fill_price": float(fill), "cash_after": float(cash),
                "realized_pnl": float(realized)}

    def snapshot(self, options: DhanOptions) -> dict:
        with self._lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            cash = self._meta(db, "cash")
            realized = self._meta(db, "realized_pnl")
            initial = self._meta(db, "initial_cash")
            positions = []
            value = Decimal(0)
            unrealized = Decimal(0)
            not_priced = 0
            for row in db.execute("SELECT * FROM paper_positions ORDER BY expiry, strike"):
                position = dict(row)
                avg = Decimal(position["avg_price"])
                quote = options.quote(position["expiry"], position["security_id"])
                mark = finite_price(quote.get("bid")) if quote else None
                position["avg_price"] = float(avg)
                if mark is None:
                    position["mark_bid"] = None
                    position["unrealized_pnl"] = None
                    not_priced += 1
                else:
                    bid = money(mark)
                    position["mark_bid"] = float(bid)
                    position["unrealized_pnl"] = float((bid - avg) * position["qty"])
                    value += bid * position["qty"]
                    unrealized += (bid - avg) * position["qty"]
                positions.append(position)
            trades = [dict(row) for row in db.execute(
                "SELECT * FROM paper_trades ORDER BY id DESC LIMIT 40")]
        for trade in trades:
            for key in ("fill_price", "gross_value", "cash_after", "realized_pnl"):
                trade[key] = float(trade[key])
        complete = not_priced == 0
        return {
            "paper_only": True, "lot_size": self.lot_size,
            "initial_cash": float(initial), "cash": float(cash),
            "realized_pnl": float(realized),
            "unrealized_pnl": float(unrealized) if complete else None,
            "equity": float(cash + value) if complete else None,
            "unpriced_positions": not_priced,
            "positions": positions, "trades": trades,
            "note": "Long CE/PE only. Buy at ask, sell at bid. No broker orders, fees, slippage, margin or expiry settlement.",
        }


class PaperOrder(BaseModel):
    action: Literal["BUY", "SELL"]
    expiry: str
    security_id: int = Field(gt=0)
    lots: int = Field(ge=1, le=20)


def register_routes(app: FastAPI, root: Path, client_id: str, access_token: str):
    """Mount read-only option market data and simulated paper-only endpoints."""
    options = DhanOptions(client_id, access_token)
    ledger = PaperLedger(root / "data" / "paper_options.sqlite3",
                         initial_cash=int(os.getenv("TB_PAPER_STARTING_CASH", "100000")),
                         lot_size=int(os.getenv("TB_PAPER_OPTION_LOT_SIZE", "65")))
    app.state.paper_options = options
    app.state.paper_ledger = ledger

    @app.get("/api/options/expiries")
    def option_expiries():
        try:
            return {"underlying": "NIFTY 50 SPOT", "expiries": options.expiries()}
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/options/chain")
    def option_chain(expiry: str = Query(..., min_length=10, max_length=10)):
        try:
            return options.chain(expiry, window=15)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/paper")
    def paper_state():
        return ledger.snapshot(options)

    @app.post("/api/paper/orders")
    def paper_order(body: PaperOrder):
        # Explicitly refuse trades on absent/stale option-chain prices.
        quote = options.quote(body.expiry, body.security_id, max_age=20)
        if quote is None:
            raise HTTPException(409, "Quote missing or older than 20 seconds. Refresh the option chain and retry")
        try:
            return ledger.order(action=body.action, lots=body.lots, quote=quote)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
