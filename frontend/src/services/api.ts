// Which gateway this page talks to. Default: the one on this machine. `npm run dev:lab-server` builds the page
// for the lab server instead (frontend/.env.lab-server: through the SSH tunnel), with a badge naming it.
export const API_BASE: string = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'
export const SERVER_LABEL: string | undefined = import.meta.env.VITE_SERVER_LABEL || undefined
export const SERVER_IS_REAL_MONEY: boolean = import.meta.env.VITE_SERVER_TONE === 'danger'

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

// Set by the auth store: what to do when the gateway rejects our login token.
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
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
    // An expired or invalid token fails every later call too: go back to the
    // login page instead of leaving every page silently empty.
    if (resp.status === 401 && token && path !== '/api/auth/login') onUnauthorized?.()
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
  key_note?: string | null        // real money: the exchange's verdict on our API key (trade-only?)
  fee_note?: string | null        // real money: the account's own taker fee, as the exchange reports it
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

// ── Research and prediction tools (Phase B) ──────────────────────────
export type ResearchTf = '1m' | '1h' | '1d'

export interface OosScores {
  n: number
  accuracy: number
  baseline_accuracy: number
  up_rate: number
  brier: number
  brier_baseline: number
  brier_skill: number
  log_loss: number
  log_loss_baseline: number
  p_value: number
  edge: boolean
  calibration: { bucket: string; said: number; happened: number; n: number }[]
}

export interface LiveScores {
  n: number
  hit_rate?: number
  up_rate?: number
  brier?: number
  range_coverage80?: number | null
  n_with_edge?: number
  hit_rate_with_edge?: number | null
  since_ts?: number
}

export interface IndicatorSignal { name: string; value: number; direction: number; text: string }

export interface HorizonForecast {
  horizon: ResearchTf
  label: string
  unavailable?: string
  as_of_ts?: number
  price?: number
  p_up?: number
  lean?: 'up' | 'down'
  edge?: boolean
  call?: string
  range80?: [number, number] | null
  range50?: [number, number] | null
  typical_move_pct?: number
  drivers?: { feature: string; text: string; push: number }[]
  signals?: IndicatorSignal[]
  model?: { out_of_sample: OosScores | null; range_coverage80: number | null; trained_bars: number;
            trained_last_ts: number; source: string | null }
  live?: LiveScores
}

export interface ForecastResponse { symbol: string; horizons: HorizonForecast[]; disclaimer: string }

export interface ScoreboardRow {
  symbol: string
  horizon: ResearchTf
  label: string
  out_of_sample: OosScores | null
  range_coverage80: number | null
  live: LiveScores
}

export interface ResearchChart {
  symbol: string
  timeframe: ResearchTf
  ts: number[]
  open: (number | null)[]; high: (number | null)[]; low: (number | null)[]; close: (number | null)[]
  volume: (number | null)[]
  sma20: (number | null)[]; sma50: (number | null)[]
  bb_upper: (number | null)[]; bb_mid: (number | null)[]; bb_lower: (number | null)[]
  rsi: (number | null)[]; macd: (number | null)[]; macd_signal: (number | null)[]; macd_hist: (number | null)[]
  atr: (number | null)[]
}

export interface BookSignals {
  symbol: string
  available: boolean
  text: string
  mid?: number
  spread_bps?: number
  imbalance_10bps?: number | null
  imbalance_25bps?: number | null
  microprice?: number | null
  microprice_vs_mid_bps?: number | null
  bid_depth_10bps_usdt?: number
  ask_depth_10bps_usdt?: number
  venues?: string[]
  direction?: number
  note?: string
}

export type BacktestRule = 'buy_hold' | 'sma_cross' | 'rsi_reversion' | 'bollinger' | 'macd_cross' | 'momentum'
  | 'donchian_breakout' | 'model'

// ── Technique study (GET /api/research/techniques) ──────────────
export interface TechniqueStats {
  total_return: number; buy_hold_return: number; sharpe: number | null; max_drawdown: number
  trades: number; win_rate: number | null; exposure: number; start_ts: number; end_ts: number
}
export interface TechniqueRow {
  key: string; name: string; family: string; idea: string; strategy: string; params: Record<string, unknown>
  signal: 'in' | 'out'; fresh: boolean; verdict: 'worked' | 'mixed' | "didn't work" | 'too few trades'; why: string
  full: TechniqueStats; recent: TechniqueStats
}
export interface TechniqueStudy {
  symbol: string; timeframe: ResearchTf; fee_bps: number; slippage_bps: number; slippage_source: string
  bars: number; from_ts: number; to_ts: number; recent_from_ts: number
  hold: { full: TechniqueStats; recent: TechniqueStats }
  techniques: TechniqueRow[]; worked_in: string[]; worked_out: string[]; note: string
}

