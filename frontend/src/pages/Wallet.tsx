import React from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { SYMBOLS } from '../mockData'

const CASH_STARTING_BALANCE = 1_000_000

interface Holding {
  symbol: string
  quantity: number
  avgEntry: number
  marketPrice: number
  marketValue: number
  costBasis: number
  unrealizedPnl: number
  unrealizedPnlPct: number
}

export function Wallet() {
  const riskStatus       = useDataStore((s) => s.riskStatus)
  const nbbos            = useDataStore((s) => s.nbbos)
  const executionReports = useDataStore((s) => s.executionReports)
  const orders           = useDataStore((s) => s.orders)

  const positions = riskStatus?.positions ?? {}

  // Build the holdings table from real risk-engine positions + live NBBO mid prices
  const holdings: Holding[] = []
  for (const sym of SYMBOLS) {
    const p = positions[sym]
    if (!p || p.net_quantity === 0) continue
    const nbbo = nbbos[sym]
    const mid = nbbo ? (nbbo.best_bid + nbbo.best_ask) / 2 : p.avg_price
    const marketValue = p.net_quantity * mid
    const costBasis = p.net_quantity * p.avg_price
    const pnl = marketValue - costBasis
    const pnlPct = costBasis !== 0 ? (pnl / Math.abs(costBasis)) * 100 : 0
    holdings.push({
      symbol: sym,
      quantity: p.net_quantity,
      avgEntry: p.avg_price,
      marketPrice: mid,
      marketValue,
      costBasis,
      unrealizedPnl: pnl,
      unrealizedPnlPct: pnlPct,
    })
  }

  const totalMarketValue = holdings.reduce((sum, h) => sum + h.marketValue, 0)
  const totalCostBasis   = holdings.reduce((sum, h) => sum + h.costBasis, 0)
  const totalPnl         = totalMarketValue - totalCostBasis
  const totalPnlPct      = totalCostBasis !== 0 ? (totalPnl / Math.abs(totalCostBasis)) * 100 : 0

  // Cash: starting balance minus what's been spent on positions
  const cashUsed = totalCostBasis
  const cashAvailable = CASH_STARTING_BALANCE - cashUsed
  const portfolioValue = cashAvailable + totalMarketValue

  // Recent fills (for the wallet activity feed)
  const recentFills = executionReports
    .filter((r) => r.exec_type === 'FILL' || r.exec_type === 'PARTIAL')
    .slice(0, 10)

  const th: React.CSSProperties = { padding: '6px 10px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }
  const td: React.CSSProperties = { padding: '8px 10px', color: C.text, fontSize: F.base }

  return (
    <div>
      <div style={{ fontSize: F.lg, color: C.text, marginBottom: 6 }}>Wallet — Portfolio Holdings</div>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 16 }}>
        Live view of your positions, cash, and unrealized P&L. Mid prices are pulled from the NBBO across all 5 venues.
      </div>

      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>PORTFOLIO VALUE</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: C.text }}>
            ${portfolioValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>Cash + Holdings</div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>CASH AVAILABLE</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: C.green }}>
            ${cashAvailable.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
            from ${CASH_STARTING_BALANCE.toLocaleString()} starting
          </div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>HOLDINGS VALUE</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: C.blue }}>
            ${totalMarketValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
            across {holdings.length} symbol{holdings.length === 1 ? '' : 's'}
          </div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>UNREALIZED P&L</div>
          <div style={{
            fontSize: F.xxl, fontWeight: 700,
            color: totalPnl >= 0 ? C.green : C.red,
          }}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
          <div style={{ fontSize: F.xs, color: totalPnl >= 0 ? C.green : C.red, marginTop: 4 }}>
            {totalPnl >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%
          </div>
        </Card>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>

        {/* Holdings table */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <CardTitle>HOLDINGS</CardTitle>
            <span style={{ fontSize: F.xs, color: C.dim }}>
              {holdings.length === 0 ? 'No positions yet' : `${holdings.length} position${holdings.length === 1 ? '' : 's'}`}
            </span>
          </div>

          {holdings.length === 0 ? (
            <div style={{ padding: 40, textAlign: 'center', color: C.dim, fontSize: F.base }}>
              You don't own any shares yet.<br />
              <span style={{ fontSize: F.sm, color: C.muted }}>Place an order from the Order Ticket page to start building positions.</span>
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>Symbol</th>
                  <th style={{ ...th, textAlign: 'right' }}>Qty</th>
                  <th style={{ ...th, textAlign: 'right' }}>Avg Entry</th>
                  <th style={{ ...th, textAlign: 'right' }}>Market Price</th>
                  <th style={{ ...th, textAlign: 'right' }}>Market Value</th>
                  <th style={{ ...th, textAlign: 'right' }}>Cost Basis</th>
                  <th style={{ ...th, textAlign: 'right' }}>Unrealized P&L</th>
                  <th style={{ ...th, textAlign: 'right' }}>%</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h) => (
                  <tr key={h.symbol} style={tr}>
                    <td style={{ ...td, fontWeight: 700, fontSize: 13 }}>{h.symbol}</td>
                    <td style={{ ...td, textAlign: 'right', color: h.quantity >= 0 ? C.green : C.red }}>
                      {h.quantity >= 0 ? '+' : ''}{h.quantity}
                    </td>
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}>${h.avgEntry.toFixed(2)}</td>
                    <td style={{ ...td, textAlign: 'right' }}>${h.marketPrice.toFixed(2)}</td>
                    <td style={{ ...td, textAlign: 'right', color: C.blue, fontWeight: 600 }}>
                      ${h.marketValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </td>
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}>
                      ${h.costBasis.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </td>
                    <td style={{ ...td, textAlign: 'right', color: h.unrealizedPnl >= 0 ? C.green : C.red, fontWeight: 600 }}>
                      {h.unrealizedPnl >= 0 ? '+' : ''}${h.unrealizedPnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </td>
                    <td style={{ ...td, textAlign: 'right', color: h.unrealizedPnl >= 0 ? C.green : C.red }}>
                      {h.unrealizedPnl >= 0 ? '+' : ''}{h.unrealizedPnlPct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
                <tr style={{ borderTop: `2px solid ${C.border}`, background: C.surface }}>
                  <td style={{ ...td, fontWeight: 700 }}>TOTAL</td>
                  <td style={td}></td>
                  <td style={td}></td>
                  <td style={td}></td>
                  <td style={{ ...td, textAlign: 'right', color: C.blue, fontWeight: 700 }}>
                    ${totalMarketValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: C.muted, fontWeight: 600 }}>
                    ${totalCostBasis.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: totalPnl >= 0 ? C.green : C.red, fontWeight: 700 }}>
                    {totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: totalPnl >= 0 ? C.green : C.red, fontWeight: 700 }}>
                    {totalPnl >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%
                  </td>
                </tr>
              </tbody>
            </table>
          )}
        </Card>

        {/* Allocation bars + activity */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Card>
            <CardTitle>ALLOCATION</CardTitle>
            {holdings.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', color: C.dim, fontSize: F.sm }}>
                Nothing to allocate yet.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {/* Cash row */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.sm, marginBottom: 4 }}>
                    <span style={{ color: C.muted }}>Cash</span>
                    <span style={{ color: C.text }}>
                      {((cashAvailable / portfolioValue) * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{
                      height: '100%',
                      width: `${(cashAvailable / portfolioValue) * 100}%`,
                      background: C.green, borderRadius: 3,
                    }} />
                  </div>
                </div>
                {/* Per-symbol rows */}
                {holdings.map((h) => {
                  const pct = (Math.abs(h.marketValue) / portfolioValue) * 100
                  return (
                    <div key={h.symbol}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.sm, marginBottom: 4 }}>
                        <span style={{ color: C.muted }}>{h.symbol}</span>
                        <span style={{ color: C.text }}>{pct.toFixed(1)}%</span>
                      </div>
                      <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{
                          height: '100%', width: `${pct}%`,
                          background: C.blue, borderRadius: 3,
                        }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>

          <Card>
            <CardTitle>RECENT FILLS</CardTitle>
            {recentFills.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', color: C.dim, fontSize: F.sm }}>
                No fills yet.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 240, overflowY: 'auto' }}>
                {recentFills.map((r, i) => (
                  <div key={`${r.child_order_id}-${i}`} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '6px 8px', background: C.surface, borderRadius: 3, fontSize: F.sm,
                  }}>
                    <span style={{ color: C.dim, fontSize: F.xs }}>{r.timestamp.slice(11, 19)}</span>
                    <span style={{ fontWeight: 600, width: 44 }}>{r.symbol}</span>
                    <span style={{ color: r.side === 'BUY' ? C.green : C.red, fontWeight: 700, width: 30, fontSize: F.xs }}>
                      {r.side}
                    </span>
                    <span style={{ color: C.muted }}>{r.quantity}</span>
                    <span style={{ color: C.text, marginLeft: 'auto' }}>${r.price.toFixed(2)}</span>
                    <span style={{ color: C.blue, fontSize: F.xs }}>{r.venue_id}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* Footer note */}
      <div style={{
        marginTop: 14, padding: 10, background: C.surface, borderRadius: 4,
        fontSize: F.xs, color: C.dim, textAlign: 'center',
      }}>
        â“˜ Cash starts at $1,000,000 for the POC. Prices update live as the NBBO refreshes every 3 seconds.
        {' '}
        Orders placed: {orders.length} · Fills recorded: {executionReports.filter(r => r.exec_type === 'FILL').length}
      </div>
    </div>
  )
}
