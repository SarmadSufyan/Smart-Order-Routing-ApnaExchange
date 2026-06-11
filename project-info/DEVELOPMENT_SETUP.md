# DEVELOPMENT_SETUP.md — Local Environment Setup Guide

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) or `winget install Python.Python.3.11` |
| Node.js | 20+ | [nodejs.org](https://nodejs.org/) or `winget install OpenJS.NodeJS.LTS` |
| pnpm | 8+ | `npm install -g pnpm` |
| Docker Desktop | Latest | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Git | Latest | [git-scm.com](https://git-scm.com/) |
| VS Code | Latest | [code.visualstudio.com](https://code.visualstudio.com/) |
| PostgreSQL | 16 | Via Docker (recommended) or standalone install |
| Redis | 7 | Via Docker (recommended) or Memurai for Windows |

---

## Step 1: Clone the Repository

```powershell
git clone https://github.com/<your-org>/apna-exchange.git
cd apna-exchange
```

---

## Step 2: Set Up Python Backend

### Create Virtual Environment

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If you get an execution policy error:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\.venv\Scripts\Activate.ps1
```

### Install Dependencies

```powershell
pip install -r requirements.txt
```

**Key packages:**
```
fastapi==0.111.0
uvicorn[standard]==0.30.1
pydantic==2.7.4
pydantic-settings==2.3.4
sqlalchemy[asyncio]==2.0.31
asyncpg==0.29.0
redis[hiredis]==5.0.7
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
structlog==24.2.0
httpx==0.27.0
websockets==12.0
alembic==1.13.1
numpy==1.26.4
scipy==1.13.1
```

---

## Step 3: Set Up Frontend

```powershell
cd ..\frontend
pnpm install
```

**Key packages:**
```json
{
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "react-router-dom": "^6.23.0",
    "@tanstack/react-query": "^5.45.0",
    "zustand": "^4.5.0",
    "ag-grid-react": "^31.0.0",
    "ag-grid-community": "^31.0.0",
    "lightweight-charts": "^4.1.0",
    "recharts": "^2.12.0",
    "lucide-react": "^0.383.0"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "vite": "^5.3.0",
    "@types/react": "^18.3.0",
    "tailwindcss": "^3.4.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0"
  }
}
```

---

## Step 4: Set Up Infrastructure (Docker)

Start PostgreSQL and Redis via Docker:

```powershell
cd ..\infra\docker

# Start only infrastructure (not the full stack)
docker-compose -f docker-compose.yml up -d postgres redis
```

Or if you want to use docker-compose from the root:

```powershell
docker-compose up -d postgres redis
```

### Verify Infrastructure

```powershell
# PostgreSQL
docker exec -it apna-exchange-postgres-1 psql -U sor_user -d apna_exchange -c "SELECT 1;"

# Redis
docker exec -it apna-exchange-redis-1 redis-cli ping
# Expected: PONG
```

---

## Step 5: Environment Configuration

```powershell
cd ..\..\
Copy-Item .env.example .env
```

Edit `.env` with your local values. For local development, the defaults should work.

---

## Step 6: Database Setup

```powershell
cd backend
.\.venv\Scripts\Activate.ps1

# Run migrations
alembic upgrade head

# Seed initial data (venues, users, risk limits)
python -m backend.shared.database.seed
```

---

## Step 7: Start Development Servers

Open **4 terminals** in VS Code:

### Terminal 1 — Venue Simulators

```powershell
cd venues
.\..\backend\.venv\Scripts\Activate.ps1  # or create venues-specific venv
python start_all_venues.py
```

Or start individually:
```powershell
$env:VENUE_ID="V1"; $env:VENUE_PORT="8001"; $env:VENUE_PROFILE="alpha_exchange"; python venue_server.py
```

### Terminal 2 — Backend Gateway

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn gateway.main:app --reload --host 0.0.0.0 --port 8000
```

### Terminal 3 — Frontend Dev Server

```powershell
cd frontend
pnpm dev
```

### Terminal 4 — General Purpose

For running tests, database operations, etc.

---

## Step 8: Verify Everything

| Service | URL | Expected |
|---|---|---|
| Frontend | http://localhost:5173 | Login page |
| API Gateway | http://localhost:8000 | `{"message": "Apna Exchange API"}` |
| Swagger Docs | http://localhost:8000/docs | Interactive API docs |
| Health Check | http://localhost:8000/health | `{"status": "healthy", ...}` |
| Venue V1 | http://localhost:8001/health | `{"venue_id": "V1", "status": "ok"}` |
| Venue V2 | http://localhost:8002/health | `{"venue_id": "V2", "status": "ok"}` |
| Venue V3 | http://localhost:8003/health | `{"venue_id": "V3", "status": "degraded"}` |
| Venue V4 | http://localhost:8004/health | `{"venue_id": "V4", "status": "ok"}` |
| Venue V5 | http://localhost:8005/health | `{"venue_id": "V5", "status": "ok"}` |

---

## VS Code Recommended Extensions

```json
{
  "recommendations": [
    "ms-python.python",
    "ms-python.vscode-pylance",
    "charliermarsh.ruff",
    "dbaeumer.vscode-eslint",
    "bradlc.vscode-tailwindcss",
    "esbenp.prettier-vscode",
    "ms-azuretools.vscode-docker",
    "humao.rest-client",
    "prisma.prisma"
  ]
}
```

---

## VS Code Settings (Workspace)

```json
{
  "python.defaultInterpreterPath": "./backend/.venv/Scripts/python.exe",
  "python.analysis.typeCheckingMode": "basic",
  "editor.formatOnSave": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  },
  "[typescript]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "[typescriptreact]": {
    "editor.defaultFormatter": "esbenp.prettier-vscode"
  },
  "tailwindCSS.experimental.classRegex": [
    ["cva\\(([^)]*)\\)", "[\"'`]([^\"'`]*).*?[\"'`]"]
  ]
}
```

---

## Common Issues & Solutions

### PowerShell Execution Policy

```powershell
# Error: "running scripts is disabled on this system"
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Port Already in Use