// ── Copilot (/api/copilot/*) ────────────────────────────────────
export interface CopilotStatus {
  available: boolean; provider: string; model: string | null; reason: string | null
  environment: string; auto_allowed: boolean; auto_max_usdt: number; analysis_available: boolean
}
export interface AnalysisCard {
  symbol: string; verdict: 'BUY' | 'NO_TRADE' | 'REDUCE'; confidence: string; score: number; summary: string
  for: string[]; against: string[]; notes: string[]; disclaimer: string
  trade: { side: string; quantity?: number; entry?: number; stop?: number; target?: number;
           notional_usdt?: number; holding_value_usdt?: number } | null
  techniques?: { name: string; family: string; verdict: string; signal: string; return_full: number;
                 return_recent: number; why: string }[]
  hold_return_recent?: number | null
  study_note?: string | null
  overview?: { symbol: string; verdict: string; score: number; summary: string }[]
}
export type CopilotTurnStatus = 'PROPOSED' | 'ANSWERED' | 'CLARIFY' | 'REFUSED' | 'CONFIRMED' | 'FAILED'
  | 'CANCELLED' | 'EXPIRED'
export interface CopilotTurn {
  id: string; status: CopilotTurnStatus; text: string; message: string
  kind: 'order' | 'twap' | 'algo' | 'bot' | null
  answer: string | null; analysis: AnalysisCard | null; topic: string | null
  warnings: string[]; conversion: string | null; estimated_notional_usdt: number | null
  algo_reasons?: string[]  // Auto algorithm: why the engine chose this way to execute
  expires_at: string | null; auto: boolean; auto_executed: boolean; model: string | null
  result_message: string | null; result_order_id: string | null; result_algo_id: string | null
  result_bot_id: string | null
}

export interface BacktestResponse {
  symbol: string; timeframe: ResearchTf; strategy: BacktestRule; params: Record<string, unknown>
  fee_bps: number; slippage_bps: number; capital: number
  bars: number; start_ts: number; end_ts: number
  total_return: number; buy_hold_return: number; cagr: number | null
  max_drawdown: number; buy_hold_max_drawdown: number; sharpe: number | null
  exposure: number; trades: number; win_rate: number | null; avg_trade_return: number | null
  fees_paid: number; final_equity: number
  equity_curve: { ts: number; equity: number; buy_hold: number | null }[]
  trade_list: { entry_ts: number; exit_ts: number; entry: number; exit: number; return: number; open?: boolean }[]
  note: string
}

export interface TradePlanResponse {
  symbol: string; timeframe: ResearchTf; side: string; equity: number; risk_pct: number
  k_atr: number; rr: number; max_bars: number; atr: number; cost_bps_round_trip: number
  plan: { entry: number; stop: number; target: number; quantity: number; notional: number
          risk_amount: number; reward_amount: number; fees_round_trip: number; verdict: 'positive' | 'skip'
          reasons: string[] }
  history: { samples: number; wins: number; losses: number; timeouts: number; win_rate: number | null
             expectancy_r: number | null; break_even_win_rate: number; kelly_fraction: number | null }
  forecast: { p_up: number; edge: boolean; call: string } | null
  disclaimer: string
}

// ── Algo (parent) orders — TWAP, VWAP, IS, POV, ICEBERG ─────────────
export type AlgoStatus = 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'EXPIRED' | 'CANCELLED' | 'FAILED'
export type AlgoKind = 'TWAP' | 'VWAP' | 'IS' | 'POV' | 'ICEBERG'
export type Urgency = 'LOW' | 'MEDIUM' | 'HIGH'

/** Every execution algorithm (backend: shared/models/strategy.py, services/algos/). */
export const ALGO_KINDS: { id: AlgoKind; label: string; hint: string }[] = [
  { id: 'TWAP', label: 'TWAP', hint: 'Equal slices, evenly spaced over the window.' },
  { id: 'VWAP', label: 'VWAP', hint: "Slices follow the market's real volume over the same time on past days; judged against the market's VWAP." },
  { id: 'IS', label: 'Implementation shortfall', hint: 'Almgren–Chriss: front-loaded by urgency to cut exposure to price moves; shows expected cost and risk.' },
  { id: 'POV', label: 'POV (% of volume)', hint: 'Trades a fixed share of the real market volume as it happens — the smallest footprint, no fixed end.' },
  { id: 'ICEBERG', label: 'Iceberg', hint: 'Never shows more than a display size; takes liquidity only at or better than your limit, piece by piece.' },
]

