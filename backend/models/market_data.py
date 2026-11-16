from datetime import datetime
from pydantic import BaseModel, Field, model_validator, ConfigDict


class Candle(BaseModel):
    """Represents one OHLCV market candle."""

    model_config = ConfigDict(allow_inf_nan=False)

    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_prices(self):
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("High must be the highest price.")

        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Low must be the lowest price.")

        return self


class CandleBatch(BaseModel):
    """Represents multiple candles for a trading instrument."""

    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    candles: list[Candle] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timestamps(self):
        timestamps = [candle.timestamp for candle in self.candles]

        for previous, current in zip(timestamps, timestamps[1:]):
            if current <= previous:
                raise ValueError(
                    "Candles must be in chronological order "
                    "with unique timestamps."
                )

        return self