#!/usr/bin/env bash
set -euo pipefail

# Create a WAL-consistent SQLite backup without changing bot state.
# Backups include operator authentication data: keep them private and off Git.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
CONTAINER=${CONTAINER:-alphahunter}
BACKUP_DIR=${BACKUP_DIR:-$APP_DIR/backups}
KEEP_BACKUPS=${KEEP_BACKUPS:-7}
umask 077
case "$KEEP_BACKUPS" in ''|*[!0-9]*) printf 'KEEP_BACKUPS must be a positive integer\n' >&2; exit 2;; esac
[ "$KEEP_BACKUPS" -gt 0 ] || { printf 'KEEP_BACKUPS must be positive\n' >&2; exit 2; }
install -d -m 700 "$BACKUP_DIR"
ts=$(date -u +%Y%m%dT%H%M%SZ)
out="$BACKUP_DIR/db-$ts.db"
tmp="$BACKUP_DIR/.db-$ts-$$.tmp"
container_tmp="/data/.alphahunter-backup-$ts-$$.db"
cleanup() {
  docker exec "$CONTAINER" rm -f "$container_tmp" >/dev/null 2>&1 || true
  rm -f "$tmp"
}
trap cleanup EXIT

# sqlite3.Connection.backup includes committed WAL pages; copying only the main
# database file does not. The temporary file stays inside the named volume until
# its integrity has been verified, then is copied atomically to the host.
docker exec "$CONTAINER" python -c '
import sqlite3, sys
source, destination = sys.argv[1:]
src = sqlite3.connect(source)
dst = sqlite3.connect(destination)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
' /data/alphahunter.db "$container_tmp"
integrity=$(docker exec "$CONTAINER" python -c 'import sqlite3, sys; print(sqlite3.connect(sys.argv[1]).execute("pragma integrity_check").fetchone()[0])' "$container_tmp")
[ "$integrity" = ok ] || { printf 'backup integrity check failed: %s\n' "$integrity" >&2; exit 1; }
docker cp "$CONTAINER:$container_tmp" "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$out"
sha256sum "$out" > "$out.sha256"
printf '%s\n' "$integrity" > "$out.integrity"

rotated=0
while IFS= read -r stale; do
  [ -n "$stale" ] || continue
  rm -f -- "$stale" "$stale.sha256" "$stale.integrity"
  rotated=$((rotated + 1))
done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'db-*.db' -printf '%T@ %p\n' | sort -nr | awk -v keep="$KEEP_BACKUPS" 'NR > keep {sub(/^[^ ]+ /, ""); print}')

trap - EXIT
docker exec "$CONTAINER" rm -f "$container_tmp"
printf 'backup=%s\nchecksum=%s\nintegrity=%s\nrotated=%s\n' "$out" "$(cut -d' ' -f1 "$out.sha256")" "$integrity" "$rotated"
