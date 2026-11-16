"""NIFTY 50 cash-equity movers. All market prices originate in Dhan snapshots.

A 20-second cache and one bulk quote request keep this off the chart WebSocket.
Constituent membership is downloaded from NSE Indices, never guessed from names.
"""
from __future__ import annotations

import csv
import io
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

INDICES_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
DHAN_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DHAN_QUOTE_URL = "https://api.dhan.co/v2/marketfeed/quote"


def _get(url: str, limit: int) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 TradingBot/1.0", "Accept": "text/csv,*/*"})
    try:
        with urlopen(req, timeout=12) as response:
            body = response.read(limit + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Cannot download current NIFTY 50 constituents or Dhan instruments") from None
    if len(body) > limit:
        raise ValueError("Instrument/constituent CSV exceeded safety limit")
    return body.decode('utf-8-sig', errors='replace')


def parse_constituents(content: str) -> list[str]:
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    headers = {str(k).strip().lower(): k for k in reader.fieldnames or []}
    if 'symbol' not in headers:
        raise ValueError('NSE NIFTY 50 constituent file has no Symbol column')
    key = headers['symbol']
    series_key = headers.get('series')
    out = []
    seen = set()
    for row in reader:
        value = str(row.get(key) or '').strip().upper()
        series = str(row.get(series_key) or 'EQ').strip().upper() if series_key else 'EQ'
        if not value or series != 'EQ' or value in seen:
            continue
        seen.add(value)
        out.append(value)
    if not 40 <= len(out) <= 60:
        raise ValueError('Unexpected NSE NIFTY 50 constituent count; rejecting incomplete list')
    return out


def _equity_symbol(raw: str) -> str:
    name = raw.strip().upper()
    return name[:-3] if name.endswith('-EQ') else name


def match_dhan_equities(content: str, symbols: list[str]) -> dict[str, int]:
    allowed = set(symbols)
    found: dict[str, int] = {}
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    required = {'SEM_EXM_EXCH_ID', 'SEM_SEGMENT', 'SEM_SMST_SECURITY_ID'}
    if not required.issubset(set(reader.fieldnames or [])):
        raise ValueError('Dhan instrument CSV does not have the required columns')
    for row in reader:
        if (row.get('SEM_EXM_EXCH_ID') or '').strip().upper() != 'NSE':
            continue
        if (row.get('SEM_SEGMENT') or '').strip().upper() != 'E':
            continue
        series = (row.get('SEM_SERIES') or '').strip().upper()
        if series and series != 'EQ':
            continue
        trading = _equity_symbol(row.get('SEM_TRADING_SYMBOL') or '')
        custom = _equity_symbol(row.get('SEM_CUSTOM_SYMBOL') or '')
        name = _equity_symbol(row.get('SM_SYMBOL_NAME') or '')
        symbol = next((s for s in (trading, custom, name) if s in allowed), None)
        if symbol is None:
            continue
        raw_id = (row.get('SEM_SMST_SECURITY_ID') or '').strip()
        if raw_id.isdecimal() and int(raw_id) > 0:
            found.setdefault(symbol, int(raw_id))
    return found


def calculate_movers(raw: dict, symbols_to_ids: dict[str, int]) -> dict:
    if raw.get('status') != 'success':
        raise ValueError('Dhan market quote request did not succeed')
    quotes = raw.get('data', {}).get('NSE_EQ', {})
    if not isinstance(quotes, dict):
        raise ValueError('Dhan NSE_EQ quote data missing')
    movers = []
    for symbol, sid in symbols_to_ids.items():
        item = quotes.get(str(sid))
        if not isinstance(item, dict):
            continue
        try:
            last = float(item.get('last_price'))
            change = float(item.get('net_change'))
        except (ValueError, TypeError, OverflowError):
            continue
        previous = last - change
        if not all(math.isfinite(v) for v in (last, change, previous)) or previous <= 0 or last <= 0:
            continue
        movers.append({'symbol': symbol, 'security_id': sid, 'price': round(last, 2),
                       'change': round(change, 2), 'change_pct': round(100.0 * change / previous, 2)})
    winners = sorted((x for x in movers if x['change'] > 0), key=lambda x: -x['change_pct'])[:25]
    losers = sorted((x for x in movers if x['change'] < 0), key=lambda x: x['change_pct'])[:25]
    return {'winners': winners, 'losers': losers, 'valid_quotes': len(movers),
            'unchanged_count': len(movers) - sum(x['change'] != 0 for x in movers)}


class MoversService:
    def __init__(self, client_id: str, access_token: str, *, root: Path, fetch=None, post=None, clock=None):
        self.client_id, self.token = client_id, access_token
        self.root = Path(root)
        self.fetch = fetch or _get
        self.post = post or self._dhan_post
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._symbol_ids = None
        self._mapped_at = 0.0
        self._result = None
        self._checked_at = 0.0

    def _dhan_post(self, ids: list[int]):
        if not self.client_id or not self.token:
            raise RuntimeError('Dhan API credentials missing in .env')
        req = Request(DHAN_QUOTE_URL,
                      data=json.dumps({'NSE_EQ': ids}).encode('utf-8'), method='POST',
                      headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                               'client-id': self.client_id, 'access-token': self.token})
        try:
            with urlopen(req, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f'Dhan market quote HTTP {exc.code}; check subscription/token') from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError('Dhan market quotes unavailable; check connection') from None

    def _load_ids(self):
        # The official NSE file can be supplied manually if NSE blocks automated requests.
        local = self.root / 'data' / 'nifty50_constituents.csv'
        constituents = local.read_text(encoding='utf-8-sig') if local.exists() else self.fetch(INDICES_URL, 120_000)
        names = parse_constituents(constituents)
        master = self.fetch(DHAN_MASTER_URL, 55_000_000)
        matched = match_dhan_equities(master, names)
        if len(matched) < 40:
            raise RuntimeError(f'Only {len(matched)} of {len(names)} NIFTY constituents mapped to Dhan; check instrument CSV')
        return matched, len(names)

    def get(self):
        with self._lock:
            now = self.clock()
            if self._result and now - self._checked_at < 20:
                return dict(self._result, age_seconds=round(now - self._checked_at, 1))
            if self._symbol_ids is None or now - self._mapped_at > 6*3600:
                try:
                    mapped, count = self._load_ids()
                    self._symbol_ids = (mapped, count)
                    self._mapped_at = self.clock()
                except (RuntimeError, ValueError, OSError) as exc:
                    if self._symbol_ids is None:
                        raise RuntimeError(str(exc)) from None
            try:
                mapped, count = self._symbol_ids
                ranked = calculate_movers(self.post(list(mapped.values())), mapped)
                if ranked['valid_quotes'] < 40:
                    raise RuntimeError(f'Only {ranked["valid_quotes"]} usable Dhan quotes; cannot rank reliably')
                self._checked_at = self.clock()
                self._result = dict(ranked, universe='NIFTY 50 NSE equities',
                                    constituents=count, mapped=len(mapped),
                                    snapshot_utc=datetime.fromtimestamp(self._checked_at,timezone.utc).isoformat(),
                                    source='Dhan marketfeed/quote', stale=False)
            except (RuntimeError, ValueError) as exc:
                if not self._result:
                    raise RuntimeError(str(exc)) from None
                # Return stale data clearly labeled, never as real-time.
                return dict(self._result, age_seconds=round(now - self._checked_at, 1),
                            stale=True, warning=str(exc))
            return dict(self._result, age_seconds=0.0)
