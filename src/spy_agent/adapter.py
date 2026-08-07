from __future__ import annotations

import math
import re
from collections.abc import Collection, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .models import Bar, OptionQuote

_CHICAGO = ZoneInfo("America/Chicago")
_DEFAULT_SYMBOLS = ("SPY", "QQQ", "IWM")
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class AdapterError(ValueError):
    """Raised when a Robinhood read-only payload cannot be normalized safely."""


def normalize_bars(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    expected_symbols: Collection[str] = _DEFAULT_SYMBOLS,
    expected_interval: str = "5minute",
    expected_bounds: str = "regular",
) -> dict[str, list[Bar]]:
    """Normalize the documented equity-historicals response for SPY, QQQ, and IWM.

    The input is the complete MCP result. Only ``structuredContent.data`` is
    treated as authoritative; the human-readable content and guide are never
    parsed as data.
    """

    observed_at = _aware_now(now)
    symbols = _expected_symbols(expected_symbols)
    if not expected_interval:
        raise AdapterError("expected_interval: expected a non-empty string")
    if not expected_bounds:
        raise AdapterError("expected_bounds: expected a non-empty string")
    data = _structured_data(payload, context="equity historicals")
    rows = _required_list(data, "results", context="equity historicals data")

    normalized: dict[str, list[Bar]] = {}
    for index, raw_row in enumerate(rows):
        context = f"equity historicals results[{index}]"
        row = _required_mapping(raw_row, context=context)
        symbol = _required_string(row, "symbol", context=context)
        if symbol not in symbols:
            raise AdapterError(f"{context}.symbol: unexpected symbol {symbol!r}")
        if symbol in normalized:
            raise AdapterError(f"{context}.symbol: duplicate historical result for {symbol}")

        # These fields were present in the inspected response shape. Requiring
        # them prevents a changed wrapper from being silently interpreted.
        interval = _required_string(row, "interval", context=context)
        bounds = _required_string(row, "bounds", context=context)
        if interval != expected_interval:
            raise AdapterError(
                f"{context}.interval: expected {expected_interval!r}, got {interval!r}"
            )
        if bounds != expected_bounds:
            raise AdapterError(
                f"{context}.bounds: expected {expected_bounds!r}, got {bounds!r}"
            )
        raw_bars = _required_list(row, "bars", context=context)
        if not raw_bars:
            raise AdapterError(f"{context}.bars: no bars")

        bars: list[Bar] = []
        seen_timestamps: set[datetime] = set()
        for bar_index, raw_bar in enumerate(raw_bars):
            bar_context = f"{context}.bars[{bar_index}]"
            source = _required_mapping(raw_bar, context=bar_context)
            timestamp = _required_rfc3339(source, "begins_at", context=bar_context)
            if timestamp > observed_at:
                raise AdapterError(
                    f"{bar_context}.begins_at: future timestamp "
                    f"{timestamp.isoformat()} exceeds {observed_at.isoformat()}"
                )
            if timestamp in seen_timestamps:
                raise AdapterError(
                    f"{bar_context}.begins_at: duplicate timestamp {timestamp.isoformat()}"
                )
            seen_timestamps.add(timestamp)

            if "interpolated" in source:
                interpolated = source["interpolated"]
                if not isinstance(interpolated, bool):
                    raise AdapterError(f"{bar_context}.interpolated: expected boolean")
                if interpolated:
                    raise AdapterError(f"{bar_context}.interpolated: interpolated bar rejected")
            if "session" in source:
                if not isinstance(source["session"], str):
                    raise AdapterError(f"{bar_context}.session: expected string")
                if expected_bounds == "regular" and source["session"] != "reg":
                    raise AdapterError(
                        f"{bar_context}.session: expected 'reg', got {source['session']!r}"
                    )

            try:
                bars.append(
                    Bar(
                        timestamp=timestamp,
                        open=_required_decimal_string(source, "open_price", context=bar_context),
                        high=_required_decimal_string(source, "high_price", context=bar_context),
                        low=_required_decimal_string(source, "low_price", context=bar_context),
                        close=_required_decimal_string(source, "close_price", context=bar_context),
                        volume=_required_nonnegative_int(source, "volume", context=bar_context),
                    )
                )
            except ValidationError as exc:
                raise AdapterError(f"{bar_context}: invalid OHLCV bar: {_validation_message(exc)}") from exc

        normalized[symbol] = sorted(bars, key=lambda bar: bar.timestamp)

    missing = [symbol for symbol in symbols if symbol not in normalized]
    if missing:
        raise AdapterError(f"equity historicals data: missing results for {', '.join(missing)}")
    return {symbol: normalized[symbol] for symbol in symbols}


