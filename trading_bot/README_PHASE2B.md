# Trading Bot — Phase 2B: NIFTY futures contract lookup + OHLC crosshair

Incremental patch for the **Phase 2A Trading Bot** located at
`C:\Users\avi\Desktop\trading_bot` (the package is its nested `trading_bot` folder).
No credentials or order-execution code are included.

## What's included

- `trading_bot/contracts.py` — read-only official Dhan compact instrument-master lookup and explicit local configuration commands.
- `trading_bot/static/index.html` — chart OHLC line.
- `trading_bot/static/app.js` — hover-to-inspect OHLC and candle change.
- `trading_bot/static/style.css` — OHLC display styles.
- `trading_bot/tests/test_contracts.py` — regression tests with synthetic security-master CSV, **no network required**.

**The existing Dhan feed, historical-data service, candle logic, order restrictions, and `.env` file are not included or replaced by this patch.**

## Installation (PowerShell)

1. Stop the running Uvicorn process with `Ctrl+C`.
2. Make a backup of the `trading_bot` project folder before replacing files.
3. Extract this ZIP **into the project's root** (`C:\Users\avi\Desktop\trading_bot`), merging the nested `trading_bot` folder. Replace the three existing `static` files; the Python module and test are new.
4. Run:

```powershell
python -m pytest trading_bot/tests -q
python -m trading_bot.contracts
```

The second command downloads Dhan's latest official security-master CSV, filters active NSE FUTIDX NIFTY 50 contracts, and prints valid Security IDs, expiries, and lot sizes. **It does not change your configuration yet.**

## Switch chart to NIFTY futures

While Uvicorn is stopped, run:

```powershell
python -m trading_bot.contracts --nearest
python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
```

`--nearest` changes only these keys in the existing local `trading_bot/.env`:

- `TB_EXCHANGE=NSE_FNO`
- `TB_INSTRUMENT=FUTIDX`
- `TB_SECURITY_ID=<ID loaded from latest official list>`
- `TB_SYMBOL=NIFTY FUT <expiry date>`

Your credentials and unrelated settings remain untouched. To choose a particular listed expiry instead, first run `python -m trading_bot.contracts` and then run `python -m trading_bot.contracts --select SECURITY_ID`.

Verify in another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/api/status | Format-List
Invoke-RestMethod http://127.0.0.1:8001/api/quality | Format-List
```

Expected: exchange `NSE_FNO`, symbol `NIFTY FUT YYYY-MM-DD`; connected feed when market data subscription/token works. If the history API fails, check the token and Data API subscription, and do not assume historical data loaded.

## Restore NIFTY 50 spot chart

Stop Uvicorn, then run:

```powershell
python -m trading_bot.contracts --restore-index
python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
```

## Risk and data-quality notes

- Futures are actual traded contracts, unlike the spot index; **do not compare their LTP 1:1**.
- Futures contracts expire. Always refresh Dhan's instrument master to avoid stale security IDs. This module fetches the official list each time you list/select contracts; it never uses hardcoded futures IDs.
- This phase **does not place orders**. `orders_enabled` remains false.
- The current live *Ticker* feed only supplies sampled last-traded price and receipt time; its OHLC may miss market highs/lows and is **not suitable for signal-grade backtesting**. Futures volume from historical data is retained; live Ticker-mode volume is `null`.
- `--nearest` selects the first future by listed expiry date, **not** by volume/liquidity. Make sure it is the contract you intended before any later execution-related development.
- The CSV download needs network access; no credentials are sent in the request (the official instrument master is public). The CSV's schema may change; on unexpected format the program refuses to select a contract rather than guessing.
