# AlphaHunter Acceptance Packet — September 28, 2026

## Scope

Private paper-only deployment. No live-money execution. Bot remains paused during acceptance and soak.

## Release manifest

| Item | Verified value |
|---|---|
| Git HEAD | `af72352` |
| R3 handoff tag | `alphahunter-r3-handoff-20260928` |
| Container | `alphahunter` |
| Image tag | `alphahunter:1.0.3` |
| Image ID | `sha256:8c5b50931d03e46718f90fec1f1e602a3816dec99e2e4d2654ebc8a09ba8660e` |
| Runtime user | `alphahunter` non-root |
| Host bind | `127.0.0.1:8010 -> 8010/tcp` |
| Database mount | named volume `alphahunter_alphahunter-data` at `/data` |
| Health | `healthy` |
| Restart count | `0` |
| Trading mode | `paper` |
| Live execution | `false` |

## Test summary

- Full local pytest: `105 passed in 2.93s`.
- Python compileall: passed.
- `git diff --check`: passed.
- Shell syntax checks: passed.
- Provider 429 cooldown regression: passed.
- Provider transient 5xx retry regression: passed.
- SQLite backup checksum and integrity check: passed.

## Semantic smoke

Previously verified through the real HTTPS origin:

- Anonymous protected route redirects to `/login`.
- Login redirects to `/app/dashboard`.
- Secure production cookie is issued.
- Authenticated dashboard, opportunities, settings, positions, trades, and learning render.
- Populated opportunity detail renders explanation, feature snapshot, feasibility, and hard gates.
- Missing/null feasibility values render as unknown (`-`), while actual zero remains `0`.

Current browser probe reconfirmed anonymous `/app/dashboard` protection and redirect to `/login` on September 28, 2026.

## Restore

Isolated application restore drill passed previously using a separate Docker volume/container:

- backup loaded without replacing production DB;
- release image booted;
- login and authenticated UI rendered;
- restored domain data was readable;
- isolated container and volume were removed.

## Rollback

1. Keep bot paused.
2. Select previous verified image tag from the timestamped deployment backup.
3. Restore the pre-change Compose file.
4. Run `docker compose up -d --no-build`.
5. Verify image, health, restart count, safety flags, HTTPS auth, persistence, and SQLite integrity.
6. Do not restore the production database unless a separate incident decision explicitly requires it; the image/config rollback is additive-safe.

## Soak

A read-only 24-hour soak and automatic finalizer are running from the server. The observer never starts scans and never changes bot state. The finalizer writes a sanitized result after the real duration and exits with an alert state if health, restarts, SQLite integrity, or paused safety state fail.

## Unresolved by evidence, not forgotten

- Phase 0.5 quantitative acceptance is not possible yet: production has 6 opportunities, 0 paper trades, 0 learning events, and no point-in-time market snapshot dataset.
- No profitability, edge, Sharpe, or live-money readiness claim is permitted.
- The 24-hour soak result is time-dependent and will be produced automatically; it is not claimed complete before its end.
