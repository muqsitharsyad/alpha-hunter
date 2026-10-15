# Observation release — 2026-10-15

## Delivery verdict

**READY for private read-only collection; NOT ACCEPTED for autonomous paper-entry or profitability claims.** The remaining external evidence gaps cannot be replaced with invented safe defaults. No live-money execution exists or was enabled.

## Verified implementation

- Collector opt-in (`COLLECTOR_ENABLED=true`) continues when entries are paused/emergency-blocked. It never resumes the bot or executes entries. Market observations persist even without whale events; wallet transactions deduplicate across scans.
- Fair pool/feed rotation respects the shared GeckoTerminal request allowance and leaves quota for monitoring. Local quota/cooldown deferrals are not counted as provider outages. Actual upstream failures remain failures. Calls persist in `provider_calls` without credentials.
- Wallet qualification reloads persisted ledger history across scans/restarts. At least five fully closed positions are required; fragmented sales do not inflate sample count. Tier is based on decayed reputation. Unknown NAV drawdown/exit timing are not fabricated. Coverage is still limited by public observed history.
- GoPlus Solana adapter supplies bounded-TTL retrieval evidence for holders/concentration/mint/freeze/fees when available. It does not establish pool-specific LP locks, deployer ownership, or route/size-specific sell simulation. Non-Solana safety remains unknown.
- Smart exits execute atomically with fresh price/depth for crash and explicit liquidity/security/distribution evidence. Monitoring wires liquidity collapse against frozen entry depth, positive current security rejection, and recent net-selling by at least two historically qualified S/A wallets. Missing/stale executable quotes halt entries and never create synthetic exits.
- Decisions retain input pool, swaps, safety evidence, tier list, gates and timestamp limitations. New additive `forward_observations` freezes qualified detection cohorts, excludes hindsight whale prices, retries fairly, and marks missed 24h+15min outcome windows as missing. Outcomes are gross observed-mark returns, not executable net returns; report stays `INSUFFICIENT EVIDENCE`.
- `/research/forward` remains bearer-protected. Private System Health shows collection state/counts, forward coverage and security blockers.

## Actual verification

- Full suite: **267 passed**, compileall and diff check passed.
- Final production image: `alphahunter:1.0.8-observation`, `sha256:22b247866c4548769b4cd4bf9f6a631f8eb801092b2d0b832a5f5860ca817e1b` (includes explicit safe-mode eligibility).
- All 25 running application Python files match local SHA256. Container healthy, restart0, non-root, paper-only, paused; SQLite integrity ok.
- First live collection: market snapshots 4, wallet transactions grew 124→128, wallets 82; no paper positions/trades. These are real observations, not fixtures. Existing six historical opportunities are not new trade evidence.
- External health UI returned 200 with enabled collector/research markers; anonymous research API returned 401.
- Independent in-container keyless GoPlus USDC probe observed holder evidence and active mint/freeze authorities: safety REJECT; LP lock and sell simulation remained null. This validates fail-closed behavior, not token audit completeness.
- Final exact-image clean install and online-backup application restore both PASS: eight pages, API401 and integrity. Stopped drill containers `alphahunter-smoke-20261015T233809Z` and `alphahunter-restore-20261015T233832Z`, volumes retained.
- Follow-up at 2026-10-16 06:39 WIB: real collector accumulated **810 market snapshots, 690 wallet transactions, 346 wallets and 1927 provider-call records**, zero paper trades. Current wallet cohort is all C-tier: insufficient qualifying history, not an entry failure to bypass. Follow-up records show successful real GT/Binance requests and bounded actual errors; local budget deferrals no longer inflate upstream error rate.

## Release safety

- Existing SQLite migrated additively via `create_all`; no existing column alteration, deletion or history rewrite.
- Rollback `/var/docker-apps/alphahunter/rollback/observation-20261015/REVERT.sh` restores prior source/override and selects preserved prior image; live data is retained.
- Backup archive SHA256 `18b17f2800f503d0b72b233d5155df7bc0213fd10ba47366e8a374d36670fbc8`, MD5 `2563840eea8c944674846c49f14152e0`; online SQLite backup `/data/pre-observation-20261015.db`.
- Superseded previous short post-release soak after final image change. Current durable observer `alphahunter-soak-durable-20261015T233855Z`; 24h finalizer **2026-10-17 06:40:55 WIB**, seven-day followup only on real PASS (`CHAIN_7D=1` read back). Collector activity remains entry-paused; not a strategy validation.

## External/time gates still blocking autonomous acceptance

1. A verified, licensed provider/chain execution integration must supply **sell simulation for the exact route/size**, **pool LP lock coverage/expiry**, and **verified deployer concentration**. Current free GoPlus evidence is insufficient. No paid subscription or credential was purchased/configured implicitly.
2. Actual chronological data must accumulate; five resolved wallet positions and forward outcome windows cannot be manufactured. Broader statistical PRD gates require 200 resolved trades and 30/regime, OOS/FDR/cost/uncertainty evaluation.
3. Safe mode has an explicit `SAFE_MODE_ASSETS` chain:token-address allowlist (empty by default), minimum score 90, verified passing gates and at least $1M observed liquidity. It never bypasses missing security evidence; no production assets were implicitly approved.
4. Full PRD multi-chain/500 events per second/1000-user capacity is not proven by this approved single-user SQLite/free-provider deployment.

The application is usable now for collection, historical ledger inspection and safe monitoring. It is not honestly deliverable as unrestricted autonomous trading under the current evidence coverage.
