import React, { useEffect, useState } from 'react'
import { C } from '../theme'
import { Card, CardTitle, ConnDot, StateBadge, ArcGauge, Sparkline, fmtAge } from '../components/shared'
import { NewOrderModal } from '../components/NewOrderModal'
import { useDataStore } from '../stores/dataStore'
import { backendOrderToUi, backendVenueToUi } from '../adapters'
import { SYMBOLS } from '../mockData'

const RAFT_NODES = [
  { id: 'node-01', role: 'LEADER',      healthy: true,  hb: '45ms' },
  { id: 'node-02', role: 'FOLLOWER',    healthy: true,  hb: '47ms' },
  { id: 'node-03', role: 'FOLLOWER',    healthy: true,  hb: '52ms' },
]

const POSITION_LIMIT = 25000
const NOTIONAL_LIMIT = 1_000_000

export function Dashboard() {
  const backendVenues   = useDataStore((s) => s.venues)
  const backendOrders   = useDataStore((s) => s.orders)
  const nbbos           = useDataStore((s) => s.nbbos)
  const riskStatus      = useDataStore((s) => s.riskStatus)
  const routingStats    = useDataStore((s) => s.routingStats)

  const [showOrderModal, setShowOrderModal] = useState(false)

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

  const netSharesAbs = riskStatus
    ? Object.values(riskStatus.positions || {}).reduce((sum, p) => sum + Math.abs(p.net_quantity || 0), 0)
    : 0
  const notional = Math.max(0, riskStatus?.total_exposure ?? 0)
  const ordersToday = routingStats?.orders_routed_today ?? 0

  const th: React.CSSProperties = { padding: '3px 6px', fontSize: 10, color: C.dim, textAlign: 'left', borderBottom: `1px solid ${C.border}`, fontWeight: 400 }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ fontSize: 15, color: C.text }}>Command Center</div>
        <button
          onClick={() => setShowOrderModal(true)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 18px', border: 'none', borderRadius: 4,
            background: C.blue, color: '#fff',
            fontSize: 12, fontFamily: "'Consolas','IBM Plex Mono',monospace",
            cursor: 'pointer', letterSpacing: '.05em', fontWeight: 600,
            transition: 'opacity .15s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.85')}
          onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
        >
          <span style={{ fontSize: 16, fontWeight: 400 }}>+</span>
          New Order
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>

        {/* COL 1: Order Flow */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Card>
            <CardTitle>LIVE ORDER FLOW</CardTitle>
            <div style={{ maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {orders.length === 0 && (
                <div style={{ padding: '20px 8px', textAlign: 'center', color: C.dim, fontSize: 11 }}>
                  No orders yet. Click <span style={{ color: C.blue }}>+ New Order</span> above.
                </div>
              )}
              {orders.map((o) => (
                <div key={o.id} style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '5px 8px', background: C.surface, borderRadius: 3, fontSize: 11,
                }}>
                  <span style={{ color: C.dim, width: 64, fontSize: 10, flexShrink: 0 }}>{o.id}</span>
                  <span style={{ width: 50, fontWeight: 600 }}>{o.sym}</span>
                  <span style={{ color: o.side === 'BUY' ? C.green : C.red, width: 30, fontSize: 10, fontWeight: 700 }}>{o.side}</span>
                  <span style={{ color: C.muted, width: 40, textAlign: 'right' }}>{o.qty.toLocaleString()}</span>
                  <StateBadge state={o.state} />
                  <span style={{ color: C.blue, fontSize: 10, background: '#3B8BD420', padding: '1px 5px', borderRadius: 2 }}>{o.venue}</span>
                  <span style={{ color: C.dim, fontSize: 10, marginLeft: 'auto' }}>{fmtAge(o.ageMs)}</span>
                </div>
              ))}
            </div>
          </Card>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            {[
              { label: 'Orders Today', val: String(total),                                                  color: C.text  },
              { label: 'Filled %',     val: total > 0 ? `${((filled / total) * 100).toFixed(1)}%`   : '—',  color: C.green },
              { label: 'Rejected %',   val: total > 0 ? `${((rejected / total) * 100).toFixed(1)}%` : '—',  color: C.red   },
            ].map((m) => (
              <Card key={m.label} style={{ textAlign: 'center', padding: 12 }}>
                <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>{m.label}</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: m.color }}>{m.val}</div>
              </Card>
            ))}
          </div>

          <Card>
            <CardTitle>ROUTED ORDERS (RUNNING TOTAL)</CardTitle>
            <Sparkline data={throughput} />
            <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>
              Avg routing time: <span style={{ color: C.text }}>{routingStats?.avg_routing_time_ms ?? 0} ms</span>
            </div>
          </Card>
        </div>

        {/* COL 2: NBBO + Venue Health */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Card>
            <CardTitle>NBBO — LIVE QUOTES</CardTitle>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
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
                      <td style={{ padding: '6px', textAlign: 'right', color: C.green }}>{n.best_bid?.toFixed(2)}</td>
                      <td style={{ padding: '6px', textAlign: 'center', color: C.dim, fontSize: 10 }}>{n.best_bid_venue}</td>
                      <td style={{ padding: '6px', textAlign: 'right', color: C.red   }}>{n.best_ask?.toFixed(2)}</td>
                      <td style={{ padding: '6px', textAlign: 'center', color: C.dim, fontSize: 10 }}>{n.best_ask_venue}</td>
                      <td style={{ padding: '6px', textAlign: 'right', color: C.orange, fontSize: 10 }}>{n.spread?.toFixed(3)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>

          <Card>
            <CardTitle>VENUE HEALTH MATRIX</CardTitle>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
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
                    background: v.status === 'Degraded' ? '#EF9F2708' : v.status === 'Disconnected' ? '#E24B4A08' : undefined,
                  }}>
                    <td style={{ padding: '6px' }}>{v.name}</td>
                    <td style={{ padding: '6px', textAlign: 'center' }}><ConnDot status={v.status} /></td>
                    <td style={{ padding: '6px', textAlign: 'right', color: v.latencyEma > 50 ? C.orange : C.muted }}>
                      {v.latencyEma > 0 ? v.latencyEma.toFixed(1) : '—'}
                    </td>
                    <td style={{ padding: '6px', textAlign: 'right', color: v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red }}>
                      {v.fillRate}%
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
              <ArcGauge value={netSharesAbs} max={POSITION_LIMIT * 5}  label="Net Position" unit="shares" size={110} />
              <ArcGauge value={Math.round(notional)} max={NOTIONAL_LIMIT} label="Notional" unit="USD" size={110} />
              <ArcGauge value={ordersToday} max={500} label="Orders Routed" unit="today" size={110} />
              <div style={{ textAlign: 'center', paddingTop: 8 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: riskStatus?.kill_switch_active ? C.red : C.green }}>
                  {riskStatus?.kill_switch_active ? 'ACTIVE' : 'INACTIVE'}
                </div>
                <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>Kill Switch</div>
              </div>
            </div>
          </Card>

          <Card style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px' }}>
            <ConnDot status={riskStatus?.kill_switch_active ? 'Disconnected' : 'Connected'} />
            <span style={{ fontSize: 13, color: riskStatus?.kill_switch_active ? C.red : C.green }}>
              {riskStatus?.kill_switch_active ? 'KILL SWITCH ACTIVE' : 'KILL SWITCH INACTIVE'}
            </span>
          </Card>

          <Card>
            <CardTitle>RAFT CONSENSUS NODES</CardTitle>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {RAFT_NODES.map((n) => (
                <div key={n.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 10px', background: C.surface, borderRadius: 4,
                }}>
                  <ConnDot status={n.healthy ? 'Connected' : 'Disconnected'} />
                  <span style={{ flex: 1, fontSize: 11 }}>{n.id}</span>
                  <span style={{
                    fontSize: 10, padding: '1px 7px', borderRadius: 3,
                    background: n.role === 'LEADER' ? '#3B8BD420' : C.surface2,
                    color:      n.role === 'LEADER' ? C.blue      : C.muted,
                    border: n.role === 'FOLLOWER' ? `1px solid ${C.border}` : undefined,
                  }}>
                    {n.role}
                  </span>
                  <span style={{ fontSize: 10, color: n.healthy ? C.dim : C.red }}>{n.hb}</span>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 9, color: C.dim, marginTop: 8, textAlign: 'center' }}>
              ⓘ Simulated for POC — real RAFT cluster lands in M3
            </div>
          </Card>
        </div>

      </div>

      <NewOrderModal open={showOrderModal} onClose={() => setShowOrderModal(false)} />
    </div>
  )
}
