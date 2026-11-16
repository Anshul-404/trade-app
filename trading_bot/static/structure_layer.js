// Phase 3 - read-only BOS and CHoCH markers. Compatible with Lightweight Charts 5.
(() => {
  const host = window.TradingBotChart;
  if (!host || !window.LightweightCharts?.createSeriesMarkers) {
    console.warn('Trading Bot: chart marker API not found.');
    return;
  }
  const chartMarkers = LightweightCharts.createSeriesMarkers(host.series, []);
  const toolbar = document.querySelector('.toolbar');
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'structure-toggle enabled';
  toggle.textContent = 'BOS / CHoCH: ON';
  toggle.setAttribute('aria-pressed', 'true');
  const summary = document.createElement('span');
  summary.className = 'structure-summary';
  summary.textContent = 'Analyzing closed candles…';
  toolbar.appendChild(toggle);
  const report = document.querySelector('.below');
  report?.appendChild(summary);

  let enabled = true;
  let generation = 0;
  let cached = [];

  function draw(events) {
    if (!enabled) { chartMarkers.setMarkers([]); return; }
    const visible = new Set(host.times || []);
    chartMarkers.setMarkers(events.filter(event => visible.has(event.time)).map(event => ({
      time: event.time,
      position: event.direction === 'BULLISH' ? 'belowBar' : 'aboveBar',
      color: event.direction === 'BULLISH' ? '#23cfaa' : '#fb7a8d',
      shape: event.direction === 'BULLISH' ? 'arrowUp' : 'arrowDown',
      text: event.break_type,
      size: 1,
    })));
  }

  async function refresh() {
    const request = ++generation;
    const tf = host.getTimeframe();
    try {
      const response = await fetch(`/api/structure?timeframe=${tf}`, { cache:'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const result = await response.json();
      if (request !== generation || tf !== host.getTimeframe()) return;
      cached = result.events || [];
      draw(cached);
      const indicative = cached.filter(e => e.quality !== 'historical_ohlc').length;
      summary.textContent = `${cached.length} structure breaks · ${result.bars_analyzed} closed bars` +
        (indicative ? ` · ${indicative} indicative (sampled LTP)` : '') +
        (result.skipped_engine_events ? ` · ${result.skipped_engine_events} skipped` : '');
    } catch (error) {
      if (request !== generation) return;
      cached = [];
      chartMarkers.setMarkers([]);
      summary.textContent = 'Structure overlay unavailable: ' + error.message;
    }
  }

  toggle.addEventListener('click', () => {
    enabled = !enabled;
    toggle.textContent = enabled ? 'BOS / CHoCH: ON' : 'BOS / CHoCH: OFF';
    toggle.classList.toggle('enabled', enabled);
    toggle.setAttribute('aria-pressed', String(enabled));
    draw(cached);
    if (enabled) refresh();
  });

  document.addEventListener('tb-candles-loaded', refresh);
  document.addEventListener('tb-new-bar', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (enabled && !document.hidden) refresh(); }, 30000);
  setTimeout(refresh, 300);
})();
