"""Causal swing detection and the support/resistance book used for targets.

A swing with N right-hand bars exists only once those N bars have completed;
`known_at` records that moment. Nothing here can see a later candle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .bars import Bar


@dataclass
class Level:
    price: float
    side: str            # "R" resistance (from highs) or "S" support (from lows)
    source: str          # swing_60m | swing_1d | prior_day | prior_week
    origin_time: datetime
    known_at: datetime


class SwingTracker:
    """Strict left/right swing highs and lows, emitted only at confirmation."""

    def __init__(self, left: int, right: int, keep: int = 400):
        if left < 1 or right < 1:
            raise ValueError("swing left/right must be >= 1")
        self.left, self.right, self.keep = left, right, keep
        self.bars: list[Bar] = []

    def add(self, bar: Bar) -> list[tuple[str, float, datetime]]:
        self.bars.append(bar)
        if len(self.bars) > self.keep:
            del self.bars[: len(self.bars) - self.keep]
        n = len(self.bars)
        i = n - 1 - self.right
        if i < self.left:
            return []
        pivot = self.bars[i]
        around = self.bars[i - self.left:i] + self.bars[i + 1:i + 1 + self.right]
        found = []
        if all(pivot.high > other.high for other in around):
            found.append(("R", pivot.high, pivot.start))
        if all(pivot.low < other.low for other in around):
            found.append(("S", pivot.low, pivot.start))
        return found


@dataclass
class LevelBook:
    """Active (not yet closed-through) levels known so far."""

    levels: list[Level] = field(default_factory=list)

    def add(self, level: Level) -> None:
        self.levels.append(level)

    def replace_source(self, source: str, new: list[Level]) -> None:
        self.levels = [lv for lv in self.levels if lv.source != source] + new

    def on_close(self, close: float) -> None:
        """A completed close through a level retires it (wicks do not)."""
        self.levels = [lv for lv in self.levels
                       if not ((lv.side == "R" and close > lv.price) or (lv.side == "S" and close < lv.price))]

    def nearest_target(self, direction: int, entry: float, sources: list[str],
                       cluster_tolerance: float, zone_edge: str):
        """Nearest opposing level beyond `entry`; never skips an intervening one.

        Returns None when no level exists, else a dict with the target price,
        the clustered zone and the next level beyond it (diagnostics only).
        """
        side = "R" if direction > 0 else "S"
        ahead = sorted((lv for lv in self.levels
                        if lv.side == side and lv.source in sources and direction * (lv.price - entry) > 0),
                       key=lambda lv: direction * (lv.price - entry))
        if not ahead:
            return None
        zone = [ahead[0]]
        for lv in ahead[1:]:
            if abs(lv.price - zone[-1].price) <= cluster_tolerance:
                zone.append(lv)
            else:
                break
        prices = [lv.price for lv in zone]
        near, far = (min(prices), max(prices)) if direction > 0 else (max(prices), min(prices))
        target = {"near": near, "far": far, "mid": (near + far) / 2.0}[zone_edge]
        beyond = ahead[len(zone)].price if len(ahead) > len(zone) else None
        return {"target": target, "zone_near": near, "zone_far": far,
                "zone_levels": [{"price": lv.price, "source": lv.source,
                                 "origin": lv.origin_time.isoformat()} for lv in zone],
                "zone_touches": len(zone), "next_level_beyond": beyond,
                "levels_ahead": len(ahead)}
