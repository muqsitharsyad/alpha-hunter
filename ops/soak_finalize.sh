#!/usr/bin/env bash
set -euo pipefail

# Finalize a running soak without changing application state.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
PID_FILE=${PID_FILE:-$APP_DIR/ops/soak.pid}
LOG_FILE=${1:?usage: soak_finalize.sh LOG_FILE}
OUT_FILE=${2:-$APP_DIR/ops/soak-final-$(date -u +%Y%m%dT%H%M%SZ).md}

while [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; do
  sleep 60
done

health=$(docker inspect --format '{{.State.Health.Status}}' alphahunter 2>/dev/null || printf 'missing')
restarts=$(docker inspect --format '{{.RestartCount}}' alphahunter 2>/dev/null || printf 'unknown')
integrity=$(docker exec alphahunter python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("pragma integrity_check").fetchone()[0])' 2>/dev/null || printf 'unknown')
bot=$(docker exec alphahunter python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("select status from bot_state where id=1").fetchone()[0])' 2>/dev/null || printf 'unknown')
samples=$(grep -Ec ' health=' "$LOG_FILE" || true)
failures=$(grep -Eic 'health=(unhealthy|missing)|sqlite=(unknown|corrupt)|http=000' "$LOG_FILE" || true)

cat > "$OUT_FILE" <<EOF
# AlphaHunter Soak Result

- Completed at: $(date -Is)
- Log: $LOG_FILE
- Samples: $samples
- Failed samples: $failures
- Final health: $health
- Final restart count: $restarts
- Final SQLite integrity: $integrity
- Final bot state: $bot

## Decision

$(if [ "$samples" -gt 0 ] && [ "$failures" -eq 0 ] && [ "$health" = healthy ] && [ "$restarts" = 0 ] && [ "$integrity" = ok ] && [ "$bot" = paused ]; then printf 'PASS: read-only soak safety criteria met.'; else printf 'FAIL: operator review required.'; fi)
EOF
rm -f "$PID_FILE"
printf 'result=%s samples=%s failures=%s\n' "$OUT_FILE" "$samples" "$failures"
