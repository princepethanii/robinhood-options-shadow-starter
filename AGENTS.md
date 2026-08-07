# Trading Project Rules

## Absolute safety boundary

This repository is SHADOW-ONLY unless the user separately changes both the MCP allow-list and this file.
Never call `place_option_order`, `place_equity_order`, `cancel_option_order`, or any other brokerage write tool.
Never submit, modify, cancel, exercise, or queue a live order.

## Allowed work

- Read repository files.
- Run tests and local deterministic analysis.
- Use Robinhood read-only account, market-data, equity, and option tools.
- Use `review_option_order` only as a non-binding simulation when explicitly requested.
- Write normalized data, reports, and logs inside this repository.

## Strategy principles

- The LLM orchestrates; deterministic Python decides.
- Never infer or invent a missing quote, Greek, bar, timestamp, spread, or fill.
- Never use a last trade as a substitute for a live executable bid/ask quote.
- Reject stale, crossed, zero-bid, illiquid, or incomplete contracts.
- Prefer `NO_TRADE` over relaxing a rule to fit the $50 budget.
- Do not optimize for win rate alone. Report expectancy, average win, average loss, profit factor, drawdown, and slippage.
- Do not introduce machine learning until there is enough clean, out-of-sample data to evaluate it.

## Time and market rules

- Time zone: America/Chicago.
- Opening range: 8:30-8:45 AM CT.
- Earliest candidate evaluation: 8:50 AM CT.
- Last new candidate: 10:30 AM CT.
- A live design, if later approved, must force exit no later than 2:00 PM CT.

## Output contract

Every scan must return exactly one decision:

- `NO_TRADE`
- `CALL_CANDIDATE`
- `PUT_CANDIDATE`

Every candidate must include data timestamps, underlying evidence, option liquidity, total debit, maximum premium loss,
break-even, invalidation, hypothetical stop, hypothetical target, and all rejection reasons considered.
