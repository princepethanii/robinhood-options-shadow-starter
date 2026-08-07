from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from spy_agent.pipeline import evaluate_snapshot, save_sanitized_snapshot
from spy_agent.strategy import StrategyConfig, load_strategy_config

FIXTURES = Path(__file__).parent / "fixtures"
SCAN_NOW = datetime(2026, 8, 6, 13, 55, 10, tzinfo=UTC)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def bullish_historicals() -> dict[str, Any]:
    payload = load_fixture("equity_historicals.json")
    rows = payload["structuredContent"]["data"]["results"]
    prices = {
        "SPY": [100.00, 100.02, 100.09, 100.12, 100.28],
        "QQQ": [200.00, 200.04, 200.08, 200.12, 200.25],
        "IWM": [150.00, 150.01, 150.02, 150.03, 150.04],
    }
    for result in rows:
        symbol = result["symbol"]
        for index, (bar, price) in enumerate(zip(result["bars"], prices[symbol], strict=True)):
            previous = prices[symbol][max(0, index - 1)]
            if symbol == "SPY" and index == 2:
                open_price, high, low = 100.02, 100.10, 100.01
            else:
                open_price = previous
                high = max(open_price, price) + 0.02
                low = min(open_price, price) - 0.02
            bar.update(
                {
                    "open_price": f"{open_price:.6f}",
                    "high_price": f"{high:.6f}",
                    "low_price": f"{low:.6f}",
                    "close_price": f"{price:.6f}",
                    "volume": 160 if index == 4 else 100,
                }
            )
    return payload


def fresh_option_payloads() -> tuple[dict[str, Any], dict[str, Any]]:
    instruments = load_fixture("option_instruments.json")
    quotes = load_fixture("option_quotes.json")
    timestamp = (SCAN_NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    for row in quotes["structuredContent"]["data"]["results"]:
        row["quote"]["updated_at"] = timestamp
    return instruments, quotes


def write_event_config(tmp_path: Path, events: str = "[]") -> Path:
    path = tmp_path / "high-impact-events.yaml"
    path.write_text(
        f"timezone: America/Chicago\nevents: {events}\n",
        encoding="utf-8",
    )
    return path


def late_neutral_historicals() -> dict[str, Any]:
    payload = load_fixture("equity_historicals.json")
    base_prices = {"SPY": 100.0, "QQQ": 200.0, "IWM": 150.0}
    start = datetime(2026, 8, 6, 13, 30, tzinfo=UTC)
    for result in payload["structuredContent"]["data"]["results"]:
        price = base_prices[result["symbol"]]
        result["bars"] = [
            {
                "begins_at": (start + timedelta(minutes=5 * index))
                .isoformat()
                .replace("+00:00", "Z"),
                "open_price": f"{price:.6f}",
                "high_price": f"{price + 0.05:.6f}",
                "low_price": f"{price - 0.05:.6f}",
                "close_price": f"{price:.6f}",
                "volume": 100,
                "session": "reg",
            }
            for index in range(25)
        ]
    return payload


def test_pipeline_returns_schema_shaped_no_trade_for_valid_neutral_data() -> None:
    bars = load_fixture("equity_historicals.json")

    result = evaluate_snapshot(bars, now=SCAN_NOW, snapshot_root=None)
    schema = json.loads(Path("schemas/signal.schema.json").read_text(encoding="utf-8"))

    assert result["decision"] == "NO_TRADE"
    assert result["data_fresh"] is True
    assert result["contract"] is None
    assert set(result) == set(schema["required"])
    assert set(result["confirmation"]) == set(
        schema["properties"]["confirmation"]["required"]
    )


def test_pipeline_converts_missing_required_field_to_precise_no_trade() -> None:
    bars = load_fixture("equity_historicals.json")
    bars["structuredContent"]["data"]["results"][0]["bars"][0].pop("open_price")

    result = evaluate_snapshot(bars, now=SCAN_NOW, snapshot_root=None)

    assert result["decision"] == "NO_TRADE"
    assert result["data_fresh"] is False
    assert result["contract"] is None
    assert any("open_price" in reason for reason in result["rejections"])


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("wrong_interval", "interval"),
        ("misaligned", "not aligned"),
        ("incomplete", "incomplete"),
        ("stale", "stale"),
    ],
)
def test_pipeline_rejects_inconsistent_or_stale_underlying_data(
    mutation: str, expected_reason: str
) -> None:
    bars = load_fixture("equity_historicals.json")
    now = SCAN_NOW
    if mutation == "wrong_interval":
        bars["structuredContent"]["data"]["results"][0]["interval"] = "day"
    elif mutation == "misaligned":
        qqq_bars = bars["structuredContent"]["data"]["results"][1]["bars"]
        extra = deepcopy(qqq_bars[0])
        extra["begins_at"] = "2026-08-06T13:25:00Z"
        qqq_bars.insert(0, extra)
    elif mutation == "incomplete":
        now = SCAN_NOW - timedelta(minutes=5)
    else:
        now = SCAN_NOW + timedelta(minutes=6)

    result = evaluate_snapshot(bars, now=now, snapshot_root=None)

    assert result["decision"] == "NO_TRADE"
    assert result["data_fresh"] is False
    assert any(expected_reason in reason for reason in result["rejections"])


