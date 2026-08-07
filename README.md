# Robinhood Options Shadow Agent Starter

This repository is intentionally **shadow-only**. It contains no brokerage order-placement code.
It is designed to be used with Codex CLI plus Robinhood's Trading MCP for read-only data retrieval,
deterministic signal evaluation, and auditable logging.

## 1. Connect Codex CLI to Robinhood

```bash
codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading
codex mcp list
codex mcp login robinhood-trading
```

Then run `codex`, enter `/mcp`, select `robinhood-trading`, and complete Robinhood's desktop OAuth/onboarding flow.
Do not paste Robinhood credentials into prompts.

## 2. Create a local project

```bash
cd robinhood-options-shadow-starter
git init
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e '.[dev]'
pytest
```

## 3. Lock MCP to read-only tools

Open `~/.codex/config.toml`. Find the existing `[mcp_servers."robinhood-trading"]` block and merge in the
settings from `config/codex-shadow-snippet.toml`. Do not create two blocks with the same name.
Restart Codex after editing.

## 4. Audit the MCP connection

Start Codex inside this repository and paste `prompts/01_mcp_audit.md`.
The expected result is a read-only capability report and a small SPY/QQQ data sample. Any attempt to use
`place_option_order` is a failure.

## 5. Build the data adapter with Codex

Paste `prompts/02_build_adapter.md`. Codex should inspect the actual Robinhood MCP response shapes and implement
an adapter that writes normalized snapshots to `data/`. Do not hard-code undocumented MCP response fields.

## 6. Run a shadow scan

At or after 8:50 AM America/Chicago on a regular trading day, run one scan:

```bash
./scripts/run_shadow.sh
```

To collect one shadow snapshot on every 5-minute boundary through 10:30 AM CT:

```bash
./scripts/run_shadow_session.sh
```

Each structured result is saved under `logs/YYYY-MM-DD/`. The session script is shadow-only and does not place or cancel orders.

The output must be `NO_TRADE`, `CALL_CANDIDATE`, or `PUT_CANDIDATE`. It must never submit an order.

### High-impact-event safety gate

Maintain the calendar manually in `config/high-impact-events.yaml`. The file must declare
`timezone: America/Chicago` and an `events` list. Every event has exactly these fields:

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

## 7. Evaluate before live use

Do not enable order tools until all of the following are true:

- At least 20 shadow sessions have completed without data or logic errors.
- At least 50 qualifying out-of-sample signals have been recorded.
- Results include realistic bid/ask spread and slippage assumptions.
- Out-of-sample expectancy is positive and drawdown is acceptable to you.
- You have manually reviewed randomly selected logs against Robinhood charts.

A profitable backtest is not proof of future profitability. Do not optimize dozens of thresholds against the same sample.

## Safety invariants

- Long calls or long puts only.
- Maximum hypothetical debit: $50.
- One contract and one entry per day.
- No short options, spreads, exercise, averaging down, or holding through expiration.
- One missing or stale required data field means `NO_TRADE`.
- A contract that only fits the budget by using very low delta or a wide spread means `NO_TRADE`.
- `NO_TRADE` is a successful outcome.
