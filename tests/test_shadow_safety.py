from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHADOW_CONFIG = ROOT / "config" / "codex-shadow-snippet.toml"
PROMPTS = ROOT / "prompts"

ALLOWED_TOOLS = (
    "get_equity_historicals",
    "get_equity_quotes",
    "get_index_quotes",
    "get_option_chains",
    "get_option_instruments",
    "get_option_quotes",
)

PROHIBITED_TOOL_TOKENS = frozenset(
    {
        "account",
        "accounts",
        "add",
        "assignment",
        "balance",
        "balances",
        "buying",
        "cancel",
        "cancellation",
        "create",
        "delete",
        "exercise",
        "loss",
        "modify",
        "mutate",
        "mutation",
        "order",
        "orders",
        "place",
        "placement",
        "pnl",
        "portfolio",
        "position",
        "positions",
        "profit",
        "remove",
        "review",
        "scan",
        "scanner",
        "submit",
        "set",
        "transaction",
        "transactions",
        "update",
        "watchlist",
        "watchlists",
        "write",
    }
)

_FORBIDDEN_TOOL_IDENTIFIER = (
    r"(?:"
    r"(?:get|list|read|fetch)_[a-z0-9_]*"
    r"(?:account|balance|buying_power|portfolio|pnl|profit_loss|position|"
    r"transaction|order|scan|watchlist)[a-z0-9_]*"
    r"|review_[a-z0-9_]*order[a-z0-9_]*"
    r"|(?:buy|cancel|modify|place|queue|sell|stage|submit)_[a-z0-9_]*order[a-z0-9_]*"
    r"|(?:create|update|delete|modify)_[a-z0-9_]*(?:scan|watchlist)[a-z0-9_]*"
    r"|[a-z0-9_]*(?:exercise|assignment|mutation)[a-z0-9_]*"
    r"|(?:add|create|delete|modify|mutate|remove|set|update|write)_[a-z0-9_]+"
    r")"
)

_PRIVATE_CAPABILITY = (
    r"(?:account(?:s| ids?| identifiers?| numbers?)?|buying power|balances?|"
    r"portfolios?|holdings?|brokerage p(?:&|/)l|p(?:&|/)l|pnl|profit[- ]and[- ]loss|"
    r"(?:realized|unrealized) (?:p&l|profit|loss)|positions?|transactions?|"
    r"trade history|order history|orders?|(?:(?:brokerage|mcp|robinhood)\s+)?"
    r"scanners?|(?:brokerage|mcp|robinhood)\s+scans?|watchlists?|"
    r"mutation (?:capabilities?|tools?)|private brokerage data)"
)

_AFFIRMATIVE_PATTERNS = (
    re.compile(
        rf"\b(?:call|enable|execute|invoke|query|run|use)\b[^.!?;]{{0,100}}"
        rf"\b{_FORBIDDEN_TOOL_IDENTIFIER}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:access|call|check|collect|create|display|download|enable|export|fetch|"
        rf"get|identify|inspect|invoke|list|load|log|obtain|open|print|pull|query|"
        rf"read|record|report|request|retrieve|return|save|show|use|view|write)\b"
        rf"[^.!?;]{{0,120}}\b{_PRIVATE_CAPABILITY}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![-_])\b(?:buy|cancel|modify|perform|place|prepare|queue|review|sell|"
        r"stage|submit)\b[^.!?;]{0,100}\b(?:(?:brokerage|equity|option)\s+)?"
        r"(?:order review|orders?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![-_])\b(?:assign|exercise)\b[^.!?;]{0,100}"
        r"\b(?:calls?|contracts?|options?|puts?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![-_])\b(?:call|create|enable|invoke|list|modify|retrieve|run|update|"
        r"use)\b[^.!?;]{0,100}\b(?:(?:brokerage|mcp|robinhood)\s+scans?|"
        r"scanners?(?:\s+tools?)?|watchlists?)\b",
        re.IGNORECASE,
    ),
)

_NEGATION = re.compile(
    r"\b(?:do not|never|must not|may not|cannot|without|prohibited|forbidden|"
    r"disallowed|not authorized|not permitted|not allowed|refuse|fail if)\b",
    re.IGNORECASE,
)

_BLOCK_START = re.compile(r"^(?:#{1,6}\s+|[-*]\s+|\d+[.)]\s+)")
_BLOCK_MARKER = re.compile(r"^(?:#{1,6}\s+|[-*]\s+|\d+[.)]\s+)")
_CLAUSE_SPLIT = re.compile(
    r"(?<=[.!?;])\s+|,\s*(?:and\s+)?then\s+|\s+(?:but|(?:and\s+)?then)\s+",
    re.IGNORECASE,
)

_RUNNER_INSTRUCTION = re.compile(
    r"\b(?:execute|invoke|launch|run|schedule|start)\b[^.!?;]{0,100}"
    r"\b(?:scripts/)?run_shadow(?:_session)?\.sh\b",
    re.IGNORECASE,
)


def _shadow_server() -> dict[str, object]:
    with SHADOW_CONFIG.open("rb") as handle:
        document = tomllib.load(handle)
    return document["mcp_servers"]["robinhood-trading"]


