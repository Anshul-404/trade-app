"""Backtest / replay the strategy on historical candles.

    python -m strategy_bot.backtest --config strategy_bot/configs/experiment_baseline.toml \
        --csv data/historical/NIFTYFUT/1m.csv --timeframe 1 --out data/strategy/backtest_run1

Results are in NIFTY underlying points and R multiples only. They are NOT
option-premium or rupee P&L; that needs historical option data and a separate
execution model.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from . import config as config_module
from .bars import Bar, aggregate, load_csv
from .engine import StrategyEngine


def run(bars15: list[Bar], cfg, detector=None):
    engine = StrategyEngine(cfg, structure_detector=detector)
    events: list[dict] = []
    for bar in bars15:
        events.extend(engine.on_bar(bar))
    events.extend(engine.finish())
    return engine, events


def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    r = [t["net_r"] for t in trades]
    wins = [t for t in trades if t["net_points"] > 0]
    losses = [t for t in trades if t["net_points"] <= 0]
    equity = peak = drawdown = 0.0
    streak = worst_streak = 0
    for value in r:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if value <= 0 else 0
        worst_streak = max(worst_streak, streak)
    monthly: dict[str, dict] = defaultdict(lambda: {"trades": 0, "net_r": 0.0, "net_points": 0.0})
    yearly: dict[str, dict] = defaultdict(lambda: {"trades": 0, "net_r": 0.0, "net_points": 0.0})
    for t in trades:
        for bucket, key in ((monthly, t["exit_time"][:7]), (yearly, t["exit_time"][:4])):
            bucket[key]["trades"] += 1
            bucket[key]["net_r"] = round(bucket[key]["net_r"] + t["net_r"], 4)
            bucket[key]["net_points"] = round(bucket[key]["net_points"] + t["net_points"], 4)
    holds = [t["hold_minutes"] for t in trades]
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 4),
        "net_expectancy_r": round(statistics.fmean(r), 4),
        "gross_expectancy_r": round(statistics.fmean(t["gross_r"] for t in trades), 4),
        "average_win_r": round(statistics.fmean(t["net_r"] for t in wins), 4) if wins else None,
        "average_loss_r": round(statistics.fmean(t["net_r"] for t in losses), 4) if losses else None,
        "total_net_r": round(sum(r), 4),
        "total_net_points": round(sum(t["net_points"] for t in trades), 4),
        "total_cost_points": round(sum(t["gross_points"] - t["net_points"] for t in trades), 4),
        "max_drawdown_r": round(drawdown, 4), "longest_losing_streak": worst_streak,
        "average_hold_minutes": round(statistics.fmean(holds), 1),
        "median_hold_minutes": statistics.median(holds),
        "exposure_minutes": sum(holds),
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "by_year": dict(yearly), "by_month": dict(monthly),
    }


def report(engine: StrategyEngine, events: list[dict], cfg, data_audit: dict) -> dict:
    counters = dict(sorted(engine.counters.items()))
    result = {
        "config": {"name": cfg.meta.name, "status": cfg.meta.status},
        "units": "NIFTY underlying points and R multiples - not option or rupee P&L",
        "data_audit": data_audit,
        "funnel": {
            "hourly_triggers": counters.get("h1_trigger", 0),
            "signals_generated": counters.get("signal", 0),
            "entries_filled": counters.get("entry", 0),
            "setups_rejected_rr": counters.get("skip:rr_below_minimum", 0),
            "setups_rejected_no_target": counters.get("skip:no_causal_structural_target", 0),
            "missed_or_failed_entries": sum(v for k, v in counters.items() if k.startswith("skip:fill_")
                                            or k in {"skip:risk_daily_loss_cap", "skip:friday_cutoff_no_new_entry"}),
        },
        "counters": counters,
        "all": metrics(engine.trades),
    }
    split = cfg.backtest.out_of_sample_start
    if split:
        date.fromisoformat(split)
        result["in_sample"] = metrics([t for t in engine.trades if t["entry_time"][:10] < split])
        result["out_of_sample"] = metrics([t for t in engine.trades if t["entry_time"][:10] >= split])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the NIFTY strategy (paper, causal, completed bars only)")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path, help="Downloader CSV of completed candles")
    parser.add_argument("--timeframe", required=True, type=int, choices=(1, 5, 15),
                        help="Minutes per candle in --csv (1 and 5 are aggregated to 15)")
    parser.add_argument("--out", required=True, type=Path, help="Directory for trades.csv / events.jsonl / report.json")
    args = parser.parse_args(argv)

    cfg = config_module.load(args.config)
    bars, audit = load_csv(args.csv, args.timeframe, volume_available=cfg.data.volume_available)
    data_audit = {"source": str(args.csv), "rows_accepted": len(bars), "rows_rejected": len(audit),
                  "reject_reasons": dict(Counter(a["reason"] for a in audit))}
    if args.timeframe != 15:
        need = cfg.data.min_1m_bars_per_15m if args.timeframe == 1 else 3
        bars, bucket_audit = aggregate(bars, 15, min_parts=need)
        data_audit["incomplete_15m_buckets_excluded"] = len(bucket_audit)
        audit += bucket_audit
    if not bars:
        parser.error("no usable candles after validation")
    data_audit.update(first_bar=bars[0].start.isoformat(), last_bar=bars[-1].end.isoformat(), bars_15m=len(bars))

    engine, events = run(bars, cfg)
    summary = report(engine, events, cfg, data_audit)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.out / "data_audit.json").write_text(json.dumps(audit, indent=1), encoding="utf-8")
    (args.out / "config_used.toml").write_bytes(args.config.read_bytes())
    with (args.out / "events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            if event["type"] != "daily":
                handle.write(json.dumps(event, default=str) + "\n")
    if engine.trades:
        with (args.out / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(engine.trades[0]))
            writer.writeheader()
            writer.writerows(engine.trades)
    print(json.dumps({k: summary[k] for k in ("config", "units", "data_audit", "funnel")}, indent=2))
    print(json.dumps({k: v for k, v in summary["all"].items() if k not in {"by_month"}}, indent=2))
    print(f"\nWritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
