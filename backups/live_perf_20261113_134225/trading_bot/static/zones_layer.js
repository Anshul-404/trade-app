// Phase 4: read-only Order Block and FVG shaded zones. No trading actions.
(() => {
  'use strict';
  const host = window.TradingBotChart;
  const container = document.getElementById('chart');
  const toolbar = document.querySelector('.toolbar');
  if (!host || !container || !toolbar) return;

  const canvas = document.createElement('canvas');
  canvas.className = 'tb-zone-overlay';
  canvas.setAttribute('aria-hidden', 'true');
  container.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  let showOB = true;
  let showFVG = true;
  let zones = { order_blocks: [], fair_value_gaps: [] };
  let requestId = 0;
  let frame = 0;
  let lastEpoch = 0;

  function toggle(label) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'zone-toggle enabled';
    btn.setAttribute('aria-pressed', 'true');
    btn.textContent = label + ': ON';
    toolbar.appendChild(btn);
    return btn;
  }
  const obButton = toggle('OB');
  const fvgButton = toggle('FVG');
  const summary = document.createElement('span');
  summary.className = 'zones-summary';
  summary.textContent = 'Loading zones…';
  document.querySelector('.below')?.appendChild(summary);

  function redraw() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const width = container.clientWidth, height = container.clientHeight;
      if (!width || !height) return;
      const pixelRatio = window.devicePixelRatio || 1;
      const w = Math.round(width * pixelRatio), h = Math.round(height * pixelRatio);
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w; canvas.height = h;
        canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
      }
      ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const entries = [
        ...(showOB ? zones.order_blocks.map(z => ({ ...z, kind: 'OB' })) : []),
        ...(showFVG ? zones.fair_value_gaps.map(z => ({ ...z, kind: 'FVG' })) : []),
      ];
      const ts = host.chart.timeScale();
      const priceToY = p => host.series.priceToCoordinate(p);
      const timeToX = t => ts.timeToCoordinate(t);
      for (const z of entries) {
        const start = timeToX(z.start_time);
        const end = timeToX(z.end_time);
        const yTop = priceToY(z.top);
        const yBottom = priceToY(z.bottom);
        if (![start, end, yTop, yBottom].every(Number.isFinite)) continue;
        if (start > width || end < 0 || yBottom < 0 || yTop > height) continue;
        const x = Math.max(0, Math.min(start, end));
        const right = Math.min(width, Math.max(start + 6, end + 6));
        const y = Math.max(0, Math.min(yTop, yBottom));
        const bottom = Math.min(height, Math.max(yTop, yBottom));
        if (right <= x || bottom <= y) continue;
        const bullish = z.direction === 'BULLISH';
        const inactive = z.invalidated || z.status === 'FULLY_MITIGATED';
        const alpha = inactive ? 0.045 : (z.quality === 'historical_ohlc' ? 0.15 : 0.075);
        const rgb = bullish ? '33, 191, 149' : '240, 106, 120';
        ctx.fillStyle = `rgba(${rgb}, ${alpha})`;
        ctx.strokeStyle = `rgba(${rgb}, ${inactive ? 0.14 : 0.48})`;
        ctx.lineWidth = 1;
        ctx.fillRect(x, y, right - x, bottom - y);
        ctx.strokeRect(x + 0.5, y + 0.5, Math.max(0, right-x-1), Math.max(0, bottom-y-1));
        if (right - x > 45 && bottom - y > 13) {
          ctx.font = '10px system-ui, sans-serif';
          ctx.fillStyle = bullish ? '#4be1b6' : '#ff929e';
          ctx.fillText(z.kind, x + 5, y + 11);
        }
      }
    });
  }
  async function refresh() {
    const current = ++requestId;
    const tf = host.getTimeframe();
    try {
      const response = await fetch(`/api/zones?timeframe=${tf}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      if (current !== requestId || tf !== host.getTimeframe()) return;
      zones = result;
      const indicative = [...result.order_blocks, ...result.fair_value_gaps]
        .filter(z => z.quality !== 'historical_ohlc').length;
      summary.textContent = `${result.total_order_blocks} OB · ${result.total_fair_value_gaps} FVG` +
        (indicative ? ` · ${indicative} indicative shown` : '') +
        ' · last 40 each';
      redraw();
    } catch (error) {
      if (current !== requestId) return;
      zones = { order_blocks: [], fair_value_gaps: [] };
      summary.textContent = 'Zone overlay unavailable: ' + error.message;
      redraw();
    }
  }
  obButton.addEventListener('click', () => {
    showOB = !showOB;
    obButton.textContent = 'OB: ' + (showOB ? 'ON' : 'OFF');
    obButton.classList.toggle('enabled', showOB);
    obButton.setAttribute('aria-pressed', String(showOB));
    redraw();
  });
  fvgButton.addEventListener('click', () => {
    showFVG = !showFVG;
    fvgButton.textContent = 'FVG: ' + (showFVG ? 'ON' : 'OFF');
    fvgButton.classList.toggle('enabled', showFVG);
    fvgButton.setAttribute('aria-pressed', String(showFVG));
    redraw();
  });
  document.addEventListener('tb-candles-loaded', () => { redraw(); refresh(); });
  document.addEventListener('tb-new-bar', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  host.chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
  host.chart.subscribeCrosshairMove(redraw);
  window.addEventListener('resize', redraw);
  new ResizeObserver(redraw).observe(container);
  setInterval(() => { if (!document.hidden) { redraw(); if (Date.now() - lastEpoch > 30000) {lastEpoch = Date.now(); refresh();} } }, 1000);
  setTimeout(refresh, 400);
})();
