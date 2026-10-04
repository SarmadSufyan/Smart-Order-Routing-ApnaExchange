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
  orders_total?: number           // 0 -> fill/reject rates are undefined, show "—"
  // Live-exchange diagnostics (measured, not estimated)
  latency_p50_ms?: number
  latency_p99_ms?: number
  mode?: string                   // testnet | mainnet
  feed_connected?: boolean
  feed_age_ms?: number | null
  books_synced?: number
  books_total?: number
  resyncs?: number
  reconnects?: number
  degraded_mode?: boolean
  data_mode?: string              // where the order book comes from
  execution_available?: boolean   // false = data-only venue (e.g. Coinbase: no real testnet)
  trading_enabled?: boolean       // execution available AND API keys present
  execution_note?: string | null  // why the venue can't trade, if it can't
  // Per symbol: does the venue's execution (testnet) book track the real market?
  execution_coherence?: Record<string, CoherenceState>
}

export interface CoherenceState {
  status: 'OK' | 'DIVERGED' | 'EMPTY' | 'UNKNOWN' | 'SAME_BOOK'
  divergence_bps: number | null
  exec_bid?: number | null
  exec_ask?: number | null
  checked_at?: string | null
}

export interface WalletAsset {
  asset: string
  free: number
  used: number
  total: number
  price_usdt: number | null
  value_usdt: number | null
}

export interface VenueWallet {
  venue_id: string
  name: string
  mode: string
  assets: WalletAsset[]
  total_value_usdt: number
  other_assets_count: number
  error: string | null
  updated_at: string
}

export interface AssetTotal {
  asset: string
  total: number
  free: number
  value_usdt: number | null
  by_venue: Record<string, number>
}

export interface WalletSnapshot {
  as_of: string | null
  mode: string
  total_value_usdt: number
  tracked_assets: string[]
  venues: VenueWallet[]
  assets: AssetTotal[]
}

// ── Portfolio book of record (P&L + reconciliation) ─────────────────
export interface AssetPnL {
  asset: string
  quantity: number                 // expected holdings from our ledger
  opening_quantity: number
  avg_cost: number | null
  mark: number | null
  value_usdt: number | null
  realized_pnl: number
  unrealized_pnl: number | null
  fees_usdt: number
  total_pnl: number | null
  hold_pnl: number | null
  by_venue: Record<string, number>
}

export type ReconStatus = 'OK' | 'BREAK' | 'PENDING' | 'UNKNOWN' | 'NO_BASELINE'

export interface ReconItem {
  asset: string
  expected: number
  actual: number | null
  difference: number | null
  difference_usdt: number | null
  status: ReconStatus
}

export interface VenueReconciliation {
  venue_id: string
  name: string
  status: ReconStatus
  baseline_at: string | null
  baseline_set_by: string | null
  note: string | null
  items: ReconItem[]
}

export interface PortfolioPnL {
  as_of: string
  mode: string
  since: string | null
  complete: boolean
  unpriced_assets: string[]
  opening_value_usdt: number | null
  value_usdt: number | null
  total_pnl: number | null
  hold_pnl: number | null
  trading_pnl: number | null
  realized_pnl: number | null
  unrealized_pnl: number | null
  fees_usdt: number
  fills_applied: number
  fills_before_baseline: number
  assets: AssetPnL[]
  reconciliation: VenueReconciliation[]
  notes: string[]
}

export interface ConcentrationItem {
  kind: 'asset' | 'venue'
  key: string
  name: string
  value_usdt: number
  share_pct: number
  limit_pct: number
  breached: boolean
}

export interface PortfolioRiskState {
  as_of: string | null
  trading_pnl: number | null
  loss_limit_usdt: number
  loss_used_pct: number | null
  loss_limit_breached: boolean
  concentration: ConcentrationItem[]
  alerts: string[]
}

export interface AuditEvent {
  seq: number
  ts: string
  event_type: string
  actor: string | null
  order_id: string | null
  venue_id: string | null
  symbol: string | null
  summary: string
  payload: Record<string, unknown>
}

export interface AuditEventList {
  events: AuditEvent[]
  has_more: boolean
  store: string
}

