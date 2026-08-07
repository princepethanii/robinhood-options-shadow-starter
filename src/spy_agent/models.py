from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Bar(BaseModel):
    timestamp: datetime
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: int = Field(ge=0)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("bar timestamp must include a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_ohlc(self) -> Bar:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high is inconsistent with OHLC")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low is inconsistent with OHLC")
        return self


class OptionQuote(BaseModel):
    symbol: str = Field(min_length=1)
    option_type: Literal["call", "put"]
    strike: float = Field(gt=0, allow_inf_nan=False)
    expiration: date
    dte: int = Field(ge=0)
    timestamp: datetime
    bid: float = Field(ge=0, allow_inf_nan=False)
    ask: float = Field(ge=0, allow_inf_nan=False)
    delta: float = Field(allow_inf_nan=False)
    volume: int = Field(ge=0)
    open_interest: int = Field(ge=0)
    trade_value_multiplier: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("expiration", mode="before")
    @classmethod
    def normalize_expiration(cls, value: object) -> object:
        # Older callers constructed quotes with a datetime. Expiration is a
        # calendar date, so preserve compatibility without inventing a time.
        if isinstance(value, datetime):
            return value.date()
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("option quote timestamp must include a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_quote(self) -> OptionQuote:
        if self.ask < self.bid:
            raise ValueError("crossed option quote")
        if self.option_type == "call" and not 0 <= self.delta <= 1:
            raise ValueError("invalid call delta")
        if self.option_type == "put" and not -1 <= self.delta <= 0:
            raise ValueError("invalid put delta")
        return self

    @property
    def mark(self) -> float:
        return round((self.bid + self.ask) / 2, 4)

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 4)

    @property
    def spread_pct_of_mid(self) -> float:
        return float("inf") if self.mark == 0 else self.spread / self.mark
