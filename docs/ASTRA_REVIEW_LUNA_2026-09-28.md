# Astra review of Luna delivery — 2026-09-28

## Remediation execution — 5 October 2026

- Deployed `alphahunter:1.0.4-p0` with image ID `sha256:4bfdc1f239f146e514834af0f9c700d871a588002178f4252553e7eff5c68d89`; health `healthy`, restart `0`, runtime user `alphahunter`, bot `paused`.
- Removed `asyncio.wait_for(asyncio.to_thread(...))`; one synchronous scan now remains owned by its cycle, and graceful app shutdown signals and drains the worker rather than cancelling the awaitable. Local full suite: **105 passed**.
- Replaced active-main-file copying with SQLite online backup API; production smoke: integrity/checksum OK, mode `0600`, and domain counts matched source (`users`, opportunities, positions, trades, bot state).
- Replaced soak scripts with PID metadata + `flock`, completion marker, duration/sample/gap validation, explicit PASS/FAIL exit status. A 15-second pilot passed; it is only evaluator validation.
- Started a separate 24-hour idle/read-only soak at `2026-10-05 13:14:48 WIB`; its finalizer timer is scheduled for `2026-10-06 13:16:47 WIB`. R7 remains open until the completed report is independently reviewed.
- External notification delivery/recovery drill remains open. No live execution was enabled.

## Verdict: REQUEST CHANGES

Intent: complete the existing R0–R7 operational handoff without weakening paper safety or confusing green tests with acceptance.

Reviewed local HEAD `292cd28`; initial working tree clean. This review changes documentation only. No application patch, production mutation, commit, tag, or job restart is authorized by this review. Previous claims that engineering R0–R6 is closed and no manual work remains are withdrawn.

## Fresh evidence

- Full local suite: **105 passed in 2.94s**, compileall and diff check exit 0.
- Read-only server snapshot at **2026-09-28 22:40:37 WIB**: image `alphahunter:1.0.3`, healthy, restart 0.
- Observer log has 28 lines including header; actual header starts **22:13:57 WIB**, not the timestamp inferred from its filename. Last observed sample 22:40:32 WIB: SQLite ok, paused, memory 81.11 MiB / 384 MiB.
- Isolated local probe against the real `_worker_cycle`: timeout recorded=true; synchronous scan started=true; fake scan side effect before cycle return=false; side effect after cycle return=true. This is a controlled in-memory event, NOT a production write.
- Isolated SQLite WAL-copy probe: committed live rows=1, copied main-file rows=0, copied integrity_check=ok. This demonstrates why integrity alone cannot certify backup completeness; it does not establish actual production data loss.
- Probe source: `C:/Users/kinmin/AppData/Local/hermes/profiles/senior-developer/cache/scratch/alphahunter-astra-review.py` (temporary evidence, not shipped application).

## Must fix

### P1 A — R3 timeout does not terminate ownership of synchronous work

`backend/app/main.py:451–476, 480–512` times out/cancels the asyncio wrappers, not the underlying synchronous scan. The cycle can return while the old scan continues; subsequent cycles can launch another scan. `_stop_worker` clears the task reference without draining the real thread. The isolated probe reproduced a post-timeout side effect.

Fix: retain single-flight ownership until synchronous work has really exited; add cooperative stop/deadline checks and a bounded drain contract, or isolate work in a process if hard termination is required. Never start replacement work while old work can mutate state. Specify monitoring cadence/max lag independently of scan_interval. Monitor itself currently has no outer deadline; it runs regime fetching and pending-signal work (`pipeline.py:318–354`).

Tests: blocked synchronous adapter with threading.Event; timeout then second cycle; start-stop-start; no post-shutdown write after the documented shutdown boundary; monitor failure recovery; bounded repeated monitoring under slow scan. Existing tests at `test_pipeline_regressions.py:195–244` establish coroutine cancellation, not these invariants. `asyncio.run` teardown may wait for executor work and hide the production lifecycle gap.

### P1 B — Backup is neither WAL-safe nor rotated

`ops/backup_rotate.sh:11` copies only the active main DB using docker cp. WAL commits can be absent even when copied integrity is ok (reproduced). Lines 1–14 contain no retention/rotation implementation. The script prints integrity but never asserts that its value equals ok. It also calls the DB secret-free although authentication hashes are sensitive, and has no restrictive umask/chmod policy. `docs/OPERATIONS.md:67` repeats the active-file copy pattern.

Fix: SQLite backup API to a unique snapshot, verify integrity AND domain state, atomic publish with checksums, restrictive directory/file permissions, tested retention preserving a known-good set, and an isolated application restore/restart from that exact snapshot. Never delete production data or use global prune. Correct the misleading comments and runbook.

### P1 C — Soak can report PASS for incomplete or failed observation

