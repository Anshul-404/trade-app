"""Apply Phase 5 options-paper side panel without touching Dhan feed or credentials.

Run from project root: python apply_options_paper.py
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / 'trading_bot'
APP = PACKAGE / 'app.py'
HTML = PACKAGE / 'static' / 'index.html'
if not APP.exists() or not HTML.exists():
    raise SystemExit('Run this from your Trading Bot root, and extract the patch ZIP here first.')

app = APP.read_text(encoding='utf-8')
html = HTML.read_text(encoding='utf-8')
if 'register_routes(app, ROOT, CLIENT_ID, ACCESS_TOKEN)' in app or 'id="op-expiry"' in html:
    raise SystemExit('Already installed (or partially installed). No files changed. Check app.py and index.html.')
if 'from .history import fetch_recent_1m' not in app:
    raise SystemExit('Expected history import missing. Nothing changed; inspect app.py manually.')
if 'app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")' not in app:
    raise SystemExit('Expected app.mount line missing. Nothing changed; inspect app.py manually.')
if '<section class="workspace">' not in html or '</section>\n    <p class="notice"' not in html:
    raise SystemExit('Expected chart HTML layout missing. Nothing changed; inspect index.html manually.')
if '</body>' not in html or '</head>' not in html:
    raise SystemExit('HTML closing tags missing. Nothing changed.')

app = app.replace('from .history import fetch_recent_1m', 'from .history import fetch_recent_1m\nfrom .options_paper import register_routes')
marker = 'app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")'
app = app.replace(marker, marker + '\nregister_routes(app, ROOT, CLIENT_ID, ACCESS_TOKEN)')
html = html.replace('</head>', '  <link rel="stylesheet" href="/static/options_paper.css" />\n</head>')
html = html.replace('<section class="workspace">', '<div class="market-layout">\n    <section class="workspace">', 1)
side = '''</section>
    <aside class="op-panel" aria-label="Option chain and paper trading">
      <h2>NIFTY Option Chain <span class="op-muted">· DhanHQ</span></h2>
      <p class="op-subtitle">NIFTY 50 spot underlying · Index security 13 · PAPER ONLY</p>
      <div class="op-head">
        <label><span class="op-label">Option expiry</span><select id="op-expiry" aria-label="Option expiry"></select></label>
        <button class="op-btn" id="op-refresh" type="button">Refresh</button>
      </div>
      <p class="op-muted"><span id="op-underlying">Loading spot price…</span> · <span id="op-age">—</span></p>
      <div class="op-table-wrap"><table class="op-table">
        <thead><tr><th>CE OI</th><th>CE LTP</th><th>Strike</th><th>PE LTP</th><th>PE OI</th></tr></thead>
        <tbody id="op-rows"></tbody>
      </table></div>
      <h3>Paper Trading <span class="op-muted">· Simulated orders only</span></h3>
      <div class="op-selected op-muted" id="op-selected">Select a CE or PE contract from the chain</div>
      <div class="op-actions">
        <label><span class="op-label">Lots</span><input id="op-lots" type="number" min="1" max="20" step="1" value="1"/></label>
        <button class="op-btn op-buy" id="op-buy" type="button" disabled>PAPER BUY</button>
        <button class="op-btn op-sell" id="op-sell" type="button" disabled>PAPER SELL</button>
      </div>
      <div class="op-message" id="op-message" role="status"></div>
      <div class="op-stats">
        <div class="op-stat"><span>Virtual cash</span><strong id="op-cash">—</strong></div>
        <div class="op-stat"><span>Est. equity</span><strong id="op-equity">—</strong></div>
        <div class="op-stat"><span>Realized P&amp;L</span><strong id="op-realized">—</strong></div>
        <div class="op-stat"><span>Unrealized P&amp;L</span><strong id="op-unrealized">—</strong></div>
      </div>
      <h3>Open positions</h3><ul class="op-items" id="op-positions"></ul>
      <h3>Latest paper trades</h3><ul class="op-items" id="op-trades"></ul>
      <p class="op-warning">1 lot = <span id="op-lot-size">—</span> units (verify with Dhan for each expiry). BUY uses ask; SELL closes existing longs at bid. No real orders, fees, slippage, margin, or expiry settlement. Refresh chain to value positions.</p>
    </aside>
    </div>
    <p class="notice"'''
html = html.replace('</section>\n    <p class="notice"', side, 1)
html = html.replace('No orders can be placed', 'Real orders disabled · paper trading available')
html = html.replace('</body>', '  <script src="/static/options_paper.js"></script>\n</body>')

# Preflight succeeded; do not replace original files until backups are created.
for path in (APP, HTML):
    backup = path.with_name(path.name + '.pre_options_paper.bak')
    if backup.exists():
        raise SystemExit(f'Backup already exists: {backup}. Move it before applying.')
for path in (APP, HTML):
    shutil.copy2(path, path.with_name(path.name + '.pre_options_paper.bak'))
APP.write_text(app, encoding='utf-8')
HTML.write_text(html, encoding='utf-8')
print('Options + paper panel installed. Existing Dhan feed, futures symbol, and .env untouched.')
print('Backups: app.py.pre_options_paper.bak and index.html.pre_options_paper.bak')
print('Run: python -m pytest trading_bot/tests -q')
print('Then restart Uvicorn and refresh http://127.0.0.1:8001')
