// Static cosmetic data only — real data comes from the dataStore via the API.
import type { Venue, Order, Position, TokenBucket, RiskCheck, Alert, CircuitBreaker } from './types'

// Skeleton venues used before first API response arrives.
export const MOCK_VENUES: Venue[] = [
  { id: 1, name: 'V1 Alpha',   protocol: 'REST', status: 'Connected', latencyEma: 0, p99: 0, fillRate: 0, rejectRate: 0, ordersToday: 0, circuitBreaker: 'OK' },
  { id: 2, name: 'V2 Beta',    protocol: 'REST', status: 'Connected', latencyEma: 0, p99: 0, fillRate: 0, rejectRate: 0, ordersToday: 0, circuitBreaker: 'OK' },
  { id: 3, name: 'V3 Gamma',   protocol: 'REST', status: 'Degraded',  latencyEma: 0, p99: 0, fillRate: 0, rejectRate: 0, ordersToday: 0, circuitBreaker: 'OK' },
  { id: 4, name: 'V4 Delta',   protocol: 'REST', status: 'Connected', latencyEma: 0, p99: 0, fillRate: 0, rejectRate: 0, ordersToday: 0, circuitBreaker: 'OK' },
  { id: 5, name: 'V5 Epsilon', protocol: 'REST', status: 'Connected', latencyEma: 0, p99: 0, fillRate: 0, rejectRate: 0, ordersToday: 0, circuitBreaker: 'OK' },
]

export const MOCK_ORDERS: Order[] = []
export const MOCK_POSITIONS: Position[] = []

export const MOCK_TOKEN_BUCKETS: TokenBucket[] = [
  { account: 'admin',    current: 480, max: 500 },
  { account: 'trader1',  current: 420, max: 500 },
  { account: 'risk_mgr', current: 495, max: 500 },
]

export const MOCK_RISK_CHECKS: RiskCheck[] = [
  { name: 'KillSwitch',    total: 0, passed: 0, rejected: 0, rejPct: 0, avgNs: 0 },
  { name: 'PositionLimit', total: 0, passed: 0, rejected: 0, rejPct: 0, avgNs: 0 },
  { name: 'NotionalLimit', total: 0, passed: 0, rejected: 0, rejPct: 0, avgNs: 0 },
  { name: 'RateLimit',     total: 0, passed: 0, rejected: 0, rejPct: 0, avgNs: 0 },
]

export const MOCK_ALERTS: Alert[] = []
export const MOCK_CIRCUIT_BREAKERS: CircuitBreaker[] = []

export const BOOT_LINES = [
  '[boot] sor-engine v4.12.3 initializing…',
  '[net]  5 venue gateways probed: V1..V5',
  '[md]   aggregator: 5/5 venues subscribed',
  '[risk] pre-trade checks armed',
  '[auth] awaiting operator credentials',
]

// Symbols supported by the backend price engine
export const SYMBOLS = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA'] as const
