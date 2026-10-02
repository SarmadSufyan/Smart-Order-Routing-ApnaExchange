# Scripts

Dev utilities, one-shot tools, repo automation. Run them from the project root with the venv's Python
(`.venv/Scripts/python.exe scripts/<name>.py`).

## Scripts

| Script | Purpose |
|---|---|
| `check_exchange_keys.py` | Which venues authenticate with your `.env` keys, with test balances |
| `check_binance_testnet.py` | Binance spot testnet end-to-end check |
| `benchmark_risk_batch.py` | Phase 6: risk batch in one process vs a pool of worker processes; writes `project-info/benchmarks/risk-batch.{json,svg}` (`--quick` ≈ 20 s) |
| `raft_demo.py` | Phase 6: start 3 RAFT nodes, kill the leader, show nothing is lost. `--gateway --twap 0.0006` does it against the running system (`RAFT_ENABLED=true`) in the middle of a testnet TWAP |

## Planned scripts

| Script | Purpose |
|---|---|
| `seed_database.py` | Seed venues, users, risk limits, policies (M3 — no DB in POC) |
| `generate_test_orders.py` | Generate N random orders for load / demo |
| `run_all_venues.ps1` | PowerShell launcher — alternative to `venues/start_all_venues.py` |
| `run_all_venues.sh` | Bash equivalent |
| `reset_local.ps1` | Nuclear reset — drops DB, clears Redis, restarts everything (M3) |
| `export_metrics.py` | Dump performance metrics to CSV |

Not written yet: scripts get written when the use case is real, not pre-emptively.

## Convention

PowerShell scripts are the primary form (Windows-first team). Bash equivalents are courtesy copies for anyone on Linux/Mac.