def test_pipeline_builds_candidate_economics_and_exact_contract_schema() -> None:
    bars = bullish_historicals()
    instruments, quotes = fresh_option_payloads()

    result = evaluate_snapshot(
        bars,
        instruments,
        quotes,
        now=SCAN_NOW,
        snapshot_root=None,
    )
    schema = json.loads(Path("schemas/signal.schema.json").read_text(encoding="utf-8"))

    assert result["decision"] == "CALL_CANDIDATE"
    assert result["data_fresh"] is True
    assert result["contract"] is not None
    assert set(result) == set(schema["required"])
    assert set(result["contract"]) == set(
        schema["properties"]["contract"]["required"]
    )
    assert result["contract"]["proposed_limit"] == 0.41
    assert result["contract"]["quantity"] == 1
    assert result["contract"]["total_debit_usd"] == 41.0
    assert result["contract"]["max_premium_loss_usd"] == 41.0
    assert result["contract"]["break_even"] == 769.41
    assert result["hypothetical_stop_price"] == 0.246
    assert result["hypothetical_target_price"] == 0.738
    assert any(reason.startswith("underlying data timestamps:") for reason in result["reasons"])
    assert any(reason.startswith("option quote timestamp:") for reason in result["reasons"])
    assert any(reason.startswith("invalidation:") for reason in result["reasons"])
    assert any("high-impact event gate: CLEAR" in reason for reason in result["reasons"])


def test_high_impact_event_gate_blocks_candidate_before_market_processing(
    tmp_path: Path,
) -> None:
    event_config = write_event_config(
        tmp_path,
        """\
\n  - date: 2026-08-06
    timestamp: "09:00"
    event_name: "Employment report"
    source: "Official statistical agency"
    risk_level: high""",
    )

    result = evaluate_snapshot(
        {},
        now=SCAN_NOW,
        event_config_path=event_config,
        snapshot_root=None,
    )

    assert result["decision"] == "NO_TRADE"
    assert result["data_fresh"] is False
    assert result["contract"] is None
    assert result["hypothetical_stop_price"] is None
    assert result["hypothetical_target_price"] is None
    assert any("high-impact event gate: BLOCKED" in reason for reason in result["reasons"])
    rejection = result["rejections"][0]
    assert "Employment report" in rejection
    assert "Official statistical agency" in rejection
    assert "risk level 'high'" in rejection
    assert "2026-08-06T08:50:00-05:00" in rejection
    assert "2026-08-06T09:15:00-05:00" in rejection
    assert "required market data" not in rejection


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, "config file is missing"),
        (
            "timezone: America/Chicago\nevents:\n  - date: 2026-08-06\n",
            "missing required field",
        ),
    ],
)
def test_pipeline_reports_missing_or_malformed_event_data(
    tmp_path: Path,
    content: str | None,
    expected: str,
) -> None:
    event_config = tmp_path / "high-impact-events.yaml"
    if content is not None:
        event_config.write_text(content, encoding="utf-8")

    result = evaluate_snapshot(
        bullish_historicals(),
        now=SCAN_NOW,
        event_config_path=event_config,
        snapshot_root=None,
    )

    assert result["decision"] == "NO_TRADE"
    assert result["data_fresh"] is False
    assert result["contract"] is None
    assert any("CONFIG_ERROR" in reason for reason in result["reasons"])
    assert any(expected in rejection for rejection in result["rejections"])


