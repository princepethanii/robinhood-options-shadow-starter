from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .adapter import AdapterError, normalize_bars, normalize_option_chain
from .events import (
    BLACKOUT_AFTER,
    BLACKOUT_BEFORE,
    DEFAULT_EVENT_CONFIG_PATH,
    EventConfigError,
    EventGateResult,
    HighImpactEvent,
    evaluate_event_gate,
    load_high_impact_events,
)
from .indicators import opening_range
from .models import Bar, OptionQuote
from .strategy import (
    Decision,
    StrategyConfig,
    UnderlyingSignal,
    evaluate_underlying,
    load_strategy_config,
    select_option,
)

_CHICAGO = ZoneInfo("America/Chicago")
_SYMBOLS = ("SPY", "QQQ", "IWM")


class _ConfirmationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spy: str
    qqq: str
    iwm: str


class _ContractPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    option_type: Literal["call", "put"]
    strike: float = Field(allow_inf_nan=False)
    expiration: str
    dte: int
    bid: float = Field(allow_inf_nan=False)
    ask: float = Field(allow_inf_nan=False)
    mark: float = Field(allow_inf_nan=False)
    delta: float = Field(allow_inf_nan=False)
    volume: int
    open_interest: int
    proposed_limit: float = Field(allow_inf_nan=False)
    quantity: int
    total_debit_usd: float = Field(allow_inf_nan=False)
    max_premium_loss_usd: float = Field(allow_inf_nan=False)
    break_even: float = Field(allow_inf_nan=False)


