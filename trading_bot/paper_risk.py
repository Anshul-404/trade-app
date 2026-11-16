"""Paper-only risk references and atomic SELL ALL for simulated long options.

SL/TP references NEVER trigger broker or automatic paper orders. Sell-all is an
explicit user action, uses fresh bids, and is one SQLite transaction.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .options_paper import PaperLedger, DhanOptions, finite_price, money


class BracketRequest(BaseModel):
    expiry: str
    security_id: int = Field(gt=0)
    sl: float | None = None
    tp: float | None = None


class CloseAllRequest(BaseModel):
    confirm: str


class PaperRisk:
    def __init__(self, ledger: PaperLedger, options: DhanOptions):
        self.ledger = ledger
        self.options = options
        with self.ledger._lock, sqlite3.connect(self.ledger.path) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS paper_risk_levels (
                expiry TEXT NOT NULL, security_id INTEGER NOT NULL,
                sl_price TEXT, tp_price TEXT, updated_utc TEXT NOT NULL,
                PRIMARY KEY(expiry, security_id))''')
            db.commit()

    def list(self) -> dict:
        snapshot = self.ledger.snapshot(self.options)
        with self.ledger._lock, sqlite3.connect(self.ledger.path) as db:
            levels = {(e, int(s)): (float(sl) if sl is not None else None,
                                     float(tp) if tp is not None else None)
                      for e, s, sl, tp in db.execute(
                          'SELECT expiry, security_id, sl_price, tp_price FROM paper_risk_levels')}
        for p in snapshot['positions']:
            p['sl'], p['tp'] = levels.get((p['expiry'], p['security_id']), (None, None))
        return {'paper_only': True, 'positions': snapshot['positions'],
                'note': 'SL/TP are saved VISUAL references, not automatic triggers. Premium units (₹).'}

    def save(self, body: BracketRequest) -> dict:
        def validate(value, label):
            if value is None:
                return None
            if not isinstance(value, (int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{label} must be a positive finite premium or blank')
            return money(value)
        stop, target = validate(body.sl,'SL'), validate(body.tp,'TP')
        if stop is not None and target is not None and stop >= target:
            raise ValueError('For long options, SL must be below TP')
        with self.ledger._lock, sqlite3.connect(self.ledger.path, timeout=10) as db:
            row = db.execute('SELECT avg_price FROM paper_positions WHERE expiry=? AND security_id=?',
                             (body.expiry, body.security_id)).fetchone()
            if row is None:
                raise ValueError('No open paper position for that option')
            avg = Decimal(row[0])
            if stop is not None and stop >= avg:
                raise ValueError('For long option positions, SL must be below entry average')
            if target is not None and target <= avg:
                raise ValueError('For long option positions, TP must be above entry average')
            if stop is None and target is None:
                db.execute('DELETE FROM paper_risk_levels WHERE expiry=? AND security_id=?',
                           (body.expiry,body.security_id))
            else:
                db.execute('''INSERT INTO paper_risk_levels(expiry,security_id,sl_price,tp_price,updated_utc)
                              VALUES (?,?,?,?,?) ON CONFLICT(expiry,security_id) DO UPDATE SET
                              sl_price=excluded.sl_price,tp_price=excluded.tp_price,
                              updated_utc=excluded.updated_utc''',
                           (body.expiry, body.security_id, str(stop) if stop is not None else None,
                            str(target) if target is not None else None, datetime.now(timezone.utc).isoformat()))
            db.commit()
        return {'paper_only': True, 'expiry':body.expiry, 'security_id':body.security_id,
                'sl':float(stop) if stop is not None else None,
                'tp':float(target) if target is not None else None}

    def close_all(self) -> dict:
        with self.ledger._lock, sqlite3.connect(self.ledger.path,timeout=10) as db:
            db.row_factory = sqlite3.Row
            # IMPORTANT: perform all validation before the first journal change.
            db.execute('BEGIN IMMEDIATE')
            positions = [dict(p) for p in db.execute('SELECT * FROM paper_positions ORDER BY expiry,security_id')]
            if not positions:
                db.rollback()
                return {'paper_only':True, 'closed':0, 'positions':[], 'realized_pnl':0.0,
                        'message':'No open paper positions to close'}
            executable = []
            for position in positions:
                if int(position['qty']) <= 0 or int(position['qty']) % self.ledger.lot_size:
                    raise ValueError('Invalid paper position quantity; no positions closed')
                quote = self.options.quote(position['expiry'], int(position['security_id']), max_age=20)
                bid = finite_price(quote.get('bid')) if quote else None
                if bid is None:
                    # No partial liquidation. The user must refresh relevant expiries.
                    raise ValueError('Missing/stale bid for ' + position['symbol'] +
                                     '. Refresh that option expiry; nothing was closed')
                if (quote.get('expiry') != position['expiry'] or
                        int(quote.get('security_id',0)) != int(position['security_id'])):
                    raise ValueError('Quote identity mismatch; nothing was closed')
                executable.append((position, money(bid)))
            cash = self.ledger._meta(db,'cash')
            total_realized = self.ledger._meta(db,'realized_pnl')
            realized_all = Decimal('0.00')
            trades = []
            for position, fill in executable:
                qty = int(position['qty'])
                gross = fill * qty
                pnl = money((fill - Decimal(position['avg_price'])) * qty)
                cash += gross
                total_realized += pnl
                realized_all += pnl
                db.execute('DELETE FROM paper_positions WHERE expiry=? AND security_id=?',
                           (position['expiry'], position['security_id']))
                db.execute('DELETE FROM paper_risk_levels WHERE expiry=? AND security_id=?',
                           (position['expiry'], position['security_id']))
                db.execute('''INSERT INTO paper_trades(timestamp_utc,action,expiry,security_id,
                              symbol,lots,qty,fill_price,gross_value,cash_after,realized_pnl)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                           (datetime.now(timezone.utc).isoformat(),'SELL',position['expiry'],
                            position['security_id'],position['symbol'],qty // self.ledger.lot_size,
                            qty,str(fill),str(gross),str(cash),str(pnl)))
                trades.append({'symbol': position['symbol'], 'qty':qty, 'fill_price':float(fill),
                               'realized_pnl':float(pnl)})
            db.execute("UPDATE paper_meta SET value=? WHERE key='cash'",(str(cash),))
            db.execute("UPDATE paper_meta SET value=? WHERE key='realized_pnl'",(str(total_realized),))
            db.commit()
        return {'paper_only':True, 'closed':len(trades), 'positions':trades,
                'realized_pnl':float(realized_all), 'cash_after':float(cash),
                'message':'Closed all simulated long option positions at cached fresh bids'}


def register_dashboard_plus(app: FastAPI, root, client_id: str, access_token: str) -> None:
    from .movers import MoversService
    if not hasattr(app.state, 'paper_options') or not hasattr(app.state, 'paper_ledger'):
        raise RuntimeError('Paper options panel must be installed before Dashboard Plus')
    movers = MoversService(client_id, access_token, root=root)
    risk = PaperRisk(app.state.paper_ledger, app.state.paper_options)
    app.state.movers = movers
    app.state.paper_risk = risk

    @app.get('/api/movers')
    def get_movers():
        try:
            return movers.get()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(503,str(exc)) from None

    @app.get('/api/paper/risk')
    def get_risk():
        return risk.list()

    @app.post('/api/paper/risk')
    def set_risk(body: BracketRequest):
        try:
            return risk.save(body)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None

    @app.post('/api/paper/close-all')
    def close_all(body: CloseAllRequest, x_paper_action: str | None = Header(default=None)):
        if body.confirm != 'CLOSE_ALL_PAPER' or x_paper_action != 'paper-close-all':
            raise HTTPException(400,'Explicit paper-only confirmation is required')
        try:
            return risk.close_all()
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None
