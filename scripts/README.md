# Scripts

Dev utilities, one-shot tools, repo automation.

## Planned scripts

| Script | Purpose |
|---|---|
| `seed_database.py` | Seed venues, users, risk limits, policies (M3 — no DB in POC) |
| `generate_test_orders.py` | Generate N random orders for load / demo |
| `run_all_venues.ps1` | PowerShell launcher — alternative to `venues/start_all_venues.py` |
| `run_all_venues.sh` | Bash equivalent |
| `reset_local.ps1` | Nuclear reset — drops DB, clears Redis, restarts everything (M3) |
| `export_metrics.py` | Dump performance metrics to CSV |

Empty for now. Scripts get written when the use case is real, not pre-emptively.

## Convention

PowerShell scripts are the primary form (Windows-first team). Bash equivalents are courtesy copies for anyone on Linux/Mac.
