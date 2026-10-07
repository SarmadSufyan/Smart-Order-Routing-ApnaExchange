import React, { useCallback, useEffect, useState } from 'react'
import { C, F } from '../theme'
import { Card } from '../components/shared'
import { AuditRow } from '../components/AuditTimeline'
import { api, type AuditEvent } from '../services/api'

const FILTERS: { label: string; types: string }[] = [
  { label: 'Everything', types: '' },
  { label: 'Operator actions', types: 'kill_switch,kill_switch_release_refused,risk_limits_changed,baseline_set,venue_blacklisted,venue_unblacklisted,venue_degraded,venue_recovered' },
  { label: 'Orders', types: 'order_submitted,order_status' },
  { label: 'Risk', types: 'risk_check,risk_preflight,risk_limits_changed,concentration_alert,concentration_cleared,kill_switch,kill_switch_release_refused' },
  { label: 'Routing & fills', types: 'routing_decision,execution_report,venue_trading_blocked' },
  { label: 'Algos (TWAP)', types: 'algo_started,algo_slice,algo_paused,algo_resumed,algo_finished' },
  { label: 'Copilot', types: 'copilot_proposed,copilot_clarify,copilot_refused,copilot_confirmed,copilot_cancelled,copilot_confirm_failed,copilot_unreadable_answer' },
  { label: 'RAFT', types: 'consensus_committed,consensus_pending,consensus_refused,consensus_adopted,consensus_synced,consensus_unreachable' },
  { label: 'Arbitrage', types: 'arb_opportunity' },
  { label: 'Logins', types: 'login,login_failed' },
  { label: 'System', types: 'gateway_started,gateway_stopped,baseline_set' },
]
const PAGE = 100

export function AuditLog() {
  const [filter, setFilter] = useState(FILTERS[0])
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [store, setStore] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (beforeSeq?: number) => {
    setLoading(true)
    try {
      const res = await api.getAudit({ event_type: filter.types, before_seq: beforeSeq, limit: PAGE })
      setEvents((prev) => (beforeSeq ? [...prev, ...res.events] : res.events))
      setHasMore(res.has_more)
      setStore(res.store)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
    setLoading(false)
  }, [filter])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ fontSize: F.lg, color: C.text }}>Audit Log</div>
        <button onClick={() => load()} disabled={loading} style={{
          padding: '5px 12px', border: `1px solid ${C.border}`, borderRadius: 4,
          background: C.surface, color: C.muted, fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
        }}>
          {loading ? 'Loading…' : '↻ Refresh'}
        </button>
      </div>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 14 }}>
        Append-only record of everything that happened — every order step, risk decision, routing decision and
        fill, and every operator action with who did it. Survives restarts{store && ` (stored in ${store})`}.
        Open any order in the blotter to see its own timeline.
      </div>

      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        {FILTERS.map((f) => (
          <button key={f.label} onClick={() => setFilter(f)} style={{
            padding: '5px 12px', borderRadius: 14, fontSize: F.xs, fontFamily: 'inherit', cursor: 'pointer',
            border: `1px solid ${f === filter ? C.blue : C.border}`,
            background: f === filter ? C.blue : C.surface, color: f === filter ? '#fff' : C.muted,
          }}>{f.label}</button>
        ))}
      </div>

      <Card>
        {error && <div style={{ color: C.red, fontSize: F.sm, marginBottom: 8 }}>{error}</div>}
        {events.length === 0 && !loading && (
          <div style={{ padding: 30, textAlign: 'center', color: C.dim, fontSize: F.sm }}>No events recorded yet.</div>
        )}
        {events.map((e) => <AuditRow key={e.seq} e={e} showDate />)}
        {hasMore && (
          <button onClick={() => load(events[events.length - 1]?.seq)} disabled={loading} style={{
            marginTop: 10, width: '100%', padding: 8, borderRadius: 4, border: `1px solid ${C.border}`,
            background: C.surface, color: C.muted, fontSize: F.sm, cursor: 'pointer', fontFamily: 'inherit',
          }}>
            {loading ? 'Loading…' : 'Load older events'}
          </button>
        )}
      </Card>
    </div>
  )
}
