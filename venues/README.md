# Venue Simulators

Five independent FastAPI servers simulating trading exchanges, each with its own order book, matching engine, GBM price simulator, and personality profile.

## The five venues

| ID | Name | Cloud (narrative) | Port | Personality |
|----|------|-------------------|------|-------------|
| V1 | AlphaExchange | AWS | 8001 | Stable all-rounder, moderate latency (5–15ms), reliable fills |
| V2 | BetaLiquidity | GCP | 8002 | Best prices, fast (2–8ms), deep liquidity |
| V3 | GammaMarkets | Azure | 8003 | **Degraded** (50–200ms latency, high reject rate). Demo target for blacklist + routing-skip behavior. |
| V4 | DeltaPrime | AWS | 8004 | Premium venue, lowest latency (1–5ms), thin books |
| V5 | EpsilonPool | GCP | 8005 | Dark pool, balanced |

> **Note:** "Cloud" labels are narrative only for the FYP panel. Actual deployment is AWS-only (separate VMs per venue, not three different providers). See `../project-info/DEPLOYMENT.md` + memory for the AWS-only decision.

## Layout

```
venues/
├── server/
│   ├── venue_app.py        FastAPI app factory (one app per venue)
│   ├── config.py           Venue-specific settings loaded from env + profile
│   ├── routers/            /quote /orderbook /execute-order /health /admin
│   ├── engine/             GBM price engine, order book, fill simulator, latency model
│   └── profiles/           One module per venue describing its personality
├── start_all_venues.py     Launches all 5 in subprocesses (local dev only)
└── tests/
```

## Endpoints (each venue)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Venue status (used by Venue Health Monitor) |
| GET | `/quote?symbol=AAPL` | Current bid/ask snapshot |
| GET | `/orderbook?symbol=AAPL&depth=10` | Top-N levels |
| POST | `/execute-order` | Submit an order, get fill/partial/reject |
| GET | `/metrics` | Prometheus-style metrics |
| POST | `/admin/degrade` | Force into degraded mode (demo) |
| POST | `/admin/recover` | Recover from degraded mode |

Full payload shapes: not duplicated here — see implementation once it lands. Same FastAPI auto-generates Swagger at `/docs` on each venue's port.

## Running locally

```powershell
cd venues
pip install -r requirements.txt
python start_all_venues.py
```

Each venue logs to stdout prefixed with its ID.

## Algorithms

GBM + regime-switching pseudocode and complexity: [`../project-info/ALGORITHMS.md#3`](../project-info/ALGORITHMS.md).
Square-root market impact: [`../project-info/ALGORITHMS.md#5`](../project-info/ALGORITHMS.md).
