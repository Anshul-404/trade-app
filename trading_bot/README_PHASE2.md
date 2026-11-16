# Trading Bot — Phase 2A: data-quality update

This is an **incremental patch** for the Dhan live-chart starter used in this conversation.
It is NOT a complete application and it does NOT place trades.

## Files included

- `trading_bot/candles.py`: provider-vs-live provenance, unknown live volume represented as JSON `null`, session-aligned OHLC aggregation, data-quality diagnostics.
- `trading_bot/app.py`: `/api/quality` endpoint and explicit receipt-time/live-OHLC metadata. No Dhan connection or chart URL changes.
- `trading_bot/tests/test_candles.py`: candle and data-quality tests.

## Install (Windows PowerShell)

1. Stop Uvicorn with Ctrl+C.
2. Back up your current `trading_bot/candles.py`, `trading_bot/app.py` and `trading_bot/tests/test_candles.py` files. **Do not replace your `.env` file.**
3. Copy the files from this ZIP into the matching folders under `C:\Users\avi\Desktop\trading_bot\trading_bot\`, replacing the matching starter files. If you modified your starter files yourself, review/merge rather than overwrite.
4. In a terminal opened at `C:\Users\avi\Desktop\trading_bot`, activate your venv and run:

```powershell
python -m pytest trading_bot/tests -q
python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
```

Then in another PowerShell terminal:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8001/api/quality' | Format-List
$r = Invoke-RestMethod 'http://127.0.0.1:8001/api/candles?timeframe=2'
$r.candles | Select-Object -Last 5 | Format-List *
```

## Interpretation

`source: historical`: Dhan historical OHLC.
`source: live_sampled`: built from streamed last-traded prices received by your computer; may miss exchange highs and lows.
`source: mixed`: a candle combining historical and tick samples, or aggregate containing both.
`volume: null`: volume is unknown/not applicable; **not** zero trade volume.

For `IDX_I` / `INDEX` instruments, volume is suppressed because an index doesn't trade as a security. For future futures subscriptions, historical volume can be retained, while new Ticker-only live candles still have unknown volume until the stream uses quote volume and correct cumulative-volume differencing.

`observed_intraday_missing_minutes` checks gaps only between observed minute bars within the same regular market session. It does not count overnight/weekend gaps. A missing bar is a diagnostic, not necessarily an exchange feed failure.

`large_1m_ranges_at_least_1pct` flags wide 1-minute bars for manual review. It is not an automatic error detector.

**Safety:** Ticker-sampled live bars are for display only. For backtesting or execution signals, use confirmed/verified provider historical OHLC and appropriate feed timestamps; do not assume receipt-time-timestamped samples are complete tick data.