export function algoKindLabel(kind?: string | null): string {
  return ALGO_KINDS.find((k) => k.id === kind)?.label ?? (kind || 'TWAP')
}

export interface TwapParams {
  kind?: AlgoKind
  symbol: string
  side: string
  quantity: number
  duration_s: number
  slices?: number | null
  limit_price?: number | null
  jitter_pct?: number
  routing?: RoutingStrategyName
  volume_days?: number          // VWAP
  urgency?: Urgency             // IS
  participation_pct?: number | null  // POV
  check_interval_s?: number     // POV
  display_qty?: number | null   // ICEBERG
  refresh_s?: number            // ICEBERG
  size_variance_pct?: number    // ICEBERG
}
export type AlgoParams = TwapParams

export interface AlgoRecommendation {
  choice: string                // 'SINGLE' | AlgoKind | 'NONE'
  params: AlgoParams | null
  reasons: string[]
  warnings: string[]
  alternatives: string[]
  metrics: {
    mid?: number; spread_bps?: number; impact_now_bps?: number | null; volume_per_min?: number | null
    volume_venues?: string[]; min_tradable?: number; quantity?: number; notional_usdt?: number
  }
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
  market_vwap?: number | null              // the real market's VWAP over the run
  vs_market_vwap_bps?: number | null
  market_volume?: number | null            // POV: real-market volume since the start
  participation_actual_pct?: number | null // POV
  waiting_reason?: string | null           // POV / ICEBERG: why nothing was sent at the last check
  deadline_at?: string | null              // POV / ICEBERG
  plan?: Record<string, unknown>           // kind-specific numbers (IS cost/risk, VWAP profile, ...)
  notes: string[]
}
export type AlgoState = TwapState

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

// ── RAFT cluster (Phase 6) — GET /api/consensus/status ──────────────
export interface ConsensusNode {
  id: number
  address: string
  reachable: boolean
  role: string | null           // FOLLOWER / CANDIDATE / LEADER
  term: number | null
  leader: number | null         // who this node thinks leads
  commit_index: number | null
  applied_index: number | null
  pid: number | null
  mode: string | null           // testnet / mainnet: must match the gateway's
  rejected_messages: number | null  // forged / too old / replayed messages refused
}

export interface ConsensusStatus {
  enabled: boolean
  signed: boolean               // every RAFT message carries an HMAC-SHA256 signature
  leader: number | null
  majority_reachable: boolean
  nodes: ConsensusNode[]
  pending_writes: string[]      // our changes the cluster hasn't confirmed yet
  synced_index: number          // committed log index this gateway has adopted
  last_sync: string | null
  message: string
}

// ── Pre-flight (Phase 6 risk batch) — POST /api/risk/preflight ──────
export interface PreflightOrder {
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
}

export interface PreflightStress {
  position_after: number         // base units held after the order (signed)
  var_99_usdt: number            // worst loss within the horizon on 99% of simulated paths
  expected_shortfall_usdt: number  // average loss over the worst 1% of paths
  horizon_min: number
  paths: number
  sigma_per_min_pct: number      // measured volatility, % per minute
}

export interface PreflightItem {
  index: number
  symbol: string
  side: string
  quantity: number
  mid_price: number
  result: string                 // APPROVED or REJECTED_<CHECK>
  checks_passed: string[]
  checks_failed: string[]
  message: string | null
  position_after: number         // if this and every earlier order filled
  stress: PreflightStress | null
}

export interface PreflightResponse {
  all_approved: boolean
  approved: number
  rejected: number
  executor: string               // "in-process" or "pool (N processes)"
  elapsed_ms: number
  notes: string[]
  results: PreflightItem[]
}

/** The backend checks at most this many orders in one pre-flight. */
export const PREFLIGHT_MAX_ORDERS = 200

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
  fee_bps?: number                // taker fee charged by this venue
  extra_cost_bps?: number         // smart routing: expected cost of this venue missing
  effective_price?: number        // price incl. fee (+ extra): what the ranking compares
  fill_ratio?: number | null      // smart routing: share of our recent IOC quantity it filled
}

