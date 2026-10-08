#!/usr/bin/env bash
set -euo pipefail

# Fail-closed soak evaluator. PASS requires a complete, gap-bounded observation.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
CONTAINER=${CONTAINER:-alphahunter}
PID_FILE=${PID_FILE:-$APP_DIR/ops/soak.pid}
LOG_FILE=${1:?usage: soak_finalize.sh LOG_FILE [REPORT_FILE]}
OUT_FILE=${2:-$APP_DIR/ops/soak-final-$(date -u +%Y%m%dT%H%M%SZ).md}
MAX_GAP_FACTOR=${MAX_GAP_FACTOR:-3}
CHAIN_7D=${CHAIN_7D:-1}

case "$MAX_GAP_FACTOR" in ''|*[!0-9]*) printf 'MAX_GAP_FACTOR must be a positive integer\n' >&2; exit 2;; esac
[ "$MAX_GAP_FACTOR" -gt 0 ] || { printf 'MAX_GAP_FACTOR must be positive\n' >&2; exit 2; }
case "$CHAIN_7D" in 0|1) ;; *) printf 'CHAIN_7D must be 0 or 1\n' >&2; exit 2;; esac

if [ -f "$PID_FILE" ]; then
  pid=$(awk -F'[ =]' '/^pid=/{print $2}' "$PID_FILE")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    printf 'observer pid=%s still running; refusing early finalization\n' "$pid" >&2
    exit 4
  fi
fi

reason=()
[ -s "$LOG_FILE" ] || reason+=("missing-log")
header=$(head -n 1 "$LOG_FILE" 2>/dev/null || true)
duration=$(printf '%s\n' "$header" | sed -n 's/.*duration_s=\([0-9][0-9]*\).*/\1/p')
interval=$(printf '%s\n' "$header" | sed -n 's/.*interval_s=\([0-9][0-9]*\).*/\1/p')
start=$(printf '%s\n' "$header" | sed -n 's/.*start_epoch=\([0-9][0-9]*\).*/\1/p')
run_id=$(printf '%s\n' "$header" | sed -n 's/.*run_id=\([^ ]*\).*/\1/p')
[ -n "$duration" ] && [ -n "$interval" ] && [ -n "$start" ] && [ -n "$run_id" ] || reason+=("invalid-header")
finished=$(grep 'finished_at=' "$LOG_FILE" 2>/dev/null | tail -n 1 || true)
end=$(printf '%s\n' "$finished" | sed -n 's/.*end_epoch=\([0-9][0-9]*\).*/\1/p')
[ -n "$end" ] || reason+=("missing-completion-marker")

samples=$(grep -c ' epoch=.* health=' "$LOG_FILE" 2>/dev/null || true)
# The UI probe must return a successful 2xx response. A 3xx redirect can hide
# an auth regression, while 4xx/5xx and curl's 000 are availability failures.
failures=$(grep -Eic 'health=(unhealthy|missing)|restart=([^0]|unknown)|sqlite=(unknown|corrupt)|http=(000|[13-9][0-9][0-9])|bot=(running|safe|idle|unknown)' "$LOG_FILE" 2>/dev/null || true)
if [ -n "$duration" ] && [ -n "$interval" ] && [ -n "$start" ] && [ -n "$end" ]; then
  [ "$end" -ge $((start + duration)) ] || reason+=("short-duration")
  expected=$((duration / interval))
  [ "$expected" -lt 1 ] && expected=1
  [ "$samples" -ge "$expected" ] || reason+=("insufficient-samples")
  max_gap=$((interval * MAX_GAP_FACTOR))
  previous=0
  while IFS= read -r epoch; do
    if [ "$previous" -ne 0 ] && [ $((epoch - previous)) -gt "$max_gap" ]; then reason+=("sample-gap"); break; fi
    previous=$epoch
  done < <(sed -n 's/.* epoch=\([0-9][0-9]*\) health=.*/\1/p' "$LOG_FILE")
fi
[ "$failures" -eq 0 ] || reason+=("failed-sample")
health=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || printf 'missing')
restarts=$(docker inspect --format '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || printf 'unknown')
integrity=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("pragma integrity_check").fetchone()[0])' 2>/dev/null || printf 'unknown')
bot=$(docker exec "$CONTAINER" python -c 'import sqlite3; print(sqlite3.connect("/data/alphahunter.db").execute("select status from bot_state where id=1").fetchone()[0])' 2>/dev/null || printf 'unknown')
[ "$health" = healthy ] || reason+=("final-health")
[ "$restarts" = 0 ] || reason+=("final-restarts")
[ "$integrity" = ok ] || reason+=("final-integrity")
[ "$bot" = paused ] || reason+=("final-bot-state")

status=PASS
[ "${#reason[@]}" -eq 0 ] || status=FAIL
{
  printf '# AlphaHunter Soak Result\n\n'
  printf -- '- Decision: **%s**\n- Evaluated at: %s\n- Log: %s\n- Run ID: %s\n- Duration requested: %ss\n- Samples: %s\n- Failed samples: %s\n- Final health: %s\n- Final restart count: %s\n- Final SQLite integrity: %s\n- Final bot state: %s\n' "$status" "$(date -Is)" "$LOG_FILE" "${run_id:--}" "${duration:--}" "$samples" "$failures" "$health" "$restarts" "$integrity" "$bot"
  printf -- '- Reasons: %s\n' "$(IFS=,; printf '%s' "${reason[*]:-none}")"
} > "$OUT_FILE"
rm -f "$PID_FILE"
printf 'result=%s status=%s samples=%s failures=%s\n' "$OUT_FILE" "$status" "$samples" "$failures"
if [ "$status" = PASS ] && [ "$CHAIN_7D" = 1 ]; then
  chain_stamp=$(date -u +%Y%m%dT%H%M%SZ)
  next_log="$APP_DIR/ops/soak-7d-$chain_stamp.log"
  next_report="$APP_DIR/ops/soak-7d-$chain_stamp.md"
  monitor_unit="alphahunter-soak-7d-$chain_stamp"
  finalizer_unit="alphahunter-soak-finalize-7d-$chain_stamp"
  systemd-run --user --unit "$monitor_unit" --collect /bin/bash -lc \
    "cd '$APP_DIR' && INTERVAL_S=60 ./ops/soak_monitor.sh 604800 '$next_log'"
  systemd-run --user --unit "$finalizer_unit" --on-active=7d2min --collect /bin/bash -lc \
    "cd '$APP_DIR' && PID_FILE='$APP_DIR/ops/soak.pid' CHAIN_7D=0 ./ops/soak_finalize.sh '$next_log' '$next_report'"
  printf 'followup=7d monitor=%s.service finalizer=%s.timer log=%s\n' "$monitor_unit" "$finalizer_unit" "$next_log"
fi
[ "$status" = PASS ]