class _SignalPayload(BaseModel):
    """Exact runtime representation of ``schemas/signal.schema.json``."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    timestamp_ct: str
    data_fresh: bool
    underlying: str
    underlying_price: float | None = Field(allow_inf_nan=False)
    opening_range_high: float | None = Field(allow_inf_nan=False)
    opening_range_low: float | None = Field(allow_inf_nan=False)
    vwap: float | None = Field(allow_inf_nan=False)
    confirmation: _ConfirmationPayload
    contract: _ContractPayload | None
    hypothetical_stop_price: float | None = Field(allow_inf_nan=False)
    hypothetical_target_price: float | None = Field(allow_inf_nan=False)
    reasons: list[str]
    rejections: list[str]


def evaluate_snapshot(
    historicals_payload: Mapping[str, Any],
    instruments_payload: Mapping[str, Any] | None = None,
    quotes_payload: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
    config: StrategyConfig | None = None,
    event_config_path: str | Path = DEFAULT_EVENT_CONFIG_PATH,
    snapshot_root: str | Path | None = Path("data"),
) -> dict[str, Any]:
    """Normalize read-only MCP data, run the evaluator, and save a safe snapshot.

    Raw MCP envelopes are never written. A malformed, missing, stale, or
    inconsistent required market field produces one schema-shaped ``NO_TRADE``.
    """

    observed_at = _aware_utc(now)
    normalized_bars: dict[str, list[Bar]] = {}
    normalized_quotes: list[OptionQuote] = []
    event_gate_audit: str | None = None

    def finish(signal: _SignalPayload) -> dict[str, Any]:
        if event_gate_audit is not None:
            signal = signal.model_copy(
                update={"reasons": [event_gate_audit, *signal.reasons]}
            )
        payload = signal.model_dump(mode="json")
        if snapshot_root is not None:
            save_sanitized_snapshot(
                normalized_bars,
                normalized_quotes,
                payload,
                captured_at=observed_at,
                root=snapshot_root,
            )
        return payload

    try:
        high_impact_events = load_high_impact_events(event_config_path)
        event_gate = evaluate_event_gate(high_impact_events, observed_at)
    except EventConfigError as exc:
        event_gate_audit = "high-impact event gate: CONFIG_ERROR"
        return finish(
            _no_trade(
                observed_at,
                [f"high-impact event data invalid: {issue}" for issue in exc.issues],
            )
        )

    if event_gate.blocked:
        event_gate_audit = _event_gate_audit(event_gate, "BLOCKED")
        return finish(
            _no_trade(
                observed_at,
                [_event_blackout_rejection(event) for event in event_gate.blocked_events],
            )
        )
    event_gate_audit = _event_gate_audit(event_gate, "CLEAR")
    strategy_config = config or load_strategy_config()

    try:
        normalized_bars = normalize_bars(
            historicals_payload,
            now=observed_at,
            expected_symbols=_SYMBOLS,
        )
        _validate_bar_sets(normalized_bars, observed_at, strategy_config)
        frames = {symbol: _bars_to_frame(normalized_bars[symbol]) for symbol in _SYMBOLS}
        range_high, range_low = _opening_range_for_session(
            frames["SPY"], observed_at, strategy_config
        )
        underlying_signal = evaluate_underlying(
            frames["SPY"],
            frames["QQQ"],
            frames["IWM"],
            range_high,
            range_low,
            strategy_config,
        )
    except (AdapterError, ValueError) as exc:
        return finish(_no_trade(observed_at, f"required market data rejected: {exc}"))

    confirmation = _confirmation(normalized_bars)
    bar_timestamp_reason = _bar_timestamp_reason(normalized_bars)
    local_clock = observed_at.astimezone(_CHICAGO).time().replace(tzinfo=None)
    if not strategy_config.earliest_entry <= local_clock <= strategy_config.latest_entry:
        return finish(
            _signal_payload(
                observed_at,
                decision="NO_TRADE",
                data_fresh=True,
                underlying_signal=underlying_signal,
                confirmation=confirmation,
                reasons=[*underlying_signal.reasons, bar_timestamp_reason],
                rejections=[
                    *underlying_signal.rejections,
                    (
                        "outside the configured "
                        f"{strategy_config.earliest_entry.strftime('%H:%M')}-"
                        f"{strategy_config.latest_entry.strftime('%H:%M')} "
                        "America/Chicago candidate window"
                    ),
                ],
            )
        )

    if underlying_signal.decision == "NO_TRADE":
        return finish(
            _signal_payload(
                observed_at,
                decision="NO_TRADE",
                data_fresh=True,
                underlying_signal=underlying_signal,
                confirmation=confirmation,
                reasons=[*underlying_signal.reasons, bar_timestamp_reason],
                rejections=list(underlying_signal.rejections),
            )
        )

    if instruments_payload is None or quotes_payload is None:
        missing = []
        if instruments_payload is None:
            missing.append("option instruments response")
        if quotes_payload is None:
            missing.append("option quotes response")
        return finish(
            _signal_payload(
                observed_at,
                decision="NO_TRADE",
                data_fresh=False,
                underlying_signal=underlying_signal,
                confirmation=confirmation,
                reasons=[*underlying_signal.reasons, bar_timestamp_reason],
                rejections=[f"required option data missing: {', '.join(missing)}"],
            )
        )

    try:
        normalized_quotes = normalize_option_chain(
            instruments_payload,
            quotes_payload,
            now=observed_at,
            expected_symbol="SPY",
            allowed_dte=strategy_config.allowed_dte,
            max_quote_age_seconds=strategy_config.max_quote_age_seconds,
        )
    except AdapterError as exc:
        return finish(
            _signal_payload(
                observed_at,
                decision="NO_TRADE",
                data_fresh=False,
                underlying_signal=underlying_signal,
                confirmation=confirmation,
                reasons=[*underlying_signal.reasons, bar_timestamp_reason],
                rejections=[f"required option data rejected: {exc}"],
            )
        )

    direction: Literal["call", "put"] = (
        "call" if underlying_signal.decision == "CALL_CANDIDATE" else "put"
    )
    selected, option_rejections = select_option(
        normalized_quotes,
        direction,
        now=observed_at,
        config=strategy_config,
    )
    if selected is None:
        precise_rejections = option_rejections or [
            "no active, tradable SPY option quotes matched the configured 0-1 DTE scope"
        ]
        return finish(
            _signal_payload(
                observed_at,
                decision="NO_TRADE",
                data_fresh=True,
                underlying_signal=underlying_signal,
                confirmation=confirmation,
                reasons=[*underlying_signal.reasons, bar_timestamp_reason],
                rejections=[*precise_rejections, "no option contract passed all filters"],
            )
        )

    proposed_limit = round(
        min(
            selected.ask,
            selected.mark + strategy_config.limit_price_improvement_over_mid,
        ),
        4,
    )
    quantity = 1
    total_debit = round(
        proposed_limit * selected.trade_value_multiplier * quantity,
        2,
    )
    debit_cap = min(strategy_config.max_total_debit_usd, 50.0)
    if total_debit > debit_cap:
        return finish(
            _signal_payload(
                observed_at,
                decision="NO_TRADE",
                data_fresh=True,
                underlying_signal=underlying_signal,
                confirmation=confirmation,
                reasons=[*underlying_signal.reasons, bar_timestamp_reason],
                rejections=[
                    *option_rejections,
                    (
                        f"total debit {total_debit:.2f} USD exceeds the "
                        f"{debit_cap:.2f} USD cap"
                    ),
                ],
            )
        )

    break_even = round(
        selected.strike + proposed_limit
        if selected.option_type == "call"
        else selected.strike - proposed_limit,
        4,
    )
    stop_price = round(
        proposed_limit * (1 - strategy_config.hypothetical_stop_pct), 4
    )
    target_price = round(
        proposed_limit * (1 + strategy_config.hypothetical_target_pct), 4
    )
    contract = _ContractPayload(
        symbol=_contract_label(selected),
        option_type=selected.option_type,
        strike=selected.strike,
        expiration=selected.expiration.isoformat(),
        dte=selected.dte,
        bid=selected.bid,
        ask=selected.ask,
        mark=selected.mark,
        delta=selected.delta,
        volume=selected.volume,
        open_interest=selected.open_interest,
        proposed_limit=proposed_limit,
        quantity=quantity,
        total_debit_usd=total_debit,
        max_premium_loss_usd=total_debit,
        break_even=break_even,
    )
    invalidation = (
        f"invalidation: SPY closes at or below {underlying_signal.opening_range_high:.4f}"
        if selected.option_type == "call"
        else f"invalidation: SPY closes at or above {underlying_signal.opening_range_low:.4f}"
    )
    evidence = (
        f"underlying evidence: SPY {underlying_signal.price:.4f}, VWAP "
        f"{underlying_signal.vwap:.4f}, opening range "
        f"{underlying_signal.opening_range_low:.4f}-"
        f"{underlying_signal.opening_range_high:.4f}"
    )
    liquidity = (
        f"option liquidity: bid {selected.bid:.4f}, ask {selected.ask:.4f}, "
        f"volume {selected.volume}, open interest {selected.open_interest}"
    )
    option_timestamp = f"option quote timestamp: {selected.timestamp.isoformat()}"
    return finish(
        _signal_payload(
            observed_at,
            decision=underlying_signal.decision,
            data_fresh=True,
            underlying_signal=underlying_signal,
            confirmation=confirmation,
            contract=contract,
            stop_price=stop_price,
            target_price=target_price,
            reasons=[
                *underlying_signal.reasons,
                bar_timestamp_reason,
                option_timestamp,
                evidence,
                liquidity,
                invalidation,
            ],
            rejections=option_rejections,
        )
    )


def save_sanitized_snapshot(
    bars: Mapping[str, list[Bar]],
    option_quotes: list[OptionQuote],
    signal: Mapping[str, Any],
    *,
    captured_at: datetime,
    root: str | Path = Path("data"),
) -> Path:
    """Atomically save only normalized, allowlisted market and signal fields."""

    observed_at = _aware_utc(captured_at)
    unexpected_symbols = set(bars) - set(_SYMBOLS)
    if unexpected_symbols:
        raise ValueError(
            "snapshot bars contain unsupported symbols: "
            + ", ".join(sorted(unexpected_symbols))
        )
    safe_signal = _SignalPayload.model_validate(signal).model_dump(mode="json")
    local = observed_at.astimezone(_CHICAGO)
    directory = Path(root) / local.date().isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    base_name = f"{local.strftime('%H%M%S%f')}-snapshot"
    target = directory / f"{base_name}.json"
    counter = 1
    while target.exists():
        target = directory / f"{base_name}-{counter}.json"
        counter += 1

    payload = {
        "snapshot_version": 1,
        "captured_at": observed_at.isoformat(),
        "bars": {
            symbol: [
                {
                    "timestamp": bar.timestamp.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in symbol_bars
            ]
            for symbol, symbol_bars in bars.items()
        },
        "option_quotes": [
            {
                "symbol": quote.symbol,
                "option_type": quote.option_type,
                "strike": quote.strike,
                "expiration": quote.expiration.isoformat(),
                "dte": quote.dte,
                "timestamp": quote.timestamp.isoformat(),
                "bid": quote.bid,
                "ask": quote.ask,
                "mark": quote.mark,
                "delta": quote.delta,
                "volume": quote.volume,
                "open_interest": quote.open_interest,
                "trade_value_multiplier": quote.trade_value_multiplier,
            }
            for quote in option_quotes
        ],
        "signal": safe_signal,
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".snapshot-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            temporary_path = Path(handle.name)
        temporary_path.replace(target)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return target


def _validate_bar_sets(
    bars: Mapping[str, list[Bar]],
    observed_at: datetime,
    config: StrategyConfig,
) -> None:
    expected_step = timedelta(minutes=config.bar_interval_minutes)
    sequences: dict[str, tuple[datetime, ...]] = {}
    local_date = observed_at.astimezone(_CHICAGO).date()
    opening_start = datetime.combine(local_date, config.opening_range_start, _CHICAGO)
    opening_end = datetime.combine(local_date, config.opening_range_end, _CHICAGO)
    required_opening_timestamps: set[datetime] = set()
    cursor = opening_start.astimezone(UTC)
    while cursor < opening_end.astimezone(UTC):
        required_opening_timestamps.add(cursor)
        cursor += expected_step

    for symbol in _SYMBOLS:
        rows = bars.get(symbol)
        if not rows:
            raise AdapterError(f"{symbol}: no normalized bars")
        timestamps: list[datetime] = []
        for index, bar in enumerate(rows):
            if bar.timestamp.astimezone(_CHICAGO).date() != local_date:
                raise AdapterError(
                    f"{symbol} bar[{index}] is outside the current Chicago session date"
                )
            timestamp = bar.timestamp.astimezone(UTC)
            if timestamp + expected_step > observed_at:
                raise AdapterError(
                    f"{symbol} bar[{index}] is incomplete at the evaluation timestamp"
                )
            if index and bar.timestamp - rows[index - 1].timestamp != expected_step:
                raise AdapterError(
                    f"{symbol}: bar cadence is not {config.bar_interval_minutes} minutes"
                )
            timestamps.append(timestamp)
        missing_opening = required_opening_timestamps - set(timestamps)
        if missing_opening:
            missing = ", ".join(sorted(stamp.isoformat() for stamp in missing_opening))
            raise AdapterError(f"{symbol}: opening-range bars are missing: {missing}")
        sequences[symbol] = tuple(timestamps)

    expected_first = opening_start.astimezone(UTC)
    if any(sequence[0] != expected_first for sequence in sequences.values()):
        raise AdapterError(
            "SPY, QQQ, and IWM bar sequences are not aligned to the 08:30 Chicago open"
        )
    if len(set(sequences.values())) != 1:
        raise AdapterError("SPY, QQQ, and IWM bar timestamp sequences are not aligned")
    latest_end = next(iter(sequences.values()))[-1] + expected_step
    age_seconds = (observed_at - latest_end).total_seconds()
    if age_seconds > config.max_quote_age_seconds:
        raise AdapterError(
            f"underlying bars are stale: completed-bar age {age_seconds:.3f}s exceeds "
            f"{config.max_quote_age_seconds}s"
        )


def _opening_range_for_session(
    spy: pd.DataFrame,
    observed_at: datetime,
    config: StrategyConfig,
) -> tuple[float, float]:
    local_date = observed_at.astimezone(_CHICAGO).date()
    start = datetime.combine(local_date, config.opening_range_start, _CHICAGO)
    end = datetime.combine(local_date, config.opening_range_end, _CHICAGO)
    return opening_range(
        spy,
        pd.Timestamp(start.astimezone(UTC)),
        pd.Timestamp(end.astimezone(UTC)),
    )


def _bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
    )


def _confirmation(bars: Mapping[str, list[Bar]]) -> _ConfirmationPayload:
    def summary(symbol: str) -> str:
        latest = bars[symbol][-1]
        return (
            f"{len(bars[symbol])} normalized bars; latest close {latest.close:.4f} "
            f"at {latest.timestamp.isoformat()}"
        )

    return _ConfirmationPayload(spy=summary("SPY"), qqq=summary("QQQ"), iwm=summary("IWM"))


def _bar_timestamp_reason(bars: Mapping[str, list[Bar]]) -> str:
    values = ", ".join(
        f"{symbol}={bars[symbol][-1].timestamp.isoformat()}" for symbol in _SYMBOLS
    )
    return f"underlying data timestamps: {values}"


def _contract_label(quote: OptionQuote) -> str:
    side = "C" if quote.option_type == "call" else "P"
    return f"{quote.symbol} {quote.expiration.isoformat()} {quote.strike:g}{side}"


def _no_trade(observed_at: datetime, rejections: str | list[str]) -> _SignalPayload:
    rejection_list = [rejections] if isinstance(rejections, str) else list(rejections)
    return _SignalPayload(
        decision="NO_TRADE",
        timestamp_ct=observed_at.astimezone(_CHICAGO).isoformat(timespec="seconds"),
        data_fresh=False,
        underlying="SPY",
        underlying_price=None,
        opening_range_high=None,
        opening_range_low=None,
        vwap=None,
        confirmation=_ConfirmationPayload(
            spy="unavailable", qqq="unavailable", iwm="unavailable"
        ),
        contract=None,
        hypothetical_stop_price=None,
        hypothetical_target_price=None,
        reasons=[],
        rejections=rejection_list,
    )


def _event_gate_audit(event_gate: EventGateResult, status: str) -> str:
    return (
        f"high-impact event gate: {status} at "
        f"{event_gate.evaluated_at_ct.isoformat(timespec='seconds')}; "
        f"validated {event_gate.total_events} curated event(s); blackout is "
        f"{int(BLACKOUT_BEFORE.total_seconds() // 60)} minutes before through "
        f"{int(BLACKOUT_AFTER.total_seconds() // 60)} minutes after, inclusive"
    )


def _event_blackout_rejection(event: HighImpactEvent) -> str:
    return (
        f"high-impact event entry blackout: {event.event_name!r}; "
        f"scheduled {event.scheduled_at_ct.isoformat(timespec='seconds')} "
        "America/Chicago; "
        f"source {event.source!r}; risk level {event.risk_level!r}; no new entries "
        f"from {event.blackout_start_ct.isoformat(timespec='seconds')} through "
        f"{event.blackout_end_ct.isoformat(timespec='seconds')} "
        "America/Chicago (inclusive)"
    )


def _signal_payload(
    observed_at: datetime,
    *,
    decision: Decision,
    data_fresh: bool,
    underlying_signal: UnderlyingSignal,
    confirmation: _ConfirmationPayload,
    reasons: list[str],
    rejections: list[str],
    contract: _ContractPayload | None = None,
    stop_price: float | None = None,
    target_price: float | None = None,
) -> _SignalPayload:
    return _SignalPayload(
        decision=decision,
        timestamp_ct=observed_at.astimezone(_CHICAGO).isoformat(timespec="seconds"),
        data_fresh=data_fresh,
        underlying="SPY",
        underlying_price=underlying_signal.price,
        opening_range_high=underlying_signal.opening_range_high,
        opening_range_low=underlying_signal.opening_range_low,
        vwap=underlying_signal.vwap,
        confirmation=confirmation,
        contract=contract,
        hypothetical_stop_price=stop_price,
        hypothetical_target_price=target_price,
        reasons=reasons,
        rejections=rejections,
    )


def _aware_utc(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    result = result.astimezone(UTC)
    if not math.isfinite(result.timestamp()):
        raise ValueError("now must be finite")
    return result
