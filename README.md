# Apna Exchange

**Smart Order Router + Distributed Execution Intelligence & Risk Control Platform (DEIRCP)**

UITU Final Year Project — Group CS14
Sarmad Sufyan Ahmed · Fizza Mubeen · Mahnoor Nadeem · Maham Ikram
Supervisors: Sir Usman Javed · Miss Shiza

---

## What this repo contains

| Folder | Contents |
|---|---|
| `project-info/` | All project planning & design docs — architecture, API spec, algorithms, milestones, DB schema |
| `backend/` | FastAPI gateway + platform services (market data, routing, risk, kill switch, …) |
| `venues/` | 5 venue simulator FastAPI servers (V1 AlphaExchange → V5 EpsilonPool) |
| `frontend/` | React + TypeScript dashboard (Figma Make generated — added separately by Fizza) |
| `infra/` | Docker, AWS deployment, monitoring configs |
| `tests/` | Integration, E2E, load tests |
| `scripts/` | Dev utilities |
| `CLAUDE.md` | Master reference for the AI assistant — read first if you're contributing |
| `PROGRESS.md` | Live snapshot — what's done, what's next |
| `DEVELOPMENT_JOURNAL.md` | Plain-language explanation of each change, dated chronologically |

---

## Project phases

This repo's folder layout is the **final** architecture. We fill it progressively:

| Milestone | Scope | Storage | Deployment |
|---|---|---|---|
| **M2 — POC (now)** | 5 panel-assigned use cases: venue simulation, market data aggregation, venue health, risk + kill switch, dashboard | In-memory | Local only |
| **M3 — Full system** | + RAFT consensus, policy engine, FIX protocol, advanced routing | PostgreSQL + Redis | Docker |
| **M4 — Final** | + Load testing, failure demos, full security audit | Same | AWS (single cloud, separate VMs per venue/env) |

Full milestone breakdown: [`project-info/MILESTONES.md`](project-info/MILESTONES.md)

---

## Quick start (POC, local)

Detailed setup in [`project-info/DEVELOPMENT_SETUP.md`](project-info/DEVELOPMENT_SETUP.md).

```powershell
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn gateway.main:app --reload --port 8000

# Venues (separate terminal)
cd venues
pip install -r requirements.txt
python start_all_venues.py

# Frontend (once Figma Make output is dropped into frontend/)
cd frontend
pnpm install
pnpm dev
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Gateway API | http://localhost:8000 |
| Swagger docs | http://localhost:8000/docs |
| Venue V1–V5 | http://localhost:8001 … 8005 |

Default POC login: `admin` / `admin` (hardcoded user; swapped for DB-backed users in M3).

---

## Contributing (team workflow)

1. Read `CLAUDE.md` and `project-info/PROJECT_STRUCTURE.md` before adding files.
2. Conventional commits — `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
3. Branch naming — `feature/xxx`, `fix/xxx`, `refactor/xxx`, `docs/xxx`.
4. Update `DEVELOPMENT_JOURNAL.md` with what + why for any non-trivial change.
5. Update `PROGRESS.md` if the change shifts what "next" looks like.
