// Translates backend API shapes -> frontend UI shapes used by the pages.

import type { Venue, Order as UiOrder, Position, VenueStatus, OrderSide, OrderState } from './types'
import type { VenueHealth, Order as BackendOrder, RiskStatus } from './services/api'

const VENUE_SHORT_NAMES: Record<string, string> = {
  V1: 'V1', V2: 'V2', V3: 'V3', V4: 'V4', V5: 'V5',
}

const VENUE_LONG_NAMES: Record<string, string> = {
  V1: 'V1 Alpha',
  V2: 'V2 Beta',
  V3: 'V3 Gamma',
  V4: 'V4 Delta',
  V5: 'V5 Epsilon',
}

const VENUE_NUMERIC_ID: Record<string, number> = { V1: 1, V2: 2, V3: 3, V4: 4, V5: 5 }

export function isVenueBlacklisted(v: VenueHealth): boolean {
  return (v.status || '').toUpperCase() === 'BLACKLISTED'
}

function statusFromBackend(v: VenueHealth): VenueStatus {
  const s = (v.status || '').toUpperCase()
  if (s === 'BLACKLISTED' || s === 'OFFLINE') return 'Disconnected'
  if (s === 'HEALTHY') return 'Connected'
  if (s === 'DEGRADED') return 'Degraded'
  // Fallback to health_score
  if ((v.health_score ?? 0) >= 0.8) return 'Connected'
  if ((v.health_score ?? 0) >= 0.5) return 'Degraded'
  return 'Disconnected'
}

export interface UiVenue extends Venue {
  shortName: string
  backend: VenueHealth
  blacklisted: boolean
}

export function backendVenueToUi(v: VenueHealth): UiVenue {
  const status = statusFromBackend(v)
  return {
    id: VENUE_NUMERIC_ID[v.venue_id] ?? 0,
    name: VENUE_LONG_NAMES[v.venue_id] ?? v.name ?? v.venue_id,
    shortName: VENUE_SHORT_NAMES[v.venue_id] ?? v.venue_id,
    protocol: v.cloud || 'REST',
    status,
    latencyEma: Math.round((v.latency_ms || 0) * 10) / 10,
    p99: Math.round((v.latency_ms || 0) * 2.5 * 10) / 10,
    fillRate: Math.round((v.fill_rate || 0) * 1000) / 10,
    rejectRate: Math.round((v.reject_rate || 0) * 1000) / 10,
    ordersToday: 0,
    circuitBreaker: isVenueBlacklisted(v) ? 'TRIPPED' : status === 'Degraded' ? 'LATENCY' : 'OK',
    backend: v,
    blacklisted: isVenueBlacklisted(v),
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
    venue: VENUE_SHORT_NAMES[venueId] ?? venueId,
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
