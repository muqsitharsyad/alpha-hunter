#!/usr/bin/env bash
set -euo pipefail

# Create a secret-free SQLite backup without changing bot state.
APP_DIR=${APP_DIR:-/var/docker-apps/alphahunter}
CONTAINER=${CONTAINER:-alphahunter}
BACKUP_DIR=${BACKUP_DIR:-$APP_DIR/backups}
mkdir -p "$BACKUP_DIR"
ts=$(date -u +%Y%m%dT%H%M%SZ)
out="$BACKUP_DIR/db-$ts.db"
docker cp "$CONTAINER:/data/alphahunter.db" "$out"
sha256sum "$out" > "$out.sha256"
docker run --rm --user 10001:10001 -v "$out:/data/restore.db:ro" alphahunter:1.0.3 python -c 'import sqlite3; print(sqlite3.connect("/data/restore.db").execute("pragma integrity_check").fetchone()[0])' > "$out.integrity"
printf 'backup=%s\nchecksum=%s\nintegrity=%s\n' "$out" "$(cut -d' ' -f1 "$out.sha256")" "$(cat "$out.integrity")"
