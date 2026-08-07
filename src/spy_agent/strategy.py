from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml

from .indicators import ema, session_vwap, validate_bars
from .models import OptionQuote

Decision = Literal["NO_TRADE", "CALL_CANDIDATE", "PUT_CANDIDATE"]


@dataclass(frozen=True)
class StrategyConfig:
    timezone_name: str = "America/Chicago"
    opening_range_start: time = time(8, 30)
    opening_range_end: time = time(8, 45)
    earliest_entry: time = time(8, 50)
    latest_entry: time = time(10, 30)
    forced_exit: time = time(14, 0)
    breakout_buffer_pct: float = 0.0005
    max_breakout_extension_pct: float = 0.0025
    min_volume_multiple: float = 1.20
    require_spy_vwap_confirmation: bool = True
    require_qqq_vwap_confirmation: bool = True
    require_retest: bool = True
    retest_window_minutes: int = 15
    bar_interval_minutes: int = 5
    min_abs_delta: float = 0.25
    max_abs_delta: float = 0.45
    max_premium: float = 0.50
    require_positive_bid: bool = True
    max_spread_dollars: float = 0.05
    max_spread_pct: float = 0.15
    min_volume: int = 100
    min_open_interest: int = 500
    max_quote_age_seconds: int = 10
    allowed_dte: tuple[int, ...] = (0, 1)
    prefer_dte: int = 1
    max_total_debit_usd: float = 50.0
    max_contracts: int = 1
    max_entries_per_day: int = 1
    hypothetical_stop_pct: float = 0.40
    hypothetical_target_pct: float = 0.80
    limit_price_improvement_over_mid: float = 0.01
    entry_assumption: str = "min(ask, mid + 0.01)"
    exit_assumption: str = "max(bid, mid - 0.01)"
    unfilled_after_seconds: int = 30
    chase_limit_dollars: float = 0.02

    def __post_init__(self) -> None:
        if isinstance(self.allowed_dte, (str, bytes)):
            raise TypeError("allowed_dte must contain integers")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in self.allowed_dte):
            raise TypeError("allowed_dte must contain integers")
        allowed_dte = tuple(dict.fromkeys(self.allowed_dte))
        object.__setattr__(self, "allowed_dte", allowed_dte)
        if not allowed_dte or any(value not in {0, 1} for value in allowed_dte):
            raise ValueError("allowed_dte must be a non-empty subset of (0, 1)")
        if self.prefer_dte not in allowed_dte:
            raise ValueError("prefer_dte must be present in allowed_dte")
        if self.timezone_name != "America/Chicago":
            raise ValueError("strategy timezone must be America/Chicago")
        fixed_session = (
            time(8, 30),
            time(8, 45),
            time(8, 50),
            time(10, 30),
            time(14, 0),
        )
        configured_session = (
            self.opening_range_start,
            self.opening_range_end,
            self.earliest_entry,
            self.latest_entry,
            self.forced_exit,
        )
        if configured_session != fixed_session:
            raise ValueError("strategy session times must match the fixed Chicago schedule")
        if not (
            self.opening_range_start
            < self.opening_range_end
            <= self.earliest_entry
            <= self.latest_entry
            < self.forced_exit
        ):
            raise ValueError("strategy session times are inconsistent")

        numeric_fields = (
            "breakout_buffer_pct",
            "max_breakout_extension_pct",
            "min_volume_multiple",
            "min_abs_delta",
            "max_abs_delta",
            "max_premium",
            "max_spread_dollars",
            "max_spread_pct",
            "max_quote_age_seconds",
            "max_total_debit_usd",
            "hypothetical_stop_pct",
            "hypothetical_target_pct",
            "limit_price_improvement_over_mid",
            "chase_limit_dollars",
        )
        for name in numeric_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        integer_fields = (
            "bar_interval_minutes",
            "retest_window_minutes",
            "min_volume",
            "min_open_interest",
            "prefer_dte",
            "max_contracts",
            "max_entries_per_day",
            "unfilled_after_seconds",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        boolean_fields = (
            "require_spy_vwap_confirmation",
            "require_qqq_vwap_confirmation",
            "require_retest",
            "require_positive_bid",
        )
        for name in boolean_fields:
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.bar_interval_minutes <= 0 or self.retest_window_minutes <= 0:
            raise ValueError("bar and retest intervals must be positive")
        if self.bar_interval_minutes != 5:
            raise ValueError("deterministic strategy requires 5-minute bars")
        if self.max_contracts != 1 or not 0 < self.max_total_debit_usd <= 50:
            raise ValueError("shadow risk is capped at one contract and 50 USD total debit")
        if self.max_entries_per_day != 1:
            raise ValueError("shadow strategy requires exactly one maximum entry per day")
        if not (
            self.require_spy_vwap_confirmation
            and self.require_qqq_vwap_confirmation
            and self.require_retest
            and self.require_positive_bid
        ):
            raise ValueError("required confirmations and positive-bid filtering cannot be disabled")
        if not 0 <= self.min_abs_delta <= self.max_abs_delta <= 1:
            raise ValueError("delta bounds must satisfy 0 <= min <= max <= 1")
        if self.min_abs_delta < 0.25 or self.max_abs_delta > 0.45:
            raise ValueError("delta filters cannot be looser than the shadow configuration")
        if not 0 < self.max_premium <= 0.50:
            raise ValueError("maximum option premium must be in (0, 0.50]")
        if self.max_spread_dollars < 0 or self.max_spread_pct < 0:
            raise ValueError("spread limits must be non-negative")
        if self.max_spread_dollars > 0.05 or self.max_spread_pct > 0.15:
            raise ValueError("spread filters cannot be looser than the shadow configuration")
        if self.min_volume < 0 or self.min_open_interest < 0:
            raise ValueError("liquidity minimums must be non-negative")
        if self.min_volume < 100 or self.min_open_interest < 500:
            raise ValueError("liquidity minimums cannot be lower than the shadow configuration")
        if not 0 < self.max_quote_age_seconds <= 10:
            raise ValueError("max_quote_age_seconds must be in (0, 10]")
        if self.breakout_buffer_pct < 0 or self.max_breakout_extension_pct < 0:
            raise ValueError("breakout thresholds must be non-negative")
        if self.breakout_buffer_pct < 0.0005 or self.max_breakout_extension_pct > 0.0025:
            raise ValueError("breakout filters cannot be looser than the shadow configuration")
        if self.min_volume_multiple < 1.20:
            raise ValueError("volume multiple cannot be lower than the shadow configuration")
        if self.retest_window_minutes < 15:
            raise ValueError("retest window cannot be shorter than 15 minutes")
        if not 0 < self.hypothetical_stop_pct <= 0.40:
            raise ValueError("hypothetical stop cannot risk more than 40 percent")
        if self.hypothetical_target_pct < 0.80:
            raise ValueError("hypothetical target cannot be lower than 80 percent")
        if self.limit_price_improvement_over_mid < 0 or self.chase_limit_dollars < 0:
            raise ValueError("execution price increments must be non-negative")
        if self.limit_price_improvement_over_mid > 0.01 or self.chase_limit_dollars > 0.02:
            raise ValueError("execution price increments cannot exceed shadow limits")
        if not 0 < self.unfilled_after_seconds <= 30:
            raise ValueError("unfilled timeout must be in (0, 30] seconds")


def load_strategy_config(path: str | Path | None = None) -> StrategyConfig:
    """Load the deterministic evaluator settings from ``config/strategy.yaml``."""

    config_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[2] / "config" / "strategy.yaml"
    )
    with config_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise TypeError("strategy config must be a mapping")
    if payload.get("mode", "shadow") != "shadow":
        raise ValueError("strategy config must remain in shadow mode")
    if payload.get("timezone", "America/Chicago") != "America/Chicago":
        raise ValueError("strategy timezone must be America/Chicago")

    defaults = StrategyConfig()

    def section(name: str) -> dict[str, Any]:
        value = payload.get(name, {})
        if not isinstance(value, dict):
            raise TypeError(f"strategy config section {name!r} must be a mapping")
        return value

    session = section("session")
    risk = section("risk")
    signal = section("signal")
    option = section("option_selection")
    execution = section("execution_simulation")
    allowed_dte_value = option.get("allowed_dte", defaults.allowed_dte)
    if not isinstance(allowed_dte_value, (list, tuple)):
        raise TypeError("option_selection.allowed_dte must be a list")

    return StrategyConfig(
        timezone_name=str(payload.get("timezone", defaults.timezone_name)),
        opening_range_start=_parse_clock(
            session.get("opening_range_start", defaults.opening_range_start),
            "session.opening_range_start",
        ),
        opening_range_end=_parse_clock(
            session.get("opening_range_end", defaults.opening_range_end),
            "session.opening_range_end",
        ),
        earliest_entry=_parse_clock(
            session.get("earliest_entry", defaults.earliest_entry),
            "session.earliest_entry",
        ),
        latest_entry=_parse_clock(
            session.get("latest_entry", defaults.latest_entry),
            "session.latest_entry",
        ),
        forced_exit=_parse_clock(
            session.get("forced_exit", defaults.forced_exit),
            "session.forced_exit",
        ),
        breakout_buffer_pct=_config_float(
            signal.get("breakout_buffer_pct", defaults.breakout_buffer_pct),
            "signal.breakout_buffer_pct",
        ),
        max_breakout_extension_pct=_config_float(
            signal.get("max_breakout_extension_pct", defaults.max_breakout_extension_pct),
            "signal.max_breakout_extension_pct",
        ),
        min_volume_multiple=_config_float(
            signal.get("volume_multiple_vs_session_median", defaults.min_volume_multiple),
            "signal.volume_multiple_vs_session_median",
        ),
        require_spy_vwap_confirmation=_config_bool(
            signal.get(
                "require_spy_vwap_confirmation", defaults.require_spy_vwap_confirmation
            ),
            "signal.require_spy_vwap_confirmation",
        ),
        require_qqq_vwap_confirmation=_config_bool(
            signal.get(
                "require_qqq_vwap_confirmation", defaults.require_qqq_vwap_confirmation
            ),
            "signal.require_qqq_vwap_confirmation",
        ),
        require_retest=_config_bool(
            signal.get("require_retest", defaults.require_retest),
            "signal.require_retest",
        ),
        retest_window_minutes=_config_int(
            signal.get("retest_window_minutes", defaults.retest_window_minutes),
            "signal.retest_window_minutes",
        ),
        bar_interval_minutes=_config_int(
            signal.get("bar_interval_minutes", defaults.bar_interval_minutes),
            "signal.bar_interval_minutes",
        ),
        min_abs_delta=_config_float(
            option.get("min_abs_delta", defaults.min_abs_delta),
            "option_selection.min_abs_delta",
        ),
        max_abs_delta=_config_float(
            option.get("max_abs_delta", defaults.max_abs_delta),
            "option_selection.max_abs_delta",
        ),
        max_premium=_config_float(
            option.get("max_premium_per_contract", defaults.max_premium),
            "option_selection.max_premium_per_contract",
        ),
        require_positive_bid=_config_bool(
            option.get("require_positive_bid", defaults.require_positive_bid),
            "option_selection.require_positive_bid",
        ),
        max_spread_dollars=_config_float(
            option.get("max_spread_dollars", defaults.max_spread_dollars),
            "option_selection.max_spread_dollars",
        ),
        max_spread_pct=_config_float(
            option.get("max_spread_pct_of_mid", defaults.max_spread_pct),
            "option_selection.max_spread_pct_of_mid",
        ),
        min_volume=_config_int(
            option.get("min_volume", defaults.min_volume),
            "option_selection.min_volume",
        ),
        min_open_interest=_config_int(
            option.get("min_open_interest", defaults.min_open_interest),
            "option_selection.min_open_interest",
        ),
        max_quote_age_seconds=_config_int(
            option.get("max_quote_age_seconds", defaults.max_quote_age_seconds),
            "option_selection.max_quote_age_seconds",
        ),
        allowed_dte=tuple(allowed_dte_value),
        prefer_dte=_config_int(
            option.get("prefer_dte", defaults.prefer_dte),
            "option_selection.prefer_dte",
        ),
        max_total_debit_usd=_config_float(
            risk.get("max_total_debit_usd", defaults.max_total_debit_usd),
            "risk.max_total_debit_usd",
        ),
        max_contracts=_config_int(
            risk.get("max_contracts", defaults.max_contracts),
            "risk.max_contracts",
        ),
        max_entries_per_day=_config_int(
            risk.get("max_entries_per_day", defaults.max_entries_per_day),
            "risk.max_entries_per_day",
        ),
        hypothetical_stop_pct=_config_float(
            risk.get("hypothetical_option_stop_pct", defaults.hypothetical_stop_pct),
            "risk.hypothetical_option_stop_pct",
        ),
        hypothetical_target_pct=_config_float(
            risk.get(
                "hypothetical_profit_target_pct", defaults.hypothetical_target_pct
            ),
            "risk.hypothetical_profit_target_pct",
        ),
        limit_price_improvement_over_mid=_config_float(
            option.get(
                "limit_price_improvement_over_mid",
                defaults.limit_price_improvement_over_mid,
            ),
            "option_selection.limit_price_improvement_over_mid",
        ),
        entry_assumption=str(
            execution.get("entry_assumption", defaults.entry_assumption)
        ),
        exit_assumption=str(execution.get("exit_assumption", defaults.exit_assumption)),
        unfilled_after_seconds=_config_int(
            execution.get("unfilled_after_seconds", defaults.unfilled_after_seconds),
            "execution_simulation.unfilled_after_seconds",
        ),
        chase_limit_dollars=_config_float(
            execution.get("chase_limit_dollars", defaults.chase_limit_dollars),
            "execution_simulation.chase_limit_dollars",
        ),
    )


