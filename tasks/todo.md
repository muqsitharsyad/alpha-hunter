# AlphaHunter Completion Tracker

> Source of truth for unfinished implementation after the historical review documents were reconciled on 2026-10-08. Paper-only is mandatory; no task permits live execution.

## Completed and verified

- [x] Ledger core: atomic entry/exit, duplicate-position prevention, allocated entry cost, sell orders, stale/missing quote protection, and account reconciliation regression coverage.
- [x] Lightweight deployment: FastAPI/Jinja2, SQLite WAL, one non-root `python:3.11-slim` container, no Redis/Postgres/Node/ML runtime dependency.
- [x] WAL-safe backup, isolated restore drill, rollback artifacts, private Tailscale deployment, dashboard card, and state-change Telegram health notifications.
- [x] Private UI opt-in guard restricted to `.ts.net`; API remains bearer-protected.
- [x] Operator UI redesign: shared navigation, controls, tables, cards, forms, mobile layout, keyboard focus, and empty/error states were rebuilt as a dark data-first console. Verified locally with the private-UI test configuration and the full 110-test suite. The redesign preserves paper-only wording, unknown-value rendering, API bearer boundary, and the depth-required manual-close warning.

## Active work

### Task 1 — Fix exit execution realism and ledger presentation

**Acceptance**
- [x] Exit fill applies the same depth-based adverse slippage model as entry.
- [x] `PaperOrder` and `PaperTrade` retain intended price, simulated fill, exit slippage, and net fees consistently.
- [x] Pipeline passes current fresh pool depth into exit execution; manual close rejects absent/stale depth rather than inventing an executable fill.
- [x] Regression covers exit adverse slippage and persisted intended/fill evidence; existing regressions cover partial exits, final exit, and cost allocation.

### Task 2 — Fix persisted decision semantics

**Acceptance**
- [x] Token security score is derived from token-health evidence, not a constant.
- [x] Persisted whale value uses actual observed whale flow, never the bot's own intended order size.
- [ ] Unknown evidence stays explicitly unknown or blocks qualification; historical ambiguity is documented.

### Task 3 — Reconcile runtime configuration and operator documentation

**Acceptance**
- [x] SQLite is the actual default everywhere; no removed PostgreSQL/Redis default remains.
- [ ] README reflects deployed private Tailscale/no-login mode, current image, health route, bootstrap behavior, and verified deployment limits.
- [ ] Historical documents are marked as historical rather than current operational instructions.

### Task 4 — Strengthen clean-install and release evidence

**Current state:** local shell remains without Docker, but the Docker-capable home server built and deployed the ledger batch on 2026-10-08 11:29 WIB. The image is healthy and external private dashboard/API checks pass. A separate isolated clean SQLite-volume smoke remains required before this acceptance task closes.

**Acceptance**
- [x] Fresh standalone image smoke and online-backup restore both passed on isolated SQLite volumes on 2026-10-15; eight UI routes, API 401, paper-only and integrity checked. See `docs/ACCEPTANCE_2026-10-15.md`.
- [x] Image digest, runtime user, health route, and rollback location are recorded without secrets.
- [x] Production deploy happened after Tasks 1–3 passed; a new 24-hour acceptance observation started at 2026-10-08 11:29 WIB.

### Task 5 — Quantitative research gate

**Acceptance**
- [ ] Point-in-time forward collection records source timestamp, provider provenance, decision, intended/simulated execution price, costs, no-trades, and outcome horizon.
- [ ] Rules/cost model are frozen before collection.
- [ ] Report includes coverage, dropouts, fees/slippage/latency, chronological split, uncertainty, and a go/no-go result.

**Blocker:** requires real chronological observation and adequate paper samples. It cannot be completed honestly from the current database (`0` trades, `0` market snapshots, `0` provider calls).

## Time-gated operations

- [x] UI/ledger release 24-hour infrastructure soak passed 2026-10-09: 1440 samples, zero failures. Report inspected 2026-10-15.
- [ ] Chained seven-day observation failed/interrupted by host reboot: 5901 samples, 66 failures, no completion marker. Durable calendar scheduling deployed and short-drill verified. Fresh observer `alphahunter-soak-durable-20261015T071546Z` is running until 2026-10-22 ~14:18 WIB. This is infrastructure evidence only.
- [ ] The 7-day report and Phase 0.5 result cannot be marked complete before actual elapsed time and data exist.
