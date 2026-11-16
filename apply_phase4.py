"""Surgically install order-block/FVG chart overlays on Phase 3 Trading Bot.
Run: python apply_phase4.py (from Trading Bot project root).
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
PKG = ROOT / 'trading_bot'
app = PKG / 'app.py'
html = PKG / 'static' / 'index.html'
css = PKG / 'static' / 'style.css'
files = (app, html, css, PKG / 'zones_overlay.py',
         PKG / 'static' / 'zones_layer.js')
for file in files:
    if not file.is_file():
        raise SystemExit(f'Missing {file}. Extract the ZIP to the project ROOT first.')
for module in (
    PKG / 'structure_overlay.py',
    ROOT / 'trading_engine' / 'bos_choch' / 'structure_detector.py',
    ROOT / 'trading_engine' / 'order_blocks' / 'order_block_detector.py',
    ROOT / 'trading_engine' / 'fair_value_gaps' / 'fvg_detector.py',
):
    if not module.is_file():
        raise SystemExit(f'Missing required engine file: {module}. Existing files were NOT modified.')
source = app.read_text(encoding='utf-8')
markup = html.read_text(encoding='utf-8')
styles = css.read_text(encoding='utf-8')

if '@app.get("/api/zones")' not in source:
    needle = '@app.get("/api/quality")'
    if needle not in source:
        raise SystemExit('Cannot find /api/quality. No files modified.')
    source = source.replace(needle, '''@app.get("/api/zones")
def zones(timeframe: int = Query(default=1, ge=1, le=60)):
    from fastapi import HTTPException
    from .zones_overlay import analyze_zones
    if timeframe not in SUPPORTED_MINUTES:
        raise HTTPException(status_code=400, detail="Unsupported timeframe")
    return analyze_zones(store.snapshot(timeframe), timeframe=timeframe, symbol=SYMBOL)


''' + needle, 1)

script = '  <script src="/static/zones_layer.js"></script>'
if script not in markup:
    needle = '  <script src="/static/structure_layer.js"></script>'
    if needle not in markup:
        raise SystemExit('Cannot find Phase 3 structure script tag. No files modified.')
    markup = markup.replace(needle, needle + '\n' + script, 1)
if '.tb-zone-overlay' not in styles:
    styles += '''\n/* Phase 4 zone overlay and read-only filters */
#chart{position:relative}
.tb-zone-overlay{position:absolute;top:0;left:0;pointer-events:none;z-index:2}
.zone-toggle{border:1px solid #355267;border-radius:7px;background:#1b2c3e;
  color:#c9d9ea;font-size:11px;font-weight:700;padding:8px 11px;cursor:pointer;white-space:nowrap}
.zone-toggle.enabled{border-color:#1b837e;background:#154746;color:#83f1d7}
.zones-summary{color:#9cb4cc;font-weight:600}
@media(max-width:760px){.zone-toggle{padding:6px;font-size:10px}}
'''

compile(source, str(app), 'exec')
compile((PKG / 'zones_overlay.py').read_text(encoding='utf-8'), 'zones_overlay.py', 'exec')
for path, content in ((app, source), (html, markup), (css, styles)):
    backup = path.with_name(path.name + '.pre_phase4.bak')
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(content, encoding='utf-8')
print('Phase 4 installed: /api/zones and OB / FVG toggleable chart zones.')
print('No Dhan credentials or order execution settings changed. Backups: *.pre_phase4.bak')
