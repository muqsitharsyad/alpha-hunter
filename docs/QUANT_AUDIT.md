# AlphaHunter — quantitative and deployment audit (2026-09-24)

## Decision: paper-only; no demonstrated profitable edge

The current repository does **not** contain an out-of-sample backtest or a sufficiently long, real-time paper-trading sample. Synthetic tests verify accounting and gates, not expected returns. Do not use this system as a source of needed income or fund live-money orders based on the demo.

## Verified locally

- Synthetic regression suite runs, including cost/remaining-quantity accounting, idempotent entries, loss brakes, and fail-closed unverified token/wallet metadata.
- Four independent read-only audits (ledger, API/security, data/provider, quant) were rerun after
  the initial provider usage-limit failure. Their findings drove regression tests and fixes;
  `pytest -v` on September 24, 2026: **92 passed** locally. Tests use an isolated demo database.
- Entry equity/realized P&L are synced in the fill transaction; duplicate open positions on a token
  are rejected. A paused worker still monitors open positions. Missing/stale position quotes raise
  an emergency and do not fabricate exits; manual close-all rejects stale marks (HTTP 409).
- Market snapshots older than the decision TTL or timestamped in the future are rejected.
  Mature WATCH/rejected signal outcomes can be resolved independently of open positions, limited
  to two token quote fetches per monitoring cycle. Missing prices remain unresolved.
- The tournament cannot announce a winner without enough samples and explicitly verified
  out-of-sample returns. Displayed Sharpe/deflation fields are **exploratory proxies**, not
  audited measures of edge; UI labels reflect this limitation.
- Production-mode `TestClient` smoke test with a fresh temporary SQLite database: `/login` 200; authenticated `/auth/login` 200; anonymous `/dashboard/summary` 401; operator dashboard 200; exactly one user. This is **not** a container or deployment test.
- Live-money execution is forcibly disabled in runtime configuration. Production rejects demo mode, public registration, weak ephemeral secrets, and a database containing the seeded demo account.

## Critical blockers for any real-money implementation

1. **No trustworthy positive expectancy:** collect real-time, point-in-time signals and independent outcomes. Compare after fees, gas, spread, slippage, failed fills, latency and taxes, with chronological train/test separation and multiple-testing corrections. Do not count overlapping synthetic demo opportunities as independent observations.
2. **Incomplete free data:** public pool prices alone cannot establish sellability, mint/freeze authority, holder concentration, deployer exposure, or liquidity lock status. Missing evidence is UNKNOWN and must block entries; do not replace it with a positive default or a fabricated mock.
3. **No live execution module:** no signed transaction pipeline, on-chain nonce/reorg handling, private-key custody, reconciliation, or kill-switch validation. Adding credentials or flipping an environment flag cannot turn paper fills into real fills.
4. **No continuous live-data soak:** measure endpoint errors, staleness, free-tier rate limiting and scan completion for days, then benchmark CPU/RAM/disk on the actual host. Public API policies can change.
5. **No restore/release proof:** Docker build, HTTPS reverse proxy, container health, SQLite online backup + restore, and rollback have not been exercised on the intended server.
6. **Exit fills remain optimistic:** the simulation uses fresh pool marks, but does not yet have
   an executable sell quote, modeled exit market impact, guaranteed sellability, or a full sell
   order record. `PaperTrade.slippage_bps=0` on exits is not real-world slippage. Do not
   interpret paper P&L as achievable returns.
7. **Measurement gaps:** free trending/new pools bias the universe; swap samples and pagination
   may omit transfers and failed exits. Mature signals without a valid price/pool remain pending;
   the current bounded resolver can starve newer signals behind a large invalid backlog. The
   nominal 60-second scan can exceed an upstream free quota, so use measured quotas/health,
   not nominal scan cadence, as evidence. Authentication is single-operator with in-process
   throttling, not distributed abuse protection; logout does not revoke bearer tokens.

## Paper acceptance gate

Run a multi-week forward paper observation with durable signal timestamp, provider response provenance, entry decision, intended-vs-executable price, fill assumption, all costs, and a no-trade counter. Freeze rules before evaluation. Review a statistically meaningful number of independently generated trades, drawdown and downside scenarios; if net expectancy after costs is not convincingly positive, stop rather than raising capital. Never infer profit from passing unit tests or UI demos.