```powershell
# Find process using port 8000
netstat -ano | findstr :8000
# Kill process
taskkill /PID <pid> /F
```

### Python Virtual Environment Not Activating

```powershell
# Use full path
& "E:\path\to\project\backend\.venv\Scripts\Activate.ps1"
```

### Docker Compose Network Issues

```powershell
# Reset Docker networks
docker-compose down
docker network prune -f
docker-compose up -d
```

### pnpm Lock File Mismatch

```powershell
cd frontend
Remove-Item node_modules -Recurse -Force
Remove-Item pnpm-lock.yaml
pnpm install
```

---

## Testing

### Backend Tests

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest tests/ -v --cov=backend --cov-report=html
```

### Frontend Tests

```powershell
cd frontend
pnpm test
```

### Integration Tests

```powershell
# Start all services first, then:
cd tests/integration
python -m pytest test_order_flow.py -v
```

---

## Useful Development Commands

```powershell
# Quick test an endpoint
Invoke-WebRequest -Uri "http://localhost:8000/api/venues" -Method GET | Select-Object -ExpandProperty Content | ConvertFrom-Json

# Submit a test order
$body = '{"symbol":"AAPL","side":"BUY","quantity":100,"order_type":"MARKET","strategy":"best_price"}'
Invoke-WebRequest -Uri "http://localhost:8000/api/orders" -Method POST -Body $body -ContentType "application/json"

# Check venue health
Invoke-WebRequest -Uri "http://localhost:8001/health" | Select-Object -ExpandProperty Content

# Degrade a venue (for testing)
Invoke-WebRequest -Uri "http://localhost:8003/admin/degrade" -Method POST

# Recover a venue
Invoke-WebRequest -Uri "http://localhost:8003/admin/recover" -Method POST
```
