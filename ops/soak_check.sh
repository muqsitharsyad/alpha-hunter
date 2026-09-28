#!/usr/bin/env bash
set -euo pipefail

# Secret-free readiness check for cron/systemd monitoring.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
CONTAINER=${CONTAINER:-alphahunter}
LOG_FILE=${1:-}

health=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || printf 'missing')
restarts=$(docker inspect --format '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || printf 'unknown')
integrity=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("pragma integrity_check").fetchone()[0])' 2>/dev/null || printf 'unknown')
bot=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("select status from bot_state where id=1").fetchone()[0])' 2>/dev/null || printf 'unknown')

printf 'health=%s restart=%s sqlite=%s bot=%s\n' "$health" "$restarts" "$integrity" "$bot"

bad=0
[ "$health" = healthy ] || bad=1
[ "$integrity" = ok ] || bad=1
[ "$bot" = paused ] || bad=1
case "$restarts" in 0) ;; *) bad=1 ;; esac

if [ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ]; then
  if grep -Eq 'health=(unhealthy|missing)|sqlite=(unknown|corrupt)' "$LOG_FILE" 2>/dev/null; then
    bad=1
  fi
fi

if [ "$bad" -ne 0 ]; then
  printf 'ALERT: AlphaHunter soak safety check failed\n' >&2
  exit 1
fi
printf 'alert_state=clear\n'
