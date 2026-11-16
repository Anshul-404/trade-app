# Trading Bot — Paper Trading + Dhan NIFTY Option Chain

This extension adds a **right-side option chain and paper-trading panel** to your existing Trading Bot chart. Nothing routes simulated trades to Dhan orders. Existing live futures feed, analytics overlays and `.env` are unchanged.

## Install (Windows PowerShell in `C:\Users\avi\Desktop\trading_bot`)

1. Stop Uvicorn (`Ctrl+C`). Back up the whole project folder.
2. Extract the ZIP in the project root (`C:\Users\avi\Desktop\trading_bot`) preserving the `trading_bot` subfolder.
3. `python .\apply_options_paper.py`
4. `python -m pytest trading_bot/tests -q`
5. `python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001`
6. Open `http://127.0.0.1:8001` and refresh with Ctrl+F5.

Dhan Option Chain POST uses **NIFTY 50 INDEX** `UnderlyingScrip=13`, `UnderlyingSeg=IDX_I`, independent of your chart's current NIFTY futures selection. The chain comes from DhanHQ `/optionchain` and `/optionchain/expirylist` (data API subscription required).

**PAPER ONLY:** one long-only NIFTY CE/PE options lot is configured as **65 units** for Nov 2026; verify your actual expiry lot before using the simulation for analysis. Optional configuration in `trading_bot/.env`:

```
TB_PAPER_STARTING_CASH=100000
TB_PAPER_OPTION_LOT_SIZE=65
```

Both settings are used only for new paper ledgers. If a ledger already exists, changing starting balance does not reset it. The SQLite journal is stored in `trading_bot/data/paper_options.sqlite3` (back it up; do NOT commit to Git). Virtual BUY uses current cached option-chain **ask**; SELL closes existing long positions at current **bid**. If quote data are older than 20 seconds, paper orders are rejected. No short selling, no real money, no order placement API, no commissions, tax, margin, slippage or expiry/assignment handling. Cash, position averages, realized P&L and recent virtual trade history survive restarts. Unrealized P&L uses a recent bid, and equity is shown as unavailable when any position cannot be priced.

For performance, the chain has a 7-second server cache and the panel polls every 8 seconds; this does not add load to the chart WebSocket. Dhan enforces one unique option-chain request every 3 seconds; the implementation waits if necessary in a worker thread.

Test independently:

```
Invoke-RestMethod http://127.0.0.1:8001/api/options/expiries
Invoke-RestMethod 'http://127.0.0.1:8001/api/options/chain?expiry=YYYY-MM-DD'
Invoke-RestMethod http://127.0.0.1:8001/api/paper
```

Use an actual expiry from the expiry list. Do not use a futures expiry by assumption.

If installer cannot find expected code, it aborts *before modifying files*. If needed, restore `app.py.pre_options_paper.bak` and `index.html.pre_options_paper.bak` and remove the two added assets (`static/options_paper.js`, `static/options_paper.css`) and `options_paper.py`.
