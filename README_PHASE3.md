# Trading Bot — Phase 3A: BOS / CHoCH chart markers

This is a **read-only overlay** for your existing live futures dashboard. It does **not** include `trading_engine` or change your `.env`, Dhan feed, contract, or execution settings. It imports your copied `trading_engine/bos_choch/structure_detector.py` and `backend/models/market_data.py`.

## Install (Windows PowerShell)

1. Stop Uvicorn with Ctrl+C.
2. Extract this ZIP into the **project root** `C:\Users\avi\Desktop\trading_bot` (you should see `apply_phase3.py` next to your existing `trading_bot` directory).
3. In the activated venv, run:

   ```powershell
   python .\apply_phase3.py
   python -m pytest trading_bot/tests -q
   python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
   ```

4. In a new terminal, verify:

   ```powershell
   Invoke-RestMethod "http://127.0.0.1:8001/api/structure?timeframe=2" | Format-List
   ```

5. Reload `http://127.0.0.1:8001` with Ctrl+F5. Look for **BOS / CHoCH: ON** toggle and arrow markers on completed candles.

You can run `python .\apply_phase3.py` repeatedly; it will not duplicate the changes. Before patching, the script saves `app.py.pre_phase3.bak`, `app.js.pre_phase3.bak`, etc.

## Data-quality and safety notes

- NIFTY futures security ID and data subscription remain configured exactly as before.
- Only **closed timeframe candles** are passed to `detect_market_structure`; engine swing confirmation is left unchanged. Markers on sampled LTP bars are **indicative** because the live Ticker stream can miss intrabar highs/lows.
- An empty list of markers is not a failure: it may mean no confirmed BOS/CHoCH event in the last 500 candles.
- Neither the API endpoint nor the UI includes any order placement.

## If the installer stops

The patcher requires the Phase 2A/2B dashboard containing `/api/quality`, `renderOhlc`, `series.setData(candles)`, and `if(bar.time>lastBarTime){lastBarTime=bar.time;}`. If your local copy differs, it aborts *before* overwriting the originals; share the error so we can adapt to your copy.