def _parse_clock(value: object, field: str) -> time:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field} must be HH:MM")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be HH:MM") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ValueError(f"{field} must be a timezone-naive HH:MM value")
    return parsed


def _config_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _config_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _config_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


@dataclass(frozen=True)
class UnderlyingSignal:
    decision: Decision
    reasons: tuple[str, ...]
    rejections: tuple[str, ...]
    price: float
    vwap: float
    opening_range_high: float
    opening_range_low: float


def evaluate_underlying(
    spy: pd.DataFrame,
    qqq: pd.DataFrame,
    iwm: pd.DataFrame,
    opening_range_high: float,
    opening_range_low: float,
    config: StrategyConfig | None = None,
) -> UnderlyingSignal:
    config = config or StrategyConfig()
    spy = validate_bars(spy)
    qqq = validate_bars(qqq)
    iwm = validate_bars(iwm)
    if len(spy) < 5 or len(qqq) < 5 or len(iwm) < 5:
        raise ValueError("insufficient bars")

    spy_vwap = float(session_vwap(spy).iloc[-1])
    qqq_vwap = float(session_vwap(qqq).iloc[-1])
    iwm_vwap = float(session_vwap(iwm).iloc[-1])
    spy_price = float(spy["close"].iloc[-1])
    qqq_price = float(qqq["close"].iloc[-1])
    iwm_price = float(iwm["close"].iloc[-1])
    spy_ema9 = float(ema(spy["close"], 9).iloc[-1])
    spy_ema20 = float(ema(spy["close"], 20).iloc[-1])

    recent_median_volume = float(spy["volume"].iloc[:-1].median())
    volume_multiple = (
        float(spy["volume"].iloc[-1]) / recent_median_volume if recent_median_volume > 0 else 0
    )

    call_level = opening_range_high * (1 + config.breakout_buffer_pct)
    put_level = opening_range_low * (1 - config.breakout_buffer_pct)
    call_extension = (spy_price - opening_range_high) / opening_range_high
    put_extension = (opening_range_low - spy_price) / opening_range_low

    retest_bars = max(1, config.retest_window_minutes // config.bar_interval_minutes)
    recent = spy.tail(retest_bars)
    call_retest = bool(
        (recent["low"] <= opening_range_high * (1 + config.breakout_buffer_pct)).any()
        and spy_price > call_level
        and (recent["close"] >= opening_range_high * (1 - config.breakout_buffer_pct)).all()
    )
    put_retest = bool(
        (recent["high"] >= opening_range_low * (1 - config.breakout_buffer_pct)).any()
        and spy_price < put_level
        and (recent["close"] <= opening_range_low * (1 + config.breakout_buffer_pct)).all()
    )

    call_confirmed = all(
        [
            spy_price > call_level,
            not config.require_spy_vwap_confirmation or spy_price > spy_vwap,
            not config.require_qqq_vwap_confirmation or qqq_price > qqq_vwap,
            spy_ema9 > spy_ema20,
            volume_multiple >= config.min_volume_multiple,
            call_extension <= config.max_breakout_extension_pct,
            not config.require_retest or call_retest,
        ]
    )
    put_confirmed = all(
        [
            spy_price < put_level,
            not config.require_spy_vwap_confirmation or spy_price < spy_vwap,
            not config.require_qqq_vwap_confirmation or qqq_price < qqq_vwap,
            spy_ema9 < spy_ema20,
            volume_multiple >= config.min_volume_multiple,
            put_extension <= config.max_breakout_extension_pct,
            not config.require_retest or put_retest,
        ]
    )

    reasons: list[str] = []
    rejections: list[str] = []
    if call_confirmed:
        reasons.extend(["SPY breakout", "opening-range retest held", "SPY/QQQ above VWAP", "EMA trend", "volume confirmation"])
        if iwm_price <= iwm_vwap:
            reasons.append("IWM did not confirm; treat as weaker breadth")
        return UnderlyingSignal(
            "CALL_CANDIDATE", tuple(reasons), tuple(rejections), spy_price, spy_vwap,
            opening_range_high, opening_range_low
        )
    if put_confirmed:
        reasons.extend(["SPY breakdown", "opening-range retest held", "SPY/QQQ below VWAP", "EMA trend", "volume confirmation"])
        if iwm_price >= iwm_vwap:
            reasons.append("IWM did not confirm; treat as weaker breadth")
        return UnderlyingSignal(
            "PUT_CANDIDATE", tuple(reasons), tuple(rejections), spy_price, spy_vwap,
            opening_range_high, opening_range_low
        )

    rejections.append("deterministic directional confirmation not satisfied")
    if volume_multiple < config.min_volume_multiple:
        rejections.append("insufficient breakout volume")
    if spy_price > call_level and not call_retest:
        rejections.append("bullish opening-range retest not confirmed")
    if spy_price < put_level and not put_retest:
        rejections.append("bearish opening-range retest not confirmed")
    if spy_price > call_level and call_extension > config.max_breakout_extension_pct:
        rejections.append("bullish breakout is already too extended")
    if spy_price < put_level and put_extension > config.max_breakout_extension_pct:
        rejections.append("bearish breakdown is already too extended")
    return UnderlyingSignal(
        "NO_TRADE", tuple(reasons), tuple(rejections), spy_price, spy_vwap,
        opening_range_high, opening_range_low
    )


def select_option(
    quotes: list[OptionQuote],
    direction: Literal["call", "put"],
    now: datetime | None = None,
    config: StrategyConfig | None = None,
) -> tuple[OptionQuote | None, list[str]]:
    config = config or StrategyConfig()
    now = now or datetime.now(UTC)
    rejected: list[str] = []
    candidates: list[OptionQuote] = []
    for quote in quotes:
        reasons: list[str] = []
        if quote.option_type != direction:
            continue
        if quote.dte not in config.allowed_dte:
            reasons.append("DTE outside allowed range")
        if config.require_positive_bid and quote.bid <= 0:
            reasons.append("zero bid")
        if quote.timestamp.tzinfo is None:
            reasons.append("quote timestamp is missing timezone")
        else:
            age = (now - quote.timestamp.astimezone(UTC)).total_seconds()
            if age < 0 or age > config.max_quote_age_seconds:
                reasons.append("stale quote")
        if not config.min_abs_delta <= abs(quote.delta) <= config.max_abs_delta:
            reasons.append("delta outside allowed range")
        if quote.ask > config.max_premium:
            reasons.append("premium exceeds budget")
        if quote.spread > config.max_spread_dollars:
            reasons.append("spread too wide in dollars")
        if quote.spread_pct_of_mid > config.max_spread_pct:
            reasons.append("spread too wide relative to midpoint")
        if quote.volume < config.min_volume:
            reasons.append("insufficient option volume")
        if quote.open_interest < config.min_open_interest:
            reasons.append("insufficient open interest")
        if reasons:
            side = "C" if quote.option_type == "call" else "P"
            contract = (
                f"{quote.symbol} {quote.expiration.isoformat()} {quote.strike:g}{side}"
            )
            rejected.append(f"{contract}: {', '.join(reasons)}")
            continue
        candidates.append(quote)

    if not candidates:
        return None, rejected

    # Prefer the configured DTE, then higher absolute delta, then tighter spread.
    candidates.sort(
        key=lambda q: (
            q.dte != config.prefer_dte,
            -abs(q.delta),
            q.spread_pct_of_mid,
            q.ask,
        )
    )
    return candidates[0], rejected
