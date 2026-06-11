# Backend — Gateway + Platform Services

FastAPI modular monolith. All services run in one process but live in separate modules with clean interfaces — any module can be extracted into a microservice later without touching service-layer code.

## Layout

```
backend/
├── gateway/          FastAPI app entry, config, middleware, dependency injection
├── routers/          HTTP route handlers (thin — delegate to services)
├── services/         Business logic (the brain)
│   ├── market_data/      Venue polling, NBBO aggregation                    [POC]
│   ├── routing/          Smart order routing (best-price for POC)           [POC]
│   ├── risk/             Pre-trade checks, kill switch                      [POC]
│   ├── execution/        Sends child orders to venues, processes fills      [POC]
│   ├── venue_health/     Health scoring, status transitions                 [POC]
│   ├── order_state/      Order state machine                                [POC]
│   ├── consensus/        RAFT consensus                                     [M3]
│   └── policy/           Rule-based policy engine                           [M3]
├── shared/           Cross-cutting code
│   ├── models/           Pydantic models (API contracts)
│   ├── database/         Repositories + (later) SQLAlchemy ORM              [M3]
│   ├── redis/            Redis client, cache, pub/sub                       [M3]
│   ├── websocket/        WebSocket connection manager
│   ├── events/           In-process event bus
│   └── exceptions, logging, utils
├── tasks/            Background async tasks (poll venues, health checks, …)
├── migrations/       Alembic migrations                                     [M3]
└── tests/            Backend unit tests
```

## Conventions

- **Python 3.11+**, `async`/`await` everywhere.
- **Pydantic v2** for every API model — use `model_dump()`, not `.dict()`.
- **Type hints required** on every function signature.
- **No business logic in routers** — handlers validate input, call a service, shape response.
- **No `print()`** — `structlog` only. Every log line includes `order_id`, `venue_id` where relevant.
- **Constructor injection** for service dependencies; FastAPI `Depends()` wires them at the router level.
- **Result objects** for expected business outcomes (`RoutingResult`, `RiskCheckResult`). Exceptions only for genuine errors.

## Running locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn gateway.main:app --reload --port 8000
```

Then open http://localhost:8000/docs for Swagger.

## Tests

```powershell
pytest tests/ -v --cov=backend
```

## What lives elsewhere

- API contracts: [`../project-info/API_SPECIFICATION.md`](../project-info/API_SPECIFICATION.md)
- Service deep-dives: [`../project-info/BACKEND_GUIDE.md`](../project-info/BACKEND_GUIDE.md)
- Algorithms: [`../project-info/ALGORITHMS.md`](../project-info/ALGORITHMS.md)
