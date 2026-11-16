"""Apply Phase 3 overlay in-place, without touching Dhan or .env.

Run from the Trading Bot project root: python apply_phase3.py
"""
from __future__ import annotations

import py_compile
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PKG = ROOT / "trading_bot"
APP = PKG / "app.py"
JS = PKG / "static" / "app.js"
HTML = PKG / "static" / "index.html"
CSS = PKG / "static" / "style.css"
for path in (APP, JS, HTML, CSS):
    if not path.is_file():
        raise SystemExit(f"Missing required file: {path}; extract patch in the project root.")

app_text = APP.read_text(encoding="utf-8")
js_text = JS.read_text(encoding="utf-8")
html_text = HTML.read_text(encoding="utf-8")
css_text = CSS.read_text(encoding="utf-8")

if "@app.get(\"/api/structure\")" not in app_text:
    needle = '@app.get("/api/quality")'
    if needle not in app_text:
        raise SystemExit('app.py patch aborted: cannot find /api/quality route.')
    route = '''@app.get("/api/structure")
def structure(timeframe: int = Query(default=1, ge=1, le=60)):
    from fastapi import HTTPException
    from .structure_overlay import analyze_structure
    if timeframe not in SUPPORTED_MINUTES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")
    return analyze_structure(store.snapshot(timeframe), timeframe=timeframe, symbol=SYMBOL)


'''
    app_text = app_text.replace(needle, route + needle, 1)

if "window.TradingBotChart" not in js_text:
    needle = "  function renderOhlc(bar) {"
    if needle not in js_text:
        raise SystemExit('app.js patch aborted: cannot locate renderOhlc function.')
    js_text = js_text.replace(needle,
        '  window.TradingBotChart = { chart, series, getTimeframe: () => timeframe, times: [] };\n' + needle, 1)

if "tb-candles-loaded" not in js_text:
    needle = "      series.setData(candles);"
    if needle not in js_text:
        raise SystemExit('app.js patch aborted: cannot locate series.setData call.')
    js_text = js_text.replace(needle, needle + '''
      window.TradingBotChart.times = candles.map(c => c.time);
      document.dispatchEvent(new CustomEvent('tb-candles-loaded', {detail:{timeframe}}));''', 1)

if "tb-new-bar" not in js_text:
    needle = 'if(bar.time>lastBarTime){lastBarTime=bar.time;}'
    if needle not in js_text:
        raise SystemExit('app.js patch aborted: cannot find live-bar update anchor.')
    replacement = '''if(bar.time>lastBarTime){
          lastBarTime=bar.time;
          window.TradingBotChart.times.push(bar.time);
          if(window.TradingBotChart.times.length>500) window.TradingBotChart.times.shift();
          document.dispatchEvent(new CustomEvent('tb-new-bar', {detail:{timeframe}}));
        }'''
    js_text = js_text.replace(needle, replacement, 1)

script = '  <script src="/static/structure_layer.js"></script>'
if script not in html_text:
    needle = '  <script src="/static/app.js"></script>'
    if needle not in html_text:
        raise SystemExit('index.html patch aborted: cannot locate app.js script tag.')
    html_text = html_text.replace(needle, needle + '\n' + script, 1)

if '.structure-toggle' not in css_text:
    css_text += '''
/* Trading Bot Phase 3 - market structure overlay controls */
.structure-toggle{border:1px solid #355267;border-radius:7px;background:#1b2c3e;
  color:#c9d9ea;font-size:11px;font-weight:700;padding:8px 12px;cursor:pointer;
  white-space:nowrap}
.structure-toggle.enabled{border-color:#1b837e;background:#154746;color:#83f1d7}
.structure-summary{font-weight:600;color:#9cb4cc}
@media(max-width:760px){.structure-toggle{padding:6px;font-size:10px}}
'''

# Validate Python syntax before replacing any project source.
compile(app_text, str(APP), "exec")
for path, contents in ((APP, app_text), (JS, js_text), (HTML, html_text), (CSS, css_text)):
    backup = path.with_name(path.name + '.pre_phase3.bak')
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(contents, encoding='utf-8')

py_compile.compile(str(APP), doraise=True)
py_compile.compile(str(PKG / 'structure_overlay.py'), doraise=True)
print('Phase 3 installed: /api/structure, live BOS/CHoCH markers, and ON/OFF toggle.')
print('Original files backed up as *.pre_phase3.bak; Dhan settings were not changed.')
