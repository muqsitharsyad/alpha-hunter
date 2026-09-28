# AlphaHunter AI

Autonomous crypto smart-money intelligence and **paper** trading. It discovers wallets from
on-chain flow, scores and re-scores them, gates every candidate through hard checks, opens and
manages simulated positions on its own, explains each decision in plain language, and keeps a
learning record. There is no live-money path in this build.

Built from `AlphaHunter_AI_PRD_v1.3_Production_Blueprint.docx`.

## Run it (Windows, no Docker needed)

```bash
cd AlphaHunter
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt

cd backend
ALPHAHUNTER_DATABASE_URL="sqlite:///../data/dev.db" ALPHAHUNTER_FORCE_DEMO=1 \
  ../.venv/Scripts/python run.py
```

Open http://127.0.0.1:8000 and sign in with `demo@alphahunter.local` / `demo1234`.
Press **Start AI** to try the paper workflow. A scan can produce WATCH/REJECT and no entries.

`ALPHAHUNTER_FORCE_DEMO=1` uses deterministic fixtures with no network; synthetic wallet
history is intentionally not considered verified, so a BUY is not guaranteed.
Drop that variable to scan live Solana pools through GeckoTerminal and DexScreener. Public
pool metadata alone does not verify token contract safety or wallet performance: unknown
evidence must block entries. Live-data scanning does **not** mean live-money execution.

## Run it (Docker, light profile)

```bash
cp .env.example .env      # set SECRET_KEY, PUBLIC_ORIGIN, BOOTSTRAP_EMAIL/PASSWORD
docker compose up -d --build
# Bind: 127.0.0.1:8010 only. Configure an HTTPS reverse proxy to PUBLIC_ORIGIN.
```

One container, one SQLite file in a named volume. Actual RAM use is not yet benchmarked.
Postgres is not part of this deployment; a future migration would require explicit engineering.

## Why the stack is smaller than the PRD draft

| PRD draft | Shipped here | Reason |
|---|---|---|
| Next.js + TS + Tailwind | FastAPI + Jinja2 + one CSS file | No Node, no build step, one process. Server is RAM-constrained and already hosts nine containers. |
| PostgreSQL 16 | SQLite (WAL) | One writer, one paper user; no extra database service. |
| Redis 7 streams | In-process TTL cache and an asyncio worker | Removes a whole service; the freshness gate still works. |
| Celery | asyncio loop | No broker needed at this scale. |
| pgvector | JSON columns in `ai_memory` | Pattern memory is small; a vector index is not yet earning its keep. |
| scikit-learn / PyTorch | Deterministic weighted models | The learning loop and statistical gates are implemented; model training is the next phase. |

Paid providers (Alchemy, Helius, Birdeye, Dune) slot in behind the same adapter interfaces in
`backend/app/providers/base.py`. Everything shipped uses free endpoints.

## Layout

```
backend/
  app/
    providers/      adapter interfaces + free adapters + demo fixtures
    wallet_intel.py discovery, ledger, scoring (8.2), reputation (9), consensus (10)
    token_intel.py  token health (12.3) and anti-manipulation hard gates (13)
    copy_feasibility.py  slippage / depth / age / chase model (11.2) and hard gates (15.2)
    regime.py       market regime (14)
    decision_engine.py   composite score (15.1), thresholds, explainable layer (18)
    execution.py    fill simulation (16.3), TP/SL/trailing (17), emergency layer (22)
    risk.py         sizing (16.4), limits, automatic brakes (22.1)
    learning.py     exploratory learning loop (19), tournament (20), statistical proxies
    pipeline.py     the autonomous cycle
    web.py          server-rendered dashboard (25)
    main.py         JSON API (24), SSE feed
  templates/        one file per PRD screen
  static/           app.css, app.js (no CDN, no framework)
  tests/            unit/integration regressions; not a profitability validation
```

## Tests

```bash
cd backend && ../.venv/Scripts/python -m pytest
```

Tests cover scoring bounds, reputation half-life, tier boundaries, intent classification, consensus,
copy feasibility (reject, resize, whale cap, aging, chasing), every hard gate individually,
honeypot and unlocked-liquidity rejection, sizing caps, daily-loss limits, all five emergency
brakes, regime classification, BH-FDR and deflated Sharpe, and an end-to-end cycle from signal
to learning event. They do not establish a positive out-of-sample edge.

## Production constraints and deployment gate

- Compose forces `APP_ENV=production`, `DEMO_MODE=false`, paper execution, and binds port 8010
  on localhost. Set a random persistent secret (32+ characters), HTTPS `PUBLIC_ORIGIN`, and
  a unique operator bootstrap email/password (16+ characters). Never expose port 8010 publicly.
- Use a **fresh SQLite volume** for production; startup refuses an existing demo account.
  Bootstrap creates one operator. Remove `BOOTSTRAP_PASSWORD` from `.env` after first startup;
  subsequent restarts use the account in SQLite. Back up the volume and test restoring it
  offline before relying on it.
- No Docker build or remote deployment has been verified in this environment. Do not treat
  this as a validated production release. See `docs/QUANT_AUDIT.md` for trading limitations.

## Operating it

- **Pause** stops new entries and keeps managing open positions. **Safe mode** allows only the
  strongest signals. **Close all** pauses entries and simulates exits at fresh last marks only;
  stale/missing prices return 409 and leave positions open. Even fresh marks do not guarantee
  executable exits or model exit slippage accurately.
- Automatic brakes fire on daily loss, drawdown, provider outage, stale decision-critical data
  and a crash regime. A brake blocks new entries and shows up on System Health with a reason.
- Telegram alerts are optional. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (plus
  `TELEGRAM_TOPIC_ID` for a forum topic). Without them, events are logged only, and a delivery
  failure can never block trading.
- `/docs` exposes the JSON API. The API needs a bearer token from `/auth/login`; the UI uses an
  httpOnly cookie so no token ever sits in JavaScript. Signing out clears that cookie, but does
  not revoke an already issued bearer token; use HTTPS and restrict operator access.

## Not financial advice

Paper mode only. No custody, no withdrawal permission, no guaranteed profit or accuracy. Whale
derived signals are statistical observations, not accusations of wrongdoing and not claims about
non-public information. Some jurisdictions restrict crypto tooling.
