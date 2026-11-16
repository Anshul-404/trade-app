from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.models.market_data import Candle
from trading_engine.market_structure.swing_points import (
    SwingPoint,
    detect_swing_points,
)


Trend = Literal["BULLISH", "BEARISH", "NEUTRAL"]
Direction = Literal["BULLISH", "BEARISH"]
BreakType = Literal["BOS", "CHoCH"]


@dataclass
class StructureBreak:
    """Represents a confirmed market structure break."""

    index: int
    timestamp: datetime
    break_type: BreakType
    direction: Direction
    broken_level: float
    previous_trend: Trend
    swing_index: int
    swing_timestamp: datetime


def detect_market_structure(
    candles: list[Candle],
    left_bars: int = 2,
    right_bars: int = 2,
    initial_trend: Trend = "NEUTRAL",
) -> list[StructureBreak]:
    """
    Detect confirmed BOS and CHoCH events.

    Rules:
    1. Breakouts require candle closing prices beyond swing levels.
    2. Swing points become available only after confirmation.
    3. Each swing level can generate only one event.
    4. Only one structure break is generated per candle.
    5. A break in the current trend is BOS.
    6. A break against the current trend is CHoCH.
    7. The first break from neutral is classified as BOS.
    """

    if initial_trend not in ("BULLISH", "BEARISH", "NEUTRAL"):
        raise ValueError("Invalid initial trend.")

    if not candles:
        return []

    # Validate candle ordering.
    for previous, current in zip(candles, candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "Candles must be in chronological order."
            )

    swing_points = detect_swing_points(
        candles,
        left_bars=left_bars,
        right_bars=right_bars,
    )

    swings_by_confirmation = {}

    for swing in swing_points:
        swings_by_confirmation.setdefault(
            swing.confirmation_timestamp, []
        ).append(swing)

    latest_swing_high: SwingPoint | None = None
    latest_swing_low: SwingPoint | None = None

    broken_high_indices = set()
    broken_low_indices = set()

    current_trend = initial_trend
    structure_breaks = []

    for index, candle in enumerate(candles):

        # Register only swings confirmed by this candle.
        confirmed_swings = swings_by_confirmation.get(
            candle.timestamp, []
        )

        for swing in confirmed_swings:

            if swing.kind == "HIGH":
                latest_swing_high = swing

            elif swing.kind == "LOW":
                latest_swing_low = swing

        bullish_break = (
            latest_swing_high is not None
            and latest_swing_high.index not in broken_high_indices
            and index > latest_swing_high.index
            and candle.close > latest_swing_high.price
        )

        bearish_break = (
            latest_swing_low is not None
            and latest_swing_low.index not in broken_low_indices
            and index > latest_swing_low.index
            and candle.close < latest_swing_low.price
        )

        # Avoid generating conflicting signals on the same candle.
        if bullish_break and bearish_break:
            continue

        if not bullish_break and not bearish_break:
            continue

        if bullish_break:
            swing = latest_swing_high
            direction: Direction = "BULLISH"
            broken_high_indices.add(swing.index)

        else:
            swing = latest_swing_low
            direction = "BEARISH"
            broken_low_indices.add(swing.index)

        previous_trend = current_trend

        # Determine BOS or CHoCH.
        if previous_trend == "NEUTRAL":
            break_type: BreakType = "BOS"

        elif previous_trend == direction:
            break_type = "BOS"

        else:
            break_type = "CHoCH"

        structure_breaks.append(
            StructureBreak(
                index=index,
                timestamp=candle.timestamp,
                break_type=break_type,
                direction=direction,
                broken_level=swing.price,
                previous_trend=previous_trend,
                swing_index=swing.index,
                swing_timestamp=swing.timestamp,
            )
        )

        current_trend = direction

    return structure_breaks