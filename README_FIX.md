# Trading Bot — Futures lookup diagnostic fix

This is a **minimal patch**. It changes only `trading_bot/contracts.py` and the matching tests. It does not alter live-feed code, candles, the browser chart, API credentials or order execution.

The previous contract lookup was tested only against synthetic ISO-date CSV rows. This version supports multiple expiry formats, a few additional explicit NIFTY 50 aliases, and `--diagnose` to reveal which filter is eliminating contracts. It refuses to guess an ID or overwrite `.env` when no valid active contract is found.

## Installation (PowerShell at C:\Users\avi\Desktop\trading_bot)

1. Stop Uvicorn with Ctrl+C.
2. Back up the existing `trading_bot/contracts.py` and `trading_bot/tests/test_contracts.py`.
3. Extract this ZIP into the project root, merging the nested `trading_bot` folder. **Preserve `trading_bot/.env`.**
4. Run:

```powershell
python -m pytest trading_bot/tests/test_contracts.py -q
python -m trading_bot.contracts --diagnose
```

If the diagnostics show active valid contracts, select nearest:

```powershell
python -m trading_bot.contracts --nearest
python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
```

Then verify:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/api/status | Format-List
```

Expected: `exchange: NSE_FNO`, instrument future, dynamically resolved contract ID, connected feed and loaded history (depending on Dhan account and market data access). `orders_enabled` must remain `False`.

If `--diagnose` still returns no contracts, share only the diagnostic output; **do not send `.env`, Dhan tokens, or the entire CSV**. The output contains only public instrument metadata.

Dhan instrument source: https://images.dhan.co/api-data/api-scrip-master.csv . The current code assumes the compact schema documented at https://dhanhq.co/docs/v2/instruments/ .