/** How one order is split across exchanges (backend: shared/models/order.py RoutingStrategy). */
export type RoutingStrategyName = 'best_price' | 'liquidity_sweep' | 'smart'

export const ROUTING_STRATEGIES: { id: RoutingStrategyName; label: string; hint: string }[] = [
  { id: 'best_price', label: 'Best price',
    hint: 'Cheapest levels first across every exchange, ranked by price + exchange fee.' },
  { id: 'liquidity_sweep', label: 'Liquidity sweep',
    hint: 'Every exchange at once, in proportion to its depth, under one hard price cap: most likely to fill now, slightly worse price.' },
  { id: 'smart', label: 'Smart',
    hint: 'Best price, plus what each exchange\'s measured fill rate is expected to cost when it misses.' },
]

export function routingStrategyLabel(id?: string | null): string {
  return ROUTING_STRATEGIES.find((s) => s.id === id)?.label ?? 'Best price'
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
  strategy?: string               // best_price | liquidity_sweep | smart
  expected_fees_quote?: number    // taker fees the plan expects to pay (quote asset)
  all_in_avg_price?: number       // blended price incl. fees
  price_cap?: number | null       // liquidity sweep: no child trades beyond this (all-in)
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
  strategy?: string           // routing strategy: best_price | liquidity_sweep | smart
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
  real_money_ceilings?: RiskCeilings | null
}

/** GET /api/health (no login needed): the gateway's environment and each exchange's feed. */
export interface GatewayHealth {
  status: string
  exchange_mode: string             // testnet | mainnet
  exchanges: Record<string, {
    name: string
    mode: string
    feed_connected: boolean
    books_synced: string            // e.g. "5/5"
    trading_enabled: boolean        // API keys configured
  }>
}

/** Real-money hard caps from .env (MAINNET_MAX_*): no limit in force is above them. */
export interface RiskCeilings {
  max_order_notional: number
  max_algo_notional_usdt: number
  max_notional_exposure: number
  max_trading_loss_usdt: number
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
    checks_passed: string[]
    checks_failed: string[]
  }>
  limits: RiskLimits | null
  portfolio: PortfolioRiskState | null
  ceilings: RiskCeilings | null  // real money only
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
      checks_passed: c.checks_passed ?? [],
      checks_failed: c.checks_failed ?? [],
    })),
    limits: r.risk_limits ?? null,
    portfolio: r.portfolio ?? null,
    ceilings: r.real_money_ceilings ?? null,
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