// ── Algo (parent) orders — TWAP ──────────────────────────────────────
export type AlgoStatus = 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'EXPIRED' | 'CANCELLED' | 'FAILED'

export interface TwapParams {
  symbol: string
  side: string
  quantity: number
  duration_s: number
  slices: number
  limit_price?: number | null
  jitter_pct?: number
}

export interface TwapSlice {
  index: number
  scheduled_at: string
  target_cumulative: number
  sent_at: string | null
  quantity: number
  filled: number
  avg_price: number
  mid_at_send: number | null
  order_id: string | null
  venues: string[]
  status: 'PENDING' | 'FILLED' | 'PARTIAL' | 'UNFILLED' | 'SKIPPED'
  note: string | null
}

export interface TwapState {
  id: string
  kind: string
  created_by: string
  created_at: string
  updated_at: string
  status: AlgoStatus
  status_reason: string | null
  params: TwapParams
  interval_s: number
  nominal_slice: number
  lot_step: number
  started_at: string | null
  ended_at: string | null
  arrival_mid: number | null
  filled_quantity: number
  avg_fill_price: number
  fees_usdt: number
  slices: TwapSlice[]
  arrival_slippage_bps: number | null
  interval_twap: number | null
  vs_interval_twap_bps: number | null
  notes: string[]
}

export interface VenueQuote {
  venue_id: string
  symbol: string
  bid_price: number
  ask_price: number
  bid_size: number
  ask_size: number
  last_price: number
  volume: number
  timestamp?: string
  is_stale: boolean
}

export interface NBBO {
  symbol: string
  best_bid: number
  best_ask: number
  spread: number
  best_bid_venue: string
  best_ask_venue: string
  best_bid_size: number
  best_ask_size: number
  timestamp?: string
  venue_quotes: Record<string, VenueQuote>
}

// ── Arbitrage detector (5c) — detection only, never trades ──────────
/** One direction between two venues: buy at one's best ask, sell at the other's best bid. */
export interface ArbEdge {
  symbol: string
  buy_venue: string
  sell_venue: string
  buy_price: number
  sell_price: number
  gross_bps: number        // > 0 = the two books are crossed
  fee_bps: number          // both taker fees
  net_bps: number          // gross minus fees
  size: number             // base units at the top of both books
  net_usdt: number         // negative = a loss
  fundable_size: number | null  // what our testnet/demo balances could do; null = unknown
  limited_by: string[]
}

/** A run of consecutive scans in which one direction stayed crossed. */
export interface ArbWindow {
  symbol: string
  buy_venue: string
  sell_venue: string
  opened_at: string
  closed_at: string | null  // null = still open
  duration_s: number
  scans: number
  peak_gross_bps: number
  peak: ArbEdge
  profitable: boolean
}

export interface ArbSymbolStats {
  symbol: string
  scans: number
  crossed_scans: number
  profitable_scans: number
  sum_best_gross_bps: number  // ÷ scans = average
  sum_best_net_bps: number
  windows: number
  profitable_windows: number
  window_seconds: number
  duration_buckets: Record<string, number>
  peak_gross_buckets: Record<string, number>
  max_gross: ArbEdge | null
  max_gross_at: string | null
  max_net: ArbEdge | null
  max_net_at: string | null
}

export interface ArbSymbolStatus {
  symbol: string
  venues: string[]           // venues with a live quote right now
  best: ArbEdge | null       // best net edge right now
  crossed_directions: number
  open_windows: ArbWindow[]
  stats: ArbSymbolStats
  summary: string
}

export interface ArbStatus {
  enabled: boolean
  running: boolean
  scan_interval_ms: number
  min_net_bps: number
  fees_bps: Record<string, number>
  venue_names: Record<string, string>
  stats_since: string
  symbols: ArbSymbolStatus[]
  summary: string
  note: string
}

export interface ArbWindowList {
  open: ArbWindow[]
  windows: ArbWindow[]  // closed, newest first
}

