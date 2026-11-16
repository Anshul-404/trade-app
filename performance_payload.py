from pathlib import Path
p=Path(__file__).parent
candles=p/'trading_bot/candles.py'
s=candles.read_text(encoding='utf8')
needle='    def quality_summary(self) -> dict:\n'
assert s.count(needle)==1
new='''    def count_1m(self) -> int:
        """Constant-time counter for status updates, without candle aggregation."""
        with self._lock:
            return len(self._bars)

    def latest_bars(self) -> dict[str, dict | None]:
        """Only aggregate the forming candle per timeframe for WebSocket updates.

        All timeframes share a 09:15 IST session anchor and divide 60m, so
        we only need data in the latest 60-minute bucket, not thousands of
        historical candles for every incoming price update.
        """
        with self._lock:
            if not self._bars:
                return {str(tf): None for tf in SUPPORTED_MINUTES}
            latest = max(self._bars)
            first = time_bucket(latest, 60)
            recent = [(t, dict(b)) for t, b in self._bars.items() if t >= first and t <= latest]
        recent.sort(key=lambda item: item[0])
        result = {}
        for minutes in SUPPORTED_MINUTES:
            begin = time_bucket(latest, minutes)
            relevant = [bar for t, bar in recent if t >= begin]
            if not relevant:
                result[str(minutes)] = None
                continue
            agg = {**relevant[0], 'time': begin}
            for bar in relevant[1:]:
                agg['high'] = max(agg['high'], bar['high'])
                agg['low'] = min(agg['low'], bar['low'])
                agg['close'] = bar['close']
                agg['volume'] = (agg['volume'] + bar['volume']
                                 if agg['volume'] is not None and bar['volume'] is not None
                                 else None)
                if agg['source'] != bar['source']:
                    agg['source'] = 'mixed'
            result[str(minutes)] = agg
        return result

'''
s=s.replace(needle,new+needle)
candles.write_text(s,encoding='utf8')

app=p/'trading_bot/app.py'
s=app.read_text(encoding='utf8')
s=s.replace('from pathlib import Path\n','from pathlib import Path\nfrom time import monotonic\n')
s=s.replace('"candles_1m": len(store.snapshot(1, limit=6000)),','"candles_1m": store.count_1m(),\n        "server_time_utc": datetime.now(timezone.utc).isoformat(),')
old='''async def _run_broadcasts():
    last_version = -1
    while True:
        await asyncio.sleep(0.7)
        if not clients:
            continue
        if store.version != last_version:
            last_version = store.version
            bars = {str(tf): (store.snapshot(tf, limit=1) or [None])[-1]
                    for tf in SUPPORTED_MINUTES}
            message = {"type": "bars", "bars": bars, "status": _status()}
        else:
            message = {"type": "status", "status": _status()}
        dead = []
        for websocket in tuple(clients):
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            clients.discard(websocket)
'''
new='''async def _run_broadcasts():
    """Coalesce tick updates at 4 Hz; send a low-rate idle heartbeat.

    Avoid aggregating 500+ historical bars for all six chart timeframes on
    every tick. Reading and broadcasting remain asynchronous/read-only.
    """
    last_version = -1
    last_heartbeat = 0.0
    while True:
        await asyncio.sleep(0.25)
        if not clients:
            continue
        version = store.version
        now = monotonic()
        is_changed = version != last_version
        if not is_changed and now - last_heartbeat < 3.0:
            continue
        last_heartbeat = now
        if is_changed:
            bars = store.latest_bars()
            message = {"type": "bars", "bars": bars, "status": _status()}
            last_version = version
        else:
            message = {"type": "status", "status": _status()}
        dead = []
        for websocket in tuple(clients):
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            clients.discard(websocket)
'''
assert s.count(old)==1
app.write_text(s.replace(old,new),encoding='utf8')

z=p/'trading_bot/static/zones_layer.js'
s=z.read_text(encoding='utf8')
assert 'host.chart.subscribeCrosshairMove(redraw);' in s
s=s.replace('  host.chart.subscribeCrosshairMove(redraw);\n','')
assert '}, 1000);' in s
s=s.replace('}, 1000);','}, 2000);')
z.write_text(s,encoding='utf8')