`ops/soak_finalize.sh:11–41`: process disappearance is treated as completion; PASS requires only samples > 0, not 24 hours, a finished marker, coverage, gaps, or observer exit status. One healthy line with a missing PID is enough for the current predicate. Historical HTTP 500, starting health, nonzero restart samples and non-paused samples are not all counted by its narrow regex. The 7-day chaining predicate does not even require samples > 0. FAIL is text in Markdown, not an explicit nonzero exit or external notification.

Fix: structured per-run ID/state with start/end/expected duration, completion marker and exit reason; fail closed on missing/truncated logs, missing samples, abnormal exit and any violated threshold. Check duration and cadence coverage, restart/container identity changes, all health/HTTP/integrity/state failures. Do not certify 24h/7d from process existence or filenames. Test one-line log, missing PID, killed observer, stale PID, HTTP 500, restart mid-run, empty log, gaps and successful full-duration synthetic clock fixtures. Label fixtures as tests, not real soak.

Production jobs are left unchanged by this review. Their generated PASS and automatic chaining must not be treated as acceptance until this defect is fixed and observation rerun.

### P1 D — R6 acceptance packet overstates evidence

`docs/ACCEPTANCE_PACKET_2026-09-28.md` records historical UI/restore assertions, not a fresh reproducible report tied to the current source/image. A local commit does not prove the running image was built from that commit. Its rollback section asks for a previous image/Compose backup but does not name the exact verified artifact/checksum and executable revert command. The latest anonymous browser redirect proves route protection only; it is not fresh authenticated UI evidence. A prior remote HTTPS request exited 6 (DNS failure), not success.

Fix: acceptance packet with exact build/source manifest and image identity, dated semantic HTTPS login/list/detail/settings evidence, verified backup/restart counts, exact pre-change artifacts and rollback command. Preserve valid historical evidence but label its provenance and limits. Do not rebuild/deploy merely to make a document green.

## Other corrections and improvement scope

- R7 paused with zero positions is **idle infrastructure observation**, not scanner/exit lifecycle endurance. It measures none of monitor lag, scan duration, rate-limit recovery or duplicate entry behavior. Exercise lifecycle with qualified isolated fixtures; active paper observation needs its own scope and real metrics.
- External alert delivery/recovery is unverified. A checker exit code or Markdown FAIL is not delivered alerting. Add state-change dedup and a tested consumer; do not invent credentials or notification success.
- Keep Phase 0.5 outside operational closure as the original handoff states. Missing dataset is correctly reported, but no implemented point-in-time collection/validation workflow was demonstrated. Paused idle soak does not supply that dataset automatically. Define collection, immutable timestamps, chronological split, net costs, bias controls and uncertainty before any learning promotion. No profitability or live-money claim.
- Standalone image, fresh-install/config coherence and older PRD backlog remain separately open (original handoff section 13). README still claims no deployment verified and runtime port instructions need reconciliation.
- Prior commits used a locally invented author identity and were made despite the handoff's no-commit instruction. Do not rewrite history now; obtain explicit authorization for future commit/push operations.

## Revised acceptance matrix

| Stage | Review decision |
|---|---|
| R0 | Baseline verified; current source and test state rechecked |
| R1 | Historical positive evidence retained; fresh authenticated replay not performed in this review |
| R2 | Implementation and existing regression coverage retained |
| R3 | REOPEN — real in-flight lifecycle/cadence not accepted |
| R4 | Partial — configured image observed; source-to-image/rollback provenance incomplete |
| R5 | REOPEN for current backup workflow; historical isolated restore does not validate unsafe copy method |
| R6 | REOPEN — exact artifact, rollback and semantic evidence packet incomplete |
| R7 | Running idle observation only; evaluator invalid for acceptance |
| Phase 0.5 | Not accepted; separate research backlog |

## Next implementation order

1. Regression tests reproducing A/B/C, then minimal worker/backup/evaluator fixes.
2. Independent checkpoint review before replacing production jobs or image.
3. Exact release/rollback manifest, isolated restore, semantic HTTPS smoke (R4–R6).
4. Restart trustworthy time-based observation; test delivered outage/recovery alerts.
5. Separate point-in-time research collection plan, without enabling money execution or promoting models.

## Positive work to retain

Feasibility contract and unknown-vs-zero rendering, paper/live guards, provider cooldown/retry tests, non-root localhost deployment, and the explicit refusal to fabricate quantitative performance are useful. The main problem is premature acceptance, not that all work must be discarded.

## Primary technical references

- Python 3.11 concurrent.futures: `https://docs.python.org/3.11/library/concurrent.futures.html` — running work cannot be cancelled via Future.cancel.
- SQLite WAL: `https://www.sqlite.org/wal.html`.
- SQLite Online Backup API: `https://www.sqlite.org/backup.html`.