def normalize_option_chain(
    instruments_payload: Mapping[str, Any],
    quotes_payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    expected_symbol: str = "SPY",
    allowed_dte: Collection[int] = (0, 1),
    max_quote_age_seconds: float = 10.0,
) -> list[OptionQuote]:
    """Join documented option instruments and executable quotes by instrument ID."""

    observed_at = _aware_now(now)
    if expected_symbol != "SPY":
        raise AdapterError(
            f"option instruments: expected_symbol must be 'SPY', got {expected_symbol!r}"
        )
    dtes = _allowed_dtes(allowed_dte)
    if isinstance(max_quote_age_seconds, bool) or not isinstance(
        max_quote_age_seconds, (int, float)
    ):
        raise AdapterError("max_quote_age_seconds: expected a finite non-negative number")
    max_age = float(max_quote_age_seconds)
    if not math.isfinite(max_age) or max_age < 0:
        raise AdapterError("max_quote_age_seconds: expected a finite non-negative number")

    instrument_data = _structured_data(instruments_payload, context="option instruments")
    raw_instruments = _required_list(
        instrument_data, "instruments", context="option instruments data"
    )
    quote_data = _structured_data(quotes_payload, context="option quotes")
    raw_quote_rows = _required_list(quote_data, "results", context="option quotes data")

    eligible: dict[str, _Instrument] = {}
    seen_instrument_ids: set[str] = set()
    chicago_date = observed_at.astimezone(_CHICAGO).date()
    for index, raw_instrument in enumerate(raw_instruments):
        context = f"option instruments[{index}]"
        source = _required_mapping(raw_instrument, context=context)
        instrument_id = _required_string(source, "id", context=context)
        if instrument_id in seen_instrument_ids:
            raise AdapterError(f"{context}.id: duplicate instrument ID {instrument_id!r}")
        seen_instrument_ids.add(instrument_id)

        chain_symbol = _required_string(source, "chain_symbol", context=context)
        expiration = _required_date(source, "expiration_date", context=context)
        strike = _required_decimal_string(source, "strike_price", context=context)
        option_type = _required_string(source, "type", context=context)
        state = _required_string(source, "state", context=context)
        tradability = _required_string(source, "tradability", context=context)
        multiplier = _required_decimal_string(
            source, "trade_value_multiplier", context=context
        )

        if chain_symbol != expected_symbol:
            continue
        if option_type not in {"call", "put"}:
            raise AdapterError(f"{context}.type: unsupported option type {option_type!r}")
        normalized_type: Literal["call", "put"] = (
            "call" if option_type == "call" else "put"
        )
        if strike <= 0:
            raise AdapterError(f"{context}.strike_price: expected a positive number")
        if multiplier <= 0:
            raise AdapterError(
                f"{context}.trade_value_multiplier: expected a positive number"
            )
        dte = (expiration - chicago_date).days
        if state != "active" or tradability != "tradable":
            continue
        if dte not in dtes:
            raise AdapterError(
                f"{context}.expiration_date: DTE {dte} is outside allowed DTE "
                f"values {sorted(dtes)}"
            )
        eligible[instrument_id] = _Instrument(
            instrument_id=instrument_id,
            chain_symbol=chain_symbol,
            expiration=expiration,
            strike=strike,
            option_type=normalized_type,
            dte=dte,
            multiplier=multiplier,
        )

    quote_by_instrument: dict[str, Mapping[str, Any]] = {}
    for index, raw_quote_row in enumerate(raw_quote_rows):
        context = f"option quotes results[{index}]"
        quote_row = _required_mapping(raw_quote_row, context=context)
        raw_quote_fields = _required_mapping_key(quote_row, "quote", context=context)
        # An optional `close` value is deliberately ignored: a last/close
        # value is never an executable quote substitute.
        instrument_id = _required_string(
            raw_quote_fields, "instrument_id", context=f"{context}.quote"
        )
        if instrument_id in quote_by_instrument:
            raise AdapterError(
                f"{context}.quote.instrument_id: duplicate quote for {instrument_id!r}"
            )
        quote_by_instrument[instrument_id] = raw_quote_fields

    normalized: list[OptionQuote] = []
    for instrument_id, instrument in eligible.items():
        quote_fields = quote_by_instrument.get(instrument_id)
        if quote_fields is None:
            raise AdapterError(
                f"option quotes data: missing quote for eligible instrument {instrument_id!r}"
            )
        context = f"option quote {instrument_id!r}"
        timestamp = _required_rfc3339(quote_fields, "updated_at", context=context)
        age_seconds = (observed_at - timestamp).total_seconds()
        if age_seconds < 0:
            raise AdapterError(
                f"{context}.updated_at: future quote timestamp {timestamp.isoformat()}"
            )
        if age_seconds > max_age:
            raise AdapterError(
                f"{context}.updated_at: stale quote age {age_seconds:.6f}s exceeds "
                f"{max_age:.6f}s"
            )

        bid = _required_decimal_string(quote_fields, "bid_price", context=context)
        ask = _required_decimal_string(quote_fields, "ask_price", context=context)
        delta = _required_decimal_string(quote_fields, "delta", context=context)
        volume = _required_nonnegative_int(quote_fields, "volume", context=context)
        open_interest = _required_nonnegative_int(
            quote_fields, "open_interest", context=context
        )
        if bid < 0:
            raise AdapterError(f"{context}.bid_price: expected a non-negative number")
        if ask < 0:
            raise AdapterError(f"{context}.ask_price: expected a non-negative number")
        if ask < bid:
            raise AdapterError(
                f"{context}: crossed quote (ask {ask:.4f} below bid {bid:.4f})"
            )

        try:
            normalized.append(
                OptionQuote(
                    symbol=instrument.chain_symbol,
                    option_type=instrument.option_type,
                    strike=instrument.strike,
                    expiration=instrument.expiration,
                    dte=instrument.dte,
                    timestamp=timestamp,
                    bid=bid,
                    ask=ask,
                    delta=delta,
                    volume=volume,
                    open_interest=open_interest,
                    trade_value_multiplier=instrument.multiplier,
                )
            )
        except ValidationError as exc:
            raise AdapterError(f"{context}: invalid option quote: {_validation_message(exc)}") from exc

    return normalized


