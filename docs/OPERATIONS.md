# AlphaHunter Operations Runbook

> **Review status (updated 5 October 2026):** `backup_rotate.sh` now uses SQLite's online backup API, applies `0700` backup-directory/`0600` backup-file permissions, verifies integrity, and retains the newest configurable seven backups. `soak_monitor.sh` is single-instance and logs run identity/epoch samples; `soak_finalize.sh` is fail-closed and requires completion, duration, sample coverage, and bounded gaps. Backups still contain authentication data. External notification delivery remains unverified, and a short pilot is not a 24-hour acceptance result.

AlphaHunter is a private, paper-only service. Do not enable live execution.

## Safety invariants

```text
TRADING_MODE=paper
ENABLE_LIVE_EXECUTION=false
bot state: paused during maintenance, restore, and acceptance checks
```

Never print `.env`, `operator-login.txt`, cookies, tokens, or passwords. Check only
presence/length when required.

## Health and read-only observation

```bash
cd /var/docker-apps/alphahunter
docker compose ps
docker inspect --format 'image={{.Config.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}}' alphahunter
docker stats --no-stream alphahunter
curl -fsS http://127.0.0.1:8010/app/dashboard >/dev/null
```

Run the 24-hour read-only observer:

```bash
cd /var/docker-apps/alphahunter
stamp=$(date -u +%Y%m%dT%H%M%SZ)
log="$PWD/ops/soak-24h-$stamp.log"
report="$PWD/ops/soak-24h-$stamp.md"
systemd-run --user --unit "alphahunter-soak-24h-$stamp" --collect \
  /bin/bash -lc "cd '$PWD' && INTERVAL_S=60 ./ops/soak_monitor.sh 86400 '$log'"
systemd-run --user --unit "alphahunter-soak-finalize-$stamp" --on-active=24h2min --collect \
  /bin/bash -lc "cd '$PWD' && PID_FILE='$PWD/ops/soak.pid' ./ops/soak_finalize.sh '$log' '$report'"
systemctl --user list-timers --all "alphahunter-soak-finalize-$stamp.timer"
```

The observer records health, restart count, CPU/memory, HTTP dashboard health, disk use,
SQLite integrity, and bot state. It never starts a scan or changes state.

The monitor owns a lock and records its PID, epoch range, interval, and run ID. The
finalizer is fail-closed: it rejects a live observer and only passes a completed run
with full duration, required sample coverage, bounded sample gaps, clean health/HTTP/
SQLite state, zero restarts, and a paused bot.

Run the safety alert check from cron/systemd:

```bash
ops/soak_check.sh ops/soak-<timestamp>.log
```

`soak_check.sh` is state-change-only: it sends one `stale_feed` Telegram alert on a
bad transition and one recovery alert when the service becomes healthy again. A failed
delivery leaves the state pending so the next check retries. Install the tracked
systemd user timer after configuring `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in
the protected server `.env`:

```bash
install -m 644 ops/systemd/alphahunter-safety-check.service ~/.config/systemd/user/
install -m 644 ops/systemd/alphahunter-safety-check.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now alphahunter-safety-check.timer
systemctl --user list-timers alphahunter-safety-check.timer
```

## Backup

Keep the bot paused before backup/restore work. Create a timestamped backup:

```bash
chmod 750 ops/backup_rotate.sh
ops/backup_rotate.sh
```

The script uses SQLite's online backup API, then writes a SHA-256 sidecar and
integrity result under `backups/`. It keeps the newest seven backups by default.
Preserve the matching three files together. Do not overwrite the live SQLite file
during a drill.

## Restore drill

1. Create a uniquely named Docker volume.
2. Copy one backup into that volume as `/data/alphahunter.db`.
3. Set the volume directory and database ownership to UID/GID `10001:10001`, mode
   `0700` for `/data` and `0600` for the database, so SQLite can create its WAL
   files without making the restored database world-readable.
4. Boot an isolated `alphahunter:<release>` container on an unused localhost port.
5. Verify `/login`, operator login, authenticated UI, API domain data, and
   `PRAGMA integrity_check`.
6. Remove only the named drill container and volume after evidence is captured.
7. Production remains untouched.

## Release/recreate

Before a production change:

```bash
ts=$(date -u +%Y%m%dT%H%M%SZ)
cp docker-compose.yml backups/compose-before-$ts.yml
ops/backup_rotate.sh
sha256sum backups/compose-before-$ts.yml
docker compose config --images
```

Deploy the explicitly selected image, then recreate without a one-off image override:

```bash
docker compose up -d --no-build
docker inspect --format 'image={{.Config.Image}} health={{.State.Health.Status}} restarts={{.RestartCount}}' alphahunter
```

Rollback is the inverse: restore the timestamped Compose file, select the previous
known image, recreate, and repeat health/authenticated UI/persistence checks.

## Acceptance limits

Green HTTP status, unit tests, or container health alone never proves authenticated
UI, populated opportunity rendering, recovery, profitability, or live readiness.
The service has no demonstrated positive out-of-sample edge. Paper P&L is not a
promise of executable market returns.
