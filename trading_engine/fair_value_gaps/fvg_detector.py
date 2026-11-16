from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.models.market_data import Candle


FVGType = Literal["BULLISH", "BEARISH"]
MitigationStatus = Literal[
    "UNTOUCHED",
    "PARTIALLY_MITIGATED",
    "FULLY_MITIGATED",
]
FVGStrength = Literal["WEAK", "MODERATE", "STRONG"]


@dataclass
class FairValueGap:
    """Represents a detected Fair Value Gap."""

    index: int
    timestamp: datetime
    fvg_type: FVGType

    top: float
    bottom: float
    midpoint: float

    gap_size: float
    gap_percentage: float

    first_candle_timestamp: datetime
    third_candle_timestamp: datetime

    displacement_percentage: float
    strength: FVGStrength

    mitigation_status: MitigationStatus = "UNTOUCHED"
    mitigation_percentage: float = 0.0


def detect_fair_value_gaps(
    candles: list[Candle],
    min_gap_percentage: float = 0.0,
    min_displacement_percentage: float = 0.0,
) -> list[FairValueGap]:
    """
    Detect three-candle Fair Value Gaps.

    Bullish:
        Third candle low > First candle high.

    Bearish:
        Third candle high < First candle low.

    Displacement is measured using the middle candle body
    relative to its opening price.

    Mitigation is calculated using candles after formation.
    """

    if min_gap_percentage < 0:
        raise ValueError("Minimum gap percentage cannot be negative.")

    if min_displacement_percentage < 0:
        raise ValueError(
            "Minimum displacement percentage cannot be negative."
        )

    if not candles:
        return []

    for previous, current in zip(candles, candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError("Candles must be chronological.")

    gaps = []

    for i in range(2, len(candles)):

        first = candles[i - 2]
        middle = candles[i - 1]
        third = candles[i]

        # Calculate middle candle displacement.
        displacement_percentage = (
            abs(middle.close - middle.open)
            / middle.open
        ) * 100

        if displacement_percentage < min_displacement_percentage:
            continue

        # Bullish FVG
        if third.low > first.high:

            top = third.low
            bottom = first.high

            gap_size = top - bottom
            midpoint = (top + bottom) / 2

            gap_percentage = (gap_size / bottom) * 100

            if gap_percentage < min_gap_percentage:
                continue

            if middle.close > middle.open:
                strength: FVGStrength = (
                    "STRONG"
                    if displacement_percentage >= 1.0
                    else "MODERATE"
                )
            else:
                strength = "WEAK"

            gaps.append(
                FairValueGap(
                    index=i,
                    timestamp=third.timestamp,
                    fvg_type="BULLISH",
                    top=top,
                    bottom=bottom,
                    midpoint=midpoint,
                    gap_size=gap_size,
                    gap_percentage=gap_percentage,
                    first_candle_timestamp=first.timestamp,
                    third_candle_timestamp=third.timestamp,
                    displacement_percentage=displacement_percentage,
                    strength=strength,
                )
            )

        # Bearish FVG
        elif third.high < first.low:

            top = first.low
            bottom = third.high

            gap_size = top - bottom
            midpoint = (top + bottom) / 2

            gap_percentage = (gap_size / top) * 100

            if gap_percentage < min_gap_percentage:
                continue

            if middle.close < middle.open:
                strength = (
                    "STRONG"
                    if displacement_percentage >= 1.0
                    else "MODERATE"
                )
            else:
                strength = "WEAK"

            gaps.append(
                FairValueGap(
                    index=i,
                    timestamp=third.timestamp,
                    fvg_type="BEARISH",
                    top=top,
                    bottom=bottom,
                    midpoint=midpoint,
                    gap_size=gap_size,
                    gap_percentage=gap_percentage,
                    first_candle_timestamp=first.timestamp,
                    third_candle_timestamp=third.timestamp,
                    displacement_percentage=displacement_percentage,
                    strength=strength,
                )
            )

    # Track mitigation after each FVG forms.
    for gap in gaps:

        for candle in candles[gap.index + 1:]:

            if gap.fvg_type == "BULLISH":

                if candle.low <= gap.bottom:
                    gap.mitigation_percentage = 100.0
                    gap.mitigation_status = "FULLY_MITIGATED"
                    break

                elif candle.low < gap.top:

                    penetration = gap.top - candle.low

                    percentage = (
                        penetration / gap.gap_size
                    ) * 100

                    gap.mitigation_percentage = max(
                        gap.mitigation_percentage,
                        min(100.0, percentage),
                    )

            else:

                if candle.high >= gap.top:
                    gap.mitigation_percentage = 100.0
                    gap.mitigation_status = "FULLY_MITIGATED"
                    break

                elif candle.high > gap.bottom:

                    penetration = candle.high - gap.bottom

                    percentage = (
                        penetration / gap.gap_size
                    ) * 100

                    gap.mitigation_percentage = max(
                        gap.mitigation_percentage,
                        min(100.0, percentage),
                    )

            if gap.mitigation_percentage > 0:
                gap.mitigation_status = "PARTIALLY_MITIGATED"

    return gaps