def test_explicit_empty_event_calendar_preserves_candidate(tmp_path: Path) -> None:
    event_config = write_event_config(tmp_path)
    bars = bullish_historicals()
    instruments, quotes = fresh_option_payloads()

    result = evaluate_snapshot(
        bars,
        instruments,
        quotes,
        now=SCAN_NOW,
        event_config_path=event_config,
        snapshot_root=None,
    )

    assert result["decision"] == "CALL_CANDIDATE"


@pytest.mark.parametrize("timestamp", ["08:40", "09:16"])
def test_nonempty_calendar_outside_blackout_preserves_candidate(
    tmp_path: Path,
    timestamp: str,
) -> None:
    event_config = write_event_config(
        tmp_path,
        f"""\
\n  - date: 2026-08-06
    timestamp: "{timestamp}"
    event_name: "Nonblocking event"
    source: "Official source"
    risk_level: high""",
    )
    bars = bullish_historicals()
    instruments, quotes = fresh_option_payloads()

    result = evaluate_snapshot(
        bars,
        instruments,
        quotes,
        now=SCAN_NOW,
        event_config_path=event_config,
        snapshot_root=None,
    )

    assert result["decision"] == "CALL_CANDIDATE"


@pytest.mark.parametrize("failure_mode", ["blocked", "malformed"])
def test_event_gate_early_no_trade_is_schema_shaped_and_sanitized(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    if failure_mode == "blocked":
        event_config = write_event_config(
            tmp_path,
            """\
\n  - date: 2026-08-06
    timestamp: "09:00"
    event_name: "Employment report"
    source: "Official statistical agency"
    risk_level: high""",
        )
    else:
        event_config = tmp_path / "high-impact-events.yaml"
        event_config.write_text(
            "timezone: America/Chicago\nevents:\n  - date: 2026-08-06\n",
            encoding="utf-8",
        )
    snapshot_root = tmp_path / "snapshots"

    result = evaluate_snapshot(
        {},
        now=SCAN_NOW,
        event_config_path=event_config,
        snapshot_root=snapshot_root,
    )

    schema = json.loads(Path("schemas/signal.schema.json").read_text(encoding="utf-8"))
    assert set(result) == set(schema["required"])
    assert result["decision"] == "NO_TRADE"
    snapshots = list((snapshot_root / "2026-08-06").glob("*.json"))
    assert len(snapshots) == 1
    saved = json.loads(snapshots[0].read_text(encoding="utf-8"))
    assert saved["signal"] == result
    assert saved["bars"] == {}
    assert saved["option_quotes"] == []


def test_pipeline_rejects_zero_bid_and_excess_total_debit() -> None:
    bars = bullish_historicals()
    instruments, quotes = fresh_option_payloads()
    zero_bid_quotes = deepcopy(quotes)
    zero_bid_quotes["structuredContent"]["data"]["results"][0]["quote"][
        "bid_price"
    ] = "0.000000"

    zero_bid = evaluate_snapshot(
        bars,
        instruments,
        zero_bid_quotes,
        now=SCAN_NOW,
        snapshot_root=None,
    )
    assert zero_bid["decision"] == "NO_TRADE"
    assert any("zero bid" in reason for reason in zero_bid["rejections"])

    expensive_instruments = deepcopy(instruments)
    expensive_instruments["structuredContent"]["data"]["instruments"][0][
        "trade_value_multiplier"
    ] = "200.0000"
    too_expensive = evaluate_snapshot(
        bars,
        expensive_instruments,
        quotes,
        now=SCAN_NOW,
        snapshot_root=None,
    )
    assert too_expensive["decision"] == "NO_TRADE"
    assert any("total debit" in reason for reason in too_expensive["rejections"])


def test_snapshot_is_date_partitioned_and_contains_only_normalized_data(
    tmp_path: Path,
) -> None:
    bars = load_fixture("equity_historicals.json")
    root = tmp_path / "data"

    result = evaluate_snapshot(bars, now=SCAN_NOW, snapshot_root=root)
    snapshots = list((root / "2026-08-06").glob("*.json"))

    assert len(snapshots) == 1
    saved = json.loads(snapshots[0].read_text(encoding="utf-8"))
    assert saved["signal"] == result
    assert set(saved) == {"snapshot_version", "captured_at", "bars", "option_quotes", "signal"}
    serialized = snapshots[0].read_text(encoding="utf-8").casefold()
    for forbidden in (
        "account_number",
        "structuredcontent",
        "access_token",
        "refresh_token",
        "password",
        "credential",
    ):
        assert forbidden not in serialized


def test_snapshot_writer_rejects_extra_signal_fields(tmp_path: Path) -> None:
    bars = load_fixture("equity_historicals.json")
    signal = evaluate_snapshot(bars, now=SCAN_NOW, snapshot_root=None)
    unsafe_signal = {**signal, "account_number": "redacted"}

    with pytest.raises(ValueError, match="account_number"):
        save_sanitized_snapshot(
            {},
            [],
            unsafe_signal,
            captured_at=SCAN_NOW,
            root=tmp_path / "data",
        )

    assert not list(tmp_path.rglob("*.json"))


def test_strategy_config_loads_session_and_enforces_absolute_caps() -> None:
    config = load_strategy_config()

    assert config.opening_range_start.isoformat(timespec="minutes") == "08:30"
    assert config.earliest_entry.isoformat(timespec="minutes") == "08:50"
    assert config.latest_entry.isoformat(timespec="minutes") == "10:30"
    assert config.max_total_debit_usd == 50.0
    assert config.max_contracts == 1
    with pytest.raises(ValueError, match="50 USD"):
        StrategyConfig(max_total_debit_usd=50.01)
    with pytest.raises(ValueError, match="one contract"):
        StrategyConfig(max_contracts=2)
    with pytest.raises(ValueError, match="spread filters"):
        StrategyConfig(max_spread_dollars=0.06)
    with pytest.raises(ValueError, match=r"\(0, 10\]"):
        StrategyConfig(max_quote_age_seconds=11)


def test_pipeline_enforces_configured_candidate_window() -> None:
    bars = late_neutral_historicals()
    after_window = datetime(2026, 8, 6, 15, 35, 10, tzinfo=UTC)

    result = evaluate_snapshot(
        bars,
        now=after_window,
        snapshot_root=None,
    )

    assert result["decision"] == "NO_TRADE"
    assert any("candidate window" in reason for reason in result["rejections"])


def test_strategy_config_rejects_changes_to_fixed_session_or_positive_bid() -> None:
    with pytest.raises(ValueError, match="fixed Chicago schedule"):
        StrategyConfig(earliest_entry=time(8, 51))
    with pytest.raises(ValueError, match="positive-bid"):
        StrategyConfig(require_positive_bid=False)