js=p/'trading_bot/static/app.js'
s=js.read_text(encoding='utf8')
needle="  const container = document.getElementById('chart');\n"
assert s.count(needle)==1
inject='''  const feedAgeEl = document.createElement('span');
  feedAgeEl.id = 'feed-age';
  feedAgeEl.textContent = 'Feed age: —';
  document.querySelector('.below')?.appendChild(feedAgeEl);
  let latestTickUtc = null;
  let latestServerTimeUtc = null;
  function updateFeedAge() {
    if (!latestTickUtc) { feedAgeEl.textContent = 'Feed age: awaiting tick'; return; }
    const age = Math.max(0, (Date.now() - Date.parse(latestTickUtc)) / 1000);
    const delivery = latestServerTimeUtc ? Math.max(0, Date.now() - Date.parse(latestServerTimeUtc)) : null;
    feedAgeEl.textContent = 'Feed age ' + age.toFixed(1) + 's' +
       (delivery != null && delivery < 15000 ? ' · last server update ' + Math.round(delivery) + 'ms ago' : '');
    feedAgeEl.style.color = age > 5 ? '#f4b84f' : '#82cdb5';
  }
'''
s=s.replace(needle,needle+inject)
old="  setInterval(()=>{clockEl.textContent=fmtClock.format(new Date())+' IST';},1000);"
new="  setInterval(()=>{clockEl.textContent=fmtClock.format(new Date())+' IST'; updateFeedAge();},1000);"
assert s.count(old)==1
s=s.replace(old,new)
needle="  function applyStatus(info) {\n    if (!info) return;\n"
assert s.count(needle)==1
s=s.replace(needle,needle+'''    latestTickUtc = info.last_tick_utc || latestTickUtc;
    latestServerTimeUtc = info.server_time_utc || latestServerTimeUtc;
    updateFeedAge();
''')
js.write_text(s,encoding='utf8')

# Functional tests comparing optimized latest-only aggregation against the old API.
test=p/'trading_bot/tests/test_fast_stream.py'
test.write_text('''from datetime import datetime, timezone
from trading_bot.candles import CandleStore, SUPPORTED_MINUTES

START = int(datetime(2026,11,13,3,45,tzinfo=timezone.utc).timestamp())


def make_bar(i, source="historical"):
    return dict(time=START + i * 60, open=100 + i*.05, high=101 + i*.05,
                low=99 + i*.05, close=100.5 + i*.05,
                volume=12. if source == "historical" else None,
                source=source)


def test_latest_bars_empty():
    s=CandleStore()
    assert s.count_1m() == 0
    assert set(s.latest_bars()) == {str(tf) for tf in SUPPORTED_MINUTES}
    assert all(v is None for v in s.latest_bars().values())


def test_latest_bars_agree_with_snapshot_during_session():
    s=CandleStore()
    for i in range(375):
        b=make_bar(i)
        s.seed([b])
        if i % 59 == 0:
            s.on_tick(b["close"]+0.2, b["time"]+10)
        for tf in SUPPORTED_MINUTES:
            assert s.latest_bars()[str(tf)] == s.snapshot(tf, limit=1)[-1]
    assert s.count_1m() == 375


def test_latest_bars_across_sessions_and_missing_minutes():
    s=CandleStore()
    s.seed([{k:v for k,v in make_bar(i).items() if k!="source"}
            for i in [0,1,5,16,60,62,200,201]])
    s.seed([{**make_bar(240), "time": START + 24*3600}])
    for tf in SUPPORTED_MINUTES:
        assert s.latest_bars()[str(tf)] == s.snapshot(tf,limit=1)[-1]


def test_latest_bars_does_not_modify_original_data():
    s=CandleStore()
    s.seed([{k:v for k,v in make_bar(i).items() if k!="source"} for i in range(61)])
    before=s.snapshot(1,limit=100)
    s.latest_bars()
    assert s.snapshot(1,limit=100)==before
''',encoding='utf8')
print('patched')
