import React, { useEffect, useState } from 'react'
import { C, F, tint } from '../theme'
import { api, type AuditEvent } from '../services/api'

/** Colour per audit event type (rejections / reverse outcomes turn red below). */
export function auditColor(e: AuditEvent): string {
  const s = e.summary.toUpperCase()
  if (e.event_type === 'kill_switch') return s.includes('RELEASED') ? C.green : C.red
  if (e.event_type === 'kill_switch_release_refused') return C.red
  if (s.includes('REJECT') || s.includes('BREAK')) return C.red
  switch (e.event_type) {
    case 'order_submitted':      return C.blue
    case 'order_status':         return s.includes('FILLED') ? C.green : C.muted
    case 'risk_check':           return C.green
    case 'routing_decision':     return C.purple
    case 'execution_report':     return C.green
    case 'baseline_set':         return C.gold
    case 'algo_started':
    case 'algo_slice':
    case 'algo_resumed':         return C.blue
    case 'algo_paused':          return C.orange
    case 'algo_finished':        return s.includes('COMPLETED') ? C.green : C.orange
    case 'risk_limits_changed':
    case 'concentration_alert':
    case 'venue_blacklisted':
    case 'venue_degraded':       return C.orange
    case 'login':                return C.blue
    case 'login_failed':         return C.red
    default:                     return C.dim
  }
}

export function AuditRow({ e, showDate = false }: { e: AuditEvent; showDate?: boolean }) {
  const color = auditColor(e)
  const d = new Date(e.ts)
  const time = d.toLocaleTimeString(undefined, { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0')
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', columnGap: 10, rowGap: 2, alignItems: 'baseline', padding: '5px 0', borderBottom: `1px solid ${C.border2}` }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0, transform: 'translateY(-1px)' }} />
      <span style={{ fontSize: F.xs, color: C.dim, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
        {showDate && `${d.toLocaleDateString()} `}{time}
      </span>
      <span style={{
        fontSize: F.xs, padding: '0 6px', borderRadius: 3, whiteSpace: 'nowrap',
        background: tint(color, 12), color,
      }}>{e.event_type.replace(/_/g, ' ')}</span>
      {e.actor && <span style={{ fontSize: F.xs, color: C.muted, whiteSpace: 'nowrap' }}>{e.actor}</span>}
      <span style={{ fontSize: F.sm, color: C.text, flex: '1 1 240px', minWidth: 0, wordBreak: 'break-word' }}>{e.summary}</span>
    </div>
  )
}

/** Every recorded step of one order, oldest first, straight from the audit log. */
export function AuditTimeline({ orderId }: { orderId: string }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.getAudit({ order_id: orderId, limit: 200 })
      .then((res) => alive && setEvents(res.events))
      .catch((e: Error) => alive && setError(e.message))
    return () => { alive = false }
  }, [orderId])

  if (error) return <div style={{ fontSize: F.sm, color: C.red }}>Audit trail unavailable: {error}</div>
  if (!events) return <div style={{ fontSize: F.sm, color: C.dim }}>Loading audit trail…</div>
  if (events.length === 0) {
    return <div style={{ fontSize: F.sm, color: C.dim }}>No audit records for this order (placed before the audit log existed).</div>
  }
  return <div>{events.map((e) => <AuditRow key={e.seq} e={e} />)}</div>
}
