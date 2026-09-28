# AlphaHunter Operations Runbook

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
curl -fsS http://127.0.0.1:8010/login >/dev/null
```

Run the 24-hour read-only observer:

```bash
chmod 750 ops/soak_monitor.sh
nohup ops/soak_monitor.sh 86400 > ops/soak-launch.log 2>&1 &
echo $! > ops/soak.pid
```

The observer records health, restart count, CPU/memory, HTTP login health, disk use,
SQLite integrity, and bot state. It never starts a scan or changes state.

Finalize automatically after the observer exits:

```bash
nohup ops/soak_finalize.sh ops/soak-<timestamp>.log > ops/soak-finalize.log 2>&1 &
```

The finalizer writes a secret-free result file and removes `ops/soak.pid`.

Run the safety alert check from cron/systemd:

```bash
ops/soak_check.sh ops/soak-<timestamp>.log
```

## Backup

Keep the bot paused before backup/restore work. Create a timestamped backup:

```bash
chmod 750 ops/backup_rotate.sh
ops/backup_rotate.sh
```

The script writes the SQLite file, SHA-256 sidecar, and integrity result under
`backups/`. Preserve the matching three files together. Do not overwrite the live
SQLite file during a drill.

## Restore drill

1. Create a uniquely named Docker volume.
2. Copy one backup into that volume as `/data/alphahunter.db`.
3. Boot an isolated `alphahunter:<release>` container on an unused localhost port.
4. Verify `/login`, operator login, authenticated UI, API domain data, and
   `PRAGMA integrity_check`.
5. Remove only the named drill container and volume after evidence is captured.
6. Production remains untouched.

## Release/recreate

Before a production change:

```bash
ts=$(date -u +%Y%m%dT%H%M%SZ)
cp docker-compose.yml backups/compose-before-$ts.yml
docker cp alphahunter:/data/alphahunter.db backups/db-before-$ts.db
sha256sum backups/compose-before-$ts.yml backups/db-before-$ts.db
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
