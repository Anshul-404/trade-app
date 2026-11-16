import csv
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from replay_server import HistoryStore, TIMEFRAMES, make_handler


class ReplayServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)/'data'/'historical'/'NIFTY50_13'
        self.folder.mkdir(parents=True)
        stamp=datetime(2024,1,2,9,15,tzinfo=ZoneInfo('Asia/Kolkata'))
        self.bars=[]
        for i in range(205):
            epoch=int((stamp+timedelta(minutes=i)).timestamp())
            op=21000+i
            self.bars.append({'timestamp':'2024-01-02 09:15:00+0530','epoch':epoch,'open':op,'high':op+3,'low':op-4,'close':op+1,'volume':i,'open_interest':''})
        for tf in TIMEFRAMES:
            with (self.folder/f'{tf}.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(self.bars[0]));writer.writeheader();writer.writerows(self.bars)
        self.store=HistoryStore(self.folder)

    def test_catalog(self):
        catalog=self.store.catalog()
        self.assertEqual(len(catalog['timeframes']),7)
        self.assertEqual(catalog['timeframes'][0]['count'],205)

    def test_no_future_prices_in_session(self):
        res=self.store.start_session('1m', index=190)
        self.assertEqual(res['cursor'],190)
        self.assertEqual(len(res['history']),180)
        self.assertEqual(res['history'][-1]['time'],self.bars[189]['epoch'])
        self.assertNotIn(self.bars[190]['epoch'],[b['time'] for b in res['history']])
        nxt=self.store.step('1m',res['cursor'],1)
        self.assertEqual(nxt['bars'][0]['time'],self.bars[190]['epoch'])

    def test_step_to_end_and_bounds(self):
        res=self.store.step('5m',200,50)
        self.assertEqual(len(res['bars']),5)
        self.assertTrue(res['done'])
        with self.assertRaisesRegex(ValueError,'count'):
            self.store.step('1m',0,51)
        with self.assertRaisesRegex(ValueError,'cursor'):
            self.store.step('1m',-1,1)
        with self.assertRaisesRegex(ValueError,'No candles'):
            self.store.step('3m',1,1)

    def test_start_date_IST(self):
        res=self.store.start_session('1m',date='2024-01-02T09:20')
        self.assertEqual(res['cursor'],5)
        self.assertEqual(len(res['history']),5)

    def test_no_lookahead_on_higher_timeframes(self):
        # A 15m bar starting at 09:15 must NOT be shown at 09:20; its
        # complete high/low/close become visible at 09:30, not at the open.
        at_920=self.store.start_session('15m',date='2024-01-02T09:20')
        self.assertEqual(at_920['cursor'],0)
        at_930=self.store.start_session('15m',date='2024-01-02T09:30')
        self.assertEqual(at_930['cursor'],1)
        # Weekly bars are stamped with a first-trading-day start, but
        # cannot be known before that week's Friday market close.
        self.assertEqual(self.store.start_session('1w',date='2024-01-03T11:00')['cursor'],0)
        self.assertEqual(self.store.start_session('1w',date='2024-01-05T15:30')['cursor'],205)

    def test_http(self):
        srv=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.store))
        task=threading.Thread(target=srv.serve_forever,daemon=True)
        task.start()
        self.addCleanup(lambda:(srv.shutdown(),srv.server_close(),task.join(timeout=3)))
        base=f'http://127.0.0.1:{srv.server_port}'
        catalog=json.load(urllib.request.urlopen(base+'/api/catalog'))
        self.assertEqual(catalog['symbol'],'NIFTY50_13')
        info=json.load(urllib.request.urlopen(base+'/api/start?tf=1m&index=200'))
        self.assertEqual(info['history'][-1]['time'],self.bars[199]['epoch'])
        batch=json.load(urllib.request.urlopen(base+'/api/step?tf=1m&cursor=200&count=10'))
        self.assertEqual(len(batch['bars']),5)
        req=urllib.request.Request(base+'/api/step?tf=1m&cursor=0&count=99')
        with self.assertRaises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        self.assertEqual(exc.exception.code,400)
        with urllib.request.urlopen(base+'/') as reply:
            self.assertIn(b'TRADING BOT',reply.read())

    def test_invalid_ohlc_is_skipped_and_audited(self):
        path=self.folder/'1m.csv'
        text=path.read_text()
        text=text.replace('21003,20996,21001','21003,22000,21001',1)
        path.write_text(text)
        reloaded=HistoryStore(self.folder)
        res=reloaded.start_session('1m', index=190)
        self.assertEqual(res['total'],204)
        self.assertEqual(res['quality']['skipped'],1)
        self.assertEqual(res['quality']['normalized'],0)
        report=self.folder/'replay_data_issues_1m.csv'
        self.assertTrue(report.is_file())
        with report.open(newline='') as fh:
            issues=list(csv.DictReader(fh))
        self.assertEqual(issues[0]['line'],'2')
        self.assertEqual(issues[0]['action'],'SKIPPED')
        self.assertEqual(issues[0]['low'],'22000')
        self.assertEqual(issues[0]['timestamp'],'2024-01-02 09:15:00+0530')
        self.assertIn('Invalid OHLC',issues[0]['reason'])
        # Source prices are never changed, even for outright invalid candles.
        self.assertIn('21003,22000,21001',path.read_text())

    def test_strict_mode_rejects_invalid_bar(self):
        path=self.folder/'1m.csv'
        path.write_text(path.read_text().replace('21003,20996,21001','21003,22000,21001',1))
        with self.assertRaisesRegex(ValueError, r'Invalid candle 1m.csv:2'):
            HistoryStore(self.folder, strict_data=True).candles('1m')

    def test_tiny_roundoff_normalized_without_editing_csv(self):
        path=self.folder/'1m.csv'
        original=path.read_text().replace('21003,20996,21001','21000.999999999,20996,21001',1)
        path.write_text(original)
        reload=HistoryStore(self.folder)
        bars,_=reload.candles('1m')
        self.assertEqual(len(bars),205)
        self.assertEqual(bars[0]['high'],21001)
        self.assertEqual(reload._quality['1m']['skipped'],0)
        self.assertEqual(reload._quality['1m']['normalized'],1)
        self.assertEqual(path.read_text(),original)
        with (self.folder/'replay_data_issues_1m.csv').open(newline='') as fh:
            self.assertEqual(next(csv.DictReader(fh))['action'],'ROUNDING_NORMALIZED')

    def test_malformed_ohlc_skipped_in_default_mode(self):
        path=self.folder/'1m.csv'
        path.write_text(path.read_text().replace('21003,20996,21001','non_numeric,20996,21001',1))
        bars,_=HistoryStore(self.folder).candles('1m')
        self.assertEqual(len(bars),204)

    def test_clean_data_has_no_quality_report(self):
        store=HistoryStore(self.folder)
        store.candles('1m')
        self.assertEqual(store._quality['1m']['skipped'],0)
        self.assertFalse((self.folder/'replay_data_issues_1m.csv').exists())


if __name__=='__main__': unittest.main()