// ── Bots & Lab (Phase C): the automation fund, bots, the practice lab ──
export interface Pocket {
  budget_usdt: number; cash_usdt: number
  coins: Record<string, number>; cost_usdt: Record<string, number>; market_cost_usdt: Record<string, number>
  realized_usdt: number; realized_market_usdt: number; fees_usdt: number; market_fees_usdt: number
}
export interface FundEvent { ts: string; actor: string; action: string; amount_usdt: number; bot_id: string | null; note: string | null }
export interface FundView {
  mode: string; allocated_usdt: number; unassigned_usdt: number; bots_cash_usdt: number
  coins: Record<string, number>; coins_value_usdt: number | null; value_usdt: number | null
  pnl_usdt: number | null; pnl_market_usdt: number | null
  reserved: Record<string, number>; wallet_free: Record<string, number>; manual_free: Record<string, number>
  pockets: Record<string, Pocket>; history: FundEvent[]; warnings: string[]; note: string
}
export type BotStatus = 'RUNNING' | 'PAUSED' | 'STOPPED' | 'FAILED'
export type LotStatus = 'OPEN' | 'CLOSED' | 'WRITTEN_OFF'
export interface Lot {
  id: number; symbol: string; status: LotStatus; opened_at: string; qty: number; bought_qty: number
  cost_usdt: number; market_cost_usdt: number; buy_price: number; mid_at_buy: number | null
  target_price: number; stop_price: number | null; expires_at: string | null; buy_order_id: string; venues: string[]
  sold_qty: number; proceeds_usdt: number; sold_cost_usdt: number; closed_at: string | null; exit_reason: string | null
  profit_usdt: number | null; profit_market_usdt: number | null; proposal_id: string | null
}
export interface CycleParams {
  symbol: string; budget_usdt: number; buy_usdt: number; threshold_pct: number; max_lots?: number | null
  spacing_pct: number; cooldown_s: number; max_drawdown_pct: number; check_interval_s?: number
  routing?: RoutingStrategyName; lab?: boolean; label?: string | null
}
export type AdvisorMode = 'ASK' | 'AUTO'
export interface AdvisorParams {
  symbols: string[]; budget_usdt: number; trade_usdt: number; mode: AdvisorMode; min_probability: number
  require_edge: boolean; min_expectancy_r: number; k_atr: number; rr: number; max_hold_hours: number
  max_open: number; max_trades_per_day: number; scan_interval_s: number; proposal_ttl_s: number
  routing?: RoutingStrategyName; lab?: boolean; label?: string | null
}
export type ProposalStatus = 'PROPOSED' | 'APPROVED' | 'REJECTED' | 'EXPIRED' | 'FAILED'
export interface AdvisorProposal {
  id: string; bot_id: string; created_at: string; expires_at: string; status: ProposalStatus; symbol: string
  notional_usdt: number; entry_price: number; stop_price: number; target_price: number; max_hold_hours: number
  p_up: number; reasons: string[]; decided_by: string | null; result: string | null; lot_id: number | null
}
export interface BotState {
  id: string; kind: 'CYCLE' | 'ADVISOR'; label: string; created_by: string; created_at: string; updated_at: string
  status: BotStatus; status_reason: string | null; lab: boolean
  cycle: CycleParams | null; advisor: AdvisorParams | null; lots: Lot[]; proposals: AdvisorProposal[]
  last_scan: { symbol: string; p_up?: number | null; edge?: boolean | null; decision: string; proposed?: boolean }[]
  waiting_reason: string | null
  stats: { buys: number; sells: number; lots_closed: number; wins: number; losses: number; timeouts: number; trades_today: number }
  pocket: Pocket | null; value_usdt: number | null; unrealized_usdt: number | null
  pnl_usdt: number | null; pnl_market_usdt: number | null
}
export interface Interval { mean: number; low: number; high: number }
export interface LabReport {
  mode: string; enabled: boolean; note: string; samples: Record<string, number>
  costs: { venue_id: string; symbol: string; orders: number; filled: number; fill_ratio: number | null
    slippage_bps_median: number | null; slippage_bps_mean: number | null; slippage_bps_p90: number | null
    fee_bps_charged: number | null; market_fee_bps: number | null }[]
  scoreboard: { variant: string; symbol: string; bots: string[]; running: boolean; threshold_pct: number | null
    lots: number; closed: number; open: number; days: number; profit_market_usdt: number; profit_per_day_usdt: number
    return_per_day_pct: number | null; return_per_lot_pct: Interval | null; median_hold_h: number | null
    sold_within_24h: number | null; lots_older_than_24h: number }[]
  advisor: { variant: string; trades: number; closed: number; open: number; wins: number; stops: number; timeouts: number
    win_rate: number | null; return_pct: Interval | null; total_profit_market_usdt: number
    by_probability: { bucket: string; n: number; win_rate: number; avg_return_pct: number }[] }[]
  comparisons: { symbol: string; a: string; b: string; days_a: number; days_b: number; verdict: string; p_value?: number | null }[]
  models: { kind: string; question: string; labelled: number; needed: number; status: string; edge: boolean
    base_rate?: number; scores: { n: number; accuracy: number; baseline_accuracy: number; brier_skill: number; p_value: number } | null }[]
  presets: { name: string; title: string; description: string; budget_usdt: number }[]
}

