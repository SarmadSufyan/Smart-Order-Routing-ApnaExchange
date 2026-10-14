import React, { useEffect, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle, ConnDot, StateBadge, ArcGauge, Sparkline, fmtAge } from '../components/shared'
import { NewOrderModal } from '../components/NewOrderModal'
import { OrderProofModal } from '../components/OrderProofModal'
import { RaftPanel } from '../components/RaftPanel'
import { useDataStore } from '../stores/dataStore'
import { backendOrderToUi, backendVenueToUi, fmtRate, LATENCY_WARN_MS } from '../adapters'
import { SYMBOLS } from '../mockData'
import { fmtPrice, fmtQty, spreadBps } from '../format'

// Depth-sweep preset: an order big enough to walk past the best level on a
// thin book, small enough to stay under the default $10k per-order limit.
const DEMO_NOTIONAL_USD = 4_000

export function Dashboard() {
  const backendVenues   = useDataStore((s) => s.venues)
  const backendOrders   = useDataStore((s) => s.orders)
  const nbbos           = useDataStore((s) => s.nbbos)
  const riskStatus      = useDataStore((s) => s.riskStatus)
  const routingStats    = useDataStore((s) => s.routingStats)

  const [showOrderModal, setShowOrderModal] = useState(false)
  const [demoMode, setDemoMode] = useState(false)
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null)
  const selectedOrder = selectedOrderId
    ? backendOrders.find((o) => o.id === selectedOrderId) ?? null
    : null

  // Depth sweep: pick the symbol whose best ask holds the LEAST value, then size the
  // order at ~$4k so it has the best chance of walking several price levels.
  function pickDemo(): { symbol: string; qty: string } {
    let best: { symbol: string; topValue: number; ask: number } | null = null
    for (const sym of SYMBOLS) {
      const n = nbbos[sym]
      if (!n?.best_ask || n.best_ask <= 0) continue
      const topValue = n.best_ask * n.best_ask_size
      if (!best || topValue < best.topValue) best = { symbol: sym, topValue, ask: n.best_ask }
    }
    if (!best) return { symbol: SYMBOLS[0], qty: '' }
    return { symbol: best.symbol, qty: String(Number((DEMO_NOTIONAL_USD / best.ask).toPrecision(3))) }
  }
  const demo = pickDemo()

  const orders = backendOrders.slice(0, 12).map(backendOrderToUi)
  const venues = backendVenues.map(backendVenueToUi)

  const total    = backendOrders.length
  const filled   = backendOrders.filter((o) => o.status === 'FILLED').length
  const rejected = backendOrders.filter((o) => o.status === 'REJECTED').length

  // Throughput sparkline — orders routed today over time
  const [throughput, setThroughput] = useState<number[]>(Array(40).fill(0))
  useEffect(() => {
    setThroughput((prev) => [...prev.slice(1), routingStats?.orders_routed_today ?? 0])
  }, [routingStats?.orders_routed_today])

  // Summing BTC + XRP units is meaningless, so count symbols with a position.
  const openPositions = riskStatus
    ? Object.values(riskStatus.positions || {}).filter((p) => Math.abs(p.net_quantity || 0) > 0).length
    : 0
  const notional = Math.max(0, riskStatus?.total_exposure ?? 0)
  const notionalLimit = riskStatus?.exposure_limit || 1_000_000
  const ordersToday = routingStats?.orders_routed_today ?? 0

  const th: React.CSSProperties = { padding: '3px 6px', fontSize: F.xs, color: C.dim, textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontWeight: 400 }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ fontSize: F.lg, color: C.text }}>Command Center</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => { setDemoMode(true); setShowOrderModal(true) }}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '8px 16px', borderRadius: 4,
              background: tint(C.orange, 10), border: `1px solid ${C.orange}`,
              color: C.orange, fontSize: F.sm, fontFamily: "'Consolas','IBM Plex Mono',monospace",
              cursor: 'pointer', letterSpacing: '.05em', fontWeight: 600,
              transition: 'background .15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = tint(C.orange, 18))}
            onMouseLeave={(e) => (e.currentTarget.style.background = tint(C.orange, 10))}
            title="Pre-fills a ~$4k order on the thinnest book so the SOR walks several price levels"
          >
            ⚡ Depth Sweep
          </button>
          <button
            onClick={() => { setDemoMode(false); setShowOrderModal(true) }}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '8px 18px', border: 'none', borderRadius: 4,
              background: C.blue, color: '#fff',
              fontSize: F.base, fontFamily: "'Consolas','IBM Plex Mono',monospace",
              cursor: 'pointer', letterSpacing: '.05em', fontWeight: 600,
              transition: 'opacity .15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.85')}
            onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
          >
            <span style={{ fontSize: F.lg, fontWeight: 400 }}>+</span>
            New Order
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>

        {/* COL 1: Order Flow */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <CardTitle>LIVE ORDER FLOW</CardTitle>
              <span style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.05em' }}>
                ⚡ = click row to see routing proof
              </span>
            </div>
            <div style={{ maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {orders.length === 0 && (
                <div style={{ padding: '20px 8px', textAlign: 'center', color: C.dim, fontSize: F.sm }}>
                  No orders yet. Click <span style={{ color: C.blue }}>+ New Order</span> above.
                </div>
              )}
              {orders.map((o, idx) => {
                const realOrder = backendOrders[idx]
                const hasProof = realOrder?.routing_decision != null
                const venueCount = realOrder?.child_orders?.length ?? 0
                return (
                  <div
                    key={o.id}
                    onClick={() => realOrder && setSelectedOrderId(realOrder.id)}
                    title={hasProof ? 'Click to see routing proof' : 'No routing decision (rejected before routing)'}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 5,
                      padding: '5px 8px', background: C.surface, borderRadius: 3, fontSize: F.sm,
                      cursor: realOrder ? 'pointer' : 'default',
                      transition: 'background .12s',
                    }}
                    onMouseEnter={(e) => { if (realOrder) e.currentTarget.style.background = C.surface2 }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = C.surface }}
                  >
                    <span style={{ color: C.dim, width: 64, fontSize: F.xs, flexShrink: 0 }}>{o.id}</span>
                    <span style={{ width: 50, fontWeight: 600 }}>{o.sym}</span>
                    <span style={{ color: o.side === 'BUY' ? C.green : C.red, width: 30, fontSize: F.xs, fontWeight: 700 }}>{o.side}</span>
                    <span style={{ color: C.muted, width: 70, textAlign: 'right' }}>{fmtQty(o.qty, 5)}</span>
                    <StateBadge state={o.state} />
                    <span style={{
                      color: C.blue, fontSize: F.xs, background: tint(C.blue, 14),
                      padding: '1px 5px', borderRadius: 2,
                    }}>
                      {o.venue}{venueCount > 1 ? ` +${venueCount - 1}` : ''}
                    </span>
                    {hasProof && (
                      <span title="Routing proof available" style={{ color: C.orange, fontSize: F.sm }}>⚡</span>
                    )}
                    <span style={{ color: C.dim, fontSize: F.xs, marginLeft: 'auto' }}>{fmtAge(o.ageMs)}</span>
                  </div>
                )
              })}
            </div>
          </Card>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            {[
              { label: 'Orders Today', val: String(total),                                                  color: C.text  },
              { label: 'Filled %',     val: total > 0 ? `${((filled / total) * 100).toFixed(1)}%`   : '—',  color: C.green },
              { label: 'Rejected %',   val: total > 0 ? `${((rejected / total) * 100).toFixed(1)}%` : '—',  color: C.red   },
            ].map((m) => (
              <Card key={m.label} style={{ textAlign: 'center', padding: 12 }}>
                <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 4 }}>{m.label}</div>
                <div style={{ fontSize: F.xxl, fontWeight: 700, color: m.color }}>{m.val}</div>
              </Card>
            ))}
          </div>

          <Card>
            <CardTitle>ROUTED ORDERS (RUNNING TOTAL)</CardTitle>
            <Sparkline data={throughput} />
            <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
              Avg routing time: <span style={{ color: C.text }}>{routingStats?.avg_routing_time_ms ?? 0} ms</span>
            </div>
          </Card>
        </div>

        {/* COL 2: NBBO + Venue Health */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Card>
            <CardTitle>NBBO — LIVE QUOTES</CardTitle>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
              <thead>
                <tr>
                  <th style={th}>Sym</th>
                  <th style={{ ...th, textAlign: 'right' }}>Bid</th>
                  <th style={{ ...th, textAlign: 'center' }}>@</th>
                  <th style={{ ...th, textAlign: 'right' }}>Ask</th>
                  <th style={{ ...th, textAlign: 'center' }}>@</th>
                  <th style={{ ...th, textAlign: 'right' }}>Spread</th>
                </tr>
              </thead>
              <tbody>
                {SYMBOLS.map((s) => {
                  const n = nbbos[s]
                  if (!n) {
                    return (
                      <tr key={s} style={tr}>
                        <td style={{ padding: '6px', fontWeight: 600 }}>{s}</td>
                        <td colSpan={5} style={{ padding: '6px', color: C.dim, textAlign: 'center', fontSize: 10 }}>…</td>
                      </tr>
                    )
                  }
                  return (
                    <tr key={s} style={tr}>
                      <td style={{ padding: '6px', fontWeight: 600 }}>{s}</td>
                      <td style={{ padding: '6px', textAlign: 'right', color: C.green }}>{fmtPrice(n.best_bid)}</td>
                      <td style={{ padding: '6px', textAlign: 'center', color: C.dim, fontSize: 10 }}>{n.best_bid_venue}</td>
                      <td style={{ padding: '6px', textAlign: 'right', color: C.red   }}>{fmtPrice(n.best_ask)}</td>
                      <td style={{ padding: '6px', textAlign: 'center', color: C.dim, fontSize: 10 }}>{n.best_ask_venue}</td>
                      <td style={{ padding: '6px', textAlign: 'right', color: C.orange, fontSize: 10 }}>{spreadBps(n.best_bid, n.best_ask).toFixed(2)} bps</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>

          <Card>
            <CardTitle>VENUE HEALTH MATRIX</CardTitle>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
              <thead>
                <tr>
                  <th style={th}>Venue</th>
                  <th style={{ ...th, textAlign: 'center' }}>Status</th>
                  <th style={{ ...th, textAlign: 'right' }}>Lat (ms)</th>
                  <th style={{ ...th, textAlign: 'right' }}>Fill%</th>
                  <th style={{ ...th, textAlign: 'right' }}>Rej%</th>
                </tr>
              </thead>
              <tbody>
                {venues.map((v) => (
                  <tr key={v.id} style={{
                    ...tr,
                    background: v.status === 'Degraded' ? tint(C.orange, 6) : v.status === 'Disconnected' ? tint(C.red, 6) : undefined,
                  }}>
                    <td style={{ padding: '6px' }}>{v.name}</td>
                    <td style={{ padding: '6px', textAlign: 'center' }}><ConnDot status={v.status} /></td>
                    <td style={{ padding: '6px', textAlign: 'right', color: v.latencyEma > LATENCY_WARN_MS ? C.orange : C.muted }}>
                      {v.latencyEma > 0 ? v.latencyEma.toFixed(0) : '—'}
                    </td>
                    <td style={{ padding: '6px', textAlign: 'right', color: !v.hasOrders ? C.dim : v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red }}>
                      {fmtRate(v, v.fillRate)}
                    </td>
                    <td style={{ padding: '6px', textAlign: 'right', color: v.rejectRate > 5 ? C.red : C.muted }}>
                      {v.rejectRate > 0 ? `${v.rejectRate}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>

        {/* COL 3: Risk + RAFT */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Card>
            <CardTitle>RISK GAUGES</CardTitle>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <ArcGauge value={openPositions} max={SYMBOLS.length} label="Open Positions" unit="symbols" size={110} />
              <ArcGauge value={Math.round(notional)} max={notionalLimit} label="Notional" unit="USD" size={110} />
              <ArcGauge value={ordersToday} max={500} label="Orders Routed" unit="today" size={110} />
              <div style={{ textAlign: 'center', paddingTop: 8 }}>
                <div style={{ fontSize: F.lg, fontWeight: 700, color: riskStatus?.kill_switch_active ? C.red : C.green }}>
                  {riskStatus?.kill_switch_active ? 'ACTIVE' : 'INACTIVE'}
                </div>
                <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>Kill Switch</div>
              </div>
            </div>
          </Card>

          <Card style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px' }}>
            <ConnDot status={riskStatus?.kill_switch_active ? 'Disconnected' : 'Connected'} />
            <span style={{ fontSize: F.md, color: riskStatus?.kill_switch_active ? C.red : C.green }}>
              {riskStatus?.kill_switch_active ? 'KILL SWITCH ACTIVE' : 'KILL SWITCH INACTIVE'}
            </span>
          </Card>

          <RaftPanel compact />
        </div>

      </div>

      <NewOrderModal
        open={showOrderModal}
        onClose={() => { setShowOrderModal(false); setDemoMode(false) }}
        initialSymbol={demoMode ? demo.symbol : SYMBOLS[0]}
        initialSide="BUY"
        initialQty={demoMode ? demo.qty : ''}
        banner={demoMode
          ? `DEPTH SWEEP — ~$${DEMO_NOTIONAL_USD.toLocaleString()} of ${demo.symbol} on the thinnest book. The SOR walks the consolidated book; with 2+ exchanges connected it splits across venues.`
          : undefined}
      />

      <OrderProofModal
        order={selectedOrder}
        onClose={() => setSelectedOrderId(null)}
      />
    </div>
  )
}
