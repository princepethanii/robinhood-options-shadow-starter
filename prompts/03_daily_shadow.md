# QUARANTINED SHADOW-SCAN SPECIFICATION

DO NOT EXECUTE THIS PROMPT OR USE IT TO INITIATE A SCAN.
It is retained only as a design specification for the future
deterministic-runner remediation.

The sections below describe the intended future workflow after that remediation; they do
not authorize execution in the current phase.

Future safety properties:
- A remediated runner would be read-only and would never call any order-placement, cancellation, exercise, watchlist-write, or scanner-write tool.
- It would follow AGENTS.md and config/strategy.yaml exactly.
- Missing, stale, or inconsistent data would produce NO_TRADE.

Future high-impact-event gate:
- Before retrieving market or option data, a remediated runner would apply the high-impact-event gate.
- It would load only the manually curated config/high-impact-events.yaml file as the sole event-calendar authority.
- It would use the deterministic loader and evaluator in src/spy_agent/events.py rather than calculate the window in the LLM.
- It would never scrape an event calendar, arbitrary financial website, or URL in an event's source field. Source would remain inert provenance only.
- Missing or malformed event data would produce NO_TRADE, with the precise validation issue in rejections.
- The runner would not allow a new entry from 10 minutes before through 15 minutes after a high-risk event, inclusive.
- If the gate blocked entry, the runner would return NO_TRADE with the event name, Chicago timestamp, source, risk level, and blackout bounds in rejections.

Future data workflow:
1. After the event gate cleared, a remediated runner would determine the current time in America/Chicago and verify that the U.S. options market was open.
2. It would retrieve complete 1-minute and 5-minute bars since 8:30 AM CT for SPY, QQQ, and IWM.
3. It would retrieve current SPY, QQQ, IWM, and VIX/index quotes.
4. It would compute locally: 15-minute opening range, session VWAP, EMA(9), EMA(20), breakout distance, retest state, and volume confirmation.
5. It would determine CALL, PUT, or NO_TRADE using deterministic code only.
6. For a directional signal, it would load SPY contracts with 0 or 1 DTE and evaluate liquidity/risk filters.
7. It would prefer 1 DTE and would never reduce delta quality or liquidity merely to fit the $50 budget.
8. It would return at most one contract; if none qualified, it would return NO_TRADE.
9. It would use executable bid/ask data for the hypothetical fill and would not use last trade as the fill.
10. It would append the sanitized result to logs/shadow-journal.jsonl.

Any future final response would conform to schemas/signal.schema.json.
