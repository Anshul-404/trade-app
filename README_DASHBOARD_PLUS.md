# Trading Bot — Dashboard Plus (visual market movers & paper risk)

This patch is **paper-only**. It never submits, cancels, or changes live Dhan orders.
Existing futures chart, Dhan credentials, historical data, BOS/CHoCH and OB/FVG
engines are untouched. Do not extract into your Python `venv` directory.

## Install (PowerShell, Trading Bot project root)

1. Stop Uvicorn with Ctrl+C and back up your whole project.
2. Download the ZIP and place it in Downloads.
3. In `C:\Users\avi\Desktop\trading_bot`:

```powershell
$zip = "$env:USERPROFILE\Downloads\trading_bot_dashboard_plus_patch.zip"
Expand-Archive -LiteralPath $zip -DestinationPath (Get-Location).Path -Force
python .\apply_dashboard_plus.py
python -m pytest trading_bot/tests -q
python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
```

Open http://127.0.0.1:8001 and press Ctrl+F5.

## Features

- **NIFTY 50 gainers/losers**: ranked from Dhan bulk NSE equity quotes by
  percentage move from previous close (`net_change / (LTP - net_change)`).
  Separately lists up to 25 positive and 25 negative securities, never pads
  either list. Refreshes every 25 seconds; server caches quotes for 20 seconds.
- **Index membership**: uses NSE Indices official NIFTY 50 constituent CSV
  `https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv`, mapped
  to Dhan security IDs from Dhan's official instrument master. If NSE blocks
  automated downloads, manually obtain its official CSV and place it at
  `trading_bot/data/nifty50_constituents.csv` (do not invent holdings).
- **Close All Paper**: requires a separate confirmation, verifies a fresh bid
  for every open paper option, then closes all in *one SQLite transaction*;
  missing or stale quotes abort *every* close. Requires current chain snapshots
  for each open expiry. It does not call any broker order API.
- **Option premium SL/TP**: for each *long paper option* open position, save a
  reference stop below entry and take profit above entry. They are displayed
  on a mini chart of option BID snapshots captured while the page is open.
  **These references do not automatically close paper positions**.
- **Futures chart SL/TP**: manually enter price levels and draw horizontal
  reference lines on the NIFTY futures candlestick chart. They are visual only
  and saved in the browser for the current symbol; not linked to option orders.

## Verify

```powershell
Invoke-RestMethod 'http://127.0.0.1:8001/api/movers' | Select-Object universe,valid_quotes,mapped,age_seconds
Invoke-RestMethod 'http://127.0.0.1:8001/api/paper/risk' | Format-List
```

The first movers request may take seconds because it downloads and parses the
instrument master. Subsequent requests should use the cache. If the NSE
constituent download fails, check the error before providing the local file.
Do not expect a constant 25 positive and 25 negative names each day.

### Roll back

Stop Uvicorn. Restore each file from its matching
`.pre_dashboard_plus.bak` file:
- `trading_bot/app.py`
- `trading_bot/static/index.html`
- `trading_bot/static/options_paper.js`

New files can remain, because they are not used when the imports and HTML tags
have been rolled back. Do not delete `trading_bot/data/paper_options.sqlite3`.

### Known limitations

Option premiums are cached roughly every 8 s while the option chain tab is
visible, so the mini graph is **not exchange-quality tick history** and SL/TP
are **not guaranteed exits**. Buy/close fills remain simplified best ask/bid
simulations without brokerage, margin, taxes, slippage or expiry settlement.
Market mover lists are only current **NIFTY 50 equities**, not all NSE stocks.
WebSocket/chart lag needs separate diagnosis; this patch is not a latency fix.
