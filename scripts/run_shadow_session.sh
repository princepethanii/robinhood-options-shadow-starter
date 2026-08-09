#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'BLOCKED: shadow scanner is quarantined pending deterministic-runner remediation.' >&2
exit 1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Shadow-only scanner. It launches one structured scan on each 5-minute boundary
# between 08:50 and 10:30 America/Chicago. It never places or cancels orders.
while true; do
  NOW_HM="$(TZ=America/Chicago date +%H%M)"
  DOW="$(TZ=America/Chicago date +%u)"

  if (( DOW > 5 )); then
    echo "Weekend in America/Chicago; exiting."
    exit 0
  fi
  if [[ "$NOW_HM" < "0850" ]]; then
    sleep 30
    continue
  fi
  if [[ "$NOW_HM" > "1030" ]]; then
    echo "Candidate window ended at 10:30 CT; exiting."
    exit 0
  fi

  "$ROOT/scripts/run_shadow.sh" || true

  # Advance to the next 5-minute wall-clock boundary.
  SECOND="$(TZ=America/Chicago date +%S)"
  MINUTE="$(TZ=America/Chicago date +%M)"
  WAIT=$(( (5 - (10#$MINUTE % 5)) * 60 - 10#$SECOND ))
  if (( WAIT <= 0 || WAIT > 300 )); then WAIT=300; fi
  sleep "$WAIT"
done
