import { getToken } from './api'

/**
 * Order copilot API (backend/gateway/routers/copilot.py).
 *
 *   GET  /api/copilot/status
 *   POST /api/copilot/propose  { text }         -> Proposal   (nothing is sent to an exchange)
 *   POST /api/copilot/confirm  { proposal_id }  -> Proposal   (goes through risk, kill switch, funding)
 *   POST /api/copilot/cancel   { proposal_id }  -> Proposal
 *
 * Types mirror backend/shared/models/copilot.py.
 */

// Same address as services/api.ts.
const API_BASE = 'http://127.0.0.1:8000'

export type ProposalStatus =
  | 'PROPOSED' | 'CLARIFY' | 'REFUSED' | 'CONFIRMED' | 'FAILED' | 'CANCELLED' | 'EXPIRED'

export interface ProposalOrder {
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  order_type: 'MARKET' | 'LIMIT'
  limit_price?: number | null
  strategy?: string
}

export interface ProposalTwap {
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  duration_s: number
  slices: number
  limit_price?: number | null
}

export interface Proposal {
  id: string
  status: ProposalStatus
  created_by: string
  created_at: string
  expires_at: string | null
  text: string
  message: string
  kind: 'order' | 'twap' | null
  order: ProposalOrder | null
  twap: ProposalTwap | null
  reference_mid: number | null
  estimated_notional_usdt: number | null
  conversion: string | null
  warnings: string[]
  model: string | null
  result_order_id: string | null
  result_algo_id: string | null
  result_message: string | null
}

export interface CopilotStatus {
  available: boolean
  provider: string
  model: string | null
  reason: string | null
}

/** FastAPI errors: `detail` is a string, or a list of {msg} for validation errors. */
function errorText(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>
    const d = b.detail ?? b.message ?? b.error
    if (typeof d === 'string' && d) return d
    if (Array.isArray(d)) {
      const msgs = d.map((x) => (x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : '')).filter(Boolean)
      if (msgs.length) return msgs.join('; ')
    }
  }
  return fallback
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(errorText(body, resp.statusText || `Request failed (${resp.status})`))
  }
  return resp.json() as Promise<T>
}

const post = <T,>(path: string, body: unknown) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const copilotApi = {
  status: () => call<CopilotStatus>('/api/copilot/status'),
  propose: (text: string) => post<Proposal>('/api/copilot/propose', { text }),
  confirm: (proposalId: string) => post<Proposal>('/api/copilot/confirm', { proposal_id: proposalId }),
  cancel: (proposalId: string) => post<Proposal>('/api/copilot/cancel', { proposal_id: proposalId }),
}
