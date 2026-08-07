from __future__ import annotations

import pandas as pd


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing bar columns: {sorted(missing)}")
    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True)
    result = result.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    if result.empty:
        raise ValueError("no bars")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("non-positive prices")
    if (result["high"] < result[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("invalid bar high")
    if (result["low"] > result[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("invalid bar low")
    return result


def session_vwap(frame: pd.DataFrame) -> pd.Series:
    frame = validate_bars(frame)
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    cumulative_volume = frame["volume"].cumsum()
    if (cumulative_volume == 0).any():
        raise ValueError("cannot calculate VWAP with zero cumulative volume")
    return (typical * frame["volume"]).cumsum() / cumulative_volume


def ema(series: pd.Series, span: int) -> pd.Series:
    if span <= 0:
        raise ValueError("span must be positive")
    return series.ewm(span=span, adjust=False).mean()


def opening_range(frame: pd.DataFrame, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> tuple[float, float]:
    frame = validate_bars(frame)
    mask = (frame["timestamp"] >= start_utc) & (frame["timestamp"] < end_utc)
    subset = frame.loc[mask]
    if subset.empty:
        raise ValueError("opening-range bars are missing")
    return float(subset["high"].max()), float(subset["low"].min())
