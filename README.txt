TRADING BOT — DHAN HISTORICAL DOWNLOADER (7 CSVs + ONE EXCEL SHEET)
==================================================================

NOW INCLUDED: A SINGLE EXCEL WORKSHEET, ALL SEVEN TIMEFRAMES

The downloader creates CSV files for 1m, 2m, 5m, 15m, 60m, 1d, 1w and
also combines them into one XLSX file with just one sheet: All_Candles.
There is a `timeframe` column so you can filter to any interval in Excel.
Excel timestamp_IST cells are real Excel dates in Indian Standard Time.
The worksheet contains: timeframe, timestamp_IST, epoch, open, high, low,
close, volume, open_interest.

YOU ALREADY DOWNLOADED 3 YEARS? NO NEED TO CALL DHAN AGAIN!
Run from your Trading Bot project root (PowerShell):

    python dhan_history.py --excel-only

Or directly run the standalone export script:

    python merge_history_to_xlsx.py --dir data/historical/NIFTY50_13

The resulting file is:

    data/historical/NIFTY50_13/NIFTY50_13_All_Candles.xlsx

From now on, each normal Dhan download will also create this workbook:

    python dhan_history.py --years 3

Both the downloader and Excel exporter display progress bars.

DOWNLOAD SETUP — Windows PowerShell, from the Trading Bot project root:

    python -m pip install -r requirements-downloader.txt
    if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    notepad .env

Enter your actual Dhan access token in .env. Keep it private.
For --excel-only, no Dhan token or network connection is required.

EXISTING DOWNLOAD FEATURES
- Retrieves ~3 years of 1-minute index OHLCV data in Dhan API <=85-day windows.
- Retrieves daily candles using Dhan's historical endpoint.
- Creates 2m, 5m, 15m, 60m from 1m; weekly from completed daily bars.
- CSV files retained for programmatic backtesting and reproducibility.
- Retries transient errors, uses chunk caching, and shows progress bars.
- Skips the current incomplete candle where appropriate.
- EXCEL LIMIT: one worksheet may contain at most 1,048,576 rows, including
  the heading. The normal 3-year index dataset fits; the script checks first.

DIRECTORY:

    data/historical/NIFTY50_13/
        1m.csv
        2m.csv
        5m.csv
        15m.csv
        60m.csv
        1d.csv
        1w.csv
        NIFTY50_13_All_Candles.xlsx

Note: These are NIFTY 50 INDEX prices, not NIFTY futures. Expired futures
are not automatically stitched. Validate time gaps before backtesting.
