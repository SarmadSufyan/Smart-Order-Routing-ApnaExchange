import React, { useState } from 'react'
import { ChevronRight, Plus, Zap } from 'lucide-react'
import { C, F } from '../theme'
import { ArcGauge, Button, Card, CardTitle, ConnDot, PageHeader, Stat, StateBadge, fmtAge } from '../components/shared'
import { NewOrderModal } from '../components/NewOrderModal'
import { OrderProofModal } from '../components/OrderProofModal'
import { RaftPanel } from '../components/RaftPanel'
import { useDataStore } from '../stores/dataStore'
import { backendOrderToUi, backendVenueToUi, fmtRate, LATENCY_WARN_MS } from '../adapters'
import { SYMBOLS } from '../mockData'
import { fmtPrice, fmtQty, fmtUsd, spreadBps } from '../format'

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

  const orders = backendOrders.slice(0, 14).map(backendOrderToUi)
  const venues = backendVenues.map(backendVenueToUi)
  const venueName = (id: string) => venues.find((v) => v.shortName === id)?.name ?? id

  const total    = backendOrders.length
  const filled   = backendOrders.filter((o) => o.status === 'FILLED').length
  const rejected = backendOrders.filter((o) => o.status === 'REJECTED').length

  // Summing BTC + XRP units is meaningless, so count symbols with a position.
  const openPositions = riskStatus
    ? Object.values(riskStatus.positions || {}).filter((p) => Math.abs(p.net_quantity || 0) > 0).length
    : 0
  const notional = Math.max(0, riskStatus?.total_exposure ?? 0)
  const notionalLimit = riskStatus?.exposure_limit || 1_000_000
  const ordersToday = routingStats?.orders_routed_today ?? 0
  const killActive = riskStatus?.kill_switch_active ?? false
  const connected = venues.filter((v) => v.status === 'Connected').length

  const th: React.CSSProperties = {
    padding: '0 8px 8px', fontSize: F.xs, color: C.dim, textAlign: 'left', fontWeight: 500,
    borderBottom: `1px solid ${C.border}`, whiteSpace: 'nowrap',
  }
  const td: React.CSSProperties = { padding: '9px 8px', borderBottom: `1px solid ${C.border2}`, whiteSpace: 'nowrap' }
  const num: React.CSSProperties = { ...td, textAlign: 'right' }

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle={`Live order books from ${connected} of ${venues.length} exchanges, the best price across all of them, and every order the router has sent.`}
        actions={<>
          <Button
            tone="warning" icon={<Zap size={15} />}
            onClick={() => { setDemoMode(true); setShowOrderModal(true) }}
            title="Pre-fills a ~$4k order on the thinnest book so the SOR walks several price levels"
          >
            Depth sweep
          </Button>
          <Button tone="primary" icon={<Plus size={16} />} onClick={() => { setDemoMode(false); setShowOrderModal(true) }}>
            New order
          </Button>
        </>}
      />

      {/* Key figures */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 14, marginBottom: 14 }}>
        <Stat label="Orders" value={total} sub={`${ordersToday} routed today`} />
        <Stat label="Filled" value={total > 0 ? `${((filled / total) * 100).toFixed(1)}%` : '—'} color={C.green}
          sub={`${filled} of ${total}`} />
        <Stat label="Rejected" value={total > 0 ? `${((rejected / total) * 100).toFixed(1)}%` : '—'}
          color={rejected > 0 ? C.red : C.text} sub={`${rejected} of ${total}`} />
        <Stat label="Routing time" value={`${routingStats?.avg_routing_time_ms ?? 0} ms`} sub="average, decision to orders sent" />
        <Stat label="Exposure" value={fmtUsd(notional, 0)} sub={`of ${fmtUsd(notionalLimit, 0)} limit`} />
        <Stat label="Kill switch" value={killActive ? 'Engaged' : 'Off'} color={killActive ? C.red : C.green}
          sub={killActive ? 'every new order is refused' : 'trading allowed'} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 460px), 1fr))', gap: 14 }}>

        {/* Live order flow */}
        <Card>
          <CardTitle right="Click an order for its routing proof">Live order flow</CardTitle>
          <div style={{ maxHeight: 340, overflow: 'auto' }}>
            {orders.length === 0 ? (
              <div style={{ padding: '28px 8px', textAlign: 'center', color: C.dim, fontSize: F.sm }}>
                No orders yet. Press <span style={{ color: C.blue }}>New order</span> above.
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
                <thead>
                  <tr>
                    <th style={th}>Order</th>
                    <th style={th}>Pair</th>
                    <th style={th}>Side</th>
                    <th style={{ ...th, textAlign: 'right' }}>Quantity</th>
                    <th style={th}>Status</th>
                    <th style={th}>Exchange</th>
                    <th style={{ ...th, textAlign: 'right' }}>Age</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o, idx) => {
                    const realOrder = backendOrders[idx]
                    const hasProof = realOrder?.routing_decision != null
                    const venueCount = realOrder?.child_orders?.length ?? 0
                    return (
                      <tr
                        key={o.id}
                        onClick={() => realOrder && setSelectedOrderId(realOrder.id)}
                        title={hasProof ? 'Click to see the routing proof' : 'No routing decision (rejected before routing)'}
                        style={{ cursor: realOrder ? 'pointer' : 'default' }}
                      >
                        <td style={{ ...td, color: C.dim, fontSize: F.xs }}>{o.id}</td>
                        <td style={{ ...td, fontWeight: 600 }}>{o.sym}</td>
                        <td style={{ ...td, color: o.side === 'BUY' ? C.green : C.red, fontWeight: 600 }}>
                          {o.side === 'BUY' ? 'Buy' : 'Sell'}
                        </td>
                        <td style={{ ...num, color: C.muted }}>{fmtQty(o.qty, 5)}</td>
                        <td style={td}><StateBadge state={o.state} /></td>
                        <td style={{ ...td, color: C.muted }}>
                          {o.venue === '—' ? '—' : venueName(o.venue)}
                          {venueCount > 1 && <span style={{ color: C.blue }}> +{venueCount - 1}</span>}
                        </td>
                        <td style={{ ...num, color: C.dim, fontSize: F.xs }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            {fmtAge(o.ageMs)}
                            <ChevronRight size={14} style={{ opacity: hasProof ? 0.7 : 0.2 }} />
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </Card>

        {/* Best prices across every exchange */}
        <Card>
          <CardTitle right="NBBO · live">Best prices</CardTitle>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
              <thead>
                <tr>
                  <th style={th}>Pair</th>
                  <th style={{ ...th, textAlign: 'right' }}>Best bid</th>
                  <th style={{ ...th, textAlign: 'right' }}>Best ask</th>
                  <th style={{ ...th, textAlign: 'right' }}>Spread</th>
                </tr>
              </thead>
              <tbody>
                {SYMBOLS.map((s) => {
                  const n = nbbos[s]
                  if (!n) {
                    return (
                      <tr key={s}>
                        <td style={{ ...td, fontWeight: 600 }}>{s}</td>
                        <td colSpan={3} style={{ ...td, color: C.dim, textAlign: 'center' }}>waiting for books…</td>
                      </tr>
                    )
                  }
                  const bps = spreadBps(n.best_bid, n.best_ask)
                  const crossed = bps < 0
                  return (
                    <tr key={s}>
                      <td style={{ ...td, fontWeight: 600 }}>{s}</td>
                      <td style={num}>
                        <div style={{ color: C.green }}>{fmtPrice(n.best_bid)}</div>
                        <div style={{ color: C.dim, fontSize: F.xs, marginTop: 2 }}>{venueName(n.best_bid_venue)}</div>
                      </td>
                      <td style={num}>
                        <div style={{ color: C.red }}>{fmtPrice(n.best_ask)}</div>
                        <div style={{ color: C.dim, fontSize: F.xs, marginTop: 2 }}>{venueName(n.best_ask_venue)}</div>
                      </td>
                      <td style={{ ...num, color: crossed ? C.orange : C.muted }}
                        title={crossed ? 'Crossed: one exchange bids above another one\'s ask (fees usually eat the gap)' : undefined}>
                        {bps.toFixed(2)} bps
                        {crossed && <div style={{ fontSize: F.xs, marginTop: 2 }}>crossed</div>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {/* Exchanges */}
        <Card>
          <CardTitle right={`${connected} of ${venues.length} connected`}>Exchanges</CardTitle>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
              <thead>
                <tr>
                  <th style={th}>Exchange</th>
                  <th style={th}>Status</th>
                  <th style={{ ...th, textAlign: 'right' }}>Latency</th>
                  <th style={{ ...th, textAlign: 'right' }}>Filled</th>
                  <th style={{ ...th, textAlign: 'right' }}>Rejected</th>
                </tr>
              </thead>
              <tbody>
                {venues.map((v) => (
                  <tr key={v.shortName}>
                    <td style={{ ...td, fontWeight: 600 }}>{v.name}</td>
                    <td style={td}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, color: C.muted }}>
                        <ConnDot status={v.status} />{v.dataOnly && v.status === 'Connected' ? 'Data only' : v.status}
                      </span>
                    </td>
                    <td style={{ ...num, color: v.latencyEma > LATENCY_WARN_MS ? C.orange : C.muted }}>
                      {v.latencyEma > 0 ? `${v.latencyEma.toFixed(0)} ms` : '—'}
                    </td>
                    <td style={{ ...num, color: !v.hasOrders ? C.dim : v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red }}>
                      {fmtRate(v, v.fillRate)}
                    </td>
                    <td style={{ ...num, color: v.rejectRate > 5 ? C.red : C.muted }}>
                      {v.rejectRate > 0 ? `${v.rejectRate}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* Risk */}
        <Card>
          <CardTitle right="pre-trade limits">Risk</CardTitle>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(124px, 1fr))', gap: 8 }}>
            <ArcGauge value={openPositions} max={SYMBOLS.length} label="Open positions" unit="pairs" size={120} />
            <ArcGauge value={Math.round(notional)} max={notionalLimit} label="Exposure" unit="USD" size={120} />
            <ArcGauge value={ordersToday} max={500} label="Orders routed" unit="today" size={120} />
          </div>
          <RaftPanel compact style={{ marginTop: 14 }} />
        </Card>

      </div>

      <NewOrderModal
        open={showOrderModal}
        onClose={() => { setShowOrderModal(false); setDemoMode(false) }}
        initialSymbol={demoMode ? demo.symbol : SYMBOLS[0]}
        initialSide="BUY"
        initialQty={demoMode ? demo.qty : ''}
        banner={demoMode
          ? `Depth sweep — ~$${DEMO_NOTIONAL_USD.toLocaleString()} of ${demo.symbol} on the thinnest book. The SOR walks the consolidated book; with 2+ exchanges connected it splits across venues.`
          : undefined}
      />

      <OrderProofModal
        order={selectedOrder}
        onClose={() => setSelectedOrderId(null)}
      />
    </div>
  )
}
