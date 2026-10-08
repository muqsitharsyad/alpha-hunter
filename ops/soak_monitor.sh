#!/usr/bin/env bash
set -euo pipefail

# Read-only, single-instance soak observer. It never starts scans or mutates bot state.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
CONTAINER=${CONTAINER:-alphahunter}
DURATION_S=${1:-86400}
INTERVAL_S=${INTERVAL_S:-60}
LOG_FILE=${2:-"$APP_DIR/ops/soak-$(date -u +%Y%m%dT%H%M%SZ).log"}
PID_FILE=${PID_FILE:-$APP_DIR/ops/soak.pid}
LOCK_FILE=${LOCK_FILE:-$APP_DIR/ops/soak.lock}

case "$DURATION_S:$INTERVAL_S" in *[!0-9:]*|:*|*::*) printf 'duration and interval must be positive integers\n' >&2; exit 2;; esac
[ "$DURATION_S" -gt 0 ] && [ "$INTERVAL_S" -gt 0 ] || { printf 'duration and interval must be positive\n' >&2; exit 2; }
mkdir -p "$(dirname "$LOG_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || { printf 'another soak observer owns %s\n' "$LOCK_FILE" >&2; exit 3; }

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
start_epoch=$(date +%s)
end_epoch=$((start_epoch + DURATION_S))
printf 'pid=%s start_epoch=%s end_epoch=%s duration_s=%s interval_s=%s run_id=%s log=%s\n' "$$" "$start_epoch" "$end_epoch" "$DURATION_S" "$INTERVAL_S" "$run_id" "$LOG_FILE" > "$PID_FILE"
cleanup() { rm -f "$PID_FILE"; }
interrupted() { printf 'aborted_at=%s epoch=%s run_id=%s\n' "$(date -Is)" "$(date +%s)" "$run_id" >> "$LOG_FILE"; exit 1; }
trap cleanup EXIT
trap interrupted INT TERM HUP

printf 'started_at=%s start_epoch=%s duration_s=%s interval_s=%s container=%s run_id=%s\n' "$(date -Is)" "$start_epoch" "$DURATION_S" "$INTERVAL_S" "$CONTAINER" "$run_id" >> "$LOG_FILE"
while [ "$(date +%s)" -lt "$end_epoch" ]; do
  epoch=$(date +%s)
  now=$(date -Is)
  health=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || printf 'missing')
  restarts=$(docker inspect --format '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || printf 'unknown')
  stats=$(docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' "$CONTAINER" 2>/dev/null || printf 'unknown unknown')
  # The private Tailscale UI intentionally redirects /login. Probe the actual
  # dashboard route so this observer validates the deployed UI rather than a
  # legacy authentication endpoint.
  http=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8010/app/dashboard 2>/dev/null || printf '000')
  disk=$(df -P "$APP_DIR" | awk 'NR==2 {print $5}')
  integrity=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("pragma integrity_check").fetchone()[0])' 2>/dev/null || printf 'unknown')
  bot=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("select status from bot_state where id=1").fetchone()[0])' 2>/dev/null || printf 'unknown')
  printf '%s epoch=%s health=%s restart=%s cpu_mem=%s http=%s disk=%s sqlite=%s bot=%s run_id=%s\n' "$now" "$epoch" "$health" "$restarts" "$stats" "$http" "$disk" "$integrity" "$bot" "$run_id" >> "$LOG_FILE"
  # Keep cadence anchored to the start of this sample. Sleeping a full interval
  # after Docker/SQLite probes drifts one or more seconds per cycle and makes a
  # healthy 24-hour run fail the required-sample check.
  remaining=$((end_epoch - $(date +%s)))
  [ "$remaining" -gt 0 ] || break
  next_due=$((epoch + INTERVAL_S))
  delay=$((next_due - $(date +%s)))
  [ "$delay" -gt 0 ] || delay=1
  [ "$delay" -le "$remaining" ] || delay=$remaining
  sleep "$delay"
done
printf 'finished_at=%s end_epoch=%s run_id=%s\n' "$(date -Is)" "$(date +%s)" "$run_id" >> "$LOG_FILE"