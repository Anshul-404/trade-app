"""No live trading or Dhan requests in these unit tests."""
from datetime import datetime, timezone

import pytest

from trading_bot.movers import parse_constituents, match_dhan_equities, calculate_movers, MoversService
from trading_bot.paper_risk import PaperRisk, BracketRequest
from trading_bot.options_paper import DhanOptions, PaperLedger

EXP='2026-11-23'


def constituents():
    return 'Company Name,Symbol,Series\n'+'\n'.join(f'C{i},SYM{i},EQ' for i in range(50))+'\n'


def master():
    header='SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SERIES,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_CUSTOM_SYMBOL,SM_SYMBOL_NAME\n'
    return header+'\n'.join(f'NSE,E,EQ,{1000+i},SYM{i}-EQ,,' for i in range(50))+'\n'


def quote_data():
    return {'status':'success','data':{'NSE_EQ':{str(1000+i):{'last_price':100+i, 'net_change':i-24} for i in range(50)}}}


def test_nifty50_mapping_and_strict_membership():
    s=parse_constituents(constituents())
    assert len(s)==50
    mapped=match_dhan_equities(master(),s)
    assert len(mapped)==50 and mapped['SYM0']==1000


def test_no_extra_non_nifty_or_non_eq_mapping():
    s=parse_constituents(constituents())
    text=master()+'NSE,E,BE,99999,SYM0-EQ,,\nNSE,E,EQ,99998,NOTNIFTY-EQ,,\n'
    assert match_dhan_equities(text,s)['SYM0']==1000
    assert 99998 not in match_dhan_equities(text,s).values()


def test_invalid_constituents_rejected():
    for content in ('Symbol,Series\nA,EQ\n','Name,Series\nB,EQ\n'):
        with pytest.raises(ValueError):parse_constituents(content)


def test_movers_sign_and_percentage_and_top25():
    mapped={f'SYM{i}':1000+i for i in range(50)}
    result=calculate_movers(quote_data(),mapped)
    assert all(x['change']>0 for x in result['winners'])
    assert all(x['change']<0 for x in result['losers'])
    assert len(result['winners'])==25
    assert len(result['losers'])==24
    assert result['unchanged_count']==1
    assert result['winners'][0]['change_pct'] >= result['winners'][-1]['change_pct']


def test_movers_snapshot_cache(tmp_path):
    counter={'post':0}
    now=[100.0]
    def fake_fetch(url,cap):
        return constituents() if 'nifty50list' in url else master()
    def fake_post(ids):
        counter['post']+=1
        return quote_data()
    service=MoversService('test','test',root=tmp_path,fetch=fake_fetch,post=fake_post,clock=lambda:now[0])
    first=service.get()
    now[0]+=10
    second=service.get()
    assert first['valid_quotes']==50 and second['age_seconds']==10
    assert counter['post']==1
    now[0]+=21
    service.get()
    assert counter['post']==2


def fake_options():
    now=datetime(2026,11,13,8,tzinfo=timezone.utc).timestamp()
    raw={'status':'success','data':{'last_price':25000,
      'oc':{'25000.000000':{'ce':{'security_id':123,'last_price':100,'top_bid_price':98,'top_ask_price':102},
                             'pe':{'security_id':124,'last_price':90,'top_bid_price':84,'top_ask_price':86}}}}}
    def transport(url,payload):
        return {'status':'success','data':[EXP]} if 'expirylist' in url else raw
    options=DhanOptions('test','test',transport=transport,now=lambda:now)
    options.chain(EXP)
    return options


def test_save_sl_tp_and_persistence(tmp_path):
    opts=fake_options()
    ledger=PaperLedger(tmp_path/'paper.db')
    ledger.order(action='BUY',lots=1,quote=opts.quote(EXP,123))
    risk=PaperRisk(ledger,opts)
    assert risk.save(BracketRequest(expiry=EXP,security_id=123,sl=80,tp=140))['sl']==80
    assert risk.list()['positions'][0]['tp']==140
    assert PaperRisk(PaperLedger(tmp_path/'paper.db'),opts).list()['positions'][0]['sl']==80
    with pytest.raises(ValueError,match='below entry'):
        risk.save(BracketRequest(expiry=EXP,security_id=123,sl=103,tp=140))


def test_close_all_paper_only_atomic_pnl(tmp_path):
    opts=fake_options()
    ledger=PaperLedger(tmp_path/'p.db')
    ledger.order(action='BUY',lots=1,quote=opts.quote(EXP,123))
    ledger.order(action='BUY',lots=1,quote=opts.quote(EXP,124))
    risk=PaperRisk(ledger,opts)
    risk.save(BracketRequest(expiry=EXP,security_id=123,sl=80,tp=140))
    result=risk.close_all()
    assert result['paper_only'] is True and result['closed']==2
    assert result['realized_pnl']==(98-102)*65 + (84-86)*65
    snap=ledger.snapshot(opts)
    assert not snap['positions']
    assert len(snap['trades'])==4
    assert result['cash_after']==100000+result['realized_pnl']
    assert risk.list()['positions']==[]


def test_close_all_refuses_all_when_one_quote_missing(tmp_path):
    opts=fake_options()
    ledger=PaperLedger(tmp_path/'p.db')
    ledger.order(action='BUY',lots=1,quote=opts.quote(EXP,123))
    ledger.order(action='BUY',lots=1,quote=opts.quote(EXP,124))
    risk=PaperRisk(ledger,opts)
    opts._chains[EXP]['quotes'][124]['bid']=None
    with pytest.raises(ValueError,match='nothing was closed'):
        risk.close_all()
    assert len(ledger.snapshot(opts)['positions'])==2
    assert len(ledger.snapshot(opts)['trades'])==2


def test_close_all_empty_is_idempotent(tmp_path):
    risk=PaperRisk(PaperLedger(tmp_path/'paper.db'),fake_options())
    assert risk.close_all()['closed']==0
    assert risk.close_all()['closed']==0


def test_missing_options_quotes_no_network_fallback(tmp_path):
    opts=fake_options()
    ledger=PaperLedger(tmp_path/'paper.db')
    ledger.order(action='BUY',lots=1,quote=opts.quote(EXP,123))
    risk=PaperRisk(ledger,opts)
    opts._chains.clear()
    with pytest.raises(ValueError, match='stale bid'):
        risk.close_all()
    assert len(ledger.snapshot(opts)['positions'])==1


def test_close_all_endpoint_requires_two_confirmations(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from trading_bot.paper_risk import register_dashboard_plus
    opts=fake_options()
    ledger=PaperLedger(tmp_path/'paper.db')
    ledger.order(action='BUY',lots=1,quote=opts.quote(EXP,123))
    app=FastAPI()
    app.state.paper_options=opts
    app.state.paper_ledger=ledger
    register_dashboard_plus(app,tmp_path,'test','test')
    client=TestClient(app)
    assert client.post('/api/paper/close-all',json={'confirm':'CLOSE_ALL_PAPER'}).status_code==400
    assert client.post('/api/paper/close-all',json={'confirm':'WRONG'},headers={'X-Paper-Action':'paper-close-all'}).status_code==400
    assert len(client.get('/api/paper/risk').json()['positions'])==1
    response=client.post('/api/paper/close-all',json={'confirm':'CLOSE_ALL_PAPER'},headers={'X-Paper-Action':'paper-close-all'})
    assert response.status_code==200 and response.json()['closed']==1
    assert client.get('/api/paper/risk').json()['positions']==[]
