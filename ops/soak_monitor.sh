#!/usr/bin/env bash
set -euo pipefail

# Read-only production soak observer. It never starts scans or changes bot state.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
CONTAINER=${CONTAINER:-alphahunter}
DURATION_S=${1:-86400}
INTERVAL_S=${INTERVAL_S:-60}
LOG_FILE=${2:-"$APP_DIR/ops/soak-$(date -u +%Y%m%dT%H%M%SZ).log"}
mkdir -p "$(dirname "$LOG_FILE")"
end=$(( $(date +%s) + DURATION_S ))

printf 'started_at=%s duration_s=%s interval_s=%s container=%s\n' "$(date -Is)" "$DURATION_S" "$INTERVAL_S" "$CONTAINER" >> "$LOG_FILE"
while [ "$(date +%s)" -lt "$end" ]; do
  now=$(date -Is)
  health=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || printf 'missing')
  restarts=$(docker inspect --format '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || printf 'unknown')
  stats=$(docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' "$CONTAINER" 2>/dev/null || printf 'unknown unknown')
  http=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8010/login 2>/dev/null || printf '000')
  disk=$(df -P "$APP_DIR" | awk 'NR==2 {print $5}')
  integrity=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("pragma integrity_check").fetchone()[0])' 2>/dev/null || printf 'unknown')
  bot=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("select status from bot_state where id=1").fetchone()[0])' 2>/dev/null || printf 'unknown')
  printf '%s health=%s restart=%s cpu_mem=%s http=%s disk=%s sqlite=%s bot=%s\n' "$now" "$health" "$restarts" "$stats" "$http" "$disk" "$integrity" "$bot" >> "$LOG_FILE"
  sleep "$INTERVAL_S"
done
printf 'finished_at=%s\n' "$(date -Is)" >> "$LOG_FILE"
