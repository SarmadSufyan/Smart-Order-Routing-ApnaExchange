# Tests (cross-cutting)

This folder is for tests that span multiple services or processes. Unit tests live next to the code they test (in `backend/tests/` and `venues/tests/`).

## Layout

```
tests/
├── integration/      Multi-service tests: order flow, venue blacklist, kill switch, market data
├── e2e/              Browser-driven (Playwright/Cypress) — added once frontend stabilizes
└── load/             Locust scripts for stress testing — M4
```

## What's planned

| Test | Scope | Milestone |
|---|---|---|
| `test_order_flow.py` | Submit order → route → fill → execution report | POC end |
| `test_venue_blacklist.py` | Degrade V3 → routing must skip it | POC end |
| `test_kill_switch.py` | Activate kill switch → all live orders cancelled, new ones rejected | POC end |
| `test_risk_limits.py` | Exceed each limit → correct rejection reason | POC end |
| `test_market_data.py` | NBBO computed correctly across 5 venues, stale data excluded | POC end |

## Running

```powershell
# Integration (requires the full stack running — see project-info/DEVELOPMENT_SETUP.md)
cd tests/integration
pytest -v
```

> **POC reminder:** persistence is in-memory. Tests that need a clean state must restart the gateway process between runs, or call a `/admin/reset` endpoint (TBD).
