Run today's SPY long-options shadow scan using the connected Robinhood Trading MCP and this repository.

Safety:
- Read-only. Never call any order-placement, cancellation, exercise, watchlist-write, or scanner-write tool.
- Follow AGENTS.md and config/strategy.yaml exactly.
- Missing, stale, or inconsistent data means NO_TRADE.

High-impact-event gate (run before retrieving market or option data):
- Load only the manually curated config/high-impact-events.yaml file. It is the sole event-calendar authority.
- Use the deterministic loader and evaluator in src/spy_agent/events.py; do not calculate the window in the LLM.
- Never scrape an event calendar, arbitrary financial website, or URL in an event's source field. Source is inert provenance only.
- Missing or malformed event data means NO_TRADE and the precise validation issue must appear in rejections.
- Do not allow a new entry from 10 minutes before through 15 minutes after a high-risk event, inclusive.
- If the gate blocks entry, return NO_TRADE with the event name, Chicago timestamp, source, risk level, and blackout bounds in rejections.

Data:
1. After the event gate clears, determine the current time in America/Chicago and verify the U.S. options market is open.
2. Retrieve complete 1-minute and 5-minute bars since 8:30 AM CT for SPY, QQQ, and IWM.
3. Retrieve current SPY, QQQ, IWM, and VIX/index quotes.
4. Compute locally: 15-minute opening range, session VWAP, EMA(9), EMA(20), breakout distance, retest state, and volume confirmation.
5. Determine CALL, PUT, or NO_TRADE using deterministic code only.
6. For a directional signal, load SPY contracts with 0 or 1 DTE and evaluate liquidity/risk filters.
7. Prefer 1 DTE. Never reduce delta quality or liquidity merely to fit the $50 budget.
8. Return at most one contract. If none qualifies, return NO_TRADE.
9. Use executable bid/ask data for the hypothetical fill; do not use last trade as the fill.
10. Append the sanitized result to logs/shadow-journal.jsonl.

The final response must conform to schemas/signal.schema.json.
