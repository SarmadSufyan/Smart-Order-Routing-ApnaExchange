# DEPLOYMENT.md — Docker, Infrastructure & Multi-Cloud

---

## 1. Local Development (docker-compose)

The entire stack runs locally via a single `docker-compose up`.

### docker-compose.yml

```yaml
version: "3.9"

services:
  # ─── Infrastructure ────────────────────────────────────
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: apna_exchange
      POSTGRES_USER: sor_user
      POSTGRES_PASSWORD: sor_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sor_user -d apna_exchange"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  # ─── Venue Simulators ──────────────────────────────────
  venue-v1:
    build:
      context: ./venues
      dockerfile: ../infra/docker/Dockerfile.venue
    environment:
      VENUE_ID: V1
      VENUE_NAME: AlphaExchange
      VENUE_PORT: "8001"
      VENUE_PROFILE: alpha_exchange
    ports:
      - "8001:8001"

  venue-v2:
    build:
      context: ./venues
      dockerfile: ../infra/docker/Dockerfile.venue
    environment:
      VENUE_ID: V2
      VENUE_NAME: BetaLiquidity
      VENUE_PORT: "8002"
      VENUE_PROFILE: beta_liquidity
    ports:
      - "8002:8002"

  venue-v3:
    build:
      context: ./venues
      dockerfile: ../infra/docker/Dockerfile.venue
    environment:
      VENUE_ID: V3
      VENUE_NAME: GammaMarkets
      VENUE_PORT: "8003"
      VENUE_PROFILE: gamma_markets
      VENUE_DEGRADED: "true"
    ports:
      - "8003:8003"

  venue-v4:
    build:
      context: ./venues
      dockerfile: ../infra/docker/Dockerfile.venue
    environment:
      VENUE_ID: V4
      VENUE_NAME: DeltaPrime
      VENUE_PORT: "8004"
      VENUE_PROFILE: delta_prime
    ports:
      - "8004:8004"

  venue-v5:
    build:
      context: ./venues
      dockerfile: ../infra/docker/Dockerfile.venue
    environment:
      VENUE_ID: V5
      VENUE_NAME: EpsilonPool
      VENUE_PORT: "8005"
      VENUE_PROFILE: epsilon_pool
    ports:
      - "8005:8005"

  # ─── Backend Gateway ───────────────────────────────────
  gateway:
    build:
      context: ./backend
      dockerfile: ../infra/docker/Dockerfile.gateway
    environment:
      POSTGRES_URL: "postgresql+asyncpg://sor_user:sor_password@postgres:5432/apna_exchange"
      REDIS_URL: "redis://redis:6379/0"
      VENUE_V1_URL: "http://venue-v1:8001"
      VENUE_V2_URL: "http://venue-v2:8002"
      VENUE_V3_URL: "http://venue-v3:8003"
      VENUE_V4_URL: "http://venue-v4:8004"
      VENUE_V5_URL: "http://venue-v5:8005"
      JWT_SECRET: "dev-secret-change-in-prod"
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      venue-v1:
        condition: service_started
      venue-v2:
        condition: service_started
      venue-v3:
        condition: service_started
      venue-v4:
        condition: service_started
      venue-v5:
        condition: service_started

  # ─── Frontend ──────────────────────────────────────────
  frontend:
    build:
      context: ./frontend
      dockerfile: ../infra/docker/Dockerfile.frontend
    ports:
      - "5173:80"
    depends_on:
      - gateway

volumes:
  postgres_data:
  redis_data:
```

---

## 2. Dockerfiles

### Dockerfile.gateway

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run migrations and start server
CMD ["sh", "-c", "alembic upgrade head && uvicorn gateway.main:app --host 0.0.0.0 --port 8000"]
```

### Dockerfile.venue

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "venue_server.py"]
```

### Dockerfile.frontend

