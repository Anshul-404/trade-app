# Trading Bot — Phase 4: Order Blocks and Fair Value Gaps

This patch **extends Phase 3**. It adds `/api/zones` and two separately switchable, shaded zone types (OB / FVG) to the read-only live chart. No Dhan credentials, market subscriptions, order placement, or strategy engine algorithms are changed.

## Installation (Windows PowerShell)

1. Stop Uvicorn (Ctrl+C). Back up the project.
2. Extract `trading_bot_phase4_ob_fvg_zones_patch.zip` into `C:\Users\avi\Desktop\trading_bot` (the directory containing your `trading_bot` package), merging folders. Keep `.env` unchanged.
3. In PowerShell, while in that project directory, run:

```powershell
python .\apply_phase4.py
python -m pytest trading_bot/tests -q
python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001
```

4. Open http://127.0.0.1:8001 and hard-refresh (Ctrl+F5). Use the `OB` and `FVG` toggles above the chart.
5. In a separate terminal:

```powershell
$r = Invoke-RestMethod 'http://127.0.0.1:8001/api/zones?timeframe=2'
$r | Select-Object symbol,timeframe,bars_analyzed,incomplete_bars_excluded,total_order_blocks,total_fair_value_gaps,skipped_order_blocks,skipped_fair_value_gaps
$r.order_blocks | Select-Object -Last 3 | Format-Table
$r.fair_value_gaps | Select-Object -Last 3 | Format-Table
```

## Prerequisites

Phase 3 `/api/structure` is working and the copied original engine files must exist:

- `trading_engine/bos_choch/structure_detector.py`
- `trading_engine/order_blocks/order_block_detector.py`
- `trading_engine/fair_value_gaps/fvg_detector.py`

The installer checks these files **before modifying** the existing app.

## Safety and semantics

- Uses the user's existing `detect_market_structure`, `detect_order_blocks`, and `detect_fair_value_gaps` functions.
- Excludes the current incomplete chart bar.
- OB zones begin at the breakout that **confirmed** them, not at the earlier selected opposite-colored candle. FVG zones begin at the **third candle** of the three-candle pattern.
- Mitigation/invalidation statuses reflect the latest analyzed completed candle and are *not* a precise historical replay of when the zone stopped being valid. Filled zones are faded.
- Historical OHLC is marked separately from live LTP-sampled candles. Live LTP-sampled bar highs and lows may miss true intrabar extremes; zone signals on these bars are indicative.
- The chart limits display to the most recent 40 of each type to avoid clutter; `total_*` counts report all qualifying zones from the analyzed window.
- No automatic order execution is enabled.
- Installer creates `*.pre_phase4.bak` backups of any modified HTML/CSS/Python app files. Re-running it is idempotent.

## Troubleshooting

If `/api/zones` returns HTTP 500, check the Uvicorn traceback. Original engine versions may expose different fields than the previously documented `OrderBlock` or `FairValueGap` models; capture the traceback for the next repair. The overlay does not affect your live Dhan feed.
