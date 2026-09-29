import React, { useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { fmtPrice, fmtQty, fmtUsd } from '../format'

const BAR_COLORS = [C.blue, C.green, C.orange, C.gold, C.purple, C.red]

export function Wallet() {
  const wallet           = useDataStore((s) => s.wallet)
  const refreshWallet    = useDataStore((s) => s.refreshWallet)
  const executionReports = useDataStore((s) => s.executionReports)
  const venues           = useDataStore((s) => s.venues)
  const [refreshing, setRefreshing] = useState(false)

  const total = wallet?.total_value_usdt ?? 0
  const assets = (wallet?.assets ?? []).filter((a) => a.total > 0)
  const exchanges = wallet?.venues ?? []
  const connected = exchanges.filter((v) => !v.error).length
  const hiddenAssets = exchanges.reduce((n, v) => n + v.other_assets_count, 0)
  const isMainnet = wallet?.mode === 'mainnet'

  const recentFills = executionReports
    .filter((r) => r.exec_type === 'FILL' || r.exec_type === 'PARTIAL')
    .slice(0, 10)

  async function handleRefresh() {
    setRefreshing(true)
    await refreshWallet()
    setRefreshing(false)
  }

  const th: React.CSSProperties = { padding: '6px 10px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }
  const td: React.CSSProperties = { padding: '8px 10px', color: C.text, fontSize: F.base }
  const right: React.CSSProperties = { ...td, textAlign: 'right' }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ fontSize: F.lg, color: C.text }}>Wallet — Unified Portfolio</div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          style={{
            padding: '5px 12px', border: `1px solid ${C.border}`, borderRadius: 4,
            background: C.surface, color: C.muted, fontSize: F.xs,
            cursor: 'pointer', fontFamily: 'inherit', opacity: refreshing ? 0.5 : 1,
          }}
        >
          {refreshing ? 'Refreshing…' : '↻ Refresh balances'}
        </button>
      </div>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 16 }}>
        Real balances read from each connected exchange with your API keys, valued in USDT at live
        mid prices from ApnaSOR's own order books. Read-only — funds stay on the exchange.
      </div>

      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>TOTAL VALUE</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: C.text }}>{fmtUsd(total)}</div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>USDT, tracked assets only</div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>EXCHANGES</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: connected > 0 ? C.green : C.red }}>
            {connected}/{exchanges.length || venues.length}
          </div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>balances readable</div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>MODE</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: isMainnet ? C.red : C.orange }}>
            {(wallet?.mode || '—').toUpperCase()}
          </div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
            {isMainnet ? 'REAL funds' : 'test funds — no real money'}
          </div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>LAST UPDATED</div>
          <div style={{ fontSize: F.xl, fontWeight: 700, color: C.blue, marginTop: 4 }}>
            {wallet?.as_of ? new Date(wallet.as_of).toLocaleTimeString() : '—'}
          </div>
          <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>auto-refreshes after every fill</div>
        </Card>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>

        {/* Holdings */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <CardTitle>HOLDINGS — ALL EXCHANGES</CardTitle>
            <span style={{ fontSize: F.xs, color: C.dim }}>{assets.length} asset{assets.length === 1 ? '' : 's'}</span>
          </div>

          {assets.length === 0 ? (
            <div style={{ padding: 40, textAlign: 'center', color: C.dim, fontSize: F.base }}>
              {wallet?.as_of ? 'No balances in the tracked assets.' : 'Loading balances…'}
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>Asset</th>
                  <th style={{ ...th, textAlign: 'right' }}>Total</th>
                  <th style={{ ...th, textAlign: 'right' }}>Available</th>
                  <th style={{ ...th, textAlign: 'right' }}>Price</th>
                  <th style={{ ...th, textAlign: 'right' }}>Value (USDT)</th>
                  <th style={{ ...th, textAlign: 'right' }}>Share</th>
                  <th style={th}>Held on</th>
                </tr>
              </thead>
              <tbody>
                {assets.map((a) => {
                  const priced = exchanges
                    .flatMap((v) => v.assets)
                    .find((x) => x.asset === a.asset && x.price_usdt !== null)
                  const share = total > 0 && a.value_usdt ? (a.value_usdt / total) * 100 : 0
                  return (
                    <tr key={a.asset} style={tr}>
                      <td style={{ ...td, fontWeight: 700, fontSize: 13 }}>{a.asset}</td>
                      <td style={right}>{fmtQty(a.total)}</td>
                      <td style={{ ...right, color: C.muted }}>{fmtQty(a.free)}</td>
                      <td style={right}>{fmtPrice(priced?.price_usdt)}</td>
                      <td style={{ ...right, color: C.blue, fontWeight: 600 }}>
                        {a.value_usdt !== null ? fmtUsd(a.value_usdt) : 'unpriced'}
                      </td>
                      <td style={{ ...right, color: C.muted }}>{share.toFixed(1)}%</td>
                      <td style={{ ...td, color: C.muted, fontSize: F.sm }}>
                        {Object.entries(a.by_venue).filter(([, q]) => q > 0).map(([vid]) => vid).join(', ')}
                      </td>
                    </tr>
                  )
                })}
                <tr style={{ borderTop: `2px solid ${C.border}`, background: C.surface }}>
                  <td style={{ ...td, fontWeight: 700 }}>TOTAL</td>
                  <td style={td} /><td style={td} /><td style={td} />
                  <td style={{ ...right, color: C.blue, fontWeight: 700 }}>{fmtUsd(total)}</td>
                  <td style={{ ...right, color: C.muted }}>100%</td>
                  <td style={td} />
                </tr>
              </tbody>
            </table>
          )}

          {hiddenAssets > 0 && (
            <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
              ⓘ {hiddenAssets} other non-zero balance{hiddenAssets === 1 ? '' : 's'} (test tokens outside the
              trading universe) are hidden and excluded from the total.
            </div>
          )}
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Per-exchange */}
          <Card>
            <CardTitle>BY EXCHANGE</CardTitle>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {exchanges.length === 0 && (
                <div style={{ padding: 12, textAlign: 'center', color: C.dim, fontSize: F.sm }}>No exchanges yet.</div>
              )}
              {exchanges.map((v) => (
                <div key={v.venue_id} style={{
                  padding: '10px 12px', borderRadius: 4, background: C.surface,
                  border: `1px solid ${v.error ? tint(C.red, 40) : C.border}`,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontWeight: 700 }}>{v.name} <span style={{ color: C.dim, fontWeight: 400 }}>{v.venue_id}</span></span>
                    <span style={{
                      fontSize: F.xs, padding: '1px 6px', borderRadius: 3,
                      background: tint(v.mode === 'mainnet' ? C.red : C.orange, 14),
                      color: v.mode === 'mainnet' ? C.red : C.orange,
                    }}>{v.mode.toUpperCase()}</span>
                  </div>
                  {v.error ? (
                    <div style={{ fontSize: F.xs, color: C.red, marginTop: 4 }}>{v.error}</div>
                  ) : (
                    <div style={{ fontSize: F.lg, color: C.text, fontWeight: 700, marginTop: 4 }}>
                      {fmtUsd(v.total_value_usdt)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Card>

          {/* Allocation */}
          <Card>
            <CardTitle>ALLOCATION</CardTitle>
            {assets.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', color: C.dim, fontSize: F.sm }}>Nothing to allocate yet.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {assets.map((a, i) => {
                  const pct = total > 0 && a.value_usdt ? (a.value_usdt / total) * 100 : 0
                  return (
                    <div key={a.asset}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.sm, marginBottom: 4 }}>
                        <span style={{ color: C.muted }}>{a.asset}</span>
                        <span style={{ color: C.text }}>{pct.toFixed(1)}%</span>
                      </div>
                      <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: BAR_COLORS[i % BAR_COLORS.length], borderRadius: 3 }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>

          {/* Recent fills */}
          <Card>
            <CardTitle>RECENT FILLS</CardTitle>
            {recentFills.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', color: C.dim, fontSize: F.sm }}>No fills yet.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 240, overflowY: 'auto' }}>
                {recentFills.map((r, i) => (
                  <div key={`${r.child_order_id}-${i}`} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '6px 8px', background: C.surface, borderRadius: 3, fontSize: F.sm,
                  }}>
                    <span style={{ color: C.dim, fontSize: F.xs }}>{r.timestamp.slice(11, 19)}</span>
                    <span style={{ fontWeight: 600 }}>{r.symbol}</span>
                    <span style={{ color: r.side === 'BUY' ? C.green : C.red, fontWeight: 700, fontSize: F.xs }}>{r.side}</span>
                    <span style={{ color: C.muted }}>{fmtQty(r.quantity)}</span>
                    <span style={{ color: C.text, marginLeft: 'auto' }}>{fmtPrice(r.price)}</span>
                    <span style={{ color: C.blue, fontSize: F.xs }}>{r.venue_id}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