```dockerfile
# Build stage
FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN npm install -g pnpm && pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# Production stage
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY ../infra/nginx/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

---

## 3. Multi-Cloud Venue Deployment (Production-Style)

For the FYP panel demo, venues are deployed across 3 cloud providers:

```
┌─────────────────────────────────────────────────┐
│                    AWS (us-east-1)               │
│  ┌─────────────┐  ┌─────────────┐               │
│  │ V1: Alpha   │  │ V4: Delta   │               │
│  │ Exchange    │  │ Prime       │               │
│  │ :8001       │  │ :8004       │               │
│  └─────────────┘  └─────────────┘               │
│                                                  │
│  ┌──────────────────────────────────────┐        │
│  │ Platform Cluster (primary)           │        │
│  │ Gateway + Services + RAFT Leader     │        │
│  └──────────────────────────────────────┘        │
└──────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│                    GCP (us-central1)              │
│  ┌─────────────┐  ┌─────────────┐               │
│  │ V2: Beta    │  │ V5: Epsilon │               │
│  │ Liquidity   │  │ Pool        │               │
│  │ :8002       │  │ :8005       │               │
│  └─────────────┘  └─────────────┘               │
└──────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│                    Azure (eastus)                 │
│  ┌─────────────┐                                 │
│  │ V3: Gamma   │                                 │
│  │ Markets     │                                 │
│  │ :8003       │                                 │
│  │ (degraded)  │                                 │
│  └─────────────┘                                 │
└──────────────────────────────────────────────────┘
```

### Cloud Deployment Steps

Each venue is containerized and deployed as a single container instance:

**AWS (EC2 or ECS):**
```bash
# Build and push to ECR
docker build -t venue-simulator -f infra/docker/Dockerfile.venue ./venues
docker tag venue-simulator:latest <account>.dkr.ecr.us-east-1.amazonaws.com/venue-simulator:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/venue-simulator:latest

# Run V1
docker run -d -e VENUE_ID=V1 -e VENUE_PORT=8001 -e VENUE_PROFILE=alpha_exchange -p 8001:8001 venue-simulator
```

**GCP (Cloud Run or GCE):**
```bash
gcloud run deploy venue-v2 --image=gcr.io/<project>/venue-simulator --port=8002 \
  --set-env-vars="VENUE_ID=V2,VENUE_PORT=8002,VENUE_PROFILE=beta_liquidity"
```

**Azure (Container Instances):**
```bash
az container create --name venue-v3 --image <acr>.azurecr.io/venue-simulator \
  --ports 8003 --environment-variables VENUE_ID=V3 VENUE_PORT=8003 VENUE_PROFILE=gamma_markets VENUE_DEGRADED=true
```

---

## 4. Environment Variables

### .env.example

```env
# ─── Application ─────────────────────────────
APP_NAME=Apna Exchange
DEBUG=false
API_HOST=0.0.0.0
API_PORT=8000

# ─── Database ────────────────────────────────
POSTGRES_URL=postgresql+asyncpg://sor_user:sor_password@localhost:5432/apna_exchange
REDIS_URL=redis://localhost:6379/0

# ─── Venues ──────────────────────────────────
VENUE_V1_URL=http://localhost:8001
VENUE_V2_URL=http://localhost:8002
VENUE_V3_URL=http://localhost:8003
VENUE_V4_URL=http://localhost:8004
VENUE_V5_URL=http://localhost:8005

# ─── Risk ────────────────────────────────────
DEFAULT_MAX_ORDER_SIZE=10000
DEFAULT_MAX_POSITION=50000
DEFAULT_MAX_NOTIONAL=1000000
DEFAULT_MAX_ORDERS_PER_SECOND=50

# ─── Market Data ─────────────────────────────
MARKET_DATA_POLL_INTERVAL_MS=100
DATA_STALENESS_THRESHOLD_MS=2000

# ─── RAFT ────────────────────────────────────
RAFT_NODE_ID=1
RAFT_CLUSTER_NODES=localhost:9001,localhost:9002,localhost:9003
RAFT_ELECTION_TIMEOUT_MS=300
RAFT_HEARTBEAT_INTERVAL_MS=50

# ─── Auth ────────────────────────────────────
JWT_SECRET=change-me-in-production-use-a-long-random-string
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=60

# ─── Frontend ────────────────────────────────
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws
```

---

## 5. Commands Quick Reference

```bash
# Start everything (Docker)
docker-compose up -d

# Start everything (rebuild)
docker-compose up -d --build

# Stop everything
docker-compose down

# View logs
docker-compose logs -f gateway
docker-compose logs -f venue-v3

# Run database migrations
docker-compose exec gateway alembic upgrade head

# Seed initial data
docker-compose exec gateway python -m backend.shared.database.seed

# Reset database
docker-compose down -v  # removes volumes
docker-compose up -d
```
