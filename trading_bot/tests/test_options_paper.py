import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_bot.options_paper import DhanOptions, PaperLedger, normalize_chain, register_routes

EXPIRY = '2026-11-23'


def fixture_data():
    return {
        'status': 'success',
        'data': {'last_price': 25000,
                 'oc': {'25000.000000': {'ce': {'security_id': 123, 'last_price': 100,
                     'top_bid_price': 98, 'top_ask_price': 102, 'oi': 2000, 'volume': 1000,
                     'implied_volatility': 13.1, 'greeks': {'delta': 0.5}},
                     'pe': {'security_id': 124, 'last_price': 85, 'top_bid_price': 84,
                     'top_ask_price': 86, 'oi': 3000}},
                        '25050.000000': {'ce': {'security_id': 125, 'last_price': 90,
                     'top_bid_price': 89, 'top_ask_price': 91}}}}
    }


def make_service():
    now = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc).timestamp()
    calls = []
    def transport(path, body):
        calls.append((path, body))
        return {'status': 'success', 'data': [EXPIRY]} if path.endswith('expirylist') else fixture_data()
    opts = DhanOptions('test', 'test', transport=transport, now=lambda: now)
    return opts, calls


def test_dhan_exact_underlying_and_option_payload():
    opts, calls = make_service()
    assert opts.expiries() == [EXPIRY]
    chain = opts.chain(EXPIRY)
    assert chain['rows'][0]['ce']['bid'] == 98
    assert chain['rows'][0]['pe']['ask'] == 86
    assert chain['spot'] == 25000
    assert calls[0] == ('/optionchain/expirylist', {'UnderlyingScrip':13,'UnderlyingSeg':'IDX_I'})
    assert calls[1] == ('/optionchain', {'UnderlyingScrip':13,'UnderlyingSeg':'IDX_I','Expiry':EXPIRY})


def test_cache_limits_remote_calls():
    opts, calls = make_service()
    opts.chain(EXPIRY)
    opts.chain(EXPIRY)
    assert len(calls) == 2


def test_invalid_and_unlisted_expiries_rejected():
    opts, _ = make_service()
    for value in ('2026/11/23', '2026-11-24', '../../secret'):
        with pytest.raises(ValueError): opts.chain(value)


def test_missing_option_leg_or_invalid_quote_not_tradeable():
    raw = fixture_data()
    raw['data']['oc']['25000.000000']['ce']['top_ask_price'] = 'nan'
    normalized = normalize_chain(raw, EXPIRY, 1)
    assert normalized['quotes'][123]['ask'] is None
    assert normalized['quotes'][124]['bid'] == 84


def test_paper_buy_sell_positions_cash_and_realized(tmp_path):
    opts, _ = make_service()
    opts.chain(EXPIRY)
    quote = opts.quote(EXPIRY,123)
    ledger=PaperLedger(tmp_path/'paper.db',initial_cash=100000,lot_size=65)
    entry=ledger.order(action='BUY',lots=1,quote=quote)
    assert entry['qty']==65
    assert entry['fill_price']==102
    snapshot=ledger.snapshot(opts)
    assert snapshot['cash']==93370.0
    assert snapshot['unrealized_pnl']==-260
    assert snapshot['equity']==99740
    ledger.order(action='SELL',lots=1,quote=quote)
    after=ledger.snapshot(opts)
    assert after['cash']==99740
    assert after['realized_pnl']==-260
    assert not after['positions']
    assert len(after['trades'])==2
    # Must survive app restart.
    again=PaperLedger(tmp_path/'paper.db',initial_cash=99999,lot_size=65)
    assert again.snapshot(opts)['cash']==99740


def test_no_naked_short_or_insufficient_cash(tmp_path):
    opts,_=make_service(); opts.chain(EXPIRY)
    quote=opts.quote(EXPIRY,123)
    ledger=PaperLedger(tmp_path/'p.db',initial_cash=1000,lot_size=65)
    with pytest.raises(ValueError,match='Insufficient'):
        ledger.order(action='BUY',lots=1,quote=quote)
    with pytest.raises(ValueError,match='short selling'):
        ledger.order(action='SELL',lots=1,quote=quote)
    assert ledger.snapshot(opts)['cash']==1000


def test_reject_zero_bid_and_bad_actions(tmp_path):
    opts,_=make_service();opts.chain(EXPIRY)
    q=dict(opts.quote(EXPIRY,123));q['bid']=0
    l=PaperLedger(tmp_path/'p.db')
    with pytest.raises(ValueError,match='No executable'):
        l.order(action='SELL',lots=1,quote=q)
    with pytest.raises(ValueError):
        l.order(action='SHORT',lots=1,quote=q)


def test_quote_age_prevents_paper_fills():
    now=[datetime(2026,11,13,7,0,tzinfo=timezone.utc).timestamp()]
    opts,_=make_service()
    opts.now=lambda: now[0]
    opts.chain(EXPIRY)
    now[0]+=21
    assert opts.quote(EXPIRY,123) is None


def test_api_endpoints_and_only_simulated_orders(tmp_path,monkeypatch):
    monkeypatch.setenv('TB_PAPER_STARTING_CASH','100000')
    monkeypatch.setenv('TB_PAPER_OPTION_LOT_SIZE','65')
    app=FastAPI()
    register_routes(app,tmp_path,'client','token')
    opts, _=make_service()
    app.state.paper_options.transport=opts.transport
    app.state.paper_options.now=opts.now
    client=TestClient(app)
    resp=client.get('/api/options/expiries')
    assert resp.status_code==200
    resp=client.get('/api/options/chain',params={'expiry':EXPIRY})
    assert resp.status_code==200
    resp=client.post('/api/paper/orders',json={'action':'BUY','expiry':EXPIRY,'security_id':123,'lots':1})
    assert resp.status_code==200 and resp.json()['paper_only'] is True
    assert client.get('/api/paper').json()['positions'][0]['qty']==65
    bad=client.post('/api/paper/orders',json={'action':'SELL','expiry':EXPIRY,'security_id':999,'lots':1})
    assert bad.status_code==409
