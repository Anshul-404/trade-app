# Trading Bot — Phase 2B NIFTY futures name fix

Your Dhan instrument master returned 18 NSE FUTIDX rows but `SM_SYMBOL_NAME` was blank, causing `nifty_name_matches: 0`. This update changes only the contract lookup and its tests.

The updated lookup uses the underlying name when present. When it is blank, it accepts **exact** NIFTY contract names from `SEM_TRADING_SYMBOL` (`NIFTY-Jan2027-FUT`) or `SEM_CUSTOM_SYMBOL` (`NIFTY JAN FUT`). It will not accidentally select BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYFPI, or NIFTYNXT50. It never guesses security IDs or changes your `.env` during diagnostics.

## Windows PowerShell (from `C:\Users\avi\Desktop\trading_bot`)

1. Stop Uvicorn with Ctrl+C, and back up your project files.
2. Extract this ZIP to the project **root** so `trading_bot/contracts.py` is replaced. Keep your `.env` untouched.
3. Run:

```powershell
python -m pytest trading_bot/tests -q
python -m trading_bot.contracts --diagnose
python -m trading_bot.contracts
```

Confirm that the listed **nearest contract** is `NIFTY`, with a valid expiry and lot size. The diagnostic sample from your own account included the January 2027 contract `NIFTY-Jan2027-FUT` but earlier expiries may exist; select only from the fresh listing.

4. Stop the server if running, then run:

```powershell
python -m trading_bot.contracts --nearest
python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
```

In a second terminal:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8001/api/status' | Format-List
```

Expected: `exchange: NSE_FNO`, a fresh `security_id` and `symbol: NIFTY FUT ...`. The feed and historical statuses must also be checked separately. Order execution remains disabled.
