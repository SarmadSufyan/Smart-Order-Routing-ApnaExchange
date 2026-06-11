# Market Simulator (Venues) — Team Guide

> One person owns this folder. Read this end-to-end before writing code. The five venue servers are independent processes — get one working, then replicate.

---

## What you're building

Five **independent FastAPI servers**, each simulating a trading exchange. Each one:

- Streams realistic price ticks via a **GBM price engine with regime-switching** (spec: [`../project-info/ALGORITHMS.md`](../project-info/ALGORITHMS.md) §3).
- Maintains a small in-memory order book (synthetic depth — doesn't have to be a full matching engine for POC).
- Accepts orders via `POST /execute-order` and returns fills, partial fills, or rejects based on its **personality profile**.
- Reports its own health via `GET /health`.
- Can be force-degraded for demos via `POST /admin/degrade`.

The five venues share the same code (`server/venue_app.py`) but load different **profile** files at startup that tune their personality.

---

## The five venues

| ID | Name | Narrative cloud | Port | Personality | Health status |
|---|---|---|---|---|---|
| V1 | AlphaExchange | AWS | 8001 | Stable all-rounder, moderate latency (5–15ms), reliable fills | HEALTHY |
| V2 | BetaLiquidity | GCP | 8002 | Best prices, fast (2–8ms), deep liquidity, tight spreads | HEALTHY |
| V3 | GammaMarkets | Azure | 8003 | **Degraded** — latency 50–200ms, 30%+ reject rate. Demo target. | BLACKLISTED |
| V4 | DeltaPrime | AWS | 8004 | Premium venue, lowest latency (1–5ms), thin books (small sizes) | HEALTHY |
| V5 | EpsilonPool | GCP | 8005 | Dark pool, balanced 10–25ms, variable liquidity | HEALTHY |

> **Important narrative vs reality.** "Narrative cloud" describes the FYP-story-level diversity (we tell the panel they live across AWS/GCP/Azure to demonstrate fragmented markets). The **actual deployment is AWS-only** — five EC2 VMs, potentially in different regions/AZs to keep the fragmentation real. See `infra/cloud/aws/` once you write the deployment scripts.

---

## Folder layout

```
venues/
├── server/
│   ├── venue_app.py        FastAPI app factory — one app instance per venue
│   ├── config.py           Settings loaded from env vars + profile file
│   ├── routers/            Endpoint handlers — quote, orderbook, execute, health, admin
│   ├── engine/             The simulation guts
│   │   ├── price_engine.py        GBM + regime-switching tick generation
│   │   ├── orderbook_engine.py    Synthetic depth around the current mid
│   │   ├── fill_simulator.py      Decide fill / partial / reject given an order
│   │   └── latency_model.py       Inject the venue's characteristic latency
│   └── profiles/           One module per venue — personality definitions
│       ├── base.py                Profile dataclass schema
│       ├── alpha_exchange.py      V1
│       ├── beta_liquidity.py      V2
│       ├── gamma_markets.py       V3 (degraded)
│       ├── delta_prime.py         V4
│       └── epsilon_pool.py        V5
├── start_all_venues.py     Launches all 5 venues in subprocesses (local dev)
├── requirements.txt
└── tests/
```

---

## Build order

Don't try to build all 5 at once. Get one working end-to-end, then the rest are configuration changes.

### Phase 1 — One venue, locally, end-to-end

1. **`server/config.py`** — Pydantic Settings loading `VENUE_ID`, `VENUE_NAME`, `VENUE_PORT`, `VENUE_PROFILE` from env vars. Profile module is `import_module(f"server.profiles.{settings.venue_profile}")`.
2. **`server/profiles/base.py`** — dataclass with fields: `base_latency_ms_range: tuple[float, float]`, `spread_bps: float`, `default_fill_rate: float`, `reject_rate: float`, `liquidity_depth: float`, `volatility: float`, `regime_transition_prob: float`.
3. **`server/profiles/alpha_exchange.py`** — instantiate the V1 profile with numbers matching the table above.
4. **`server/engine/price_engine.py`** — implement the GBM step from [`../project-info/ALGORITHMS.md`](../project-info/ALGORITHMS.md) §3. Keep `current_price` and `current_regime` as module-level state; a background task ticks every 100ms.
5. **`server/engine/orderbook_engine.py`** — given the current mid + spread + depth profile, generate `bid_size`/`ask_size` and a top-of-book bid/ask. Top-N depth synthesized around the mid (geometric size decay is fine).
6. **`server/engine/latency_model.py`** — `async def inject_latency()` that `await asyncio.sleep(random.uniform(*profile.base_latency_ms_range) / 1000)`. Wrap every venue endpoint in this.
7. **`server/engine/fill_simulator.py`** — given an order, return one of: `(FILL, fill_qty, fill_price)`, `(PARTIAL, fill_qty, fill_price)`, `(REJECT, reason)`. Roll dice against `profile.reject_rate`; otherwise check order qty vs `orderbook_engine.available_size()` to decide FILL vs PARTIAL.
8. **`server/routers/quote.py`** — `GET /quote?symbol=AAPL` returning the bid/ask snapshot from `orderbook_engine`. Wrap with latency injection.
9. **`server/routers/execute.py`** — `POST /execute-order` calling `fill_simulator`. Returns an execution report.
10. **`server/routers/health.py`** — `GET /health` returning `{venue_id, name, status, uptime_seconds, current_regime}`.
11. **`server/routers/admin.py`** — `POST /admin/degrade` flips an in-memory flag that multiplies latency by 10x and reject rate by 5x. `POST /admin/recover` undoes it.
12. **`server/venue_app.py`** — FastAPI app factory that includes all routers and starts the price-engine background task on `@app.on_event("startup")`.
13. **Run it:** `VENUE_ID=V1 VENUE_NAME=AlphaExchange VENUE_PORT=8001 VENUE_PROFILE=alpha_exchange uvicorn server.venue_app:app --port 8001`. Hit `http://localhost:8001/quote?symbol=AAPL` and `POST` an order. Verify health endpoint.

### Phase 2 — All five locally

14. Copy the alpha_exchange profile, tune the numbers for each of V2–V5 (use the table above).
15. **`start_all_venues.py`** — `subprocess.Popen` launches all 5 with their respective env vars. Stream stdout with line prefixes per venue.
16. Run it. Hit all 5 ports. Confirm V3 visibly behaves badly (slow responses, frequent rejects).

### Phase 3 — Tests

17. **`tests/test_price_engine.py`** — GBM produces prices > 0 across thousands of steps; regime transitions occur with roughly the expected probability.
18. **`tests/test_fill_simulator.py`** — reject rate over 10,000 simulated orders matches the profile's `reject_rate` within ±2%.
19. **`tests/test_venue_api.py`** — FastAPI TestClient hitting `/quote`, `/execute-order`, `/health` returning correct shapes.

### Phase 4 — AWS deployment (POC)

This is required for the POC demo, not deferred.

20. **`Dockerfile`** in `venues/` — base on `python:3.11-slim`, install requirements, expose configurable port via `VENUE_PORT` env var, `CMD ["uvicorn", "server.venue_app:app", "--host", "0.0.0.0", "--port", "$VENUE_PORT"]`.
21. **`infra/cloud/aws/ec2-venue-setup.sh`** (this file is in the user's domain — coordinate with him): launches an EC2 t3.micro per venue, sets up Docker, pulls the image, runs the container, opens the security group on the venue's port. One script that takes `VENUE_ID` as arg.
22. **Public DNS / Elastic IP per venue.** Document them in `infra/cloud/aws/venues.md` so the platform's `.env` can be updated.
23. **Smoke test from your laptop** — `curl http://<v1-public-ip>:8001/health`. Repeat for all 5.

> Coordinate Phase 4 with the user (backend owner) — he'll need to update the platform's `VENUE_V*_URL` env vars to point at the EC2 public DNS/IPs. The platform itself stays running on his laptop.

---

## Endpoint contracts

Match these exactly — the platform's market data aggregator and venue health monitor depend on the shapes.

### `GET /quote?symbol=<SYMBOL>`

```json
{
  "venue_id": "V1",
  "symbol": "AAPL",
  "bid_price": 150.22,
  "ask_price": 150.30,
  "bid_size": 300,
  "ask_size": 250,
  "last_price": 150.25,
  "volume": 45000,
  "timestamp": "2026-06-11T10:30:45.123Z"
}
```

### `GET /orderbook?symbol=<SYMBOL>&depth=<N>`

```json
{
  "venue_id": "V1",
  "symbol": "AAPL",
  "bids": [{"price": 150.22, "size": 300}, {"price": 150.21, "size": 250}],
  "asks": [{"price": 150.30, "size": 200}, {"price": 150.31, "size": 180}],
  "timestamp": "2026-06-11T10:30:45.123Z"
}
```

### `POST /execute-order`

Request:
```json
{
  "child_order_id": "child-001",
  "symbol": "AAPL",
  "side": "BUY",
  "quantity": 100,
  "price": 150.30,
  "order_type": "MARKET"
}
```

Response — fill:
```json
{
  "child_order_id": "child-001",
  "venue_id": "V1",
  "exec_type": "FILL",
  "filled_qty": 100,
  "fill_price": 150.28,
  "venue_exec_id": "V1-exec-9821",
  "venue_latency_ms": 8.2,
  "timestamp": "2026-06-11T10:30:45.567Z"
}
```

Response — reject:
```json
{
  "child_order_id": "child-001",
  "venue_id": "V1",
  "exec_type": "REJECT",
  "reject_reason": "Insufficient liquidity",
  "timestamp": "2026-06-11T10:30:45.567Z"
}
```

### `GET /health`

```json
{
  "venue_id": "V1",
  "name": "AlphaExchange",
  "status": "ok",
  "uptime_seconds": 12345,
  "current_regime": "NORMAL"
}
```

When degraded:
```json
{
  "venue_id": "V3",
  "name": "GammaMarkets",
  "status": "degraded",
  "uptime_seconds": 12345,
  "current_regime": "VOLATILE"
}
```

### `POST /admin/degrade` and `POST /admin/recover`

Empty body, return `{"degraded": true}` / `{"degraded": false}`.

### `GET /metrics`

Prometheus text format. At minimum: `venue_orders_total{venue,exec_type}`, `venue_latency_ms_histogram`. Defer until everything else works.

---

## Conventions (from `../CLAUDE.md`)

- Python 3.11+, `async`/`await` everywhere. **No sync def in handlers.**
- Pydantic v2 for every request/response. `model_dump()`, not `.dict()`.
- Type hints on every function signature.
- `structlog` for logging — no `print()`. Every log line includes `venue_id` and `symbol` where relevant.
- Domain exceptions (`InsufficientLiquidityError`, etc.), not raw `HTTPException` deep in the engine. Convert at the router boundary.
- No business logic in routers — routers call engine functions.
- Pin dependencies in `requirements.txt`.

---

## Coordinating with the platform team (= the user)

The market data aggregator polls your `/quote` endpoint every 100ms. The venue health monitor polls `/health` every second. Things to flag to him:

- **Before changing any endpoint shape**, message him. The aggregator will break if you change `/quote`'s schema without warning.
- **When you have AWS DNS / IPs for the 5 venues**, drop them into `infra/cloud/aws/venues.md` and ping him so he can update the platform `.env`.
- **For the demo**, V3 must be in degraded mode on startup (without anyone clicking `/admin/degrade`). Wire the `VENUE_DEGRADED=true` env var to set the degraded flag at startup, and ship V3's EC2 with that env var set.

---

## Common gotchas

- **GBM drift wrong direction** — `mu` should be small (e.g., 0.0001 per tick), and `sigma * sqrt(dt)` is the per-tick volatility. Don't put per-day volatility per tick or your prices will explode in seconds.
- **Price going negative** — clip with `max(new_price, 0.01)`. GBM should never produce negative prices, but rounding + small starting values can. Belt-and-suspenders.
- **Latency injection blocking the event loop** — use `await asyncio.sleep()`, NOT `time.sleep()`. The synchronous version will freeze every other endpoint for the latency duration.
- **All five venues sharing a random seed** — if you run all 5 in subprocesses, each gets a fresh process with its own random state. Good. But if you ever put them in threads, seed them differently per venue or they'll generate identical price paths.
- **Regime stuck on CRISIS** — check that the regime-transition Markov matrix doesn't have a self-loop probability of 1. Symptom: V3 ends up with `sigma * 4.0` forever, looks reasonable for a few seconds, then prices go wild.
- **AWS security group too permissive** — only open the venue's port (e.g., 8001 for V1), only to the platform's IP. Don't `0.0.0.0/0` it. The panel will appreciate basic hygiene.

---

## When you push to GitHub

- Conventional commits — `feat(venues):`, `fix(venues):`, `refactor(venues):`. Scope is `venues`.
- After every meaningful change, append a paragraph to `../DEVELOPMENT_JOURNAL.md` — what changed and why, dated. Specifically explain the algorithm or behavior, not the diff.
- Update `PROGRESS.md` at the end of each working session if the "what's next" list changes.
