Use the Robinhood Trading MCP in strict read-only mode.

Absolute prohibition: do not call place_option_order, place_equity_order, cancel_option_order, create_scan,
update_scan_filters, update_scan_config, or any other write tool.

Tasks:
1. Confirm the `robinhood-trading` MCP server is connected.
2. List the exact available Robinhood tool names and identify which are read-only versus write-capable.
3. Call get_accounts and identify the Agentic account without printing full account numbers; mask all but the last four digits.
4. Call get_portfolio and report buying power for the Agentic account.
5. Retrieve current quotes for SPY, QQQ, and IWM.
6. Retrieve a small recent OHLCV sample for SPY.
7. Retrieve the nearest SPY option chain and one option quote on each side of spot.
8. State whether quote timestamps, bid, ask, mark, volume, open interest, delta, gamma, theta, and implied volatility are present.
9. Save a sanitized capability report to `logs/mcp-audit.md`.

Stop and report an error if any requested field is unavailable. Do not invent substitute fields.
