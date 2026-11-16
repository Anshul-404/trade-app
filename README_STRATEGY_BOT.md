# NIFTY strategy bot (`strategy_bot/`) — paper mode

Implements the "Trading Bot — Strategy Rules and Conditions v1.0" specification:
completed-daily bias with the three-day flip → strong completed 1H trigger →
completed 15m retest + confirmation → stop at the frozen 1H trigger midpoint →
target at the nearest confirmed structural level → trade only if R:R ≥ 3.

**Status: unvalidated.** It has only been exercised on synthetic candles. It has
not been backtested on real NIFTY futures data, and it cannot place orders.

## What it does

| Piece | File |
|---|---|
| Candle validation, 09:15 IST aligned 15m / 1H aggregation | `strategy_bot/bars.py` |
| Causal swings, support/resistance book, nearest-target rule | `strategy_bot/levels.py` |
| Bias, trigger, retest, entry filter, exits, re-entry | `strategy_bot/engine.py` |
| Backtest / replay CLI with report | `strategy_bot/backtest.py` |
| Paper runner on live Dhan candles + journal | `strategy_bot/live.py` |
| Strict config loader | `strategy_bot/config.py` |
| Placeholder parameters | `strategy_bot/configs/experiment_baseline.toml` |

BOS/CHoCH comes from the existing `trading_engine/bos_choch/structure_detector.py`.

## Parameters are placeholders

Section 12 of the rules leaves the thresholds unfinalized. The engine has **no
defaults in code**: every value must be in the TOML file, and a missing key is an
error. `experiment_baseline.toml` contains placeholder numbers so the program can
run; they are not tuned and not part of the rules. Review each one, and keep the
config beside every result (the backtest copies it to `config_used.toml`).

How the unfinalized items were made objective (all changeable in the config):

- **Daily bias** — BULLISH only when the latest three-day close break is bullish
  *and* the last 4–5 closes trend up (net higher, with at least
  `min_directional_closes` up-closes). Mirror for BEARISH. Anything else is
  NEUTRAL, including the start of data. No SMA filter.
- **1H trigger** — candle in the bias direction, body/range and close-location
  minimums, close beyond the extreme of the previous N complete 1H candles,
  volume ≥ multiple of the previous-window mean. Unknown volume fails the
  filter. The 15:15 fifteen-minute "hour" is excluded.
- **BOS/CHoCH** — `mandatory`, `non_contradictory` or `record_only`, judged on the
  latest 1H structure break.
- **15m retest** — a completed 15m candle trades back to the broken 1H level
  (± tolerance) or into the trigger candle by a set fraction of its range from the
  midpoint; then a completed candle in the trade direction, with a minimum body,
  closes beyond the previous candle's high/low. A close through the midpoint
  before entry, or expiry after N candles, cancels the setup.
- **Target** — nearest active level beyond the entry among 1H swings, daily
  swings, prior-day and prior-week high/low. Levels are known only after their
  right-hand candles complete and are retired by a completed close through them.
  A nearer level is never skipped; if it gives less than 3R the setup is skipped.
- **Entry** — open of the next 15m candle; R:R is re-checked at the fill price.
- **Exits** — target (limit), protective hard stop a fixed buffer beyond the
  midpoint (gaps fill at the open, stop wins same-candle collisions), 15m close
  through the midpoint (filled at the next open), daily flip, two-session limit,
  Friday cutoff.
- **Re-entry** — only after a fresh retest and confirmation, capped per trigger.

## Backtest

```
python -m strategy_bot.backtest --config strategy_bot/configs/experiment_baseline.toml ^
    --csv data/historical/<FOLDER>/1m.csv --timeframe 1 --out data/strategy/run1
```

The CSV is the downloader format (`epoch, open, high, low, close, volume`).
Output: `report.json` (funnel of triggers → signals → fills, rejection counts,
win rate, expectancy, drawdown, yearly/monthly, in/out-of-sample if
`backtest.out_of_sample_start` is set), `trades.csv`, `events.jsonl` (every
signal and skip with its reason and diagnostics), `data_audit.json`.

Results are in **NIFTY points and R multiples**, not option premium or rupees.
Slippage is applied inside fill prices; `total_cost_points` is the per-trade
charge only.

Data caveats:

- The existing 3-year download is the NIFTY 50 **spot index**: no volume and not
  futures prices. It can only be run with a config that sets
  `data.volume_available = false` and `volume.required = false`, and that is a
  separately labelled experiment, not the specified strategy.
- A stitched historical futures series with rollover is not built yet.

## Paper run on Dhan

```
python -m trading_bot.contracts --nearest      # select the NIFTY future in trading_bot/.env
python -m strategy_bot.live --config strategy_bot/configs/experiment_baseline.toml --once
python -m strategy_bot.live --config strategy_bot/configs/experiment_baseline.toml
```

It checks the contract against Dhan's instrument master, warms up on ~30 days of
1-minute candles, then polls and acts only on completed 15m candles. Signals,
simulated fills and skips are appended to `data/strategy/paper_journal.jsonl`;
restarts rebuild state from history and never journal an event twice. Retries
with backoff on network/rate-limit errors, stops on an authorization failure, and
warns when candles go stale during market hours.

## Not built

- **Live orders.** No order code exists, and `execution.mode` accepts only
  `"paper"`.
- **Options execution**: strike/expiry choice, premium-based stop and target,
  lot sizing and rupee risk. Paper trades are simulated on the futures price.
- Exchange holiday calendar (Friday is detected by weekday), automatic contract
  rollover, and broker position reconciliation.

## Tests

```
python -m pytest strategy_bot/tests -q
```
