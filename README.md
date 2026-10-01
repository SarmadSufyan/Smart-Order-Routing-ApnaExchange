# ApnaSOR — Apna Exchange Smart Order Router

**A smart order router and risk-control platform over real crypto exchanges.**

UITU Final Year Project — Group CS14
Sarmad Sufyan Ahmed · Fizza Mubeen · Mahnoor Nadeem · Maham Ikram

---

ApnaSOR keeps live order books from real exchanges (Binance, Bybit, Gate, OKX), finds the best prices across them, runs pre-trade risk checks, and splits each order across venues as price-protected orders on the exchanges' own testnet / demo environments. Fills feed a unified wallet, a P&L ledger reconciled against every exchange, and an append-only audit log. A risk monitor can stop all trading automatically, and a TWAP bot executes large orders over time through the same checks.

## Start here

| Read | For |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Project guide: architecture, repo map, rules, gotchas — **read first** |
| [`PROGRESS.md`](PROGRESS.md) | Where things stand, who is working on what |
| [`project-info/ROADMAP.md`](project-info/ROADMAP.md) | What's next and what "done" means |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Team workflow: branches, PRs, setup |
| [`project-info/API_KEYS_GUIDE.md`](project-info/API_KEYS_GUIDE.md) | Which exchange keys you need and how to get them |
| [`DEVELOPMENT_JOURNAL.md`](DEVELOPMENT_JOURNAL.md) | Every change explained: what, why, how |

## Quick start (local)

```powershell
pip install -r backend/requirements.txt
cd frontend; npm install; cd ..
Copy-Item .env.example .env        # add your own testnet/demo keys (see API_KEYS_GUIDE)
python scripts/check_exchange_keys.py
python start_all.py
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 (login `admin` / `admin`) |
| Gateway API + Swagger | http://127.0.0.1:8000/docs |

Without exchange keys you still get live market data; trading needs testnet/demo keys. Tests: `python -m unittest discover -s backend/tests -t .`

## Repo layout

| Folder | Contents |
|---|---|
| `backend/` | FastAPI gateway, exchange adapters, routing, risk, wallet/P&L, audit log, TWAP |
| `frontend/` | React + TypeScript dashboard |
| `scripts/` | Exchange key checkers |
| `project-info/` | Roadmap, API keys guide, older design docs (being rewritten) |
| `venues/`, `tests/`, `infra/`, `diagrams/` | Legacy from the simulated June POC (also on `archive/venue-simulators`) |
