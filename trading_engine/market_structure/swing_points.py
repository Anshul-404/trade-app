from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.models.market_data import Candle


@dataclass
class SwingPoint:
    """Represents a confirmed swing high or swing low."""

    index: int
    timestamp: datetime
    price: float
    kind: Literal["HIGH", "LOW"]
    confirmation_timestamp: datetime


def detect_swing_points(
    candles: list[Candle],
    left_bars: int = 2,
    right_bars: int = 2
) -> list[SwingPoint]:
    """
    Detect confirmed swing highs and swing lows.

    A swing high must have a higher high than every candle
    in the surrounding left and right windows.

    A swing low must have a lower low than every candle
    in the surrounding left and right windows.

    Signals are confirmed only after right_bars candles
    have completed.
    """

    if left_bars < 1 or right_bars < 1:
        raise ValueError("left_bars and right_bars must be at least 1.")

    if not candles:
        return []

    for previous, current in zip(candles, candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError("Candles must be in chronological order.")

    swing_points = []

    for i in range(left_bars, len(candles) - right_bars):

        current = candles[i]

        left_window = candles[i - left_bars:i]
        right_window = candles[i + 1:i + right_bars + 1]

        surrounding_candles = left_window + right_window

        # Swing High detection
        is_swing_high = all(
            current.high > candle.high
            for candle in surrounding_candles
        )

        # Swing Low detection
        is_swing_low = all(
            current.low < candle.low
            for candle in surrounding_candles
        )

        if is_swing_high:
            swing_points.append(
                SwingPoint(
                    index=i,
                    timestamp=current.timestamp,
                    price=current.high,
                    kind="HIGH",
                    confirmation_timestamp=right_window[-1].timestamp
                )
            )

        if is_swing_low:
            swing_points.append(
                SwingPoint(
                    index=i,
                    timestamp=current.timestamp,
                    price=current.low,
                    kind="LOW",
                    confirmation_timestamp=right_window[-1].timestamp
                )
            )

    return sorted(
        swing_points,
        key=lambda point: point.index
    )