export interface ChildOrder {
  id: string
  venue_id: string
  quantity: number
  filled_quantity: number
  fill_price: number | null
  status: string
  limit_price?: number | null     // IOC limit actually sent (price protection)
  client_order_id?: string | null // id sent to the venue (idempotency)
  exchange_order_id?: string | null
  fee?: number
  fee_asset?: string | null
  reject_reason?: string | null
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
  base_asset?: string
  quote_asset?: string
  arrival_mid?: number
  best_price?: number
  expected_slippage_bps?: number
  levels_used?: number
  quantity_rounded_from?: number | null
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
  submitted_by?: string | null
  parent_id?: string | null   // the algo order (e.g. TWAP) this is a slice of
}

export interface RiskLimits {
  max_order_size: number
  max_order_notional: number
  max_algo_notional_usdt: number
  max_position_per_symbol: number
  max_notional_exposure: number
  max_orders_per_second: number
  restricted_symbols: string[]
  restricted_venues: string[]
  max_trading_loss_usdt: number
  max_asset_concentration_pct: number
  max_venue_concentration_pct: number
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
  risk_limits: RiskLimits
  recent_checks: Array<{
    order_id: string
    result: string                // APPROVED | REJECTED_*
    checks_passed: string[]
    checks_failed: string[]
    timestamp: string
    latency_us: number
  }>
  portfolio?: PortfolioRiskState | null
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
  limits: RiskLimits | null
  portfolio: PortfolioRiskState | null
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
    limits: r.risk_limits ?? null,
    portfolio: r.portfolio ?? null,
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
  exchange_order_id?: string | null
  fee?: number
  fee_asset?: string | null
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

  degradeVenue: (venueId: string) =>
    request<unknown>(`/api/venues/${venueId}/degrade`, { method: 'POST' }),

  recoverVenue: (venueId: string) =>
    request<unknown>(`/api/venues/${venueId}/recover`, { method: 'POST' }),

  getNBBO: (symbol: string) =>
    request<NBBO>(`/api/market-data/nbbo?symbol=${encodeURIComponent(symbol)}`),

  getAllNBBO: () =>
    request<{ nbbo: Record<string, NBBO> }>('/api/market-data/nbbo/all'),

  getQuotes: (symbol: string) =>
    request<{ symbol: string; quotes: Record<string, VenueQuote> }>(`/api/market-data/quotes?symbol=${encodeURIComponent(symbol)}`),

  getArbitrageStatus: () => request<ArbStatus>('/api/arbitrage/status'),

  getArbitrageWindows: (params: { symbol?: string; profitable_only?: boolean; limit?: number } = {}) => {
    const q = new URLSearchParams()
    if (params.symbol) q.set('symbol', params.symbol)
    if (params.profitable_only) q.set('profitable_only', 'true')
    if (params.limit) q.set('limit', String(params.limit))
    return request<ArbWindowList>(`/api/arbitrage/windows?${q.toString()}`)
  },

  getWallet: () => request<WalletSnapshot>('/api/wallet/'),

  refreshWallet: () => request<WalletSnapshot>('/api/wallet/refresh', { method: 'POST' }),

  getPnl: () => request<PortfolioPnL>('/api/wallet/pnl'),

  // Accept the exchanges' current balances as the new opening balances.
  resetBaseline: (venueId?: string) =>
    request<PortfolioPnL>('/api/wallet/baseline', {
      method: 'POST',
      body: JSON.stringify({ venue_id: venueId ?? null }),
    }),

  getAudit: (params: { order_id?: string; event_type?: string; before_seq?: number; limit?: number } = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v))
    return request<AuditEventList>(`/api/audit/?${q.toString()}`)
  },

  getAlgos: () => request<{ algos: TwapState[] }>('/api/algos/'),

  startTwap: (p: TwapParams) =>
    request<TwapState>('/api/algos/twap', { method: 'POST', body: JSON.stringify(p) }),

  controlAlgo: (id: string, action: 'pause' | 'resume' | 'cancel') =>
    request<TwapState>(`/api/algos/${id}/${action}`, { method: 'POST' }),

  updateRiskLimits: (limits: RiskLimits) =>
    request<RiskLimits>('/api/risk/limits', { method: 'PUT', body: JSON.stringify(limits) }),

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
