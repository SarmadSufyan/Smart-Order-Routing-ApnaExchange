# Apna Exchange — DEIRCP

**Distributed Execution Intelligence & Risk Control Platform for Fragmented Trading Systems**

---

## Overview

Apna Exchange (codename DEIRCP) is a distributed, real-time platform designed to monitor and analyze FIX protocol-based order flows across multiple fragmented trading venues. The system continuously evaluates venue health, detects execution anomalies such as delayed fills and partial executions, and predicts execution risk before orders are placed. It enforces automated control actions including venue blacklisting, order throttling, and kill switch activation to prevent financial losses. A RAFT consensus mechanism ensures consistent and fault-tolerant risk decisions across distributed platform nodes.

This is a Final Year Project (FYP) for Group CS14 at Usman Institute of Technology (UITU), supervised by Sir Usman Javed and Miss Shiza.

---

## Key Features

- **Smart Order Routing** — Intelligent distribution of orders across 5 simulated venues using best-price strategy with venue health awareness
- **Real-Time Venue Health Monitoring** — Continuous tracking of latency, fill rates, rejection rates, and anomaly detection per venue
- **Pre-Trade Risk Controls** — Position limits, notional exposure caps, rate limiting, fat-finger protection before any order reaches a venue
- **Kill Switch** — Emergency halt that instantly cancels all live orders and blocks new submissions, with RAFT-replicated state
- **RAFT Consensus** — 3-node cluster ensuring all platform nodes agree on risk state (blacklist decisions, kill switch status)
- **Multi-Cloud Venue Simulation** — 5 venue servers across AWS, GCP, and Azure with distinct personality profiles
- **Trading Dashboard** — Professional dark-theme React frontend with real-time WebSocket updates

---

## Architecture

```
 OMS / Order Generator
         │
         ▼
 ┌─── API Gateway (FastAPI) ───┐
 │                              │
 │  Market Data Aggregator      │
 │  Routing Engine              │
 │  Risk Engine + Kill Switch   │
 │  Order State Manager         │
 │  Venue Health Monitor        │
 │  RAFT Consensus Cluster      │
 │  Policy Engine               │
 │                              │
 └──────────┬───────────────────┘
            │
   ┌────────┼────────┬──────────┬──────────┐
   ▼        ▼        ▼          ▼          ▼
 V1:AWS   V2:GCP   V3:Azure   V4:AWS    V5:GCP
 :8001    :8002    :8003      :8004     :8005
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui |
| Backend | Python 3.11+, FastAPI, Pydantic v2 |
| Database | PostgreSQL 16, Redis 7 |
| Protocol | FIX 4.2 (QuickFIX-Python), REST, WebSocket |
| Consensus | RAFT (custom Python implementation) |
| Infra | Docker, docker-compose, AWS/GCP/Azure |
| CI/CD | GitHub Actions |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+ with pnpm
- Docker & Docker Compose
- PostgreSQL 16 (or use Docker)
- Redis 7 (or use Docker)

### 1. Clone & Install

```bash
git clone https://github.com/<your-org>/apna-exchange.git
cd apna-exchange

# Backend
cd backend
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
# .venv\Scripts\Activate.ps1    # Windows PowerShell
pip install -r requirements.txt

# Frontend
cd ../frontend
pnpm install
```

### 2. Environment Variables

```bash
cp .env.example .env
# Edit .env with your database credentials and port configs
```

### 3. Start Services

```bash
# Option A: Docker (recommended)
docker-compose up -d

# Option B: Manual
# Terminal 1 - Venues
cd venues && python start_all_venues.py

# Terminal 2 - Backend
cd backend/gateway && uvicorn main:app --reload --port 8000

# Terminal 3 - Frontend
cd frontend && pnpm dev
```

### 4. Access

| Service | URL |
|---|---|
| Frontend Dashboard | http://localhost:5173 |
| API Gateway | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Venue 1 (AlphaExchange) | http://localhost:8001 |
| Venue 2 (BetaLiquidity) | http://localhost:8002 |
| Venue 3 (GammaMarkets) | http://localhost:8003 |
| Venue 4 (DeltaPrime) | http://localhost:8004 |
| Venue 5 (EpsilonPool) | http://localhost:8005 |

---

## Documentation

All detailed documentation lives in the `docs/` directory:

| Document | Description |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Complete system architecture |
| [PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) | Directory layout and organization |
| [MILESTONES.md](docs/MILESTONES.md) | FYP milestones and development phases |
| [BACKEND_GUIDE.md](docs/BACKEND_GUIDE.md) | Backend services deep dive |
| [FRONTEND_GUIDE.md](docs/FRONTEND_GUIDE.md) | Frontend architecture and screens |
| [API_SPECIFICATION.md](docs/API_SPECIFICATION.md) | REST + WebSocket API contracts |
| [DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md) | PostgreSQL + Redis schemas |
| [ALGORITHMS.md](docs/ALGORITHMS.md) | Core algorithms with pseudocode |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, multi-cloud deployment |
| [DEVELOPMENT_SETUP.md](docs/DEVELOPMENT_SETUP.md) | Local environment setup |

---

## Team

| Name | Roll No | Email |
|---|---|---|
| Sarmad Sufyan Ahmed | 23SP-078-CS | 23sp-078-cs@students.uitu.edu.pk |
| Fizza Mubeen | 23SP-070-CS | 23sp-070-cs@students.uitu.edu.pk |
| Mahnoor Nadeem | 23SP-065-CS | 23sp-065-cs@students.uitu.edu.pk |
| Maham Ikram | 23SP-101-CS | 23sp-101-cs@students.uitu.edu.pk |

---

## License

This project is developed as an academic Final Year Project at UITU. All rights reserved by Group CS14.

---

## Acknowledgments

- **SOR Engineering Blueprint v1.0** — Internal technical architecture reference document
- **FIX Trading Community** — FIX protocol specifications
- **Ongaro & Ousterhout (2014)** — "In Search of an Understandable Consensus Algorithm" (RAFT)
- **Harris, L. (2003)** — "Trading and Exchanges: Market Microstructure for Practitioners"
