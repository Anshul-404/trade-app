(() => {
  const priceEl = document.getElementById('last-price');
  const statusEl = document.getElementById('feed-state');
  const dotEl = document.getElementById('status-dot');
  const noticeEl = document.getElementById('notice');
  const historyEl = document.getElementById('hist-state');
  const countEl = document.getElementById('bar-count');
  const symbolEl = document.getElementById('symbol');
  const clockEl = document.getElementById('clock');
  const tickEl = document.getElementById('last-tick');
  const container = document.getElementById('chart');
  let timeframe = 1;
  let lastBarTime = 0;
  let generation = 0;
  const fmt = new Intl.DateTimeFormat('en-IN', { timeZone:'Asia/Kolkata', day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit', hour12:false });
  const fmtClock = new Intl.DateTimeFormat('en-IN', { timeZone:'Asia/Kolkata', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
  const chart = LightweightCharts.createChart(container, {
    width:container.clientWidth, height:container.clientHeight,
    layout:{background:{type:'solid',color:'#101b2b'},textColor:'#8da2bb',fontFamily:'Inter, system-ui, sans-serif'},
    grid:{vertLines:{color:'#1d2b3f'},horzLines:{color:'#1d2b3f'}},
    rightPriceScale:{borderColor:'#273950'},
    timeScale:{borderColor:'#273950',timeVisible:true,secondsVisible:false,rightOffset:8,
      tickMarkFormatter:(t)=>typeof t==='number'?fmt.format(new Date(t*1000)):' '},
    localization:{timeFormatter:(t)=>typeof t==='number'?fmt.format(new Date(t*1000)):String(t)},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
  });
  const series=chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor:'#21bf95',downColor:'#f06a78',borderVisible:false,
    wickUpColor:'#21bf95',wickDownColor:'#f06a78',
    priceFormat:{type:'price',precision:2,minMove:0.01},
  });
  new ResizeObserver(()=>chart.resize(container.clientWidth,container.clientHeight)).observe(container);
  setInterval(()=>{clockEl.textContent=fmtClock.format(new Date())+' IST';},1000);

  window.TradingBotChart = { chart, series, getTimeframe: () => timeframe, times: [] };
  function applyStatus(info) {
    if (!info) return;
    symbolEl.textContent=info.symbol || 'Market';
    const state=info.feed_status || 'not-started';
    const lastAge=info.last_tick_utc ? Date.now()-Date.parse(info.last_tick_utc) : Infinity;
    const live=state==='connected' && lastAge < 15000;
    statusEl.textContent=live?'LIVE DATA':state==='connected'?'Connected · awaiting ticks':state==='error'?'Feed error':state.replaceAll('-',' ');
    dotEl.classList.toggle('live',live);
    dotEl.classList.toggle('bad',state==='error');
    if (info.last_price!=null) priceEl.textContent='₹'+Number(info.last_price).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
    if(info.last_tick_utc) tickEl.textContent='Last tick '+fmtClock.format(new Date(info.last_tick_utc))+' IST';
    historyEl.textContent=info.historical_status==='loaded'?'Historical data loaded':`History: ${info.historical_status}`;
    if(info.feed_error) noticeEl.textContent=info.feed_error;
    else if(info.historical_error) noticeEl.textContent=info.historical_error;
    else if(!live) noticeEl.textContent='Waiting for new market ticks. Previous candles remain visible after market close.';
    else noticeEl.textContent='Live market feed connected. Candles update as prices arrive. Orders are disabled.';
  }

  async function loadCandles(fit=false) {
    const current=++generation;
    try {
      const response=await fetch(`/api/candles?timeframe=${timeframe}`,{cache:'no-store'});
      if(!response.ok) throw Error('HTTP '+response.status);
      const data=await response.json();
      if(current!==generation) return;
      const candles=data.candles.map(({time,open,high,low,close})=>({time,open,high,low,close}));
      series.setData(candles);
      window.TradingBotChart.times = candles.map(c => c.time);
      document.dispatchEvent(new CustomEvent('tb-candles-loaded', {detail:{timeframe}}));
      lastBarTime=candles.length?candles[candles.length-1].time:0;
      countEl.textContent=`${candles.length} candles`;
      if(fit && candles.length) chart.timeScale().fitContent();
    }catch(e){noticeEl.textContent='Could not load chart candles: '+e.message;}
  }

  document.querySelectorAll('[data-tf]').forEach(button => button.addEventListener('click',()=>{
    document.querySelectorAll('[data-tf]').forEach(b=>b.classList.remove('active'));
    button.classList.add('active');
    timeframe=Number(button.dataset.tf);
    lastBarTime=0;
    loadCandles(true);
  }));

  let socket;
  function connect() {
    const protocol=location.protocol==='https:'?'wss':'ws';
    socket=new WebSocket(`${protocol}://${location.host}/ws/chart`);
    socket.onmessage=(event)=>{
      let payload;
      try{payload=JSON.parse(event.data);}catch{return;}
      applyStatus(payload.status);
      if(payload.type!=='bars') return;
      const bar=payload.bars?.[String(timeframe)];
      if(!bar) return;
      if(bar.time<lastBarTime){loadCandles(false);return;}
      try{
        series.update({time:bar.time,open:bar.open,high:bar.high,low:bar.low,close:bar.close});
        if(bar.time>lastBarTime){
          lastBarTime=bar.time;
          window.TradingBotChart.times.push(bar.time);
          if(window.TradingBotChart.times.length>500) window.TradingBotChart.times.shift();
          document.dispatchEvent(new CustomEvent('tb-new-bar', {detail:{timeframe}}));
        }
      }catch{loadCandles(false);}
    };
    socket.onclose=()=>{statusEl.textContent='Reconnecting…';dotEl.classList.remove('live');setTimeout(connect,2500);};
    socket.onerror=()=>{socket.close();};
  }
  // Initial history loads once; live updates stream in from the WebSocket.
  fetch('/api/status').then(r=>r.json()).then(applyStatus).catch(()=>{});
  loadCandles(true);
  connect();
  // Keep browser-side socket alive and ask for status when tab resumes.
  setInterval(()=>{if(socket?.readyState===WebSocket.OPEN)socket.send('ping');},25000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadCandles(false);});
})();
