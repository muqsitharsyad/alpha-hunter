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

## Functional audit: REQUEST CHANGES

Independent read-only review found the following remaining engineering gates. Unit-suite success did not establish these paths. Keep new entries paused until safety repairs are verified and deployed.

1. Historical wallet qualification: current-scan-only performance features cannot establish repeatable history; missing early/exit/drawdown evidence limits score. OPEN.
2. Reputation/consensus: tier must derive from time-decayed, minimum-sample-qualified reputation; consensus must exclude unqualified wallets. PARTIAL local repair; reputation still OPEN.
3. Security provider: free market metadata does not attest sellability, holder concentration, LP lock, authority status. External evidence adapter OPEN. UNKNOWN must remain blocked.
4. Provider scheduling: pool scan plus monitoring exceeds request budget; add fair deferred work and reserve exit quota. OPEN. Observed actual HTTP 429 in isolated scan.
5. In-flight pause/emergency: revalidate BotState in the fill transaction. ACTIVE REPAIR.
6. Smart exits and operator close: fresh price+depth based manual close ACTIVE REPAIR; qualified distribution/security/liquidity/crash smart-exit wiring OPEN.
7. Effective daily-loss cap: use operator limit consistently for entry/emergency. ACTIVE REPAIR.
8. Forward outcomes: freeze qualified cohort and detection-time price, bound maturity quote delay and retain missing outcomes. PARTIAL local repair; schema/provenance/coverage still OPEN.
9. Read-only collection/replay: separate ingestion from entry authorization, retain source provenance and complete immutable decision input. OPEN.
10. Automatic learning: exit transaction must write idempotent LearningEvent linked to actual trade and entry evidence. ACTIVE REPAIR.

These are blockers to full PRD functional acceptance, not excuses to weaken security gates, invent historical data, force demo fills into production, or claim strategy profitability.

## Functional safety release — verified and deployed

- New fill requires current running BotState, correct account and no emergency inside the write transaction. Safe-mode fills remain fail-closed until explicit safe-asset eligibility is implemented; safe mode is not an unrestricted entry permission.
- Exit writes a LearningEvent linked to the actual trade ID atomically, with frozen entry features/regime. Retries do not duplicate a fill/event. Historical positions without frozen context remain unknown.
- Entry gate and emergency evaluation now use the effective operator daily-loss cap, including boundary equality.
- Manual close fetches fresh executable pool price/depth and can close safely; missing/stale quote still returns 409. Each persisted close is idempotent. It is not an all-or-nothing multi-position transaction.
- Consensus/forward cohort excludes non-S/A wallets; forward baseline uses detection price, not the whale's earlier fill. Broader provenance and maturity/coverage requirements remain open.
- Full suite: **161 passed**; alternate focused-module order: **77 passed**; compileall and diff check passed. Fixture isolation for E2E explanations was corrected to assert only opportunities emitted by that scan.
- Image: `alphahunter:1.0.6-functional-safety`, digest `sha256:8abe5b9f9993cce753b8e98f431225674e3a73603c73a0498912a7f1fb009c0e`. Durable selection is installed as `docker-compose.override.yml`, not a shell-only image override.
- Running image SHA256 hashes for all five changed application files match local source. Health healthy, restart 0, non-root, paper-only, paused, SQLite integrity ok. External HTTPS dashboard 200; anonymous API 401.
- Clean install and online application restore were repeated successfully with this exact new image. Isolated containers: `alphahunter-smoke-20261015T072708Z`, `alphahunter-restore-20261015T072734Z`; both stopped, volumes retained.
- Rollback: `rollback/functional-safety-20261015/REVERT.sh`; source checksum manifests and pre-release SQLite online backup retained. Run `bash rollback/functional-safety-20261015/REVERT.sh` from server root. It selects a preserved prior image without rebuilding and preserves the live database.
- Previous fresh seven-day observer was superseded by this image deployment. Current durable **24-hour** observation: `alphahunter-soak-durable-20261015T072757Z`, finalizer 2026-10-16 14:29:57 WIB; `CHAIN_7D=1` verified in the persistent service configuration. First real sample clean. Not a PASS; only actual 24-hour PASS schedules the seven-day observation.
