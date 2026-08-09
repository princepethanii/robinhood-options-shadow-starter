# Robinhood MCP public-market-data audit

Audit the Robinhood Trading MCP boundary before retrieving any data.

## Safety preflight

- First inspect only the visible Robinhood tool names. Do not invoke a tool during this preflight.
- The visible allowlist must contain exactly these six public market-data tools:
  `get_equity_historicals`, `get_equity_quotes`, `get_index_quotes`,
  `get_option_chains`, `get_option_instruments`, and `get_option_quotes`.
- Fail the audit and stop before data retrieval if any other Robinhood tool is exposed.
- Prohibited capabilities include account, portfolio, brokerage P&L, position,
  transaction, order-history, order-review, order-placement, cancellation,
  exercise, assignment, scanner, scan, watchlist, and mutation tools.
- Never request account IDs, buying power, balances, portfolios, positions,
  transactions, orders, order history, P&L, watchlists, or scans.
- Never call or enable a prohibited capability, even if it is described as read-only.
- Do not print, return, log, or save private brokerage data or raw MCP responses.

## Minimal public sample

Continue only if the safety preflight passes.

1. Confirm that the `robinhood-trading` server is connected with exactly the six allowed tools.
2. Retrieve one current public equity quote each for SPY, QQQ, and IWM.
3. Retrieve the smallest supported recent 5-minute OHLCV sample for SPY, QQQ, and IWM.
4. Retrieve only enough public SPY option-chain and instrument data to identify the
   nearest-to-spot call and put in the nearest expiration.
5. Retrieve current public option quotes for at most those two SPY instruments.
6. Report the exact visible tool names, request timestamps, response timestamps, symbols,
   OHLCV fields, and option bid/ask, mark, volume, open interest, delta, gamma, theta,
   and implied-volatility fields that are actually present.

Do not write the audit or samples to disk. Stop with a failure if a required public field
is unavailable, stale, malformed, or ambiguous. Never invent or substitute a field.
