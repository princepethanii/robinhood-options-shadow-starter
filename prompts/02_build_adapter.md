Build the Robinhood read-only adapter for this repository.

First inspect AGENTS.md, config/strategy.yaml, the existing Python package, tests, and the actual response shapes returned by
the connected Robinhood MCP tools. Do not call any brokerage write tool.

Implement only what is necessary to:
- Normalize SPY, QQQ, and IWM OHLCV bars into the Bar model.
- Normalize option-chain rows into OptionQuote.
- Validate timestamps, bid/ask consistency, missing fields, and quote freshness.
- Save sanitized snapshots under data/YYYY-MM-DD/ without account numbers or secrets.
- Feed normalized data to the deterministic evaluator in src/spy_agent/strategy.py.
- Produce output conforming to schemas/signal.schema.json.
- Add tests using fixtures that mirror the real response shapes but contain no personal account information.

Do not hard-code undocumented Robinhood fields. If a required field is absent, return NO_TRADE with a precise reason.
Run pytest and report the results.
