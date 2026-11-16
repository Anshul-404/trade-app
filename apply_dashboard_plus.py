"""Install Dashboard Plus without replacing credentials or the Dhan feed.

Usage from the project root after extracting ZIP: python apply_dashboard_plus.py
Backups are stored with .pre_dashboard_plus.bak suffix.
"""
from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'trading_bot' / 'app.py'
HTML = ROOT / 'trading_bot' / 'static' / 'index.html'
OPTIONS_JS = ROOT / 'trading_bot' / 'static' / 'options_paper.js'
for file in (APP,HTML,OPTIONS_JS):
    if not file.is_file():
        raise SystemExit(f'Missing {file}. Install the existing options+paper panel first.')
app = APP.read_text(encoding='utf-8')
html = HTML.read_text(encoding='utf-8')
old_js = OPTIONS_JS.read_text(encoding='utf-8')
if 'register_dashboard_plus' in app or 'id="tb-movers-winners"' in html:
    raise SystemExit('Dashboard Plus already installed or partially applied. No changes made.')

needle_import = 'from .options_paper import register_routes'
needle_routes = 'register_routes(app, ROOT, CLIENT_ID, ACCESS_TOKEN)'
needle_html = '<div class="below">'
needle_risk = '<p class="op-warning">'
needle_footer = '<p class="notice" id="notice">'
needle_event = '    setSelected(found);\n    message('
needle_refresh = '  setSelected(null);\n  loadExpiries();'
needle_ledger_changed = "      await refreshPaper();\n    } catch(e) {message('Paper order rejected: '"
for needle,content,label in (
    (needle_import,app,'app import'),(needle_routes,app,'route registration'),
    (needle_html,html,'chart toolbar'),(needle_risk,html,'option risk insertion'),
    (needle_footer,html,'movers insertion'),(needle_event,old_js,'option chain refresh hook'),
    (needle_refresh,old_js,'paper refresh hook'),
    (needle_ledger_changed,old_js,'paper ledger hook')):
    if needle not in content:
        raise SystemExit(f'Unexpected source layout: {label} marker missing. No files changed.')
if not (ROOT/'trading_bot'/'paper_risk.py').exists() or not (ROOT/'trading_bot'/'movers.py').exists():
    raise SystemExit('Extract the ZIP to project root first; required Python modules missing.')
if not (ROOT/'trading_bot'/'static'/'dashboard_plus.js').exists():
    raise SystemExit('dashboard_plus.js missing; ZIP was not fully extracted.')

app = app.replace(needle_import, needle_import+'\nfrom .paper_risk import register_dashboard_plus',1)
app = app.replace(needle_routes,needle_routes+'\nregister_dashboard_plus(app, ROOT, CLIENT_ID, ACCESS_TOKEN)',1)
html = html.replace('</head>', '  <link rel="stylesheet" href="/static/dashboard_plus.css" />\n</head>',1)
chart_controls = '''<div class="tb-chart-controls" role="group" aria-label="NIFTY futures visual reference prices">
        <strong>Futures chart · Visual SL / TP</strong>
        <label class="tb-mini-note">SL ₹ <input id="tb-fut-sl" type="number" min="0.01" step="0.05" placeholder="Stop price" /></label>
        <label class="tb-mini-note">TP ₹ <input id="tb-fut-tp" type="number" min="0.01" step="0.05" placeholder="Target price" /></label>
        <button type="button" id="tb-chart-apply">Draw lines</button>
        <button type="button" id="tb-chart-clear">Clear</button>
        <span class="tb-mini-note" id="tb-chart-note">Reference only — does not execute orders</span>
      </div>
      '''
html = html.replace(needle_html,chart_controls+needle_html,1)
extra_risk = '''<div class="tb-paper-risk">
        <div class="tb-risk-title"><h3>Paper risk &amp; positions</h3><button class="tb-button tb-sell-all" id="tb-close-all" type="button" disabled>CLOSE ALL PAPER (<span id="tb-close-count">0</span>)</button></div>
        <p class="tb-mini-note">Sell all uses fresh option bids or rejects every close. No real Dhan orders.</p>
        <div id="tb-paper-risk-list" class="tb-risk-list" aria-live="off"></div>
        <p id="tb-risk-note" class="tb-mini-note" role="status">SL / TP are visual option-premium references, not automatic exits.</p>
        <div class="tb-option-graph"><h4>Option premium monitor · sampled bid prices + SL/TP</h4>
          <canvas id="tb-premium-canvas" aria-label="Selected paper option bid prices with reference SL and TP lines"></canvas>
          <p class="tb-mini-note">Samples begin when this dashboard opens. This is not historical option OHLC data.</p>
        </div>
      </div>
      '''
html = html.replace(needle_risk,extra_risk+needle_risk,1)
movers_html = '''<section class="tb-movers" aria-label="NIFTY 50 top market gainers and losers">
      <div class="tb-movers-header"><div><h2>NIFTY 50 · Market Movers</h2>
        <p>Up to 25 actual gainers and 25 actual losers · NSE equities · Dhan quotes</p></div>
        <button type="button" class="tb-button" id="tb-movers-refresh">Refresh movers</button></div>
      <p class="tb-mini-note" id="tb-movers-note">Loading NSE constituent list and bulk Dhan quotes…</p>
      <div class="tb-movers-grid">
        <div class="tb-movers-column"><div class="tb-movers-colhead"><span class="tb-positive">GAINERS ▲</span><span id="tb-gainers-count">0</span></div><ol id="tb-movers-winners" class="tb-movers-list"></ol></div>
        <div class="tb-movers-column"><div class="tb-movers-colhead"><span class="tb-negative">LOSERS ▼</span><span id="tb-losers-count">0</span></div><ol id="tb-movers-losers" class="tb-movers-list"></ol></div>
      </div>
    </section>
    '''
html = html.replace(needle_footer,movers_html+needle_footer,1)
html = html.replace('</body>','  <script src="/static/dashboard_plus.js"></script>\n</body>',1)
old_js = old_js.replace(needle_event,
                        '    setSelected(found);\n    document.dispatchEvent(new CustomEvent(\'tb-chain-refreshed\',{detail:data}));\n    message(',1)
old_js = old_js.replace(needle_refresh,
                        "  window.addEventListener('tb-paper-refresh',()=>refreshPaper());\n  setSelected(null);\n  loadExpiries();",1)
old_js = old_js.replace(needle_ledger_changed,
                        "      await refreshPaper();\n      window.dispatchEvent(new Event('tb-paper-ledger-changed'));\n    } catch(e) {message('Paper order rejected: '",1)

updates = ((APP,app),(HTML,html),(OPTIONS_JS,old_js))
for path,_ in updates:
    backup = path.with_name(path.name+'.pre_dashboard_plus.bak')
    if backup.exists():
        raise SystemExit(f'Backup already exists: {backup}; move the old backup before installing. No changes made.')
for path,_ in updates:
    shutil.copy2(path,path.with_name(path.name+'.pre_dashboard_plus.bak'))
for path,content in updates:
    path.write_text(content,encoding='utf-8')
print('Trading Bot Dashboard Plus installed. Paper positions and .env untouched.')
print('Backups: app.py, index.html, options_paper.js (.pre_dashboard_plus.bak).')
print('Restart Uvicorn; refresh http://127.0.0.1:8001 with Ctrl+F5.')
