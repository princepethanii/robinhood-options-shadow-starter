from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from spy_agent.adapter import AdapterError, normalize_bars, normalize_option_chain
from spy_agent.models import Bar, OptionQuote

FIXTURES = Path(__file__).parent / "fixtures"
QUOTE_NOW = datetime(2026, 8, 6, 17, 58, 10, 100_000, tzinfo=UTC)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def first_bar(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["structuredContent"]["data"]["results"][0]["bars"][0]


def first_quote(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["structuredContent"]["data"]["results"][0]["quote"]


def test_normalize_bars_returns_expected_models_for_all_symbols() -> None:
    payload = load_fixture("equity_historicals.json")

    normalized = normalize_bars(
        payload,
        expected_symbols=("SPY", "QQQ", "IWM"),
        now=datetime(2026, 8, 6, 14, 0, tzinfo=UTC),
    )

    assert set(normalized) == {"SPY", "QQQ", "IWM"}
    assert all(len(symbol_bars) == 5 for symbol_bars in normalized.values())
    assert all(isinstance(bar, Bar) for symbol_bars in normalized.values() for bar in symbol_bars)
    spy = normalized["SPY"][0]
    assert spy.timestamp == datetime(2026, 8, 6, 13, 30, tzinfo=UTC)
    assert (spy.open, spy.high, spy.low, spy.close, spy.volume) == (
        768.34,
        768.41,
        767.9005,
        767.97,
        88739,
    )


def test_normalize_bars_rejects_missing_observed_field() -> None:
    payload = load_fixture("equity_historicals.json")
    first_bar(payload).pop("close_price")

    with pytest.raises(AdapterError, match="close_price"):
        normalize_bars(payload, now=datetime(2026, 8, 6, 14, 0, tzinfo=UTC))


def test_normalize_bars_rejects_missing_expected_symbol() -> None:
    payload = load_fixture("equity_historicals.json")
    results = payload["structuredContent"]["data"]["results"]
    payload["structuredContent"]["data"]["results"] = [
        result for result in results if result["symbol"] != "IWM"
    ]

    with pytest.raises(AdapterError, match="IWM"):
        normalize_bars(payload, expected_symbols=("SPY", "QQQ", "IWM"))


def test_normalize_bars_rejects_naive_timestamp() -> None:
    payload = load_fixture("equity_historicals.json")
    first_bar(payload)["begins_at"] = "2026-08-06T13:30:00"

    with pytest.raises(AdapterError, match="timezone"):
        normalize_bars(payload, now=datetime(2026, 8, 6, 14, 0, tzinfo=UTC))


def test_normalize_bars_rejects_future_timestamp() -> None:
    payload = load_fixture("equity_historicals.json")
    first_bar(payload)["begins_at"] = "2026-08-06T14:00:00.000001Z"

    with pytest.raises(AdapterError, match="future"):
        normalize_bars(payload, now=datetime(2026, 8, 6, 14, 0, tzinfo=UTC))


def test_normalize_option_chain_joins_instruments_and_quotes() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")

    normalized = normalize_option_chain(instruments, quotes, now=QUOTE_NOW)

    assert len(normalized) == 2
    assert all(isinstance(quote, OptionQuote) for quote in normalized)
    call, put = normalized
    assert call.symbol == "SPY"
    assert call.option_type == "call"
    assert call.strike == 769.0
    assert call.expiration == date(2026, 8, 6)
    assert call.dte == 0
    assert call.timestamp == datetime(2026, 8, 6, 17, 58, 10, 12_834, tzinfo=UTC)
    assert (call.bid, call.ask, call.delta, call.volume, call.open_interest) == (
        0.39,
        0.41,
        0.3,
        380467,
        4187,
    )
    assert put.symbol == "SPY"
    assert put.option_type == "put"
    assert put.expiration == date(2026, 8, 7)
    assert put.dte == 1
    assert put.delta == -0.363718


def test_normalize_option_chain_rejects_missing_quote_field() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    first_quote(quotes).pop("delta")

    with pytest.raises(AdapterError, match="delta"):
        normalize_option_chain(instruments, quotes, now=QUOTE_NOW)


def test_normalize_option_chain_does_not_require_non_executable_close() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    for result in quotes["structuredContent"]["data"]["results"]:
        result.pop("close")

    normalized = normalize_option_chain(instruments, quotes, now=QUOTE_NOW)

    assert len(normalized) == 2


def test_normalize_option_chain_requires_observed_contract_multiplier() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    instruments["structuredContent"]["data"]["instruments"][0].pop(
        "trade_value_multiplier"
    )

    with pytest.raises(AdapterError, match="trade_value_multiplier"):
        normalize_option_chain(instruments, quotes, now=QUOTE_NOW)


def test_normalize_option_chain_rejects_naive_timestamp() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    first_quote(quotes)["updated_at"] = "2026-08-06T17:58:10.012834358"

    with pytest.raises(AdapterError, match="timezone"):
        normalize_option_chain(instruments, quotes, now=QUOTE_NOW)


def test_normalize_option_chain_accepts_quote_at_freshness_boundary() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    first_quote(quotes)["updated_at"] = "2026-08-06T17:58:00.100000000Z"

    normalized = normalize_option_chain(
        instruments,
        quotes,
        now=QUOTE_NOW,
        max_quote_age_seconds=10,
    )

    assert len(normalized) == 2


def test_normalize_option_chain_rejects_stale_quote_past_boundary() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    first_quote(quotes)["updated_at"] = "2026-08-06T17:58:00.099999000Z"

    with pytest.raises(AdapterError, match="stale"):
        normalize_option_chain(
            instruments,
            quotes,
            now=QUOTE_NOW,
            max_quote_age_seconds=10,
        )


def test_normalize_option_chain_rejects_future_quote() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    first_quote(quotes)["updated_at"] = "2026-08-06T17:58:10.100001000Z"

    with pytest.raises(AdapterError, match="future"):
        normalize_option_chain(instruments, quotes, now=QUOTE_NOW)


def test_normalize_option_chain_rejects_crossed_quote() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    first_quote(quotes)["bid_price"] = "0.420000"
    first_quote(quotes)["ask_price"] = "0.410000"

    with pytest.raises(AdapterError, match="crossed"):
        normalize_option_chain(instruments, quotes, now=QUOTE_NOW)


def test_normalize_option_chain_rejects_unsupported_dte() -> None:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    instruments["structuredContent"]["data"]["instruments"][0][
        "expiration_date"
    ] = "2026-08-08"

    with pytest.raises(AdapterError, match="DTE"):
        normalize_option_chain(
            instruments,
            quotes,
            now=QUOTE_NOW,
            allowed_dte=(0, 1),
        )


def test_market_fixtures_contain_no_personal_or_account_keys() -> None:
    forbidden_keys = {
        "account",
        "account_id",
        "account_number",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "password",
        "email",
        "phone",
        "first_name",
        "last_name",
        "tax_id",
        "social_security_number",
    }

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return {str(key).casefold() for key in value} | set().union(
                *(keys(item) for item in value.values())
            )
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    fixture_paths = sorted(FIXTURES.glob("*.json"))
    assert fixture_paths
    for fixture_path in fixture_paths:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert keys(payload).isdisjoint(forbidden_keys), fixture_path.name


def test_fixture_mutations_do_not_leak_between_tests() -> None:
    original = load_fixture("option_quotes.json")
    mutated = deepcopy(original)
    first_quote(mutated)["bid_price"] = "99.000000"

    assert first_quote(original)["bid_price"] == "0.390000"
