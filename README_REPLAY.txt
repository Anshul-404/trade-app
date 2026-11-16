TRADING BOT — HISTORICAL BAR REPLAY / MANUAL BACKTESTER
=====================================================
This project adds a local historical replay feature to your existing Trading Bot.
It does not overwrite the Dhan downloader, chart page, or existing backtesting engine.

PREREQUISITES
- Windows Python 3.10+ (your current Python environment is suitable).
- The downloaded CSV files in trading_bot/data/historical/NIFTY50_13/:
    1m.csv  2m.csv  5m.csv  15m.csv  60m.csv  1d.csv  1w.csv
- Browser: Chrome / Firefox / Edge.
- NO Dhan access token, brokerage account, or subscription is needed to replay.
- The chart JavaScript is fetched from jsDelivr the first time unless you run the
  optional one-time chart-library installation command (see below).

INSTALLATION
1. Extract ALL the files from this ZIP directly into:
      C:\Users\avi\Desktop\trading_bot\
   You should get:
      trading_bot\replay_server.py
      trading_bot\replay_static\index.html
      trading_bot\replay_static\replay.css
      trading_bot\replay_static\replay.js
      trading_bot\tests\test_replay_server.py
      trading_bot\README_REPLAY.txt
2. Open PowerShell (activate your venv if you want; no extra pip installs needed).
      cd C:\Users\avi\Desktop\trading_bot
      python replay_server.py
3. The browser opens automatically. Or manually visit:
      http://127.0.0.1:8787/
4. Stop the server by pressing Ctrl+C in PowerShell.

OFFLINE CHART JAVASCRIPT INSTALLATION (OPTIONAL BUT RECOMMENDED)
Run this ONCE while internet is available:
      python replay_server.py --install-chart-library
Then:
      python replay_server.py
You will be able to chart and replay all downloaded history without internet.
If the first command fails due to firewall, the browser will try CDN directly.

CUSTOM DATA DIRECTORY OR PORT
      python replay_server.py --data-dir "D:\mydata\NIFTY50_13" --port 8788
Only the seven CSVs are used (faster than reading the merged XLSX); the Excel file
is retained untouched. If only the Excel workbook exists, restore/recreate your
seven CSVs using the existing downloader without changing your original workbook.

HOW TO PRACTISE
1. Select a timeframe and select a date within your downloaded history.
2. Click Load Date. The chart shows up to 180 earlier candles; NO future bars.
3. Click Next, +10, or Play to reveal more candles at speeds up to 20/sec.
4. Enter a stop-loss, take-profit and quantity. Select LONG or SHORT.
5. Click Open simulated long / short. Entry is filled at the visible bar's CLOSE.
6. Future revealed OHLC candles trigger stop/target or click Close manually.
7. Check win rate, realized points and R, then Export CSV to save your journal.
8. Add horizontal chart levels for support/resistance as needed.

IMPORTANT ASSUMPTIONS / LIMITATIONS
- Uses NIFTY 50 INDEX candles. These are not NIFTY futures contract prices and
  a real NIFTY index itself cannot be directly executed as a futures contract.
- This is MANUAL replay practice; it does not automate existing BOS/CHoCH rules.
- No exchange brokerage, slippage, trading taxes, spread or market impact.
- Entries fill at the LAST REVEALED candle close; no orders are sent anywhere.
- Exits are evaluated on bars strictly AFTER the entry candle. When both stop
  and target are inside a single OHLC candle, the STOP wins (conservative).
- If a later bar opens past the stop, stop fills at that OPEN (gap adverse fill).
- Favorable gaps through target fill at target, not the more favorable open.
- Profit/loss shown as index points TIMES the chosen quantity, not INR or profit
  of a real futures contract. 1R = achieved P&L / original stop risk.
- Changing timeframe, seeking, loading date, or going backward CLEARS trade
  history for the current session: export the journal before doing so.
- The server loads CSV data into RAM on demand, caching the last two timeframes.
- This version shows no candles after the selected start until replay reveals
  them. API source data stays on your computer; no data is uploaded.
- One open paper trade at a time. Journal remains in the browser until reset.
- Incomplete/missing candles in original data are not automatically repaired.

TESTS (no broker or internet required)
      python -m unittest discover -s tests -p test_replay_server.py -v

Chart library: TradingView Lightweight Charts, Apache-2.0; see
https://github.com/tradingview/lightweight-charts and https://www.tradingview.com
