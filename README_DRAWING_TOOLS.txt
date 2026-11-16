TRADING BOT — Historical Replay Drawing Tools
=============================================

Adds these chart tools to the existing historical replay:
  1. Horizontal ray: one click to anchor time and price, extends right.
  2. Trend line: two clicks, anchored at candle timestamps/prices.
  3. Fibonacci retracement: two clicks for swing start/end, shows
     0, 0.236, 0.382, 0.5, 0.618, 0.786, and 1 levels.
  4. Long position: three clicks for entry, stop below entry, target above.
  5. Short position: three clicks for entry, stop above entry, target below.

A Pan mode lets the original chart handle zoom/pan normally.
Select mode lets you click and drag existing drawings or their handles.
There are Undo, Delete selected, and Clear all buttons.
Esc cancels a drawing and goes back to Pan; Delete key deletes a selection.

Each symbol/timeframe's drawings are saved in the same browser's localStorage.
Drawings created later in the replay are hidden if you rewind to before their
creation time, preventing future annotation lookahead.

Long/short drawing tools fill Stop Loss / Take Profit in the simulator ticket,
but the entry price of a REAL paper trade remains the last revealed candle's
close. No order is placed by drawing a position. The plan may need adjustment
if the drawn entry doesn't match the last revealed close.

INSTALL (Windows PowerShell)
----------------------------
1. Stop the replay server (Ctrl+C).
2. Download Trading_Bot_Replay_Drawing_Tools.zip into your Downloads folder.
3. Run:

   cd C:\Users\avi\Desktop\trading_bot
   $backup = "replay_static_backup_$(Get-Date -Format yyyyMMdd_HHmmss)"
   Copy-Item .\replay_static ".\$backup" -Recurse
   Expand-Archive -Path "$env:USERPROFILE\Downloads\Trading_Bot_Replay_Drawing_Tools.zip" -DestinationPath . -Force
   python replay_server.py

4. Open http://127.0.0.1:8787 and press Ctrl+F5.

This replaces ONLY:
 - replay_static/index.html
 - replay_static/replay.css
 - replay_static/replay.js
And ADDS:
 - replay_static/drawing_tools.js

It does NOT change replay_server.py, data, CSV, Excel, the downloader, or Dhan.

TESTS
-----
  node --check replay_static/drawing_tools.js
  node --check replay_static/replay.js
  python tests/test_drawings_browser.py

The browser test needs the playwright Python package and /usr/bin/chromium on
Linux. It uses a fake chart implementation and fake replay API; real-chart
interactive behavior has not been tested against your actual laptop.

LIMITATIONS
-----------
- Drawings annotate the current timeframe only. There is no cross-timeframe
  synchronization because the completed replay candles have different anchors.
- When you rewind and load only the last 180 known candles, drawings anchored
  to candles outside that loaded chart window won't display until their times
  are within the currently loaded chart data.
- No cloud sync or trade execution. Browser storage can be cleared by browser
  settings and may be different if you use a different host or browser profile.
- Stop-loss and take-profit on your paper trading ticket must be valid relative
  to the last revealed candle's close for an actual simulated trade.
- This is an original chart overlay built on Lightweight Charts 5.2.x, not the
  proprietary TradingView drawing engine.
