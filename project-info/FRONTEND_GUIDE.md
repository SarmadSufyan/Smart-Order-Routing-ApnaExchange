# FRONTEND_GUIDE.md — Frontend Architecture & Screen Map

> **Historical document (June 2026).** Written for the first prototype, which routed orders across five simulated stock venues. The project moved to real crypto exchanges in July 2026; this page is kept as the record of milestone M1 and does not describe the current system. Current design: the root [README](../README.md) and the guides listed in [project-info/README.md](README.md).

---

## 1. Overview

The frontend is a React 18 + TypeScript + Vite application styled with Tailwind CSS and shadcn/ui components. The initial code was generated from Figma Make and lives in the `frontend/` directory. The visual design follows trading dashboard conventions: dark theme, monospace fonts for numeric data, green/red for price direction, high-density data presentation.

**IMPORTANT:** The Figma Make code defines the visual design and screen structure. Backend integration (API calls, WebSocket connections, state management) is added on top of this foundation without modifying the design language.

---

## 2. State Management Architecture

Three layers of state, each with a specific tool:

```
┌────────────────────────────────────────────────┐
│           Frontend State Architecture           │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  TanStack Query (Server State)            │   │
│  │  • REST API data (orders, venues, risk)   │   │
│  │  • Automatic cache, refetch, stale-while  │   │
│  │  • Used for: GET requests, paginated data │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  Zustand (Real-Time / WebSocket State)    │   │
│  │  • Live market data, venue health updates │   │
│  │  • Kill switch status, risk alerts        │   │
│  │  • Updated via WebSocket messages         │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  React useState (Local UI State)          │   │
│  │  • Form inputs, modal open/close          │   │
│  │  • Filter selections, tab state           │   │
│  │  • Component-specific ephemeral state     │   │
│  └──────────────────────────────────────────┘   │
└────────────────────────────────────────────────┘
```

### Zustand Store Structure

A single WebSocket connection pushes messages to typed Zustand slices:

```typescript
// stores/useVenueStore.ts
interface VenueState {
  venues: Record<string, VenueHealth>;
  updateVenue: (venueId: string, data: VenueHealth) => void;
  getHealthyVenues: () => VenueHealth[];
}

// stores/useMarketDataStore.ts
interface MarketDataState {
  quotes: Record<string, Record<string, VenueQuote>>; // symbol → venueId → quote
  nbbo: Record<string, NBBO>;                          // symbol → NBBO
  updateQuote: (venueId: string, quote: VenueQuote) => void;
  updateNBBO: (nbbo: NBBO) => void;
}

// stores/useRiskStore.ts
interface RiskState {
  killSwitchActive: boolean;
  alerts: RiskAlert[];
  riskMetrics: RiskMetrics;
  setKillSwitch: (status: KillSwitchStatus) => void;
  addAlert: (alert: RiskAlert) => void;
}

// stores/useOrderStore.ts
interface OrderState {
  liveOrders: Record<string, Order>;
  updateOrder: (orderId: string, update: Partial<Order>) => void;
}
```

### WebSocket Message Router

```typescript
// services/websocket.ts
class WebSocketClient {
  private ws: WebSocket | null = null;
  private stores = {
    venue_health: useVenueStore.getState().updateVenue,
    market_data: useMarketDataStore.getState().updateQuote,
    nbbo_update: useMarketDataStore.getState().updateNBBO,
    order_update: useOrderStore.getState().updateOrder,
    risk_alert: useRiskStore.getState().addAlert,
    kill_switch: useRiskStore.getState().setKillSwitch,
  };

  connect() {
    this.ws = new WebSocket("ws://localhost:8000/ws");
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      const handler = this.stores[msg.type];
      if (handler) handler(msg.data);
    };
  }
}
```

---

## 3. Feature-Based Folder Structure

Each feature is self-contained with its own components, hooks, types, and (optionally) API functions:

```
frontend/src/features/
├── dashboard/
│   ├── Dashboard.tsx              # Page component
│   ├── components/
│   │   ├── MetricCard.tsx         # Individual metric display
│   │   ├── VenueStatusBar.tsx     # Venue health dots in header
│   │   ├── OrderSummary.tsx       # Recent order activity
│   │   └── SystemHealth.tsx       # System-wide health gauges
│   ├── hooks/
│   │   └── useDashboardData.ts    # Combines queries + store data
│   └── types.ts                   # Dashboard-specific types
│
├── order-blotter/
│   ├── OrderBlotter.tsx
│   ├── components/
│   │   ├── OrderTable.tsx         # AG Grid order table
│   │   ├── OrderDetailPanel.tsx   # Slide-out detail view
│   │   └── OrderFilters.tsx       # Status/symbol/date filters
│   ├── hooks/
│   │   └── useOrders.ts
│   └── types.ts
│
├── market-data/
│   ├── MarketData.tsx
│   ├── components/
│   │   ├── PriceChart.tsx         # TradingView Lightweight Chart
│   │   ├── OrderBookDisplay.tsx   # Bid/ask depth visualization
│   │   ├── NBBOPanel.tsx          # Best bid/offer across venues
│   │   └── VenueQuoteTable.tsx    # Per-venue quote comparison
│   ├── hooks/
│   │   └── useMarketData.ts
│   └── types.ts
│
├── venue-connectivity/
│   ├── VenueConnectivity.tsx
│   ├── components/
│   │   ├── VenueCard.tsx          # Individual venue status card
│   │   ├── LatencyGraph.tsx       # Per-venue latency over time
│   │   └── VenueDetailModal.tsx   # Detailed venue metrics
│   ├── hooks/
│   │   └── useVenueHealth.ts
│   └── types.ts
│
├── risk-manager/
│   ├── RiskManager.tsx
│   ├── components/
│   │   ├── RiskGauge.tsx          # Arc gauge for risk metrics
│   │   ├── PositionTable.tsx      # Current positions per symbol
│   │   ├── PreTradeChecks.tsx     # Recent check results
│   │   └── ExposureChart.tsx      # Notional exposure visualization
│   ├── hooks/
│   │   └── useRiskData.ts
│   └── types.ts
│
├── kill-switch/
│   ├── KillSwitch.tsx
│   ├── components/
│   │   ├── KillSwitchButton.tsx   # Big red button with confirmation
│   │   ├── AlertsFeed.tsx         # Real-time alert stream
│   │   ├── AlertHistoryTable.tsx  # Historical alerts
│   │   └── KillSwitchLog.tsx      # Activation/deactivation history
│   ├── hooks/
│   │   └── useKillSwitch.ts
│   └── types.ts
│
├── routing-engine/
│   ├── RoutingEngine.tsx
│   ├── components/
│   │   ├── RoutingVisualization.tsx  # Visual flow of order routing
│   │   ├── StrategySelector.tsx      # Active strategy display
│   │   └── VenueAllocationChart.tsx  # Pie/bar chart of venue allocation
│   ├── hooks/
│   │   └── useRouting.ts
│   └── types.ts
│
├── execution-reports/
│   ├── ExecutionReports.tsx
│   ├── components/
│   │   ├── ExecutionTable.tsx      # AG Grid execution report table
│   │   └── ExecutionDetail.tsx     # Individual report detail
│   ├── hooks/
│   │   └── useExecutionReports.ts
│   └── types.ts
│
└── auth/
    ├── Login.tsx
    ├── components/
    │   └── LoginForm.tsx
    ├── hooks/
    │   └── useAuth.ts
    └── types.ts
```

---

## 4. Screen Map

### Complete Screen Inventory (18 screens)

| # | Screen | Route | POC? | Description |
|---|---|---|---|---|
| 1 | Login | `/login` | ✅ | Authentication entry point |
| 2 | Dashboard | `/` | ✅ | System overview: venue dots, key metrics, recent activity |
| 3 | Order Blotter | `/orders` | | Live order table with filters, detail panel |
| 4 | Market Data | `/market-data` | | Price charts, order books, NBBO display |
| 5 | Venue Connectivity | `/venues` | ✅ | Per-venue health cards, latency graphs |
| 6 | Risk Manager | `/risk` | ✅ | Risk gauges, positions, pre-trade check log |
| 7 | Kill Switch & Alerts | `/kill-switch` | ✅ | Emergency button, alert feed, history |
| 8 | Routing Engine | `/routing` | | Strategy visualization, venue allocation |
| 9 | Execution Reports | `/execution` | | Fill/reject reports with detail view |
| 10 | RAFT Cluster | `/raft` | | Node status, leader election, log entries |
| 11 | Policy Engine | `/policies` | | Rule configuration, action history |
| 12 | Audit Log | `/audit` | | Full audit trail with search/filter |
| 13 | Settings | `/settings` | | System configuration, risk limits |
| 14 | Analytics | `/analytics` | | Historical performance charts |
| 15 | Order Entry | `/new-order` | | Manual order submission form |
| 16 | Venue Admin | `/venues/admin` | | Degrade/recover venue controls |
| 17 | User Management | `/users` | | User accounts, roles (admin only) |
| 18 | System Health | `/system` | | Infrastructure metrics, service status |

### Screen Wireframe Descriptions

