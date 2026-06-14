const API_BASE = 'http://127.0.0.1:8000'

let accessToken: string | null = null

export function setToken(token: string | null) {
  accessToken = token
  if (token) {
    sessionStorage.setItem('poc.token', token)
  } else {
    sessionStorage.removeItem('poc.token')
  }
}

export function getToken(): string | null {
  if (accessToken) return accessToken
  const stored = sessionStorage.getItem('poc.token')
  if (stored) accessToken = stored
  return accessToken
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ message: resp.statusText }))
    throw new Error(body.message || body.error || body.detail || resp.statusText)
  }

  return resp.json()
}

export interface User {
  id: string
  username: string
  role: string
  name: string
}

export interface AuthResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
}

// Backend venue payload (raw from /api/venues/)
export interface VenueHealth {
  venue_id: string
  name: string
  cloud: string
  status: string                  // HEALTHY | DEGRADED | OFFLINE | BLACKLISTED
  health_score: number            // 0..1
  latency_ms: number
  fill_rate: number               // 0..1
  reject_rate: number             // 0..1
  uptime: number                  // 0..1
  last_checked: string
  error_count: number
}

export interface NBBO {
  symbol: string
  best_bid: number
  best_ask: number
  spread: number
  best_bid_venue: string
  best_ask_venue: string
  timestamp?: string
}

export interface ChildOrder {
  id: string
  venue_id: string
  quantity: number
  filled_quantity: number
  fill_price: number | null
  status: string
}

export interface VenueCandidate {
  venue_id: string
  price: number
  size: number
  rank: number
  eligible: boolean
  excluded_reason?: string | null
  allocated_qty: number
  is_winner: boolean
}

export interface RoutingDecision {
  side: string
  requested_quantity: number
  total_allocated: number
  candidates: VenueCandidate[]
  winning_venues: string[]
  blended_avg_price: number
  worst_price: number
  savings_per_share: number
  total_savings: number
  is_split: boolean
  notes: string[]
}

export interface Order {
  id: string
  symbol: string
  side: string
  quantity: number
  filled_quantity: number
  avg_fill_price: number
  status: string
  order_type: string
  rejection_reason?: string | null
  created_at: string
  updated_at: string
  child_orders: ChildOrder[]
  routing_decision?: RoutingDecision | null
}

// Backend risk status shape (raw)
export interface BackendRiskStatus {
  kill_switch: {
    active: boolean
    last_activated: string | null
    last_deactivated: string | null
    activated_by: string | null
    reason: string | null
    orders_cancelled: number
  }
  positions: Record<string, {
    symbol: string
    net_position: number
    notional: number
    avg_entry_price: number
    unrealized_pnl: number
  }>
  total_notional_exposure: number
  risk_limits: {
    max_order_size: number
    max_position_per_symbol: number
    max_notional_exposure: number
    max_orders_per_second: number
    restricted_symbols: string[]
    restricted_venues: string[]
  }
  recent_checks: Array<{
    order_id: string
    result: string                // APPROVED | REJECTED_*
    checks_passed: string[]
    checks_failed: string[]
    timestamp: string
    latency_us: number
  }>
}

// UI-friendly normalized version
export interface RiskStatus {
  kill_switch_active: boolean
  kill_switch_reason: string | null
  kill_switch_activated_at: string | null
  total_exposure: number
  exposure_limit: number
  exposure_utilization_pct: number
  position_limit: number
  positions: Record<string, {
    net_quantity: number
    market_value: number
    avg_price: number
    unrealized_pnl: number
  }>
  recent_checks: Array<{
    result: string
    reason?: string | null
    check_duration_ms: number
  }>
}

