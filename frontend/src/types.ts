export type VenueStatus = 'Connected' | 'Degraded' | 'Disconnected'
export type OrderState  = 'Filled' | 'Working' | 'PartiallyFilled' | 'Rejected' | 'Cancelled'
export type OrderSide   = 'BUY' | 'SELL'

export interface Venue {
  id: number
  name: string
  protocol: string
  status: VenueStatus
  latencyEma: number
  p99: number
  fillRate: number
  rejectRate: number
  ordersToday: number
  circuitBreaker: string
}

export interface Order {
  id: string
  sym: string
  side: OrderSide
  qty: number
  state: OrderState
  venue: string
  ageMs: number
}

export interface Position {
  sym: string
  net: number
  max: number
  side: OrderSide
  price: number
  time: string
}

export interface TokenBucket {
  account: string
  current: number
  max: number
}

export interface RiskCheck {
  name: string
  total: number
  passed: number
  rejected: number
  rejPct: number
  avgNs: number
}

export interface Alert {
  sev: 'Critical' | 'Warning' | 'Info'
  sys: string
  msg: string
  status: 'Active' | 'Acknowledged' | 'Resolved'
  time: string
}

export interface CircuitBreaker {
  venue: string
  reason: string
  triggered: string
  resolved: string
  duration: string
  orders: number
  active: boolean
}
