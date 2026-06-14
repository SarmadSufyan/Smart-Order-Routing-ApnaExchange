import React, { useState } from 'react'
import { C } from '../theme'
import { Card, CardTitle, StateBadge, fmtAge } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { backendOrderToUi } from '../adapters'
import { SYMBOLS } from '../mockData'
import type { OrderSide } from '../types'

export function OrderTicket() {
  const orders            = useDataStore((s) => s.orders)
  const nbbos             = useDataStore((s) => s.nbbos)
  const executionReports  = useDataStore((s) => s.executionReports)
  const riskStatus        = useDataStore((s) => s.riskStatus)
  const submitOrder       = useDataStore((s) => s.submitOrder)
  const cancelOrder       = useDataStore((s) => s.cancelOrder)

  const [symbol, setSymbol] = useState<string>('AAPL')
  const [side, setSide]     = useState<OrderSide>('BUY')
  const [qty, setQty]       = useState<number>(100)
  const [busy, setBusy]     = useState(false)
  const [feedback, setFeedback] = useState<{ kind: 'ok' | 'err', msg: string } | null>(null)

  const killActive = riskStatus?.kill_switch_active ?? false
  const nbbo = nbbos[symbol]
  const indicativePrice = side === 'BUY' ? nbbo?.best_ask : nbbo?.best_bid
  const indicativeVenue = side === 'BUY' ? nbbo?.best_ask_venue : nbbo?.best_bid_venue
  const notional = (indicativePrice ?? 0) * qty

  async function handleSubmit() {
    if (qty <= 0) {
      setFeedback({ kind: 'err', msg: 'Quantity must be > 0' })
      return
    }
    setBusy(true)
    setFeedback(null)
    const order = await submitOrder(symbol, side, qty)
    setBusy(false)
    if (order) {
      const status = order.status
      if (status === 'FILLED') {
        setFeedback({ kind: 'ok', msg: `${side} ${qty} ${symbol} FILLED @ $${order.avg_fill_price.toFixed(2)}` })
      } else if (status === 'REJECTED') {
        setFeedback({ kind: 'err', msg: `Rejected: ${order.rejection_reason || 'unknown reason'}` })
      } else {
        setFeedback({ kind: 'ok', msg: `${side} ${qty} ${symbol} routed — status: ${status}` })
      }
    } else {
      setFeedback({ kind: 'err', msg: 'Order submission failed — see Risk console' })
    }
  }

  const uiOrders = orders.slice(0, 50).map(backendOrderToUi)
  const reports = executionReports.slice(0, 50)

  const th: React.CSSProperties = { padding: '5px 8px', fontSize: 10, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }

  return (
    <div>
      <div style={{ fontSize: 15, color: C.text, marginBottom: 14 }}>Order Ticket</div>

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 14, marginBottom: 14 }}>

        {/* ORDER ENTRY */}
        <Card>
          <CardTitle>NEW ORDER</CardTitle>

          {killActive && (
            <div style={{
              background: '#E24B4A15', border: `1px solid ${C.red}`,
              borderRadius: 4, padding: '8px 10px', color: C.red,
              fontSize: 11, marginBottom: 10,
            }}>
              ⚠ Kill switch is ACTIVE — orders will be rejected.
            </div>
          )}

          <label style={lblStyle}>SYMBOL</label>
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            style={inputStyle}
          >
            {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>

          <label style={lblStyle}>SIDE</label>
          <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
            {(['BUY', 'SELL'] as OrderSide[]).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                style={{
                  flex: 1, padding: '8px 0',
                  border: `1px solid ${side === s ? (s === 'BUY' ? C.green : C.red) : C.border}`,
                  background: side === s ? (s === 'BUY' ? '#4CAF5020' : '#E24B4A20') : C.surface,
                  color: side === s ? (s === 'BUY' ? C.green : C.red) : C.muted,
                  borderRadius: 3, fontSize: 12, cursor: 'pointer', fontFamily: 'inherit', fontWeight: 700,
                }}
              >
                {s}
              </button>
            ))}
          </div>

          <label style={lblStyle}>QUANTITY</label>
          <input
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(parseInt(e.target.value) || 0)}
            style={inputStyle}
          />

          {/* Indicative pricing from NBBO */}
          <div style={{
            background: C.surface, border: `1px solid ${C.border}`,
            borderRadius: 4, padding: 10, marginBottom: 10, fontSize: 11,
          }}>
            <div style={{ color: C.dim, fontSize: 9, letterSpacing: '.1em', marginBottom: 6 }}>INDICATIVE</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ color: C.muted }}>Price ({side === 'BUY' ? 'ASK' : 'BID'})</span>
              <span style={{ color: side === 'BUY' ? C.red : C.green }}>
                {indicativePrice ? `$${indicativePrice.toFixed(2)}` : '—'}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ color: C.muted }}>Best venue</span>
              <span style={{ color: C.blue }}>{indicativeVenue || '—'}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: C.muted }}>Notional</span>
              <span style={{ color: C.text }}>${notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
            </div>
          </div>

          <button
            onClick={handleSubmit}
            disabled={busy}
            style={{
              width: '100%', padding: '10px 0', border: 'none', borderRadius: 4,
              background: side === 'BUY' ? C.green : C.red,
              color: '#fff', fontSize: 13, fontWeight: 700, letterSpacing: '.05em',
              cursor: busy ? 'wait' : 'pointer', fontFamily: 'inherit',
              opacity: busy ? 0.7 : 1,
            }}
          >
            {busy ? 'SUBMITTING…' : `${side} ${qty} ${symbol}`}
          </button>

          {feedback && (
            <div style={{
              marginTop: 10, padding: '8px 10px', borderRadius: 4, fontSize: 11,
              background: feedback.kind === 'ok' ? '#4CAF5015' : '#E24B4A15',
              border: `1px solid ${feedback.kind === 'ok' ? C.green : C.red}40`,
              color: feedback.kind === 'ok' ? C.green : C.red,
            }}>
              {feedback.msg}
            </div>
          )}
        </Card>

        {/* ORDER BLOTTER */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <CardTitle>ORDER BLOTTER</CardTitle>
            <span style={{ fontSize: 10, color: C.dim }}>{uiOrders.length} orders</span>
          </div>
          {uiOrders.length === 0 ? (
            <div style={{ padding: 30, textAlign: 'center', color: C.dim, fontSize: 11 }}>
              No orders yet. Submit one from the left.
            </div>
          ) : (
            <div style={{ maxHeight: 380, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead style={{ position: 'sticky', top: 0, background: C.surface2 }}>
                  <tr>
                    <th style={th}>ID</th>
                    <th style={th}>Symbol</th>
                    <th style={th}>Side</th>
                    <th style={{ ...th, textAlign: 'right' }}>Qty</th>
                    <th style={{ ...th, textAlign: 'right' }}>Filled</th>
                    <th style={{ ...th, textAlign: 'right' }}>Avg Price</th>
                    <th style={th}>Status</th>
                    <th style={th}>Venue</th>
                    <th style={th}>Age</th>
                    <th style={th}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.slice(0, 50).map((o) => {
                    const ui = backendOrderToUi(o)
                    const isCancellable = o.status === 'WORKING' || o.status === 'PARTIALLY_FILLED'
                    return (
                      <tr key={o.id} style={tr}>
                        <td style={{ padding: '6px 8px', color: C.dim, fontSize: 10 }}>{ui.id}</td>
                        <td style={{ padding: '6px 8px', fontWeight: 600 }}>{o.symbol}</td>
                        <td style={{ padding: '6px 8px', color: o.side === 'BUY' ? C.green : C.red, fontWeight: 700 }}>{o.side}</td>
                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{o.quantity}</td>
                        <td style={{ padding: '6px 8px', textAlign: 'right', color: o.filled_quantity > 0 ? C.green : C.muted }}>
                          {o.filled_quantity}
                        </td>
                        <td style={{ padding: '6px 8px', textAlign: 'right', color: C.muted }}>
                          {o.avg_fill_price > 0 ? `$${o.avg_fill_price.toFixed(2)}` : '—'}
                        </td>
                        <td style={{ padding: '6px 8px' }}><StateBadge state={ui.state} /></td>
                        <td style={{ padding: '6px 8px', color: C.blue }}>{ui.venue}</td>
                        <td style={{ padding: '6px 8px', color: C.dim, fontSize: 10 }}>{fmtAge(ui.ageMs)}</td>
                        <td style={{ padding: '6px 8px' }}>
                          {isCancellable && (
                            <button
                              onClick={() => cancelOrder(o.id)}
                              style={{
                                background: 'none', border: `1px solid ${C.border}`,
                                color: C.orange, fontSize: 10, padding: '2px 6px',
                                borderRadius: 2, cursor: 'pointer', fontFamily: 'inherit',
                              }}
                            >
                              Cancel
                            </button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

      </div>

      {/* EXECUTION REPORTS */}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <CardTitle>EXECUTION REPORTS</CardTitle>
          <span style={{ fontSize: 10, color: C.dim }}>{reports.length} reports</span>
        </div>
        {reports.length === 0 ? (
          <div style={{ padding: 20, textAlign: 'center', color: C.dim, fontSize: 11 }}>
            No execution reports yet.
          </div>
        ) : (
          <div style={{ maxHeight: 280, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead style={{ position: 'sticky', top: 0, background: C.surface2 }}>
                <tr>
                  <th style={th}>Time</th>
                  <th style={th}>Symbol</th>
                  <th style={th}>Side</th>
                  <th style={th}>Type</th>
                  <th style={{ ...th, textAlign: 'right' }}>Qty</th>
                  <th style={{ ...th, textAlign: 'right' }}>Price</th>
                  <th style={th}>Venue</th>
                  <th style={{ ...th, textAlign: 'right' }}>Latency</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r, idx) => (
                  <tr key={`${r.child_order_id}-${idx}`} style={tr}>
                    <td style={{ padding: '6px 8px', color: C.dim, fontSize: 10 }}>{r.timestamp.slice(11, 19)}</td>
                    <td style={{ padding: '6px 8px', fontWeight: 600 }}>{r.symbol}</td>
                    <td style={{ padding: '6px 8px', color: r.side === 'BUY' ? C.green : C.red, fontWeight: 700 }}>{r.side}</td>
                    <td style={{ padding: '6px 8px' }}>
                      <span style={{
                        fontSize: 9, padding: '1px 5px', borderRadius: 2,
                        background: r.exec_type === 'FILL' ? '#4CAF5020' : r.exec_type === 'PARTIAL' ? '#EF9F2720' : '#E24B4A20',
                        color: r.exec_type === 'FILL' ? C.green : r.exec_type === 'PARTIAL' ? C.orange : C.red,
                      }}>
                        {r.exec_type}
                      </span>
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{r.quantity}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: C.muted }}>
                      {r.price > 0 ? `$${r.price.toFixed(2)}` : '—'}
                    </td>
                    <td style={{ padding: '6px 8px', color: C.blue }}>{r.venue_id}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', color: r.venue_latency_ms > 100 ? C.orange : C.muted }}>
                      {r.venue_latency_ms?.toFixed(0)}ms
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

const lblStyle: React.CSSProperties = {
  display: 'block', fontSize: 10, color: C.dim,
  letterSpacing: '.1em', marginBottom: 4, marginTop: 8,
}
const inputStyle: React.CSSProperties = {
  width: '100%', padding: '8px 10px',
  background: C.surface, border: `1px solid ${C.border}`, borderRadius: 4,
  color: C.text, fontSize: 12, fontFamily: 'inherit', outline: 'none',
  marginBottom: 10,
}
