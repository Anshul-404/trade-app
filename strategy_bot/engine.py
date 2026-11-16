"""Deterministic, causal strategy engine.

Feed it completed 15-minute candles in order with `on_bar`. It derives the 1H
and daily candles itself (09:15 IST aligned), so a decision can only use data
that had completed by that candle's close.

Order of evaluation, per completed 15m candle:
  1. fills scheduled for this candle's open (entry / signal exit)
  2. intrabar protective stop and structural target
  3. close-based exits (15m close through the frozen 1H midpoint, time rules)
  4. 15m retest / confirmation for a frozen 1H trigger
  5. 1H candle completion -> new trigger
  6. daily candle completion -> bias, three-day flip, levels

It emits paper signals and simulated fills only. Nothing here talks to a broker.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace

from .bars import Bar, IST, LAST_15M_START, invalid_reason, session_offset_minutes
from .levels import Level, LevelBook, SwingTracker

BULL, BEAR, NEUTRAL = "BULLISH", "BEARISH", "NEUTRAL"
SIGN = {BULL: 1, BEAR: -1}
STOP_EXITS = {"hard_stop", "hard_stop_gap", "midpoint_close_exit"}


@dataclass
class Setup:
    direction: str
    trigger: dict                 # immutable 1H trigger candle record
    midpoint: float               # frozen strategy stop
    retest_edge: float            # a 15m bar must trade back to this price to count as a retest
    diagnostics: dict
    bars_waited: int = 0
    retest_time: datetime | None = None
    attempts: int = 0
    rearmed: bool = False


@dataclass
class Position:
    direction: str
    entry_time: datetime
    entry: float
    stop: float                   # frozen 1H midpoint (15m-close exit level)
    hard_stop: float              # protective stop price
    target: float
    risk_points: float
    planned_rr: float
    signal: dict
    sessions: set = field(default_factory=set)


class StrategyEngine:
    def __init__(self, cfg: SimpleNamespace, structure_detector=None):
        self.cfg = cfg
        self.bias = NEUTRAL
        self.last_break: str | None = None
        self.trend = "UNCLEAR"
        self.hours: list[Bar] = []
        self.days: list[Bar] = []
        self._hour_parts: list[Bar] = []
        self._day_parts: list[Bar] = []
        self.last_bar: Bar | None = None
        self.book = LevelBook()
        self._swing_60m = SwingTracker(cfg.levels.swing_left, cfg.levels.swing_right)
        self._swing_1d = SwingTracker(cfg.levels.swing_left, cfg.levels.swing_right)
        self.setup: Setup | None = None
        self.position: Position | None = None
        self.pending_entry: dict | None = None
        self.pending_exit: str | None = None
        self.trades: list[dict] = []
        self.counters: Counter = Counter()
        self.day_r: Counter = Counter()
        self._events: list[dict] = []
        self._detector = structure_detector

    # ------------------------------------------------------------------ utils
    def _emit(self, kind: str, when: datetime, **data) -> None:
        self._events.append({"type": kind, "time": when.isoformat(), **data})

    def _skip(self, reason: str, when: datetime, **data) -> None:
        self.counters[f"skip:{reason}"] += 1
        self._emit("skip", when, reason=reason, **data)

    # ------------------------------------------------------------------- main
    def on_bar(self, bar: Bar) -> list[dict]:
        self._events = []
        reason = invalid_reason(bar, self.last_bar)
        if reason is None and bar.minutes != 15:
            reason = "not_a_15m_bar"
        if reason:
            self.counters[f"data_reject:{reason}"] += 1
            self._emit("data_reject", bar.start, reason=reason)
            return self._events

        previous = self.last_bar
        local = bar.start.astimezone(IST)
        if previous is not None:
            if previous.start.astimezone(IST).date() != local.date():
                self._finish_hour(previous.end)
                self._finish_day(previous.end)
                self._roll_week(previous, bar)
            elif bar.start != previous.end:
                self.counters["data_gap"] += 1
                self._emit("data_gap", bar.start, missing_from=previous.end.isoformat())
                if self.setup is not None and self.position is None:
                    self._skip("data_gap_during_setup", bar.start)
                    self.setup, self.pending_entry = None, None

        if self.position is not None:
            self.position.sessions.add(local.date())
            if self.pending_exit:
                self._close(bar.start, bar.open, self.pending_exit, slip=True)
        if self.pending_entry is not None:
            self._fill_entry(bar)
        if self.position is not None:
            self._intrabar(bar)

        self.book.on_close(bar.close)

        if self.position is not None:
            self._close_checks(bar)
        elif self.setup is not None and self.pending_entry is None:
            self._setup_step(bar, previous)

        self._add_to_hour(bar)
        self._day_parts.append(bar)
        if local.time() == LAST_15M_START:
            self._finish_day(bar.end)
        self.last_bar = bar
        return self._events

    def finish(self) -> list[dict]:
        """End of data: mark any open position closed at the last close."""
        self._events = []
        if self.position is not None and self.last_bar is not None:
            self._close(self.last_bar.end, self.last_bar.close, "end_of_data", slip=True)
        return self._events

    # ------------------------------------------------------------ aggregation
    def _add_to_hour(self, bar: Bar) -> None:
        offset = session_offset_minutes(bar.start.astimezone(IST))
        if self._hour_parts:
            first = session_offset_minutes(self._hour_parts[0].start.astimezone(IST))
            if offset // 60 != first // 60:
                self._finish_hour(bar.start)
        self._hour_parts.append(bar)
        if offset % 60 == 45 or bar.start.astimezone(IST).time() == LAST_15M_START:
            self._finish_hour(bar.end)

    def _finish_hour(self, when: datetime) -> None:
        parts = self._hour_parts
        if not parts:
            return
        self._hour_parts = []
        first = parts[0].start.astimezone(IST)
        offset = session_offset_minutes(first)
        start = parts[0].start - timedelta(minutes=offset % 60)
        short = start.time() == LAST_15M_START
        expected = 1 if short else 4
        volumes = [p.volume for p in parts]
        hour = Bar(start, 15 if short else 60, parts[0].open, max(p.high for p in parts),
                   min(p.low for p in parts), parts[-1].close,
                   None if any(v is None for v in volumes) else float(sum(volumes)),
                   complete=len(parts) == expected and offset % 60 == 0)
        previous_hours = self.hours
        self.hours = (previous_hours + [hour])[-600:]
        for side, price, origin in self._swing_60m.add(hour):
            self.book.add(Level(price, side, "swing_60m", origin, when))
        self._evaluate_trigger(hour, previous_hours, when)

    def _finish_day(self, when: datetime) -> None:
        parts = self._day_parts
        if not parts:
            return
        self._day_parts = []
        volumes = [p.volume for p in parts]
        start = parts[0].start
        day = Bar(start, 15, parts[0].open, max(p.high for p in parts), min(p.low for p in parts),
                  parts[-1].close, None if any(v is None for v in volumes) else float(sum(volumes)))
        self.days = (self.days + [day])[-300:]
        for side, price, origin in self._swing_1d.add(day):
            self.book.add(Level(price, side, "swing_1d", origin, when))
        self.book.replace_source("prior_day", [
            Level(day.high, "R", "prior_day", start, when), Level(day.low, "S", "prior_day", start, when)])
        self._update_bias(when)

    def _roll_week(self, previous: Bar, bar: Bar) -> None:
        old = previous.start.astimezone(IST).isocalendar()[:2]
        if bar.start.astimezone(IST).isocalendar()[:2] == old:
            return
        week = [d for d in self.days if d.start.astimezone(IST).isocalendar()[:2] == old]
        if week:
            self.book.replace_source("prior_week", [
                Level(max(d.high for d in week), "R", "prior_week", week[0].start, previous.end),
                Level(min(d.low for d in week), "S", "prior_week", week[0].start, previous.end)])

    # ------------------------------------------------------------- daily bias
    def _update_bias(self, when: datetime) -> None:
        cfg = self.cfg.daily
        days = self.days
        today = days[-1]
        flip = None
        if len(days) > cfg.flip_lookback_days:
            prior = days[-1 - cfg.flip_lookback_days:-1]
            if today.close > max(d.high for d in prior):
                flip = BULL
            elif today.close < min(d.low for d in prior):
                flip = BEAR
        if flip:
            self.last_break = flip

        trend = "UNCLEAR"
        if len(days) >= cfg.trend_lookback_days:
            closes = [d.close for d in days[-cfg.trend_lookback_days:]]
            ups = sum(b > a for a, b in zip(closes, closes[1:]))
            downs = sum(b < a for a, b in zip(closes, closes[1:]))
            if closes[-1] > closes[0] and ups >= cfg.min_directional_closes:
                trend = BULL
            elif closes[-1] < closes[0] and downs >= cfg.min_directional_closes:
                trend = BEAR
        self.trend = trend

        new_bias = self.last_break if (self.last_break and trend == self.last_break) else NEUTRAL
        old_bias, self.bias = self.bias, new_bias
        self._emit("daily", when, bias=new_bias, previous_bias=old_bias, trend=trend,
                   three_day_flip=flip, last_break=self.last_break,
                   day={"open": today.open, "high": today.high, "low": today.low, "close": today.close})
        if new_bias == old_bias and not flip:
            return
        if self.setup is not None and self.setup.direction != new_bias and self.position is None:
            self._skip("daily_bias_changed_before_entry", when)
            self.setup, self.pending_entry = None, None
        position = self.position
        if position is not None:
            against = (flip is not None and flip != position.direction) or \
                      (new_bias != NEUTRAL and new_bias != position.direction)
            if against and self.cfg.holding.daily_flip_action == "exit_next_open":
                self.pending_exit = self.pending_exit or "daily_bias_flip"

    # ----------------------------------------------------------- 1H trigger
    def _structure(self) -> dict:
        cfg = self.cfg.structure
        hours = [h for h in self.hours[-cfg.window_bars:]]
        if self._detector is None:
            from backend.models.market_data import Candle
            from trading_engine.bos_choch.structure_detector import detect_market_structure

            def detector(bars, left, right):
                candles = [Candle(timestamp=b.start, open=b.open, high=b.high, low=b.low,
                                  close=b.close, volume=b.volume or 0.0) for b in bars]
                return [(e.break_type, e.direction, e.broken_level, e.timestamp)
                        for e in detect_market_structure(candles, left_bars=left, right_bars=right)]
            self._detector = detector
        if len(hours) < cfg.swing_left + cfg.swing_right + 2:
            return {"last_break": None}
        events = self._detector(hours, cfg.swing_left, cfg.swing_right)
        if not events:
            return {"last_break": None}
        kind, direction, level, stamp = events[-1]
        return {"last_break": {"type": kind, "direction": direction, "level": level,
                               "time": stamp.isoformat()}}

    def _evaluate_trigger(self, hour: Bar, earlier: list[Bar], when: datetime) -> None:
        if self.bias == NEUTRAL:
            self.counters["h1:bias_neutral"] += 1
            return
        if self.position is not None or self.setup is not None or self.pending_entry is not None:
            self.counters["h1:busy"] += 1
            return
        cfg, sign = self.cfg, SIGN[self.bias]

        def reject(reason: str) -> None:
            self.counters[f"h1_reject:{reason}"] += 1

        if not hour.complete:
            return reject("incomplete_hour")
        if hour.minutes < 60 and not cfg.hourly.allow_short_session_bar:
            return reject("short_session_bar")
        if hour.range <= 0:
            return reject("zero_range")
        body = sign * (hour.close - hour.open)
        body_ratio = abs(hour.close - hour.open) / hour.range
        location = (hour.close - hour.low) / hour.range if sign > 0 else (hour.high - hour.close) / hour.range
        if body <= 0:
            return reject("candle_against_bias")
        if body_ratio < cfg.hourly.min_body_ratio:
            return reject("weak_body")
        if location < cfg.hourly.min_close_location:
            return reject("weak_close_location")

        reference = earlier if cfg.hourly.allow_short_session_bar else [h for h in earlier if h.minutes == 60]
        lookback = reference[-cfg.hourly.breakout_lookback_bars:]
        if len(lookback) < cfg.hourly.breakout_lookback_bars:
            return reject("insufficient_history")
        level = max(h.high for h in lookback) if sign > 0 else min(h.low for h in lookback)
        if sign * (hour.close - level) <= 0:
            return reject("no_close_breakout")

        relative_volume = None
        window = reference[-cfg.volume.window_bars:]
        if hour.volume is not None and len(window) == cfg.volume.window_bars \
                and all(h.volume is not None for h in window):
            mean = sum(h.volume for h in window) / len(window)
            relative_volume = hour.volume / mean if mean > 0 else None
        if cfg.volume.required:
            if relative_volume is None:
                return reject("volume_unavailable")
            if relative_volume < cfg.volume.min_relative_multiple:
                return reject("low_relative_volume")

        structure = self._structure()
        last = structure["last_break"]
        if cfg.structure.mode == "mandatory" and (last is None or last["direction"] != self.bias):
            return reject("structure_not_confirming")
        if cfg.structure.mode == "non_contradictory" and last is not None and last["direction"] != self.bias:
            return reject("structure_contradicts")

        trigger = {"open": hour.open, "high": hour.high, "low": hour.low, "close": hour.close,
                   "volume": hour.volume, "start": hour.start.isoformat(), "close_time": hour.end.isoformat()}
        diagnostics = {"daily_bias": self.bias, "daily_trend": self.trend, "last_three_day_break": self.last_break,
                       "body_ratio": round(body_ratio, 4), "close_location": round(location, 4),
                       "breakout_level": level,
                       "relative_volume": None if relative_volume is None else round(relative_volume, 4),
                       "structure": last}
        # Retest zone: back to the broken 1H level (+tolerance) or into the trigger
        # candle by the configured fraction of its range above/below the midpoint,
        # whichever is reached first. The midpoint itself remains the stop.
        by_level = level + sign * cfg.retest.tolerance_points
        by_range = hour.midpoint + sign * cfg.retest.pullback_fraction_of_trigger_range * hour.range
        edge = max(by_level, by_range) if sign > 0 else min(by_level, by_range)
        diagnostics["retest_edge"] = edge
        self.setup = Setup(self.bias, trigger, hour.midpoint, edge, diagnostics)
        self.counters["h1_trigger"] += 1
        self._emit("trigger", when, direction=self.bias, trigger=trigger, midpoint=hour.midpoint, **diagnostics)

    # ----------------------------------------------------- 15m retest / entry
    def _setup_step(self, bar: Bar, previous: Bar | None) -> None:
        setup, cfg = self.setup, self.cfg
        sign = SIGN[setup.direction]
        setup.bars_waited += 1
        if setup.bars_waited > cfg.retest.expiry_bars:
            self._skip("setup_expired", bar.end, trigger_close=setup.trigger["close_time"])
            self.setup = None
            return
        if not setup.rearmed and sign * (bar.close - setup.midpoint) < 0:
            self._skip("midpoint_closed_before_entry", bar.end, trigger_close=setup.trigger["close_time"])
            self.setup = None
            return
        if setup.retest_time is None:
            touched = bar.low <= setup.retest_edge if sign > 0 else bar.high >= setup.retest_edge
            if touched:
                setup.retest_time = bar.start
        if setup.retest_time is None or previous is None or bar.range <= 0:
            return
        strong = sign * (bar.close - bar.open) > 0 and \
            abs(bar.close - bar.open) / bar.range >= cfg.retest.min_confirm_body_ratio
        broke = bar.close > previous.high if sign > 0 else bar.close < previous.low
        if strong and broke:
            self._evaluate_entry(bar, setup, sign)

    def _evaluate_entry(self, bar: Bar, setup: Setup, sign: int) -> None:
        cfg = self.cfg
        entry, stop = bar.close, setup.midpoint
        base = {"direction": setup.direction, "planned_entry": entry, "stop": stop,
                "trigger": setup.trigger, "confirmation_close_time": bar.end.isoformat(),
                "retest_time": setup.retest_time.isoformat(), **setup.diagnostics}
        if sign * (entry - stop) <= 0:
            # Midpoint on the wrong side of the entry: never use the absolute distance.
            self._skip("stop_on_wrong_side", bar.end, **base)
            if not setup.rearmed:
                self.setup = None
            return
        found = self.book.nearest_target(sign, entry, cfg.levels.target_sources,
                                         cfg.levels.cluster_tolerance_points, cfg.levels.zone_edge)
        if found is None:
            self._skip("no_causal_structural_target", bar.end, **base)
            self.setup = None
            return
        risk = abs(entry - stop)
        reward = sign * (found["target"] - entry)
        rr = reward / risk if risk > 0 else 0.0
        details = {**base, **found, "risk_points": risk, "reward_points": reward, "planned_rr": round(rr, 4)}
        if reward <= 0:
            self._skip("target_on_wrong_side", bar.end, **details)
            self.setup = None
            return
        if rr < cfg.strategy.min_rr:
            self._skip("rr_below_minimum", bar.end, **details)
            self.setup = None
            return
        setup.attempts += 1
        details["attempt"] = setup.attempts
        details["id"] = f"{setup.trigger['close_time']}|{bar.end.isoformat()}|{setup.direction}"
        details["hard_stop"] = stop - sign * cfg.hard_stop.buffer_points
        self.pending_entry = details
        self.counters["signal"] += 1
        self._emit("signal", bar.end, mode="PAPER_SIGNAL_NOT_A_LIVE_ORDER", **details)

    def _fill_entry(self, bar: Bar) -> None:
        signal, cfg = self.pending_entry, self.cfg
        self.pending_entry = None
        sign = SIGN[signal["direction"]]
        local = bar.start.astimezone(IST)
        price = bar.open + sign * cfg.costs.slippage_points_per_side
        stop, target = signal["stop"], signal["target"]

        def missed(reason: str, **extra) -> None:
            self._skip(reason, bar.start, id=signal["id"], fill_price=price, **extra)
            self.setup = None

        if self.day_r[local.date()] <= -cfg.risk.max_daily_loss_r:
            return missed("risk_daily_loss_cap")
        if local.weekday() == 4 and not cfg.holding.friday_carry_allowed \
                and bar.end.astimezone(IST).time() >= cfg.holding.friday_cutoff:
            return missed("friday_cutoff_no_new_entry")
        if sign * (price - stop) <= 0:
            return missed("fill_stop_on_wrong_side")
        if sign * (target - price) <= 0:
            return missed("fill_beyond_target")
        risk = abs(price - stop)
        rr = sign * (target - price) / risk
        if rr < cfg.strategy.min_rr:
            return missed("fill_rr_below_minimum", fill_rr=round(rr, 4))
        self.position = Position(signal["direction"], bar.start, price, stop, signal["hard_stop"], target,
                                 risk, rr, signal, {local.date()})
        self.counters["entry"] += 1
        self._emit("entry", bar.start, id=signal["id"], direction=signal["direction"], entry=price,
                   stop=stop, hard_stop=signal["hard_stop"], target=target,
                   risk_points=risk, planned_rr=round(rr, 4))

    # ------------------------------------------------------------------ exits
    def _intrabar(self, bar: Bar) -> None:
        position, sign = self.position, SIGN[self.position.direction]
        hard, target = position.hard_stop, position.target
        adverse = bar.low if sign > 0 else bar.high
        favourable = bar.high if sign > 0 else bar.low
        if sign * (bar.open - hard) <= 0:
            return self._close(bar.start, bar.open, "hard_stop_gap", slip=True)
        hit_stop = sign * (adverse - hard) <= 0
        hit_target = sign * (favourable - target) >= 0
        if hit_stop and hit_target and self.cfg.hard_stop.same_bar_collision == "target_first":
            self.counters["same_bar_collision"] += 1
            return self._close(bar.end, target, "target", slip=False)
        if hit_stop:
            if hit_target:
                self.counters["same_bar_collision"] += 1
            return self._close(bar.end, hard, "hard_stop", slip=True)
        if hit_target:
            # A favourable gap through the target still fills at the target.
            return self._close(bar.end, target, "target", slip=False)

    def _close_checks(self, bar: Bar) -> None:
        position, cfg = self.position, self.cfg.holding
        sign = SIGN[position.direction]
        local_end = bar.end.astimezone(IST)
        if local_end.weekday() == 4 and not cfg.friday_carry_allowed and local_end.time() >= cfg.friday_cutoff:
            return self._close(bar.end, bar.close, "friday_exit", slip=True)
        if len(position.sessions) >= cfg.max_sessions and local_end.time() >= cfg.forced_exit:
            return self._close(bar.end, bar.close, "max_hold_exit", slip=True)
        if sign * (bar.close - position.stop) < 0:
            self.pending_exit = "midpoint_close_exit"

    def _close(self, when: datetime, price: float, reason: str, *, slip: bool) -> None:
        position, cfg = self.position, self.cfg
        sign = SIGN[position.direction]
        fill = price - sign * cfg.costs.slippage_points_per_side if slip else price
        gross = sign * (fill - position.entry)
        net = gross - cfg.costs.round_trip_cost_points
        trade = {
            "id": position.signal["id"], "direction": position.direction,
            "entry_time": position.entry_time.isoformat(), "exit_time": when.isoformat(),
            "entry": position.entry, "exit": fill, "exit_reason": reason,
            "stop": position.stop, "hard_stop": position.hard_stop, "target": position.target,
            "risk_points": position.risk_points, "planned_rr": round(position.planned_rr, 4),
            "gross_points": round(gross, 4), "net_points": round(net, 4),
            "gross_r": round(gross / position.risk_points, 4), "net_r": round(net / position.risk_points, 4),
            "hold_minutes": int((when - position.entry_time).total_seconds() // 60),
            "sessions_held": len(position.sessions), "attempt": position.signal["attempt"],
            "trigger_close_time": position.signal["trigger"]["close_time"],
        }
        self.trades.append(trade)
        self.day_r[when.astimezone(IST).date()] += trade["net_r"]
        self.position, self.pending_exit = None, None
        self.counters[f"exit:{reason}"] += 1
        self._emit("exit", when, **trade)

        setup, rules = self.setup, cfg.reentry
        if setup is None:
            return
        if (reason in STOP_EXITS and not rules.require_new_trigger and setup.attempts < rules.max_attempts
                and self.bias == setup.direction):
            # Re-entry needs a completely fresh 15m retest + confirmation.
            setup.rearmed, setup.bars_waited, setup.retest_time = True, 0, None
        else:
            self.setup = None

    # ---------------------------------------------------------------- summary
    def state(self) -> dict:
        return {"bias": self.bias, "trend": self.trend, "last_three_day_break": self.last_break,
                "setup": None if self.setup is None else {
                    "direction": self.setup.direction, "midpoint": self.setup.midpoint,
                    "retest_edge": self.setup.retest_edge, "trigger": self.setup.trigger,
                    "attempts": self.setup.attempts, "bars_waited": self.setup.bars_waited},
                "position": None if self.position is None else {
                    k: v for k, v in asdict(self.position).items() if k not in {"signal", "sessions"}},
                "pending_entry": self.pending_entry, "active_levels": len(self.book.levels)}
