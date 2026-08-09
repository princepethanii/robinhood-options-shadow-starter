#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'BLOCKED: shadow scanner is quarantined pending deterministic-runner remediation.' >&2
exit 1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DATE="$(TZ=America/Chicago date +%F)"
TIME="$(TZ=America/Chicago date +%H%M%S)"
mkdir -p "logs/${DATE}"

codex exec --ephemeral \
  --output-schema schemas/signal.schema.json \
  -o "logs/${DATE}/${TIME}-shadow.json" \
  "$(cat prompts/03_daily_shadow.md)"