def _markdown_clauses(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []

    def finish_block() -> None:
        if current:
            blocks.append(" ".join(current))
            current.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            finish_block()
            continue
        if _BLOCK_START.match(line):
            finish_block()
        current.append(_BLOCK_MARKER.sub("", line, count=1))
    finish_block()

    return [
        clause.strip()
        for block in blocks
        for clause in _CLAUSE_SPLIT.split(block)
        if clause.strip()
    ]


def _is_affirmative_prohibited_instruction(clause: str) -> bool:
    for pattern in _AFFIRMATIVE_PATTERNS:
        for match in pattern.finditer(clause):
            context = clause[max(0, match.start() - 80) : match.end()]
            if _NEGATION.search(context) is None:
                return True
    return False


def test_shadow_server_is_disabled_by_default() -> None:
    assert _shadow_server()["enabled"] is False


def test_enabled_tools_are_exact_public_market_data_allowlist() -> None:
    enabled_tools = _shadow_server()["enabled_tools"]

    assert isinstance(enabled_tools, list)
    assert all(isinstance(tool, str) for tool in enabled_tools)
    assert enabled_tools == list(ALLOWED_TOOLS)


def test_enabled_tools_contain_no_prohibited_capability() -> None:
    enabled_tools = _shadow_server()["enabled_tools"]
    assert isinstance(enabled_tools, list)

    violations: dict[str, list[str]] = {}
    for tool in enabled_tools:
        assert isinstance(tool, str)
        tokens = set(filter(None, re.split(r"[^a-z0-9]+", tool.casefold())))
        prohibited = sorted(tokens & PROHIBITED_TOOL_TOKENS)
        if prohibited:
            violations[tool] = prohibited

    assert not violations


def test_prompt_guard_distinguishes_prohibitions_from_unsafe_instructions() -> None:
    safe_clauses = (
        "Never call review_option_order.",
        "Save normalized snapshots without account numbers.",
        "Inspect visible tool names and fail if get_accounts is exposed.",
        "Run the local shadow scan.",
    )
    unsafe_clauses = (
        "Call review_option_order.",
        "Retrieve account balances.",
        "Print account IDs.",
        "Return account balances.",
        "Use an account tool.",
        "Enable portfolio reads.",
        "Check account balances.",
        "Load the portfolio.",
        "Open the portfolio.",
        "Retrieve portfolios.",
        "Pull current positions.",
        "View open positions.",
        "Obtain brokerage P/L.",
        "Use create_scan to build a scanner tool.",
        "Run create_scan.",
        "Run a Robinhood scan.",
        "Use the scanner.",
        "Cancel the option order.",
        "Exercise the option contract.",
        "Exercise a SPY call.",
        "Perform an order review.",
        "Run a scanner tool.",
        "Create a mutation tool.",
        "Use set_preferences.",
        "Never call review_option_order, then retrieve account balances.",
    )

    assert not any(
        _is_affirmative_prohibited_instruction(clause)
        for item in safe_clauses
        for clause in _markdown_clauses(item)
    )
    missed = [
        item
        for item in unsafe_clauses
        if not any(
            _is_affirmative_prohibited_instruction(clause)
            for clause in _markdown_clauses(item)
        )
    ]
    assert not missed


def test_prompts_never_affirmatively_instruct_prohibited_capabilities() -> None:
    prompt_paths = sorted(PROMPTS.rglob("*.md"))
    assert prompt_paths

    violations: list[str] = []
    for path in prompt_paths:
        text = path.read_text(encoding="utf-8")
        for clause in _markdown_clauses(text):
            if _is_affirmative_prohibited_instruction(clause):
                violations.append(f"{path.relative_to(ROOT)}: {clause}")

    assert not violations, "\n".join(violations)


def test_live_review_template_does_not_exist() -> None:
    assert not (PROMPTS / "04_live_review_TEMPLATE.md").exists()


def test_agents_contract_is_shadow_only_and_public_market_data_only() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Current phase: SHADOW-ONLY." in text
    assert "Only public market-data retrieval is permitted." in text
    assert "Portfolio, brokerage P&L, position, transaction, order, or order-history reads." in text
    assert "Order review, placement, modification, cancellation, exercise" in text
    assert "Use Robinhood read-only account" not in text
    assert "Use `review_option_order` only" not in text


def test_readme_keeps_scanner_quarantined_after_allowlist_configuration() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(text.casefold().split())

    allowlist_heading = "## 2. configure least privilege before login or scanning"
    login_heading = "## 3. authenticate only after the allowlist is verified"
    assert text.casefold().index(allowlist_heading) < text.casefold().index(login_heading)
    assert "scanner remains disabled until the deterministic-runner remediation" in normalized
    assert "current shell runners are not approved for regular" in normalized

    runner_violations: list[str] = []
    for clause in _markdown_clauses(text):
        for match in _RUNNER_INSTRUCTION.finditer(clause):
            context = clause[max(0, match.start() - 80) : match.end()]
            if _NEGATION.search(context) is None:
                runner_violations.append(clause)
    assert not runner_violations


def test_daily_shadow_prompt_is_quarantined_pending_runner_remediation() -> None:
    text = (PROMPTS / "03_daily_shadow.md").read_text(encoding="utf-8")
    normalized = " ".join(text.casefold().split())

    assert normalized.startswith("# quarantined shadow-scan specification")
    assert "do not execute this prompt or use it to initiate a scan" in normalized
    assert "deterministic-runner remediation" in normalized


def test_gitignore_protects_runtime_and_private_local_data() -> None:
    lines = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {
        "logs/*",
        "!logs/.gitkeep",
        "data/*",
        "!data/.gitkeep",
        ".env",
        ".env.*",
        "!.env.example",
        ".envrc",
        "credentials",
        "credentials/",
        "credentials.*",
        "secret",
        "secret.*",
        "secrets/",
        "secrets.*",
        "token",
        "token.*",
        "token.json",
        "tokens.json",
        "tokens/",
        "tokens.*",
        "oauth",
        "oauth.*",
        ".codex/",
    }

    assert required <= lines