class _Instrument:
    __slots__ = (
        "chain_symbol",
        "dte",
        "expiration",
        "instrument_id",
        "multiplier",
        "option_type",
        "strike",
    )

    def __init__(
        self,
        *,
        instrument_id: str,
        chain_symbol: str,
        expiration: date,
        strike: float,
        option_type: Literal["call", "put"],
        dte: int,
        multiplier: float,
    ) -> None:
        self.instrument_id = instrument_id
        self.chain_symbol = chain_symbol
        self.expiration = expiration
        self.strike = strike
        self.option_type = option_type
        self.dte = dte
        self.multiplier = multiplier


def _structured_data(payload: Mapping[str, Any], *, context: str) -> Mapping[str, Any]:
    root = _required_mapping(payload, context=context)
    structured = _required_mapping_key(root, "structuredContent", context=context)
    return _required_mapping_key(structured, "data", context=f"{context}.structuredContent")


def _required_mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AdapterError(f"{context}: expected object")
    return value


def _required_mapping_key(
    source: Mapping[str, Any], key: str, *, context: str
) -> Mapping[str, Any]:
    if key not in source:
        raise AdapterError(f"{context}: missing required field {key!r}")
    return _required_mapping(source[key], context=f"{context}.{key}")


def _required_list(source: Mapping[str, Any], key: str, *, context: str) -> list[Any]:
    if key not in source:
        raise AdapterError(f"{context}: missing required field {key!r}")
    value = source[key]
    if not isinstance(value, list):
        raise AdapterError(f"{context}.{key}: expected array")
    return value


