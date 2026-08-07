from datetime import UTC, datetime, timedelta

import pandas as pd

from spy_agent.models import OptionQuote
from spy_agent.strategy import StrategyConfig, evaluate_underlying, select_option


def bars(prices: list[float], volumes: list[int]) -> pd.DataFrame:
    start = datetime(2026, 8, 6, 13, 30, tzinfo=UTC)
    rows = []
    for i, (price, volume) in enumerate(zip(prices, volumes, strict=True)):
        rows.append(
            {
                "timestamp": start + timedelta(minutes=5 * i),
                "open": price - 0.05,
                "high": price + 0.10,
                "low": price - 0.10,
                "close": price,
                "volume": volume,
            }
        )
    return pd.DataFrame(rows)


def test_no_trade_without_confirmation() -> None:
    frame = bars([100, 100.05, 99.98, 100.02, 100.01], [100, 100, 100, 100, 100])
    result = evaluate_underlying(frame, frame, frame, 100.20, 99.80)
    assert result.decision == "NO_TRADE"


def test_call_candidate_on_confirmed_breakout() -> None:
    spy = bars([100.00, 100.04, 100.08, 100.12, 100.28], [100, 100, 100, 100, 160])
    qqq = bars([200.00, 200.04, 200.08, 200.12, 200.25], [100, 100, 100, 100, 160])
    iwm = bars([150.00, 150.01, 150.02, 150.03, 150.04], [100, 100, 100, 100, 120])
    result = evaluate_underlying(spy, qqq, iwm, 100.10, 99.70)
    assert result.decision == "CALL_CANDIDATE"


def test_option_selection_rejects_wide_spread() -> None:
    now = datetime.now(UTC)
    wide = OptionQuote(
        symbol="SPYTEST1",
        option_type="call",
        strike=101,
        expiration=now + timedelta(days=1),
        dte=1,
        timestamp=now,
        bid=0.20,
        ask=0.40,
        delta=0.30,
        volume=1000,
        open_interest=5000,
        trade_value_multiplier=100.0,
    )
    selected, rejected = select_option([wide], "call", now=now)
    assert selected is None
    assert any("spread too wide" in item for item in rejected)


def test_option_selection_prefers_one_dte() -> None:
    now = datetime.now(UTC)
    common = {
        "option_type": "call",
        "strike": 101,
        "timestamp": now,
        "bid": 0.39,
        "ask": 0.41,
        "delta": 0.30,
        "volume": 1000,
        "open_interest": 5000,
        "trade_value_multiplier": 100.0,
    }
    zero = OptionQuote(
        symbol="SPY0DTE", expiration=now, dte=0, **common
    )
    one = OptionQuote(
        symbol="SPY1DTE", expiration=now + timedelta(days=1), dte=1, **common
    )
    selected, _ = select_option([zero, one], "call", now=now, config=StrategyConfig())
    assert selected is not None
    assert selected.symbol == "SPY1DTE"
