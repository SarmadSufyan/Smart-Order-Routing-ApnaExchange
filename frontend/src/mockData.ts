// Static cosmetic data only — real data comes from the dataStore via the API.
import type { Order, Position, TokenBucket, RiskCheck, Alert, CircuitBreaker } from './types'

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
  '[boot] apnasor gateway initializing…',
  '[net]  exchange adapters: websocket market data + rest execution',
  '[md]   local order books: snapshot + update-chain sync',
  '[risk] pre-trade checks and kill switch armed',
  '[auth] awaiting operator credentials',
]

// Trading universe — mirrors the backend's SYMBOLS setting.
export const SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'] as const
