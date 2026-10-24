# Project documents

The guides that describe ApnaSOR **as built** (real crypto exchanges, from July 2026), and the historical design
documents of the June 2026 prototype. Start with the root [`README.md`](../README.md).

## Current — read these

| Document | What it explains |
|---|---|
| [`ALGORITHMS_GUIDE.md`](ALGORITHMS_GUIDE.md) | Routing strategies (best price after fees, liquidity sweep, smart) and execution algorithms (TWAP, VWAP, implementation shortfall, POV, iceberg, Auto): idea, maths, live results, Q&A |
| [`RESEARCH_GUIDE.md`](RESEARCH_GUIDE.md) | Indicators, forecasts and their track record, backtests, the technique study, trade plans |
| [`COPILOT_GUIDE.md`](COPILOT_GUIDE.md) | The copilot and its analyst: what it does, how it decides, safety, Q&A |
| [`AUTOMATION_GUIDE.md`](AUTOMATION_GUIDE.md) | The automation fund, profit-cycle bot, advisor agent and practice lab |
| [`PHASE6_GUIDE.md`](PHASE6_GUIDE.md) | The risk batch (pre-flight, Monte Carlo stress test, worker processes) and the RAFT cluster |
| [`SECURITY_GUIDE.md`](SECURITY_GUIDE.md) | Logins and passwords, the signing key, CORS, the demo-laptop checklist |
| [`EXCHANGE_API_KEY_SECURITY.md`](EXCHANGE_API_KEY_SECURITY.md) | How exchange API keys are stored, checked and limited |
| [`API_KEYS_GUIDE.md`](API_KEYS_GUIDE.md) | Which sandbox keys a developer needs and how to get them |
| [`START_STOP_GUIDE.md`](START_STOP_GUIDE.md) | `start_all.py`: start order, RAFT leader wait, clean stop |
| [`LAB_SERVER_GUIDE.md`](LAB_SERVER_GUIDE.md) | The 24/7 lab server on AWS: setup, releases, what happens when, costs, security |
| [`REAL_MONEY_GUIDE.md`](REAL_MONEY_GUIDE.md) | Real money: the safety rails, trade-only keys, the separate server, the first orders, Q&A |
| [`PHASE_D_PLAN.md`](PHASE_D_PLAN.md) | The real-money plan: stages D0–D7 with "done when", and the decisions log |
| [`ROADMAP.md`](ROADMAP.md) | Phases, acceptance criteria, what is next |
| [`benchmarks/`](benchmarks/) | Measured results (the risk batch: one process vs a worker pool) |

Also current, at the root: [`PROGRESS.md`](../PROGRESS.md) (state and handoff notes),
[`DEVELOPMENT_JOURNAL.md`](../DEVELOPMENT_JOURNAL.md) (every change, dated, with its reasons),
[`CONTRIBUTING.md`](../CONTRIBUTING.md) and [`ONBOARDING.md`](../ONBOARDING.md). The live API contract is the
gateway's Swagger page (`http://127.0.0.1:8000/docs`).

## Historical — the June 2026 prototype

Until June 2026 the project (then called DEIRCP) routed orders across **five simulated stock venues**. The FYP panel
asked for real data, so in July 2026 the team moved to real crypto exchanges. These documents describe that first
design; they are kept as the record of milestone M1 and are **not** a description of the current system. Each one
says so at its top.

| Document | Was about |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | The simulated-venue architecture |
| [`API_SPECIFICATION.md`](API_SPECIFICATION.md) | The first API contract |
| [`ALGORITHMS.md`](ALGORITHMS.md) | First algorithm notes (superseded by `ALGORITHMS_GUIDE.md`) |
| [`BACKEND_GUIDE.md`](BACKEND_GUIDE.md) · [`FRONTEND_GUIDE.md`](FRONTEND_GUIDE.md) | The first backend and frontend plans |
| [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md) | A planned PostgreSQL + Redis design (the system uses SQLite) |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | A planned multi-cloud venue deployment |
| [`MILESTONES.md`](MILESTONES.md) | The original timeline |
| [`PROJECT_STRUCTURE.md`](PROJECT_STRUCTURE.md) · [`DEVELOPMENT_SETUP.md`](DEVELOPMENT_SETUP.md) | The first repository layout and setup |

The simulated venues themselves are in [`venues/`](../venues/) (and on the `feature/venues` branch).
