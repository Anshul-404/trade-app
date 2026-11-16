/* Trading Bot: historical candles and MANUAL simulated fills. No broker API. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = { tf: '1m', side: 'LONG', cursor: 0, total: 0, last: null, previous: null,
    chart: null, series: null, markers: null, marks: [], lines: [], customLevels: [],
    trade: null, trades: [], id: 0, playing: false, timer: null, busy: false,
    generation: 0, follow: true, catalogue: null, resize: null };
  const formatPrice = x => Number.isFinite(x) ? Number(x).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : '—';
  const signed = x => `${x > 0 ? '+' : ''}${Number(x).toFixed(2)}`;
  const timeFormatter = new Intl.DateTimeFormat('en-IN', {timeZone:'Asia/Kolkata', day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false});
  const dateParts = new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Kolkata', day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false});
  const fmt = epoch => epoch == null ? '—' : timeFormatter.format(new Date(epoch * 1000));
  const dateInput = epoch => {
    const parts = Object.fromEntries(dateParts.formatToParts(new Date(epoch * 1000)).filter(p => p.type !== 'literal').map(p => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  };
  const notice = msg => { $('notice').textContent = msg; $('notice').classList.remove('hidden'); };
  const clearNotice = () => $('notice').classList.add('hidden');
  async function api(path, params={}) {
    const url = new URL(path, window.location.href);
    Object.entries(params).forEach(([k,v]) => { if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v)); });
    const response = await fetch(url, {cache:'no-store'});
    let result;
    try { result = await response.json(); } catch { throw new Error(`HTTP ${response.status}: invalid server reply`); }
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }
  const asPriceBar = b => ({time:b.time, open:b.open, high:b.high, low:b.low, close:b.close});
  const tick = t => {
    if (typeof t !== 'number') return String(t);
    const d = new Date(t*1000);
    const isLarge = state.tf === '1d' || state.tf === '1w';
    return new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Kolkata',day:'2-digit',month:'short',...(isLarge ? {year:'2-digit'} : {hour:'2-digit',minute:'2-digit',hour12:false})}).format(d);
  };

  function newChart(history) {
    if (!window.LightweightCharts?.CandlestickSeries) throw new Error('Could not load the chart library. Connect to the internet once or run: python replay_server.py --install-chart-library');
    if (state.resize) { state.resize.disconnect(); state.resize = null; }
    if (state.chart) { state.chart.remove(); state.chart = null; }
    const host = $('chart'); host.replaceChildren();
    const L = window.LightweightCharts;
    const chart = L.createChart(host, {
      layout: {background:{type:'solid',color:'#0c1420'},textColor:'#9dafc9',fontFamily:'Inter, system-ui, sans-serif',fontSize:11},
      grid: {vertLines:{color:'#1b2a3c'},horzLines:{color:'#1b2a3c'}},
      rightPriceScale: {borderColor:'#344457',scaleMargins:{top:0.10,bottom:0.08}},
      timeScale: {borderColor:'#344457',timeVisible:true,secondsVisible:false,rightOffset:10,barSpacing:7,minBarSpacing:2,tickMarkFormatter:tick},
      crosshair: {mode:L.CrosshairMode?.Normal ?? 0,vertLine:{color:'#49627d',labelBackgroundColor:'#486280'},horzLine:{color:'#49627d',labelBackgroundColor:'#486280'}},
      localization: {timeFormatter:tick,priceFormatter:formatPrice},
      handleScroll:true,handleScale:true,
    });
    const series = chart.addSeries(L.CandlestickSeries, {
      upColor:'#24b99a',downColor:'#ec626c',wickUpColor:'#24b99a',wickDownColor:'#ec626c',borderVisible:false,
      priceFormat:{type:'price',precision:2,minMove:0.01},
    });
    state.chart = chart; state.series = series; state.marks=[]; state.customLevels=[]; state.lines=[];
    series.setData(history.map(asPriceBar));
    if (typeof L.createSeriesMarkers === 'function') state.markers = L.createSeriesMarkers(series, []);
    else state.markers = null;
    if (history.length) chart.timeScale().fitContent();
    state.resize = new ResizeObserver(() => { if (state.chart === chart) chart.resize(host.clientWidth, host.clientHeight); });
    state.resize.observe(host);
  }

  function pause() {
    if (state.timer !== null) clearInterval(state.timer);
    state.timer = null; state.playing = false;
    $('play').textContent = '▶ Play'; $('play').classList.remove('playing');
    $('feed-state').textContent = state.cursor >= state.total && state.total ? 'End of data' : 'Historical replay / paused';
  }
  function play() {
    if (state.cursor >= state.total || !state.total) { notice('This session is already at the last historical candle. Choose an earlier date.'); return; }
    clearNotice(); state.playing = true; $('play').textContent = '❚❚ Pause'; $('play').classList.add('playing'); $('feed-state').textContent = 'Replaying candles…';
    const interval = Math.max(50, 1000 / Number($('speed').value));
    state.timer = setInterval(() => { if (!state.busy) void advance(1); }, interval);
    if (!state.busy) void advance(1);
  }
  function togglePlay() { if (state.playing) pause(); else play(); }
  function setSide(side) {
    state.side=side; $('long').classList.toggle('selected',side==='LONG');$('short').classList.toggle('selected',side==='SHORT');
    $('place-trade').classList.toggle('short',side==='SHORT');
    $('place-trade').textContent=`Open simulated ${side.toLowerCase()}`;
    updateRR();
  }
  function updateRR() {
    const entry=state.last?.close, stop=Number($('stop-loss').value), target=Number($('take-profit').value);
    const valid=Number.isFinite(entry) && stop>0 && target>0 && (state.side==='LONG' ? stop<entry && entry<target : target<entry && entry<stop);
    $('risk-reward').textContent=valid ? `1 : ${(Math.abs(target-entry)/Math.abs(entry-stop)).toFixed(2)}` : '—';
  }
  function priceLine(price,color,title) {
    return state.series.createPriceLine({price,color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title});
  }
  function refreshTradeLines() {
    state.lines.forEach(line => { try { state.series.removePriceLine(line); } catch {} }); state.lines=[];
    if (!state.trade || !state.series) return;
    const t=state.trade;
    state.lines=[priceLine(t.entry,'#93b5ff','ENTRY'),priceLine(t.stop,'#f47b80','STOP'),priceLine(t.target,'#28cba1','TARGET')];
  }
  function drawMarkers() {
    if (state.markers) state.markers.setMarkers([...state.marks].sort((a,b)=>a.time-b.time));
  }
  function openTrade() {
    clearNotice();
    if (!state.last) { notice('Reveal a candle first. Trading is only possible at a visible candle close.'); return; }
    if (state.cursor >= state.total) { notice('At the end of history. Choose an earlier date.'); return; }
    if (state.trade) { notice('Close the existing position before opening another.'); return; }
    const entry=state.last.close, stop=Number($('stop-loss').value), target=Number($('take-profit').value), qty=Number($('quantity').value);
    if (!(qty >= 1 && Number.isInteger(qty) && qty <= 1000000)) { notice('Quantity must be an integer between 1 and 1,000,000.'); return; }
    if (!(stop>0 && target>0 && Number.isFinite(stop) && Number.isFinite(target))) { notice('Set valid positive stop-loss and take-profit prices.'); return; }
    const valid=state.side==='LONG' ? stop<entry && target>entry : stop>entry && target<entry;
    if (!valid) { notice(state.side==='LONG' ? 'For a long position, set Stop < Entry < Target.' : 'For a short position, set Target < Entry < Stop.'); return; }
    state.trade={id:++state.id,tf:state.tf,side:state.side,entry,stop,target,qty,
      entryTime:state.last.available,entryBarTime:state.last.time,notes:$('plan-notes').value.trim(),risk:Math.abs(entry-stop)*qty};
    state.marks.push({time:state.last.time,position:state.side==='LONG'?'belowBar':'aboveBar',color:state.side==='LONG'?'#3bd5a2':'#f47b80',shape:state.side==='LONG'?'arrowUp':'arrowDown',text:state.side==='LONG'?'LONG':'SHORT'});
    drawMarkers();refreshTradeLines();updateTradePanel();
  }
  function exitTrade(price,time,reason,barTime=time) {
    const t=state.trade; if (!t) return;
    const pnl=(price-t.entry)*t.qty*(t.side==='LONG'?1:-1),r=t.risk?pnl/t.risk:0;
    state.trades.push({...t,exit:price,exitTime:time,reason,pnl,r});
    state.marks.push({time:barTime,position:t.side==='LONG'?'aboveBar':'belowBar',color:pnl>=0?'#60ecc1':'#fc8f9a',shape:'circle',text:reason==='TARGET'?'TP':reason.startsWith('STOP')?'SL':'EXIT'});
    state.trade=null;refreshTradeLines();drawMarkers();updateTradePanel();renderJournal();
  }
  function checkExit(b) {
    const t=state.trade; if (!t || b.time<=t.entryBarTime) return;
    if (t.side==='LONG') {
      if (b.open <= t.stop) return exitTrade(b.open,b.available,'STOP (gap)',b.time);
      if (b.open >= t.target) return exitTrade(t.target,b.available,'TARGET (gap)',b.time);
      if (b.low <= t.stop) return exitTrade(t.stop,b.available,b.high >= t.target?'STOP (both touched)':'STOP',b.time);
      if (b.high >= t.target) return exitTrade(t.target,b.available,'TARGET',b.time);
    } else {
      if (b.open >= t.stop) return exitTrade(b.open,b.available,'STOP (gap)',b.time);
      if (b.open <= t.target) return exitTrade(t.target,b.available,'TARGET (gap)',b.time);
      if (b.high >= t.stop) return exitTrade(t.stop,b.available,b.low <= t.target?'STOP (both touched)':'STOP',b.time);
      if (b.low <= t.target) return exitTrade(t.target,b.available,'TARGET',b.time);
    }
  }
  function updatePrices() {
    const b=state.last,p=state.previous;
    $('entry-price').textContent=b?formatPrice(b.close):'—';
    $('last-price').textContent=b?formatPrice(b.close):'—';
    const change=b&&p?b.close-p.close:null;
    $('last-change').textContent=change==null?'No previous candle':`${signed(change)} points (${signed(change/p.close*100)}%)`;
    $('last-change').className=change==null?'muted':change>=0?'positive':'negative';
    $('clock').textContent=b?fmt(b.available):'No candles yet';
    $('progress').textContent=`${state.cursor.toLocaleString('en-IN')} / ${state.total.toLocaleString('en-IN')} bars`;
    $('seek').max=String(Math.max(0,state.total-1));$('seek').value=String(Math.min(state.cursor,Math.max(0,state.total-1)));
    updateRR();updateTradePanel();
  }
  function updateTradePanel() {
    const t=state.trade;
    $('place-trade').disabled=!!t || !state.last || state.cursor>=state.total;
    $('position-card').classList.toggle('hidden',!t);
    if (!t) return;
    $('position-title').textContent=`${t.side} ${t.qty} @ ${formatPrice(t.entry)}`;
    const u=(state.last.close-t.entry)*t.qty*(t.side==='LONG'?1:-1);
    $('unrealized').textContent=`${signed(u)} pts × qty`;
    $('unrealized').className=u>=0?'positive':'negative';
  }
  function renderJournal() {
    const tBody=$('journal-body');tBody.replaceChildren();
    if (!state.trades.length) { const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=10;td.className='empty';td.textContent='No completed paper trades yet.';tr.append(td);tBody.append(tr); }
    for (const t of [...state.trades].reverse()) {
      const tr=document.createElement('tr');
      const cells=[t.id,t.side,fmt(t.entryTime),formatPrice(t.entry),fmt(t.exitTime),formatPrice(t.exit),t.qty,t.reason,signed(t.pnl),`${signed(t.r)} R`];
      cells.forEach((value,i)=>{const td=document.createElement('td');td.textContent=value;if(i===1)td.className=t.side==='LONG'?'positive':'negative';if(i===8||i===9)td.className=t.pnl>=0?'positive':'negative';tr.append(td);});
      tBody.append(tr);
    }
    const count=state.trades.length, wins=state.trades.filter(t=>t.pnl>0).length;
    const net=state.trades.reduce((sum,t)=>sum+t.pnl,0), rr=state.trades.reduce((sum,t)=>sum+t.r,0);
    $('stat-trades').textContent=String(count);$('stat-winrate').textContent=count?`${(100*wins/count).toFixed(1)}%`:'—';
    $('stat-points').textContent=signed(net);$('stat-points').className=net>=0?'positive':'negative';
    $('stat-r').textContent=`${signed(rr)} R`;$('stat-r').className=rr>=0?'positive':'negative';
  }
  function applyBar(b) {
    state.previous=state.last;state.last=b;
    state.series.update(asPriceBar(b));
    checkExit(b);updatePrices();
    if (state.follow) state.chart.timeScale().scrollToRealTime();
  }
  async function advance(count=1) {
    if (state.busy || state.cursor>=state.total) {if (state.cursor>=state.total)pause();return;}
    const gen=state.generation, tf=state.tf, at=state.cursor;
    state.busy=true;
    try {
      const result=await api('/api/step',{tf,cursor:at,count});
      if(gen!==state.generation)return;
      for(const b of result.bars)applyBar(b);
      state.cursor=result.cursor;updatePrices();
      if(result.done){pause();$('feed-state').textContent='End of historical data';}
    } catch(err){if(gen===state.generation){pause();notice(`Replay failed: ${err.message}`);}}
    finally{if(gen===state.generation)state.busy=false;}
  }
  function resetJournal() {
    state.trade=null;state.trades=[];state.id=0;state.marks=[];
    refreshTradeLines();renderJournal();updateTradePanel();
  }
  async function startSession({tf=state.tf,date=null,index=null,ask=true}={}) {
    if(ask&&(state.trade||state.trades.length)&&!window.confirm('Changing the replay position or timeframe will clear paper trades and the journal. Export the journal first if needed. Continue?'))return false;
    pause();const gen=++state.generation;state.busy=true;clearNotice();
    try {
      const data=await api('/api/start',{tf,date,index});if(gen!==state.generation)return false;
      state.tf=tf;state.cursor=data.cursor;state.total=data.total;
      state.last=data.history.at(-1)||null;state.previous=data.history.at(-2)||null;
      state.follow=true;$('follow').classList.add('active');
      newChart(data.history);
      resetJournal();
      $('timeframe').value=tf;
      $('start-date').value=date || dateInput(data.history.length?data.history.at(-1).time:data.first);
      $('chart-title').textContent=`${state.catalogue.symbol.replaceAll('_',' ')} · ${tf}`;
      const q = data.quality || {};
      const flagged = (q.skipped || 0) + (q.normalized || 0);
      $('chart-subtitle').textContent=`Historical candles · IST · ${data.total.toLocaleString('en-IN')} bars${flagged ? ` · ⚠ ${flagged} data issue(s)` : ''}`;
      updatePrices();$('feed-state').textContent='Historical replay / paused';
      if(flagged) notice(`DATA QUALITY WARNING: ${q.warning} Details: data/historical/${state.catalogue.symbol}/${q.report_file}. Original data was not changed.`);
      return true;
    }catch(err){if(gen===state.generation)notice(`Cannot load replay: ${err.message}`);return false;}
    finally{if(gen===state.generation)state.busy=false;}
  }
  function addLevel() {
    const price=Number($('level-price').value);
    if(!(price>0&&Number.isFinite(price))){notice('Enter a valid price level greater than zero.');return;}
    if(!state.series)return;
    const line=priceLine(price,'#e9c17d',`LEVEL ${state.customLevels.length+1}`);
    state.customLevels.push(line);$('level-price').value='';clearNotice();
  }
  function clearLevels(){state.customLevels.forEach(line=>{try{state.series.removePriceLine(line);}catch{}});state.customLevels=[];}
  function exportJournal() {
    if(!state.trades.length){notice('No completed trades to export yet.');return;}
    const columns=['id','timeframe','side','entry_time_IST','entry','stop','target','exit_time_IST','exit','quantity','reason','points_x_qty','R','notes'];
    const escapeCell=value=>{const raw=String(value??'');const safe=/^[=+@\-\t\r]/.test(raw)&&typeof value==='string'?`'${raw}`:raw;return `"${safe.replaceAll('"','""')}"`;};
    const rows=state.trades.map(t=>[t.id,t.tf,t.side,fmt(t.entryTime),t.entry,t.stop,t.target,fmt(t.exitTime),t.exit,t.qty,t.reason,t.pnl,t.r,t.notes]);
    const csv='\ufeff'+[columns,...rows].map(r=>r.map(escapeCell).join(',')).join('\r\n');
    const file=new Blob([csv],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(file);
    const a=document.createElement('a');a.href=url;a.download=`trading_bot_journal_${state.tf}_${new Date().toISOString().slice(0,10)}.csv`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),3000);
  }
  async function init() {
    if(!window.LightweightCharts?.createChart) {notice('The chart library did not load. Internet access is required once, or run: python replay_server.py --install-chart-library');$('chart-loading').textContent='Chart library unavailable';return;}
    try{
      state.catalogue=await api('/api/catalog');
      const available=state.catalogue.timeframes;
      if(!available.length)throw Error('No historical CSVs found. Check data/historical/NIFTY50_13.');
      $('symbol').textContent=`${state.catalogue.symbol.replaceAll('_',' ')} · INDEX`;
      $('timeframe').replaceChildren();
      available.forEach(m=>{const o=document.createElement('option');o.value=m.timeframe;o.textContent=m.timeframe; $('timeframe').append(o);});
      state.tf=available.some(t=>t.timeframe==='1m')?'1m':available[0].timeframe;
      $('timeframe').value=state.tf;
      const m=available.find(t=>t.timeframe===state.tf);
      $('coverage').textContent=`${fmt(m.first)} — ${fmt(m.last)}`;
      const preferred=Date.parse('2024-01-02T03:45:00Z')/1000;
      const start=Math.max(m.first,Math.min(preferred,m.last-1));
      $('start-date').value=dateInput(start);
      await startSession({tf:state.tf,date:$('start-date').value,ask:false});
    }catch(err){notice(`Cannot initialize the replay: ${err.message}`);$('chart-loading').textContent='Check replay server and downloaded CSV files';}
  }
  $('play').addEventListener('click',togglePlay);
  $('next').addEventListener('click',()=>{pause();void advance(1);});
  $('next-ten').addEventListener('click',()=>{pause();void advance(10);});
  $('previous').addEventListener('click',()=>{if(state.cursor>0)void startSession({index:Math.max(0,state.cursor-1)});});
  $('speed').addEventListener('change',()=>{if(state.playing){pause();play();}});
  $('timeframe').addEventListener('change',async e=>{
    const tf=e.target.value;
    const oldTime=state.last?.available;
    const date=oldTime?dateInput(oldTime):$('start-date').value;
    if(!await startSession({tf,date}))$('timeframe').value=state.tf;
    const m=state.catalogue.timeframes.find(x=>x.timeframe===state.tf);$('coverage').textContent=`${fmt(m.first)} — ${fmt(m.last)}`;
  });
  $('jump').addEventListener('click',()=>{void startSession({tf:$('timeframe').value,date:$('start-date').value});});
  $('seek').addEventListener('change',()=>{void startSession({index:Number($('seek').value)});});
  $('follow').addEventListener('click',()=>{state.follow=!state.follow;$('follow').classList.toggle('active',state.follow);if(state.follow&&state.chart)state.chart.timeScale().scrollToRealTime();});
  $('long').addEventListener('click',()=>setSide('LONG'));
  $('short').addEventListener('click',()=>setSide('SHORT'));
  ['stop-loss','take-profit'].forEach(k=>$(k).addEventListener('input',updateRR));
  $('place-trade').addEventListener('click',openTrade);
  $('close-trade').addEventListener('click',()=>{if(state.trade&&state.last)exitTrade(state.last.close,state.last.available,'MANUAL',state.last.time);});
  $('add-level').addEventListener('click',addLevel);
  $('clear-levels').addEventListener('click',clearLevels);
  $('download-journal').addEventListener('click',exportJournal);
  document.addEventListener('keydown',e=>{if(e.target.closest('input,textarea,select,button'))return;if(e.code==='Space'){e.preventDefault();togglePlay();}else if(e.code==='ArrowRight'){e.preventDefault();pause();void advance(1);}});
  void init();
})();