**Dashboard (/):**
```
┌──────────────────────────────────────────────────┐
│ TOPBAR: Logo │ Venue Dots (5) │ Kill Switch LED │ │
├──────────┬───────────────────────────────────────┤
│ SIDEBAR  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ │
│          │  │ Orders   │ │ Fill    │ │ Avg     │ │
│ Dashboard│  │ Today    │ │ Rate    │ │ Latency │ │
│ Orders   │  │ 1,247    │ │ 94.2%   │ │ 12.3ms  │ │
│ Market   │  └─────────┘ └─────────┘ └─────────┘ │
│ Venues   │                                       │
│ Risk     │  ┌───────────────────────────────────┐ │
│ Kill SW  │  │ RECENT ORDERS (live table)        │ │
│ Routing  │  │ ID │ Symbol │ Side │ Qty │ Status │ │
│ Exec Rpt │  │ ...                               │ │
│ Settings │  └───────────────────────────────────┘ │
│          │                                       │
│          │  ┌────────────────┐ ┌────────────────┐ │
│          │  │ VENUE HEALTH   │ │ RAFT STATUS    │ │
│          │  │ V1 ● V2 ● V3 ◉│ │ Leader: Node 1 │ │
│          │  │ V4 ● V5 ●     │ │ Term: 47       │ │
│          │  └────────────────┘ └────────────────┘ │
└──────────┴───────────────────────────────────────┘
```

---

## 5. API Client

```typescript
// services/api.ts
const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

class ApiClient {
  private token: string | null = null;

  setToken(token: string) {
    this.token = token;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(this.token && { Authorization: `Bearer ${this.token}` }),
    };

    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { ...headers, ...options.headers },
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: "Unknown error" }));
      throw new ApiError(response.status, error.message || error.error);
    }

    return response.json();
  }

  // Orders
  getOrders = (params?: OrderFilters) =>
    this.request<Order[]>(`/api/orders?${toQueryString(params)}`);

  submitOrder = (order: OrderCreate) =>
    this.request<Order>("/api/orders", { method: "POST", body: JSON.stringify(order) });

  cancelOrder = (orderId: string) =>
    this.request<void>(`/api/orders/${orderId}/cancel`, { method: "POST" });

  // Venues
  getVenues = () => this.request<VenueHealth[]>("/api/venues");
  getVenueDetail = (id: string) => this.request<VenueDetail>(`/api/venues/${id}`);

  // Market Data
  getNBBO = (symbol: string) => this.request<NBBO>(`/api/market-data/nbbo?symbol=${symbol}`);
  getQuotes = (symbol: string) => this.request<VenueQuote[]>(`/api/market-data/quotes?symbol=${symbol}`);

  // Risk
  getRiskStatus = () => this.request<RiskStatus>("/api/risk/status");
  getKillSwitchStatus = () => this.request<KillSwitchStatus>("/api/risk/kill-switch");
  activateKillSwitch = (reason: string) =>
    this.request<void>("/api/risk/kill-switch/activate", {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  deactivateKillSwitch = () =>
    this.request<void>("/api/risk/kill-switch/deactivate", { method: "POST" });

  // Auth
  login = (credentials: LoginRequest) =>
    this.request<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(credentials),
    });
}

export const api = new ApiClient();
```

---

## 6. Component Conventions

### Visual Design Tokens

```
Background:      #0a0a0f (near-black)
Surface:         #12121a (card backgrounds)
Border:          #1e1e2e (subtle borders)
Text Primary:    #e0e0e0 (high contrast)
Text Secondary:  #888899 (muted)
Accent Green:    #00c853 (healthy, buy, positive)
Accent Red:      #ff1744 (critical, sell, negative)
Accent Yellow:   #ffc107 (degraded, warning)
Accent Blue:     #2196f3 (info, links)
Font Body:       Inter, system-ui
Font Mono:       JetBrains Mono, monospace (all numeric data)
```

### Trading Dashboard Rules

- All prices, quantities, and latency values use monospace font
- Positive values (gains, healthy) are green; negative (losses, errors) are red
- Numbers update in place — no layout shift on data change
- Tables are dense (compact row height) — traders need to see many rows
- Timestamps in UTC, formatted as `HH:mm:ss.SSS` for real-time, `YYYY-MM-DD HH:mm` for historical
- Loading states use skeleton placeholders, not spinners
- Error states show the error inline, not as a modal popup

---

## 7. Connecting Frontend to Backend (Integration Steps)

### Step 1: Environment Variable

```env
# frontend/.env
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws
```

### Step 2: Replace Mock Data (Per Screen)

For each screen, follow this pattern:

```typescript
// BEFORE (mock data)
const venues = MOCK_VENUES;

// AFTER (real API + real-time)
const { data: venues, isLoading } = useQuery({
  queryKey: ["venues"],
  queryFn: () => api.getVenues(),
  refetchInterval: 5000,  // Poll every 5s as fallback
});

// ALSO: subscribe to real-time updates via Zustand
const liveVenues = useVenueStore((s) => s.venues);

// Merge: use live data when available, fall back to REST
const mergedVenues = Object.values(liveVenues).length > 0
  ? Object.values(liveVenues)
  : venues ?? [];
```

### Step 3: WebSocket Connection (App-level)

```typescript
// In App.tsx or a top-level provider
useEffect(() => {
  const wsClient = new WebSocketClient();
  wsClient.connect();
  return () => wsClient.disconnect();
}, []);
```