def _required_string(source: Mapping[str, Any], key: str, *, context: str) -> str:
    if key not in source:
        raise AdapterError(f"{context}: missing required field {key!r}")
    value = source[key]
    if not isinstance(value, str) or not value:
        raise AdapterError(f"{context}.{key}: expected non-empty string")
    return value


def _required_decimal_string(
    source: Mapping[str, Any], key: str, *, context: str
) -> float:
    value = _required_string(source, key, context=context)
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise AdapterError(f"{context}.{key}: invalid numeric string {value!r}") from exc
    if not number.is_finite():
        raise AdapterError(f"{context}.{key}: expected a finite numeric string")
    result = float(number)
    if not math.isfinite(result):
        raise AdapterError(f"{context}.{key}: number is outside the supported finite range")
    return result


def _required_nonnegative_int(
    source: Mapping[str, Any], key: str, *, context: str
) -> int:
    if key not in source:
        raise AdapterError(f"{context}: missing required field {key!r}")
    value = source[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterError(f"{context}.{key}: expected integer")
    if value < 0:
        raise AdapterError(f"{context}.{key}: expected a non-negative integer")
    return value


def _required_rfc3339(
    source: Mapping[str, Any], key: str, *, context: str
) -> datetime:
    value = _required_string(source, key, context=context)
    if _RFC3339.fullmatch(value) is None:
        raise AdapterError(
            f"{context}.{key}: expected timezone-aware RFC3339 timestamp"
        )
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AdapterError(f"{context}.{key}: invalid RFC3339 timestamp {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError(
            f"{context}.{key}: expected timezone-aware RFC3339 timestamp"
        )
    return parsed.astimezone(UTC)


def _required_date(source: Mapping[str, Any], key: str, *, context: str) -> date:
    value = _required_string(source, key, context=context)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise AdapterError(f"{context}.{key}: expected date in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AdapterError(f"{context}.{key}: invalid date {value!r}") from exc


def _aware_now(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdapterError("now: expected a timezone-aware datetime")
    return value.astimezone(UTC)


def _expected_symbols(symbols: Collection[str]) -> tuple[str, ...]:
    if isinstance(symbols, (str, bytes)):
        raise AdapterError("expected_symbols: expected a collection of symbols")
    result = tuple(symbols)
    if not result:
        raise AdapterError("expected_symbols: no symbols requested")
    if len(result) != len(set(result)):
        raise AdapterError("expected_symbols: duplicate symbols")
    invalid = [symbol for symbol in result if symbol not in _DEFAULT_SYMBOLS]
    if invalid:
        raise AdapterError(
            f"expected_symbols: unsupported symbols {', '.join(repr(value) for value in invalid)}"
        )
    return result


def _allowed_dtes(values: Collection[int]) -> frozenset[int]:
    if isinstance(values, (str, bytes)):
        raise AdapterError("allowed_dte: expected a collection of non-negative integers")
    result: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AdapterError("allowed_dte: expected a collection of non-negative integers")
        result.add(value)
    if not result:
        raise AdapterError("allowed_dte: no DTE values configured")
    return frozenset(result)


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False, include_context=False)
    if not errors:
        return str(exc)
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "validation failed"))
    return f"{location}: {message}" if location else message
