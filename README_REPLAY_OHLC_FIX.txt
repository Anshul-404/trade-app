TRADING BOT — HISTORICAL REPLAY OHLC VALIDATION PATCH
====================================================

What this fixes
---------------
Previous replay_server.py stopped at the first CSV OHLC inconsistency:
  Cannot load replay: Invalid candle 1m.csv:38313: invalid OHLC

This patch lets replay load while reporting data quality transparently:
- OHLC float rounding violations <= 0.000001 index points are normalized
  in memory for charting ONLY (high/low expanded to include open/close).
- Larger invalid or nonnumeric OHLC rows, and duplicate/out-of-order
  timestamps, are excluded from the chart and logged to an audit CSV.
- The source 1m/2m/5m/15m/60m/1d/1w CSV files and XLSX remain unchanged.
- The dashboard displays the count of excluded/normalized bars.
- Optional --strict-data restores abort-on-first-anomaly behavior.

Installation (PowerShell, from your trading_bot folder)
------------------------------------------------------
1. Stop the old server with Ctrl+C.
2. Back up your current files:
     Copy-Item .\replay_server.py .\replay_server.before_ohlc_fix.py
     Copy-Item .\replay_static\replay.js .\replay_static\replay.before_ohlc_fix.js
3. Extract Trading_Bot_Replay_OHLC_Fix.zip into your trading_bot folder.
   Overwrite replay_server.py and replay_static/replay.js.
4. Start:
     python replay_server.py
5. Visit http://127.0.0.1:8787 and refresh with Ctrl+F5.

New audit CSV (if issues exist):
  data\historical\NIFTY50_13\replay_data_issues_1m.csv
There can be one report per affected timeframe.

Optional strict-mode verification:
  python replay_server.py --strict-data

Optional tests (no market data or Dhan API required):
  python -m unittest discover -s tests -p test_replay_server.py -v

Important
---------
The patch does not invent missing OHLC prices and does not repair your Dhan
source file. Excluding a candle creates a historical gap. Manual backtest
results around missing candles, especially stop/target fills, are unreliable.
Inspect the report and verify those timestamps with your source if precision
matters. No broker or Dhan API connection is used.
