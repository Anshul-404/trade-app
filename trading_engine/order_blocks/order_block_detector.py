from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.models.market_data import Candle
from trading_engine.bos_choch.structure_detector import StructureBreak


OrderBlockType = Literal["BULLISH", "BEARISH"]

MitigationStatus = Literal[
    "UNTOUCHED",
    "PARTIALLY_MITIGATED",
    "FULLY_MITIGATED",
]

OrderBlockStrength = Literal[
    "WEAK",
    "MODERATE",
    "STRONG",
]


@dataclass
class OrderBlock:
    """Represents a BOS/CHoCH-confirmed Order Block."""

    index: int
    timestamp: datetime

    block_type: OrderBlockType

    top: float
    bottom: float
    midpoint: float

    break_index: int
    break_timestamp: datetime

    displacement_percentage: float
    strength: OrderBlockStrength

    mitigation_status: MitigationStatus = "UNTOUCHED"
    mitigation_percentage: float = 0.0

    invalidated: bool = False


def detect_order_blocks(
    candles: list[Candle],
    structure_breaks: list[StructureBreak],
    lookback: int = 10,
) -> list[OrderBlock]:
    """
    Detect Order Blocks associated with confirmed structure breaks.

    Bullish Order Block:
        Last bearish candle before a bullish structure break.

    Bearish Order Block:
        Last bullish candle before a bearish structure break.

    The full high-low range of the selected candle is used
    as the Order Block zone.
    """

    if lookback < 1:
        raise ValueError("Lookback must be at least 1.")

    if not candles or not structure_breaks:
        return []

    # Validate candle chronology.
        # Validate candle chronology.
    for previous, current in zip(candles, candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError("Candles must be chronological.")

    # Validate structure break event chronology.
    for previous, current in zip(
        structure_breaks,
        structure_breaks[1:],
    ):
        if current.index < previous.index:
            raise ValueError(
                "Structure break events must be chronological."
            )

    order_blocks = []
    used_indices = set()

    for event in structure_breaks:
        if event.index < 0 or event.index >= len(candles):
            raise ValueError("Structure break index is invalid.")

        if event.timestamp != candles[event.index].timestamp:
            raise ValueError("Structure break timestamp is invalid.")

        if event.swing_index < 0 or event.swing_index >= event.index:
            raise ValueError("Swing index is invalid.")

        if event.swing_timestamp != candles[event.swing_index].timestamp:
            raise ValueError("Swing timestamp is invalid.")

        if event.break_type not in ("BOS", "CHoCH"):
            raise ValueError("Invalid structure break type.")

        if event.previous_trend not in (
            "BULLISH",
            "BEARISH",
            "NEUTRAL",
            ):
            raise ValueError("Invalid previous trend.")
        start_index = max(
            0,
            event.index - lookback,
            event.swing_index,
        )

        selected_index = None

        for index in range(event.index - 1, start_index - 1, -1):

            candle = candles[index]

            if event.direction == "BULLISH":
                # Find the last bearish candle.
                if candle.close < candle.open:
                    selected_index = index
                    break

            else:
                # Find the last bullish candle.
                if candle.close > candle.open:
                    selected_index = index
                    break

        if selected_index is None:
            continue

        # Avoid duplicate Order Blocks from the same candle.
        if selected_index in used_indices:
            continue

        used_indices.add(selected_index)

        block_candle = candles[selected_index]
        break_candle = candles[event.index]

        top = block_candle.high
        bottom = block_candle.low
        midpoint = (top + bottom) / 2

        # Measure price displacement from the OB candle close
        # to the candle confirming the structure break.
        displacement_percentage = (
            abs(break_candle.close - block_candle.close)
            / block_candle.close
        ) * 100

        if displacement_percentage >= 1.0:
            strength: OrderBlockStrength = "STRONG"

        elif displacement_percentage >= 0.5:
            strength = "MODERATE"

        else:
            strength = "WEAK"

        order_blocks.append(
            OrderBlock(
                index=selected_index,
                timestamp=block_candle.timestamp,
                block_type=event.direction,
                top=top,
                bottom=bottom,
                midpoint=midpoint,
                break_index=event.index,
                break_timestamp=event.timestamp,
                displacement_percentage=displacement_percentage,
                strength=strength,
            )
        )

    # Track mitigation after the structure break.
    for block in order_blocks:

        for candle in candles[block.break_index + 1:]:

            if block.block_type == "BULLISH":

                if candle.low <= block.bottom:
                    block.mitigation_percentage = 100.0
                    block.mitigation_status = "FULLY_MITIGATED"

                elif candle.low < block.top:

                    penetration = block.top - candle.low

                    percentage = (
                        penetration / (block.top - block.bottom)
                    ) * 100

                    block.mitigation_percentage = max(
                        block.mitigation_percentage,
                        min(100.0, percentage),
                    )

            else:

                if candle.high >= block.top:
                    block.mitigation_percentage = 100.0
                    block.mitigation_status = "FULLY_MITIGATED"

                elif candle.high > block.bottom:

                    penetration = candle.high - block.bottom

                    percentage = (
                        penetration / (block.top - block.bottom)
                    ) * 100

                    block.mitigation_percentage = max(
                        block.mitigation_percentage,
                        min(100.0, percentage),
                    )

            if (
                block.mitigation_percentage > 0
                and block.mitigation_status != "FULLY_MITIGATED"
            ):
                block.mitigation_status = "PARTIALLY_MITIGATED"

            # Invalidation requires a candle close beyond the zone.
            if block.block_type == "BULLISH":
                if candle.close < block.bottom:
                    block.invalidated = True

            else:
                if candle.close > block.top:
                    block.invalidated = True

    return order_blocks