export const api = {
  login: (username: string, password: string) =>
    request<AuthResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  // Users that still accept the demo login (password = user name); empty once
  // every user has a real password, always empty on mainnet. No login needed.
  getDemoLogins: () => request<{ demo_logins: string[]; note: string }>('/api/auth/demo-logins'),

  health: () => request<GatewayHealth>('/api/health'),  // public: the Login page reads it before anyone logs in

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

  getSymbols: () =>
    request<{ symbols: string[] }>('/api/market-data/symbols'),

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

  getConsensusStatus: () => request<ConsensusStatus>('/api/consensus/status'),

  // Checks orders against pre-trade risk WITHOUT sending anything (audited).
  preflight: (orders: PreflightOrder[], stress = false) =>
    request<PreflightResponse>('/api/risk/preflight', {
      method: 'POST', body: JSON.stringify({ orders, stress }),
    }),

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

  // Bots & Lab
  getFund: () => request<FundView>('/api/automation/fund'),
  moveFund: (direction: 'deposit' | 'withdraw', amount_usdt: number) =>
    request<FundView>(`/api/automation/fund/${direction}`, { method: 'POST', body: JSON.stringify({ amount_usdt }) }),
  getBots: () => request<{ bots: BotState[] }>('/api/automation/bots'),
  startCycleBot: (p: CycleParams) =>
    request<BotState>('/api/automation/bots/cycle', { method: 'POST', body: JSON.stringify(p) }),
  startAdvisorBot: (p: AdvisorParams) =>
    request<BotState>('/api/automation/bots/advisor', { method: 'POST', body: JSON.stringify(p) }),
  controlBot: (id: string, action: 'pause' | 'resume' | 'stop') =>
    request<BotState>(`/api/automation/bots/${id}/${action}`, { method: 'POST' }),
  sellBotLots: (id: string, lot_id: number | null = null) =>
    request<BotState>(`/api/automation/bots/${id}/sell-now`, { method: 'POST', body: JSON.stringify({ lot_id }) }),
  releaseBotCoins: (id: string, how: 'to_manual' | 'lost') =>
    request<BotState>(`/api/automation/bots/${id}/release-coins`, { method: 'POST', body: JSON.stringify({ how }) }),
  decideProposal: (proposalId: string, approve: boolean) =>
    request<BotState>(`/api/automation/proposals/${proposalId}/decide`, { method: 'POST', body: JSON.stringify({ approve }) }),
  getLab: () => request<LabReport>('/api/automation/lab'),
  startLabPreset: (name: string) =>
    request<{ bots: BotState[] }>(`/api/automation/lab/presets/${name}`, { method: 'POST' }),
  getResearchForecast: (symbol: string) =>
    request<ForecastResponse>(`/api/research/forecast?symbol=${encodeURIComponent(symbol)}`),

  getResearchScoreboard: () => request<ScoreboardRow[]>('/api/research/scoreboard'),

  getResearchChart: (symbol: string, timeframe: ResearchTf, bars = 200) =>
    request<ResearchChart>(`/api/research/chart?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&bars=${bars}`),

  getResearchOrderbook: (symbol: string) =>
    request<BookSignals>(`/api/research/orderbook?symbol=${encodeURIComponent(symbol)}`),

  researchTechniques: (symbol: string, timeframe: ResearchTf) =>
    request<TechniqueStudy>(`/api/research/techniques?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}`),

  copilotStatus: () => request<CopilotStatus>('/api/copilot/status'),
  copilotPropose: (text: string, auto: boolean) =>
    request<CopilotTurn>('/api/copilot/propose', { method: 'POST', body: JSON.stringify({ text, auto }) }),
  copilotConfirm: (proposalId: string) =>
    request<CopilotTurn>('/api/copilot/confirm', { method: 'POST', body: JSON.stringify({ proposal_id: proposalId }) }),
  copilotCancel: (proposalId: string) =>
    request<CopilotTurn>('/api/copilot/cancel', { method: 'POST', body: JSON.stringify({ proposal_id: proposalId }) }),
  copilotAnalysis: (symbol: string) =>
    request<AnalysisCard>(`/api/copilot/analysis?symbol=${encodeURIComponent(symbol)}`),

  runBacktest: (req: { symbol: string; timeframe: ResearchTf; strategy: BacktestRule; params: Record<string, unknown>;
                       bars?: number; capital?: number; fee_bps?: number | null; slippage_bps?: number | null }) =>
    request<BacktestResponse>('/api/research/backtest', { method: 'POST', body: JSON.stringify(req) }),

  makeTradePlan: (req: { symbol: string; timeframe: ResearchTf; equity: number; risk_pct: number; k_atr: number;
                         rr: number; max_bars: number }) =>
    request<TradePlanResponse>('/api/research/trade-plan', { method: 'POST', body: JSON.stringify(req) }),

  startAlgo: (p: AlgoParams) =>
    request<AlgoState>('/api/algos/', { method: 'POST', body: JSON.stringify(p) }),

  recommendAlgo: (req: { symbol: string; side: string; quantity: number; urgency: Urgency;
                         max_duration_s: number; routing?: RoutingStrategyName }) =>
    request<AlgoRecommendation>('/api/algos/recommend', { method: 'POST', body: JSON.stringify(req) }),

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
    strategy: RoutingStrategyName = 'best_price',
  ) =>
    request<Order>('/api/orders/', {
      method: 'POST',
      body: JSON.stringify({
        symbol,
        side,
        quantity,
        order_type: orderType,
        strategy,
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
