#!/usr/bin/env bash
set -euo pipefail

# Secret-free readiness check for cron/systemd monitoring.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
CONTAINER=${CONTAINER:-alphahunter}
NOTIFY_CONTAINER=${NOTIFY_CONTAINER:-$CONTAINER}
LOG_FILE=${1:-}
STATE_FILE=${STATE_FILE:-$APP_DIR/ops/soak_alert.state}

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

current_state=clear
[ "$bad" -eq 0 ] || current_state=alert
previous_state=$(cat "$STATE_FILE" 2>/dev/null || printf 'unknown')

send_notification() {
  local text=$1
  docker exec "$NOTIFY_CONTAINER" python -c \
    'from app.notify import notify; import sys; sys.exit(0 if notify("stale_feed", sys.argv[1], force=True) else 1)' \
    "$text"
}

if [ "$current_state" = alert ]; then
  message="AlphaHunter health alert: health=$health restart=$restarts sqlite=$integrity bot=$bot"
  if [ "$previous_state" != alert ]; then
    send_notification "$message" && printf 'alert\n' > "$STATE_FILE" || printf 'notification_delivery=failed\n' >&2
  fi
  printf 'alert_state=alert\n'
  exit 1
fi

if [ "$previous_state" = alert ]; then
  send_notification "AlphaHunter health recovered: health=$health restart=$restarts sqlite=$integrity bot=$bot" \
    && printf 'clear\n' > "$STATE_FILE" || { printf 'notification_delivery=failed\n' >&2; exit 1; }
else
  printf 'clear\n' > "$STATE_FILE"
fi
printf 'alert_state=clear\n'
