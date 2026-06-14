import React from 'react'
import { C, F, tint } from '../theme'
import { RoutingProof } from './RoutingProof'
import type { Order as BackendOrder } from '../services/api'

/**
 * OrderProofModal
 * ────────────────
 * Replays the routing-proof view for ANY past order, not just freshly placed
 * ones. The panel can click any row in the Live Order Flow blotter to open this
 * and prove what the SOR did at decision time.
 */

interface Props {
  order: BackendOrder | null
  resolvedVenue?: string
  onClose: () => void
}

export function OrderProofModal({ order, resolvedVenue, onClose }: Props) {
  if (!order) return null

  const filled = order.status === 'FILLED'
  const partial = order.status === 'PARTIALLY_FILLED'
  const working = order.status === 'WORKING'
  const rejected = order.status === 'REJECTED'
  const stateColor =
    filled ? C.green
    : partial ? C.orange
    : working ? C.blue
    : rejected ? C.red
    : C.muted
  const stateLabel =
    filled ? 'Filled'
    : partial ? 'Partially Filled'
    : working ? 'Working'
    : rejected ? 'Rejected'
    : order.status

  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, zIndex: 1000,
    background: 'var(--modal-overlay)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    padding: 16,
  }
  const modal: React.CSSProperties = {
    background: C.surface, border: `1px solid ${C.border}`,
    borderRadius: 8, width: '100%', maxWidth: 720,
    fontFamily: "'Consolas','IBM Plex Mono',monospace",
    color: C.text, maxHeight: '90vh', overflowY: 'auto',
  }
  const header: React.CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '12px 18px', borderBottom: `1px solid ${C.border}`,
  }

  return (
    <div style={overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div style={modal}>
        <div style={header}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 34, height: 34, borderRadius: '50%',
              background: tint(stateColor, 14), border: `2px solid ${stateColor}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: F.lg, color: stateColor,
            }}>{rejected ? '✕' : '✓'}</div>
            <div>
              <div style={{ fontSize: F.md, color: stateColor, fontWeight: 700 }}>
                Order {stateLabel} · Routing Proof
              </div>
              <div style={{ fontSize: F.sm, color: C.dim }}>
                {order.id.slice(0, 8).toUpperCase()} · {order.symbol} · {order.side} {order.quantity.toFixed(0)}
                {order.avg_fill_price > 0 && ` @ avg $${order.avg_fill_price.toFixed(2)}`}
                {resolvedVenue && ` · ${resolvedVenue}`}
              </div>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: C.dim, cursor: 'pointer', fontSize: F.xl }}>✕</button>
        </div>

        <div style={{ padding: '14px 18px' }}>
          {order.routing_decision ? (
            <RoutingProof decision={order.routing_decision} symbol={order.symbol} />
          ) : (
            <div style={{ padding: 28, textAlign: 'center', color: C.dim, fontSize: F.base }}>
              <div style={{ fontSize: F.xxl, marginBottom: 8 }}>ⓘ</div>
              <div>No routing decision attached to this order.</div>
              <div style={{ fontSize: F.sm, marginTop: 6 }}>
                {rejected
                  ? 'Order was rejected before reaching the routing strategy.'
                  : 'Older orders from before the proof feature was added.'}
              </div>
            </div>
          )}

          <button
            onClick={onClose}
            style={{
              width: '100%', height: 40, marginTop: 14, border: 'none',
              borderRadius: 4, background: stateColor, color: '#fff',
              fontSize: F.base, cursor: 'pointer', fontFamily: 'inherit',
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
