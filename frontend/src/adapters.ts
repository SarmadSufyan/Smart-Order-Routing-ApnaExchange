// Translates backend API shapes -> frontend UI shapes used by the pages.

import type { Venue, Order as UiOrder, Position, VenueStatus, OrderSide, OrderState } from './types'
import type { VenueHealth, Order as BackendOrder, RiskStatus } from './services/api'

// A real exchange round-trip from Pakistan is ~400 ms; warn well above that.
export const LATENCY_WARN_MS = 900

const VENUE_NAMES: Record<string, string> = {
  V1: 'Binance', V2: 'Coinbase', V3: 'Kraken', V4: 'Bybit', V5: 'OKX', V6: 'Gate',
}

const VENUE_NUMERIC_ID: Record<string, number> = { V1: 1, V2: 2, V3: 3, V4: 4, V5: 5, V6: 6 }

export function venueName(venueId: string, venues?: VenueHealth[]): string {
  return venues?.find((v) => v.venue_id === venueId)?.name || VENUE_NAMES[venueId] || venueId
}

export function isVenueBlacklisted(v: VenueHealth): boolean {
  return (v.status || '').toUpperCase() === 'BLACKLISTED'
}

function statusFromBackend(v: VenueHealth): VenueStatus {
  const s = (v.status || '').toUpperCase()
  if (s === 'HEALTHY') return 'Connected'
  if (s === 'DEGRADED') return 'Degraded'
  return 'Disconnected' // BLACKLISTED | CRITICAL | DISCONNECTED
}

export interface UiVenue extends Venue {
  shortName: string
  backend: VenueHealth
  blacklisted: boolean
  canTrade: boolean   // can receive orders right now
  dataOnly: boolean   // streams real data but has no execution path (e.g. Coinbase)
  hasOrders: boolean  // false -> fill/reject rates are undefined (show "—", not 0%)
}

/** "—" when a venue has no order history; a 0% there would read as "all orders failed". */
export function fmtRate(v: UiVenue, rate: number): string {
  return v.hasOrders ? `${rate}%` : '—'
}

/** How an execution environment is named on screen. testnet = the exchange's own sandbox environment (sandbox
 *  balances, the exchange's order handling); mainnet = real funds. */
export function environmentLabel(mode: string | null | undefined): string {
  if (mode === 'testnet') return 'sandbox'
  return mode || '—'
}

/** Where a venue's order book comes from, on screen. */
export function dataFeedLabel(dataMode: string | null | undefined): string {
  return dataMode === 'mainnet' ? 'live market' : environmentLabel(dataMode)
}

/** One line per venue: where its order book comes from and where its orders execute. */
export function connectionLabel(v: VenueHealth): string {
  const exec = v.execution_available === false ? 'none (data only)' : environmentLabel(v.mode)
  return `data: ${dataFeedLabel(v.data_mode)} · exec: ${exec}`
}

export function isVenueTradable(v: VenueHealth): boolean {
  return v.trading_enabled ?? false
}

export function backendVenueToUi(v: VenueHealth): UiVenue {
  const status = statusFromBackend(v)
  const round1 = (x: number) => Math.round(x * 10) / 10
  return {
    id: VENUE_NUMERIC_ID[v.venue_id] ?? 0,
    name: v.name || VENUE_NAMES[v.venue_id] || v.venue_id,
    shortName: v.venue_id,
    protocol: connectionLabel(v),
    status,
    // Real measured percentiles — no derived/estimated numbers.
    latencyEma: round1(v.latency_p50_ms || v.latency_ms || 0),
    p99: round1(v.latency_p99_ms || v.latency_ms || 0),
    fillRate: Math.round((v.fill_rate || 0) * 1000) / 10,
    rejectRate: Math.round((v.reject_rate || 0) * 1000) / 10,
    ordersToday: 0,
    circuitBreaker: isVenueBlacklisted(v) ? 'TRIPPED' : status === 'Degraded' ? 'LATENCY' : 'OK',
    backend: v,
    blacklisted: isVenueBlacklisted(v),
    canTrade: isVenueTradable(v),
    dataOnly: v.execution_available === false,
    hasOrders: (v.orders_total ?? 0) > 0,
  }
}

function orderStateFromBackend(status: string): OrderState {
  const s = status.toUpperCase()
  if (s === 'FILLED') return 'Filled'
  if (s === 'PARTIALLY_FILLED') return 'PartiallyFilled'
  if (s === 'REJECTED') return 'Rejected'
  if (s === 'CANCELLED' || s === 'CANCELED') return 'Cancelled'
  return 'Working'
}

export function backendOrderToUi(o: BackendOrder): UiOrder {
  const createdMs = new Date(o.created_at).getTime()
  const ageMs = Math.max(0, Date.now() - createdMs)
  const venueId = o.child_orders[0]?.venue_id || '—'
  return {
    id: o.id.slice(0, 8).toUpperCase(),
    sym: o.symbol,
    side: o.side.toUpperCase() as OrderSide,
    qty: o.quantity,
    state: orderStateFromBackend(o.status),
    venue: venueId,
    ageMs,
  }
}

export function backendRiskToPositions(risk: RiskStatus | null): Position[] {
  if (!risk) return []
  const positions: Position[] = []
  const limit = risk.position_limit || 50000
  for (const [symbol, info] of Object.entries(risk.positions || {})) {
    const net = info.net_quantity
    if (net === 0) continue
    positions.push({
      sym: symbol,
      net,
      max: limit,
      side: net >= 0 ? 'BUY' : 'SELL',
      price: info.avg_price,
      time: '—',
    })
  }
  return positions
}
