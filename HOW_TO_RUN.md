# How to run the trading bot — step by step

These steps are for **Windows PowerShell**. Run every command from the `trade-app` folder unless a step says otherwise.

> **Important**
> - The bot is **paper mode only**. It cannot place real orders.
> - The thresholds in the settings file are **placeholders**, not validated values.
> - These commands were checked on Linux, not on a Windows machine.

## Contents

1. [Get the code](#1-get-the-code)
2. [Create a Python environment](#2-create-a-python-environment)
3. [Put your Dhan credentials back](#3-put-your-dhan-credentials-back)
4. [Run the tests](#4-run-the-tests)
5. [Review the settings file](#5-review-the-settings-file)
6. [Select the NIFTY futures contract](#6-select-the-nifty-futures-contract)
7. [Download candle data](#7-download-candle-data)
8. [Run the backtest](#8-run-the-backtest)
9. [Run the paper bot](#9-run-the-paper-bot)
10. [Optional: chart tools](#10-optional-chart-tools)
11. [Daily routine](#daily-routine)
12. [Common problems](#common-problems)

---

## 1. Get the code

**Why:** the strategy bot lives on a GitHub branch, so it has to be copied to your computer first.

```powershell
cd C:\Users\avi\Desktop
git clone -b ccr-d78433a7-nvtuaq https://github.com/Anshul-404/trade-app.git
cd trade-app
```

## 2. Create a Python environment

**Why:** the old `venv` folder was deliberately left out of the repo, so you need a fresh one with the required packages. Python **3.11 or newer** is required.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r trading_bot\requirements.txt -r requirements-downloader.txt pytest
```

You will see `(venv)` at the start of the prompt when the environment is active.

## 3. Put your Dhan credentials back

**Why:** the `.env` files hold your Dhan token, so they were never uploaded to GitHub. Copy them from your old project folder.

```powershell
Copy-Item C:\Users\avi\Desktop\trading_bot\.env .\.env
Copy-Item C:\Users\avi\Desktop\trading_bot\trading_bot\.env .\trading_bot\.env
```

> The Dhan access token expires every **24 hours**. Paste a fresh one into **both** files each day before running anything that talks to Dhan.

## 4. Run the tests

**Why:** confirms the install works before you trust any output. No Dhan connection is needed.

```powershell
python -m pytest strategy_bot/tests -q
```

**Expected result:** `29 passed`

## 5. Review the settings file

**Why:** the strategy rules leave many thresholds undecided. The file contains placeholder numbers so the bot can run; results mean nothing until you have reviewed them.

```powershell
Copy-Item strategy_bot\configs\experiment_baseline.toml strategy_bot\configs\my_config.toml
notepad strategy_bot\configs\my_config.toml
```

Every line has a comment saying what it controls. Use `my_config.toml` in all the commands below.

## 6. Select the NIFTY futures contract

**Why:** the strategy reads the futures chart, and the contract ID changes at every expiry.

```powershell
python -m trading_bot.contracts --nearest
```

This lists the active contracts and saves the nearest one into `trading_bot\.env`.
**Write down the Security ID it prints** — you need it in the next step.

## 7. Download candle data

**Why:** the backtest needs historical candles, and none were included in the archive.

```powershell
python dhan_history.py --symbol NIFTYFUT --security-id <ID> --segment NSE_FNO --instrument FUTIDX --days 60
```

Replace `<ID>` with the Security ID from step 6.

**Result:** `data\historical\NIFTYFUT_<ID>\1m.csv`

> A single futures contract only has a few months of history, so this is a short test, not a 3-year one.

## 8. Run the backtest

**Why:** shows how many setups the rules find, how many are rejected and why, and the results in points and R multiples.

```powershell
python -m strategy_bot.backtest --config strategy_bot\configs\my_config.toml --csv data\historical\NIFTYFUT_<ID>\1m.csv --timeframe 1 --out data\strategy\run1
```

Then open the `data\strategy\run1` folder:

| File | What it contains |
|---|---|
| `report.json` | Summary: triggers, signals, fills, win rate, expectancy, drawdown |
| `trades.csv` | One row per trade |
| `events.jsonl` | Every signal and every skipped setup, with the reason |
| `data_audit.json` | Candles that were rejected as invalid |
| `config_used.toml` | Copy of the settings that produced this result |

> Results are in **NIFTY points and R multiples**, not option premium or rupees.

### Using your existing 3-year spot index data instead

That data has no volume, so two settings must change in `my_config.toml`:

| Section | Setting | Value |
|---|---|---|
| `[data]` | `volume_available` | `false` |
| `[volume]` | `required` | `false` |

Then point `--csv` at that folder's `1m.csv`. Treat the result as an **experiment**: it uses spot prices and no volume filter, which is not the stated strategy.

## 9. Run the paper bot

**Why:** this is the bot watching Dhan during market hours and producing paper signals.

**First, a one-shot check** that the connection and contract work:

```powershell
python -m strategy_bot.live --config strategy_bot\configs\my_config.toml --once
```

It prints the current daily bias, any active setup and any open paper position.

**If that works, leave it running:**

```powershell
python -m strategy_bot.live --config strategy_bot\configs\my_config.toml
```

- Signals, simulated fills and skips appear in the window.
- Everything is also saved to `data\strategy\paper_journal.jsonl`.
- Stop it with **Ctrl+C**.
- Restarting is safe: it rebuilds its state and never logs an event twice.

## 10. Optional: chart tools

**Why:** to watch the same market visually while the bot runs. These are unchanged from your original project.

| Tool | Command | Open in browser |
|---|---|---|
| Live chart, option chain, paper panel | `python -m uvicorn trading_bot.app:app --host 127.0.0.1 --port 8001` | http://127.0.0.1:8001 |
| Manual bar replay on downloaded data | `python replay_server.py` | http://127.0.0.1:8787 |

---

## Daily routine

Once everything is set up, each trading day:

1. Open PowerShell in the `trade-app` folder and activate the environment:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
2. Paste the fresh Dhan token into `.env` and `trading_bot\.env`.
3. Near expiry, rerun [step 6](#6-select-the-nifty-futures-contract) to move to the next contract.
4. Start the paper bot ([step 9](#9-run-the-paper-bot)).

## Common problems

| What you see | Likely cause | What to do |
|---|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell script policy | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again |
| `Dhan authorization failed` | Token expired or no Data API plan | Paste a fresh token into both `.env` files |
| `Security id ... is not an active NIFTY future` | Contract expired | Rerun step 6 |
| `missing keys (no defaults exist)` | A setting was deleted from the config | Restore the line from `experiment_baseline.toml` |
| `volume.required=true needs data.volume_available=true` | Config says data has no volume but the volume filter is on | Set both as shown in step 8 |
| `No completed 15m candle for over 35 minutes` | Dhan data stopped arriving | Check your internet and token; the bot keeps retrying |
| Backtest shows zero trades | The rules are strict, or the data period is short | Read `report.json` → `counters` to see where setups were rejected |