export function normalizeRisk(r: BackendRiskStatus): RiskStatus {
  const total = r.total_notional_exposure ?? 0
  const limit = r.risk_limits?.max_notional_exposure ?? 0
  const positions: RiskStatus['positions'] = {}
  for (const [sym, p] of Object.entries(r.positions ?? {})) {
    positions[sym] = {
      net_quantity: p.net_position,
      market_value: p.notional,
      avg_price: p.avg_entry_price,
      unrealized_pnl: p.unrealized_pnl,
    }
  }
  return {
    kill_switch_active: r.kill_switch?.active ?? false,
    kill_switch_reason: r.kill_switch?.reason ?? null,
    kill_switch_activated_at: r.kill_switch?.last_activated ?? null,
    total_exposure: total,
    exposure_limit: limit,
    exposure_utilization_pct: limit > 0 ? (total / limit) * 100 : 0,
    position_limit: r.risk_limits?.max_position_per_symbol ?? 50000,
    positions,
    recent_checks: (r.recent_checks ?? []).map((c) => ({
      result: c.result,
      reason: c.checks_failed?.length ? c.checks_failed.join(', ') : null,
      check_duration_ms: (c.latency_us ?? 0) / 1000,
    })),
  }
}

// Backend routing status (raw)
export interface BackendRoutingStatus {
  active_strategy: string
  available_strategies: string[]
  routable_venues: string[]
  excluded_venues: Record<string, string>
  stats: {
    orders_routed_today: number
    avg_routing_time_ms: number
    venue_allocation: Record<string, number>
  }
}

export interface RoutingStats {
  orders_routed_today: number
  avg_routing_time_ms: number
  venue_allocation: Record<string, number>
  routable_venues: string[]
  excluded_venues: Record<string, string>
  active_strategy: string
}

export interface ExecutionReport {
  id?: string
  order_id: string
  child_order_id: string
  venue_id: string
  exec_type: string
  symbol: string
  side: string
  quantity: number
  price: number
  venue_latency_ms: number
  timestamp: string
  rejection_reason?: string | null
}

export const api = {
  login: (username: string, password: string) =>
    request<AuthResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  health: () => request<Record<string, unknown>>('/api/health'),

  getVenues: () => request<{ venues: VenueHealth[] }>('/api/venues/'),

  blacklistVenue: (venueId: string, reason: string = 'Manual') =>
    request<unknown>(`/api/venues/${venueId}/blacklist`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  unblacklistVenue: (venueId: string) =>
    request<unknown>(`/api/venues/${venueId}/unblacklist`, { method: 'POST' }),

  getNBBO: (symbol: string) =>
    request<NBBO>(`/api/market-data/nbbo?symbol=${symbol}`),

  getAllNBBO: () =>
    request<{ nbbo: Record<string, NBBO> }>('/api/market-data/nbbo/all'),

  getQuotes: (symbol: string) =>
    request<{ symbol: string; quotes: Record<string, any> }>(`/api/market-data/quotes?symbol=${symbol}`),

  submitOrder: (
    symbol: string,
    side: string,
    quantity: number,
    orderType: string = 'MARKET',
    limitPrice?: number,
  ) =>
    request<Order>('/api/orders/', {
      method: 'POST',
      body: JSON.stringify({
        symbol,
        side,
        quantity,
        order_type: orderType,
        ...(limitPrice !== undefined ? { limit_price: limitPrice } : {}),
      }),
    }),

  getOrders: () =>
    request<{ orders: Order[]; total: number }>('/api/orders/'),

  cancelOrder: (orderId: string) =>
    request<Order>(`/api/orders/${orderId}/cancel`, { method: 'POST' }),

  getRiskStatus: async (): Promise<RiskStatus> => {
    const raw = await request<BackendRiskStatus>('/api/risk/status')
    return normalizeRisk(raw)
  },

  activateKillSwitch: (reason: string) =>
    request<unknown>('/api/risk/kill-switch/activate', {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  deactivateKillSwitch: () =>
    request<unknown>('/api/risk/kill-switch/deactivate', { method: 'POST' }),

  getRoutingStatus: async (): Promise<RoutingStats> => {
    const raw = await request<BackendRoutingStatus>('/api/routing/status')
    return {
      orders_routed_today: raw.stats?.orders_routed_today ?? 0,
      avg_routing_time_ms: raw.stats?.avg_routing_time_ms ?? 0,
      venue_allocation: raw.stats?.venue_allocation ?? {},
      routable_venues: raw.routable_venues ?? [],
      excluded_venues: raw.excluded_venues ?? {},
      active_strategy: raw.active_strategy ?? '',
    }
  },

  getExecutionReports: () =>
    request<{ reports: ExecutionReport[]; total: number; page: number; page_size: number }>('/api/execution-reports/'),
}
