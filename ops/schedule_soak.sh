#!/usr/bin/env bash
set -euo pipefail
# Durable units: retain the finalizer across host reboots; never resume a partial log.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
DURATION_S=${1:-604800}
INTERVAL_S=${INTERVAL_S:-60}
case "$DURATION_S:$INTERVAL_S" in *[!0-9:]*|:*|*::*) exit 2;; esac
[ "$DURATION_S" -gt 0 ] && [ "$INTERVAL_S" -gt 0 ]
cd "$APP_DIR"
[ ! -f ops/soak.pid ] || { printf 'Existing observer PID file needs review\n' >&2; exit 3; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="alphahunter-soak-durable-$stamp"
log="$APP_DIR/ops/$name.log"
report="$APP_DIR/ops/$name.md"
units="$HOME/.config/systemd/user"
mkdir -p "$units"
deadline=$(date -u -d "@$(( $(date +%s) + DURATION_S + 120 ))" '+%Y-%m-%d %H:%M:%S UTC')
# Paths are operator-controlled and must not contain unit-file metacharacters.
case "$APP_DIR" in *[\ \%\"\\]*) exit 2;; esac
printf '[Unit]\nDescription=AlphaHunter single uninterrupted soak\nConditionPathExists=!%s\n[Service]\nType=simple\nWorkingDirectory=%s\nEnvironment=INTERVAL_S=%s\nExecStart=/bin/bash %s/ops/soak_monitor.sh %s %s\nRestart=no\n[Install]\nWantedBy=default.target\n' "$log" "$APP_DIR" "$INTERVAL_S" "$APP_DIR" "$DURATION_S" "$log" > "$units/$name.service"
printf '[Unit]\nDescription=AlphaHunter fail-closed soak evaluation\n[Service]\nType=oneshot\nWorkingDirectory=%s\nEnvironment=CHAIN_7D=0\nExecStart=/bin/bash %s/ops/soak_finalize.sh %s %s\n' "$APP_DIR" "$APP_DIR" "$log" "$report" > "$units/$name-finalize.service"
printf '[Unit]\nDescription=AlphaHunter durable evaluation deadline\n[Timer]\nOnCalendar=%s\nPersistent=true\nUnit=%s-finalize.service\n[Install]\nWantedBy=timers.target\n' "$deadline" "$name" > "$units/$name-finalize.timer"
systemctl --user daemon-reload
systemctl --user enable --now "$name.service" "$name-finalize.timer"
printf 'monitor=%s.service\nfinalizer=%s-finalize.timer\nlog=%s\nreport=%s\ndeadline=%s\n' "$name" "$name" "$log" "$report" "$deadline"
