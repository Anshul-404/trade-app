TRADING BOT — DHAN HISTORICAL CSV DOWNLOADER
============================================

What it does:
- Downloads up to 3 years of 1-minute data in <=85-day Dhan API batches.
- Downloads 1-day candles through Dhan's daily historical endpoint.
- Creates 2m, 5m, 15m, 60m from 1m and 1w from 1d.
- Creates 7 files: 1m.csv, 2m.csv, 5m.csv, 15m.csv, 60m.csv, 1d.csv, 1w.csv.
- Terminal progress bar; exponential-backoff retries; cached chunks for resume.
- CSV columns: timestamp (IST +0530), epoch (UTC seconds), open, high, low,
  close, volume, open_interest.
- By default, drops current incomplete minute, incomplete higher-timeframe
  intraday candle, current incomplete daily and current incomplete weekly candle.

SETUP — Windows PowerShell, from your Trading Bot project root:

  python -m pip install -r requirements-downloader.txt
  Copy-Item .env.example .env
  notepad .env

Paste your Dhan access token in .env. Keep it private.

DEMO — NIFTY 50 Index, 30 calendar days (Dhan index Security ID 13):

  python dhan_history.py --days 30

FULL DOWNLOAD — NIFTY 50 Index, last 3 years:

  python dhan_history.py

OTHER INSTRUMENT EXAMPLE — RELIANCE EQUITY:

  python dhan_history.py --symbol RELIANCE --security-id 2885 --segment NSE_EQ --instrument EQUITY

For another instrument, use the security ID from Dhan's current instrument
master. Never assume an active NIFTY futures security ID provides 3 years of
intraday futures: expired futures intraday history is not available through
Dhan's normal charts endpoint; futures rollover cannot be reconstructed from
this downloader alone.

OUTPUT

  data/historical/NIFTY50_13/1m.csv
  data/historical/NIFTY50_13/2m.csv
  data/historical/NIFTY50_13/5m.csv
  data/historical/NIFTY50_13/15m.csv
  data/historical/NIFTY50_13/60m.csv
  data/historical/NIFTY50_13/1d.csv
  data/historical/NIFTY50_13/1w.csv

Interrupted? Rerun the command, it will reuse cached downloaded chunks. Run
with --force to refetch all 1-minute chunks. Cached chunks exclude the current
minute at the end of each request. For production backtesting, audit history
coverage, missing-minute gaps, corporate actions, and exchange trading hours.

Note: Dhan's Data API access may require a paid subscription; access tokens
expire after 24 hours. Do not commit .env to source control.
