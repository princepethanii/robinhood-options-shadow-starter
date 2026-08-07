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

# Canonical project contract

## Project purpose

This repository implements a deterministic, shadow-only SPY options
research system using Robinhood MCP market data.

The system may identify hypothetical CALL_CANDIDATE, PUT_CANDIDATE,
or NO_TRADE decisions.

It is not currently authorized to place, modify, cancel, exercise,
or review a live brokerage order.

## Current operating phase

Current phase: SHADOW-ONLY RESEARCH.

No live trading is permitted.

The strategy is an unproven research hypothesis. Do not describe it as
profitable, accurate, reliable, or production-ready until sufficient
out-of-sample evidence supports that conclusion.

## Non-negotiable brokerage safety rules

- Never call place_option_order.
- Never call place_equity_order.
- Never call cancel_option_order.
- Never call cancel_equity_order.
- Never call an exercise or assignment action.
- Never create or modify Robinhood watchlists or scans.
- Never submit or modify any live brokerage order.
- Never enable a Robinhood write or mutation tool.
- Never place a market order.
- Never hold a hypothetical position through expiration.
- Never expose account numbers, credentials, OAuth data, tokens,
  balances, transactions, or private account data in repository files.
- Do not save raw Robinhood account responses.
- Missing, stale, malformed, or ambiguous data must produce NO_TRADE.
- Safety rules take precedence over strategy logic.

Robinhood MCP configuration must expose only the read-only market-data
tools needed by shadow mode. Brokerage order-placement and cancellation
tools must remain unavailable.

## Risk limits

- Maximum hypothetical debit: $50.
- Maximum contract quantity: 1.
- Maximum candidate entries per trading day: 1.
- No averaging down.
- No rolling.
- No reentry after a hypothetical exit.
- No multi-leg options positions.
- Long calls or long puts only.
- A complete premium loss must always be treated as possible.

## Strategy version 1

Timezone: America/Chicago.

Underlying signal:

- Primary underlying: SPY.
- QQQ provides directional confirmation.
- IWM and VIX may be recorded as context but must not override the
  deterministic strategy unless separately validated.
- Opening range: 8:30 AM through 8:45 AM Central.
- Earliest evaluation: 8:50 AM Central.
- Last new candidate: 10:30 AM Central.
- Hypothetical forced exit: 2:00 PM Central.

A bullish candidate requires:

- SPY closing above the opening-range high with a configured buffer.
- SPY above session VWAP.
- QQQ above session VWAP.
- SPY EMA 9 above EMA 20.
- Required volume confirmation.
- A breakout retest that holds.
- No excessive extension.
- No active high-impact-event exclusion.

A bearish candidate reverses those requirements.

Option-selection requirements:

- Expiration: 0 or 1 DTE.
- Prefer 1 DTE.
- Absolute delta between 0.25 and 0.45.
- Ask price no greater than $0.50.
- Bid greater than zero.
- Bid/ask spread no greater than $0.05.
- Spread no greater than 15% of midpoint.
- Option volume at least 100.
- Open interest at least 500.
- Quote age no greater than 10 seconds.
- Quantity exactly 1.

High-impact-event gate:

- Block new candidates from 10 minutes before through 15 minutes after
  a configured high-impact event.
- Event timestamps must use America/Chicago.
- Missing or malformed required event data must fail safely.

## Required output

Every evaluation must return one of:

- NO_TRADE
- CALL_CANDIDATE
- PUT_CANDIDATE

A candidate must include:

- Evaluation timestamp.
- Underlying price and signal information.
- Exact option symbol.
- Call or put.
- Strike.
- Expiration.
- Bid.
- Ask.
- Midpoint or mark.
- Delta.
- Volume.
- Open interest.
- Quote timestamp and age.
- Hypothetical limit price.
- Quantity.
- Total hypothetical debit.
- Maximum premium loss.
- Expiration break-even.
- Selection reasons.
- Failure and rejection reasons.

## Engineering requirements

Before declaring a code change complete:

- Run the complete test suite.
- Add tests for changed behavior.
- Validate timestamps and timezone handling.
- Validate malformed and stale market data.
- Validate option multiplier assumptions.
- Validate high-impact-event boundaries.
- Confirm no brokerage mutation function was introduced.
- Show the exact files changed.
- Report unresolved uncertainty honestly.

Do not change strategy thresholds merely to improve historical results.
Any threshold change must be treated as a new strategy version and tested
chronologically on unseen data.
