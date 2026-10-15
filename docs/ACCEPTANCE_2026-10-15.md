# AlphaHunter acceptance recovery — 2026-10-15

## Verified results

| Gate | Result | Evidence |
|---|---|---|
| 24-hour infrastructure | PASS | `ops/soak-24h-ui-20261008T070152Z.md` on server: 1440 samples, zero failed samples, final healthy/paused/integrity ok |
| Prior seven-day infrastructure | FAIL / interrupted | `ops/soak-7d-20261009T070416Z.md`: 5901 samples, 66 failed samples, missing completion marker. Host reboot interrupted transient observer and timer. Never count elapsed wall time as acceptance. |
| Clean installation | PASS | `ops/clean_smoke.py`, exit 0. Image `sha256:dad796ee55d9ff7e721c7653a48f9596b6f615c114f10020cd7f7d2797b42d44`; fresh volume `alphahunter-smoke-20261015T071001Z-data`; eight UI routes 200 with paper marker, anonymous API 401, integrity ok, one account, zero trades. Network disabled. Container stopped. |
| Online backup/application restore | PASS | `ops/restore_smoke.py`, exit 0. Isolated volume `alphahunter-restore-20261015T071327Z-data`; eight pages, API 401, integrity ok; counts match production: 1 account, 0 positions/trades, 6 opportunities, 78 wallets. No worker lifespan run. Container stopped. |
| Real provider scan | BLOCKED / insufficient coverage | Narrow scan against restored database only, no entries allowed. Public market calls work intermittently; GeckoTerminal returned HTTP 429 and cooldown blocked remaining swap ingestion. No trade evidence produced. Stop retrying rather than bypass safety. |
| Local regressions | PASS | 110 tests, exit 0. |
| Durable observer short drill | PASS | 60-second run, one sample, zero failures. Early evaluation correctly rejected while observer alive. Calendar units passed systemd verification (unrelated vendor spice-vdagent warning). Not a seven-day acceptance result. |

## Recovery changes

- `ops/schedule_soak.sh` installs named, enabled user units and an absolute UTC calendar deadline with `Persistent=true`.
- A reboot never resumes/merges a partial observation log: `ConditionPathExists=!<log>` prevents restart into an existing log. The persistent finalizer evaluates an interrupted observation as failure instead of disappearing.
- `ops/soak_finalize.sh` chains through durable scheduling and records followup scheduling failures explicitly.
- Fresh read-only seven-day observation: `alphahunter-soak-durable-20261015T071546Z`; finalizer deadline 2026-10-22 07:17:46 UTC (14:17:46 WIB). Infrastructure only; bot remains paused.
- No application image replacement, production trading activation, live execution, or safety-threshold relaxation.

## Rollback and artifacts

Server root: `/var/docker-apps/alphahunter`.
Backup of finalizer: `rollback/soak-recovery-20261015/soak_finalize.sh`.
MD5: `987bd2d55d6e9af29c51601bfe5ad1fa`.
Revert finalizer: `cp rollback/soak-recovery-20261015/soak_finalize.sh ops/soak_finalize.sh && bash -n ops/soak_finalize.sh` from server root. Disable only the exact durable service/timer above if reverting scheduler behavior. Stopped isolated containers/volumes and online backup are retained, not deleted.

## PRD limits

A healthy paused service is not autonomous trading acceptance. There are still no closed trades or chronological market snapshots/provider-call records in production. No strategy edge, forward wallet persistence, statistical model promotion, 90-day KPIs, or 500 events/second/1000-user SLO is established. Approved single-user SQLite/free-provider scope differs from full PRD scale and paid archival provider assumptions. Functional code audit is separate from endurance evidence.
