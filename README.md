# Robinhood Options Shadow Agent Starter

Current phase: **SHADOW-ONLY**.

This repository supports deterministic SPY options research with public market data only.
It does not authorize private brokerage reads, order review, order placement, cancellation,
exercise, scanners, watchlists, or any brokerage mutation.

The scanner remains disabled until the deterministic-runner remediation is completed and
audited. The current shell runners are not approved for regular, ad hoc, or scheduled execution.

## 1. Create the local project

```bash
cd robinhood-options-shadow-starter
git init
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e '.[dev]'
pytest -q
```

Do not store Robinhood credentials, OAuth data, tokens, account data, or other private
brokerage data in this repository.

## 2. Configure least privilege before login or scanning

Configure the public-market-data-only allowlist before any Robinhood login, MCP audit, or
scan attempt. Start from `config/codex-shadow-snippet.toml`, which deliberately has
`enabled = false`.

1. Review the local Codex configuration and this repository.
2. Merge the snippet into the local `[mcp_servers."robinhood-trading"]` block. Do not create
   duplicate server blocks.
3. Confirm that `enabled_tools` contains exactly these six names and no others:

   - `get_equity_historicals`
   - `get_equity_quotes`
   - `get_index_quotes`
   - `get_option_chains`
   - `get_option_instruments`
   - `get_option_quotes`

4. Confirm that account, portfolio, P&L, position, transaction, order-history, order-review,
   order-placement, cancellation, exercise, scanner, watchlist, and mutation tools are absent.
5. Only after the local configuration review and repository audit both pass may the server be
   enabled in the local configuration for the public-market-data audit.

Do not change the repository snippet's disabled default. Enabling the six-tool public-data
connection locally does not enable or authorize the scanner.

## 3. Authenticate only after the allowlist is verified

After the least-privilege boundary is in place and the local server has been deliberately
enabled, restart Codex and then authenticate:

```bash
codex mcp list
codex mcp login robinhood-trading
```

Complete Robinhood's desktop OAuth flow. Never paste credentials, tokens, account identifiers,
or other private brokerage data into a prompt or repository file.

## 4. Audit the public-data connection

Start Codex inside this repository and use `prompts/01_mcp_audit.md`. The audit first inspects
visible tool names without invoking them. It must fail before retrieving data if any tool other
than the six-tool public market-data allowlist is exposed.

After that preflight passes, the prompt permits only a minimal SPY/QQQ/IWM public market-data
sample and a minimal SPY option-data sample. It never requests or saves private brokerage data.

## Scanner quarantine

Do not execute `scripts/run_shadow.sh` or `scripts/run_shadow_session.sh` to initiate regular,
manual, or scheduled scans. Both runners remain quarantined until the deterministic-runner
remediation is complete, covered by tests, and separately audited. Static inspection and shell
syntax validation do not authorize a scan.

`prompts/02_build_adapter.md` and `prompts/03_daily_shadow.md` are retained as research and
remediation artifacts; their presence is not execution authorization.

## High-impact-event safety gate

The deterministic research contract uses the manually curated
`config/high-impact-events.yaml` file. It must declare `timezone: America/Chicago` and an
`events` list. Every event has exactly these fields:

```yaml
- date: 2026-08-07
  timestamp: "07:30"
  event_name: "Example event"
  source: "Official publisher"
  risk_level: high
```

The evaluator rejects new candidates from 10 minutes before through 15 minutes after each event,
including both endpoints. A missing or malformed calendar fails closed to `NO_TRADE` and reports the
problem in `rejections`. The `source` value is audit provenance only; the scanner never follows it or
scrapes event calendars or financial websites.

## Research evaluation gates

These research-quality gates remain required for evaluating the hypothesis, but satisfying
them does not authorize private brokerage access, order tools, or live use:

- At least 20 shadow sessions have completed without data or logic errors.
- At least 50 qualifying out-of-sample signals have been recorded.
- Results include realistic bid/ask spread and slippage assumptions.
- Out-of-sample expectancy is positive and drawdown is acceptable to you.
- You have manually reviewed randomly selected logs against Robinhood charts.

A profitable backtest is not proof of future profitability. Do not optimize dozens of
thresholds against the same sample.

## No live-use authorization

No amount of shadow data, backtesting, or out-of-sample performance authorizes private
brokerage access or order capabilities under the current phase. Any later phase would require
a separate, explicit change to both the repository safety contract and the local MCP allowlist.

The strategy is an unproven research hypothesis. Do not describe it as profitable, accurate,
reliable, or production-ready without sufficient chronological out-of-sample evidence.

## Safety invariants

- Public market data only; no account, portfolio, P&L, position, transaction, or order reads.
- No order review, placement, cancellation, exercise, scanner, watchlist, or mutation tools.
- Long calls or long puts only.
- Maximum hypothetical debit: $50.
- One contract and one entry per day.
- No short options, spreads, exercise, averaging down, or holding through expiration.
- One missing or stale required data field means `NO_TRADE`.
- A contract that only fits the budget by using very low delta or a wide spread means `NO_TRADE`.
- `NO_TRADE` is a successful outcome.
