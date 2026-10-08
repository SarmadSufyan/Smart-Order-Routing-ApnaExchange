import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { useAuthStore } from '../stores/authStore'
import { fmtPnl, fmtPrice, fmtQty, fmtUsd } from '../format'
import { api } from '../services/api'
import type { FundView, ReconStatus } from '../services/api'
import { MQ, useMediaQuery, useTileColumns } from '../hooks/useMediaQuery'

const BAR_COLORS = [C.blue, C.green, C.orange, C.gold, C.purple, C.red]

const RECON_STYLE: Record<ReconStatus, { color: string; label: string }> = {
  OK:          { color: C.green,  label: 'MATCHED' },
  PENDING:     { color: C.blue,   label: 'SETTLING' },
  BREAK:       { color: C.red,    label: 'BREAK' },
  UNKNOWN:     { color: C.dim,    label: 'UNREADABLE' },
  NO_BASELINE: { color: C.dim,    label: 'NO BASELINE' },
}

// The automation fund (Bots & Lab): what is set aside for bots, and what manual trading may still use.
function FundStrip() {
  const [fund, setFund] = useState<FundView | null>(null)
  useEffect(() => {
    let live = true
    const load = () => api.getFund().then((f) => { if (live) setFund(f) }).catch(() => undefined)
    load()
    const timer = setInterval(load, 15_000)
    return () => { live = false; clearInterval(timer) }
  }, [])
  if (!fund || (fund.allocated_usdt <= 0 && Object.keys(fund.pockets).length === 0)) return null
  const coins = Object.entries(fund.reserved).filter(([a, q]) => a !== 'USDT' && q > 0)
  return (
    <div style={{
      marginBottom: 14, padding: '10px 14px', borderRadius: 6, background: tint(C.purple, 8),
      border: `1px solid ${tint(C.purple, 40)}`, fontSize: F.sm, color: C.text, display: 'flex', gap: 14, flexWrap: 'wrap',
    }}>
      <span><b>Automation fund</b>: {fmtUsd(fund.allocated_usdt)} set aside for bots · worth {fund.value_usdt === null ? '—' : fmtUsd(fund.value_usdt)}
        {' '}({fmtPnl(fund.pnl_market_usdt, 2)} at real-market fees)</span>
      <span style={{ color: C.muted }}>Free for manual trading: {fmtUsd(fund.manual_free.USDT ?? 0)} USDT
        {coins.map(([a, q]) => ` · ${fmtQty(q)} ${a} held by bots`)}</span>
      <Link to="/bots" style={{ color: C.purple, fontWeight: 700 }}>Bots &amp; Lab →</Link>
    </div>
  )
}

function pnlColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Math.abs(v) < 0.005) return C.text
  return v > 0 ? C.green : C.red
}

function fmtTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—'
}

export function Wallet() {
  const wallet           = useDataStore((s) => s.wallet)
  const pnl              = useDataStore((s) => s.pnl)
  const riskStatus       = useDataStore((s) => s.riskStatus)
  const refreshWallet    = useDataStore((s) => s.refreshWallet)
  const fetchPnl         = useDataStore((s) => s.fetchPnl)
  const resetBaseline    = useDataStore((s) => s.resetBaseline)
  const executionReports = useDataStore((s) => s.executionReports)
  const venues           = useDataStore((s) => s.venues)
  const role             = useAuthStore((s) => s.user?.role)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [rebasing, setRebasing]     = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const isNarrow    = useMediaQuery(MQ.narrow)
  const tileColumns = useTileColumns(4)

  const canRebaseline = role === 'ADMIN' || role === 'RISK_MANAGER'
  const total = wallet?.total_value_usdt ?? 0
  const assets = (wallet?.assets ?? []).filter((a) => a.total > 0)
  const exchanges = wallet?.venues ?? []
  const connected = exchanges.filter((v) => !v.error).length
  const hiddenAssets = exchanges.reduce((n, v) => n + v.other_assets_count, 0)
  const isMainnet = wallet?.mode === 'mainnet'

  const pnlByAsset = Object.fromEntries((pnl?.assets ?? []).map((a) => [a.asset, a]))
  const recon = pnl?.reconciliation ?? []
  const breaks = recon.filter((r) => r.status === 'BREAK')
  const concentration = riskStatus?.portfolio?.concentration ?? []
  const assetLimit = Object.fromEntries(concentration.filter((c) => c.kind === 'asset').map((c) => [c.key, c]))
  const venueLimit = Object.fromEntries(concentration.filter((c) => c.kind === 'venue').map((c) => [c.key, c]))
  const alerts = riskStatus?.portfolio?.alerts ?? []

  const recentFills = executionReports
    .filter((r) => r.exec_type === 'FILL' || r.exec_type === 'PARTIAL')
    .slice(0, 10)

  async function handleRefresh() {
    setRefreshing(true)
    setRefreshError(await refreshWallet())
    await fetchPnl()
    setRefreshing(false)
  }

  async function handleRebaseline(venueId: string, name: string) {
    const ok = window.confirm(
      `Accept ${name}'s current balances as the new opening balances?\n\n` +
      `P&L and net-traded positions for ${name} restart from now. ` +
      `The history stays in the audit log.`,
    )
    if (!ok) return
    setRebasing(venueId)
    setActionError(await resetBaseline(venueId))
    setRebasing(null)
  }

  const th: React.CSSProperties = { padding: '6px 10px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }
  const td: React.CSSProperties = { padding: '8px 10px', color: C.text, fontSize: F.base }
  const right: React.CSSProperties = { ...td, textAlign: 'right' }
  const statLabel: React.CSSProperties = { fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }
  const statSub: React.CSSProperties = { fontSize: F.xs, color: C.muted, marginTop: 4 }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
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
      {refreshError && (
        <div style={{ marginBottom: 8, padding: '6px 10px', borderRadius: 4, background: tint(C.red, 10), color: C.red, fontSize: F.sm }}>
          Couldn't refresh the balances: {refreshError} — showing the last balances read.
        </div>
      )}
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 16 }}>
        Real balances read from each exchange with your API keys, valued in USDT at live mid prices from
        ApnaSOR's own order books. Read-only — funds stay on the exchange. P&L comes from our own records
        (opening balances + every fill) and is reconciled against what the exchanges report.
      </div>

      {/* Summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: tileColumns, gap: 12, marginBottom: 14 }}>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={statLabel}>TOTAL VALUE</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: C.text }}>{fmtUsd(total)}</div>
          <div style={statSub}>
            USDT ·{' '}
            <span style={{ color: isMainnet ? C.red : C.orange, fontWeight: 600 }}>
              {isMainnet ? 'REAL funds' : `${(wallet?.mode || '—').toUpperCase()} — no real money`}
            </span>
          </div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={statLabel}>P&L SINCE OPENING BALANCES</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: pnlColor(pnl?.total_pnl) }}>
            {pnl?.complete ? fmtPnl(pnl.total_pnl) : '—'}
          </div>
          <div style={statSub}>since {fmtTime(pnl?.since)}</div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={statLabel}>TRADING P&L</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: pnlColor(pnl?.trading_pnl) }}>
            {pnl?.complete ? fmtPnl(pnl.trading_pnl) : '—'}
          </div>
          <div style={statSub}>
            what our trades added vs. just holding · fees {fmtUsd(pnl?.fees_usdt ?? 0)}
          </div>
        </Card>
        <Card style={{ textAlign: 'center', padding: 18 }}>
          <div style={statLabel}>RECONCILIATION</div>
          <div style={{ fontSize: F.xxl, fontWeight: 700, color: breaks.length ? C.red : C.green }}>
            {breaks.length ? `${breaks.length} BREAK${breaks.length > 1 ? 'S' : ''}` : 'MATCHED'}
          </div>
          <div style={statSub}>
            {connected}/{exchanges.length || venues.length} exchanges readable · updated{' '}
            {wallet?.as_of ? new Date(wallet.as_of).toLocaleTimeString() : '—'}
          </div>
        </Card>
      </div>

      <FundStrip />

      {alerts.length > 0 && (
        <div style={{
          marginBottom: 14, padding: '10px 14px', borderRadius: 6,
          background: tint(C.orange, 10), border: `1px solid ${tint(C.orange, 45)}`,
          color: C.text, fontSize: F.sm, display: 'flex', flexDirection: 'column', gap: 4,
        }}>
          {alerts.map((a) => <div key={a}>⚠ {a}</div>)}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? 'minmax(0, 1fr)' : 'minmax(0, 2fr) minmax(0, 1fr)', gap: 14 }}>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
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
              <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={th}>Asset</th>
                    <th style={{ ...th, textAlign: 'right' }}>Total</th>
                    <th style={{ ...th, textAlign: 'right' }}>Price</th>
                    <th style={{ ...th, textAlign: 'right' }}>Value (USDT)</th>
                    <th style={{ ...th, textAlign: 'right' }}>Share</th>
                    <th style={{ ...th, textAlign: 'right' }}>Avg cost</th>
                    <th style={{ ...th, textAlign: 'right' }}>P&L</th>
                    <th style={th}>Held on</th>
                  </tr>
                </thead>
                <tbody>
                  {assets.map((a) => {
                    const priced = exchanges
                      .flatMap((v) => v.assets)
                      .find((x) => x.asset === a.asset && x.price_usdt !== null)
                    const share = total > 0 && a.value_usdt ? (a.value_usdt / total) * 100 : 0
                    const p = pnlByAsset[a.asset]
                    const lim = assetLimit[a.asset]
                    const isCash = a.asset === 'USDT'
                    const tip = p && !isCash
                      ? `Realized ${fmtPnl(p.realized_pnl)} · Unrealized ${fmtPnl(p.unrealized_pnl)} · Fees ${fmtUsd(p.fees_usdt)} · Hold ${fmtPnl(p.hold_pnl)}`
                      : undefined
                    return (
                      <tr key={a.asset} style={tr}>
                        <td style={{ ...td, fontWeight: 700, fontSize: 13 }}>{a.asset}</td>
                        <td style={right}>{fmtQty(a.total)}</td>
                        <td style={right}>{fmtPrice(priced?.price_usdt)}</td>
                        <td style={{ ...right, color: C.blue, fontWeight: 600 }}>
                          {a.value_usdt !== null ? fmtUsd(a.value_usdt) : 'unpriced'}
                        </td>
                        <td style={{ ...right, color: lim?.breached ? C.red : C.muted, fontWeight: lim?.breached ? 700 : 400 }}
                            title={lim ? `Concentration limit ${lim.limit_pct}%` : undefined}>
                          {share.toFixed(1)}%{lim?.breached ? ' ⚠' : ''}
                        </td>
                        <td style={{ ...right, color: C.muted }}>{isCash ? '—' : fmtPrice(p?.avg_cost)}</td>
                        <td style={{ ...right, color: pnlColor(p?.total_pnl), fontWeight: 600 }} title={tip}>
                          {isCash ? '—' : fmtPnl(p?.total_pnl)}
                        </td>
                        <td style={{ ...td, color: C.muted, fontSize: F.sm }}>
                          {Object.entries(a.by_venue).filter(([, q]) => q > 0).map(([vid]) => vid).join(', ')}
                        </td>
                      </tr>
                    )
                  })}
                  <tr style={{ borderTop: `2px solid ${C.border}`, background: C.surface }}>
                    <td style={{ ...td, fontWeight: 700 }}>TOTAL</td>
                    <td style={td} /><td style={td} />
                    <td style={{ ...right, color: C.blue, fontWeight: 700 }}>{fmtUsd(total)}</td>
                    <td style={{ ...right, color: C.muted }}>100%</td>
                    <td style={td} />
                    <td style={{ ...right, color: pnlColor(pnl?.total_pnl), fontWeight: 700 }}>
                      {pnl?.complete ? fmtPnl(pnl.total_pnl) : '—'}
                    </td>
                    <td style={td} />
                  </tr>
                </tbody>
              </table>
              </div>
            )}

            {hiddenAssets > 0 && (
              <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
                ⓘ {hiddenAssets} other non-zero balance{hiddenAssets === 1 ? '' : 's'} (test tokens outside the
                trading universe) are hidden and excluded from the total.
              </div>
            )}
          </Card>

          {/* P&L breakdown */}
          <Card>
            <CardTitle>P&L BREAKDOWN</CardTitle>
            {!pnl?.complete ? (
              <div style={{ padding: 16, color: C.dim, fontSize: F.sm }}>
                {(pnl?.notes ?? ['Loading…']).map((n) => <div key={n}>{n}</div>)}
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 18 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.base }}>
                  <tbody>
                    {[
                      ['Opening value (at baseline prices)', fmtUsd(pnl.opening_value_usdt), C.muted],
                      ['Value now (our ledger, live prices)', fmtUsd(pnl.value_usdt), C.text],
                      ['Total P&L', fmtPnl(pnl.total_pnl), pnlColor(pnl.total_pnl)],
                      ['  ↳ Hold P&L — market move on opening balances', fmtPnl(pnl.hold_pnl), pnlColor(pnl.hold_pnl)],
                      ['  ↳ Trading P&L — ApnaSOR\'s trades', fmtPnl(pnl.trading_pnl), pnlColor(pnl.trading_pnl)],
                    ].map(([label, value, color]) => (
                      <tr key={label} style={tr}>
                        <td style={{ padding: '6px 4px', color: C.muted, whiteSpace: 'pre-wrap' }}>{label}</td>
                        <td style={{ padding: '6px 4px', textAlign: 'right', color, fontWeight: 600 }}>{value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.base }}>
                  <tbody>
                    {[
                      ['Realized (coins sold vs. their cost)', fmtPnl(pnl.realized_pnl), pnlColor(pnl.realized_pnl)],
                      ['Unrealized (holdings vs. their cost)', fmtPnl(pnl.unrealized_pnl), pnlColor(pnl.unrealized_pnl)],
                      ['Fees paid', fmtUsd(-(pnl.fees_usdt || 0)), (pnl.fees_usdt || 0) > 0 ? C.red : C.text],
                      ['Fills since baseline', String(pnl.fills_applied), C.text],
                    ].map(([label, value, color]) => (
                      <tr key={label} style={tr}>
                        <td style={{ padding: '6px 4px', color: C.muted }}>{label}</td>
                        <td style={{ padding: '6px 4px', textAlign: 'right', color, fontWeight: 600 }}>{value}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim, lineHeight: 1.6 }}>
              Average-cost accounting per exchange. Testnet coins have no purchase price, so each exchange's
              balances are marked at our mid price when first read (the <i>opening balances</i>); every fill
              since then is applied on top. Total = realized + unrealized − fees. Trading P&L is what
              ApnaSOR's orders added compared with simply holding the opening balances.
            </div>
          </Card>

          {/* Reconciliation */}
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
              <CardTitle>RECONCILIATION — OUR LEDGER VS. THE EXCHANGE</CardTitle>
              <span style={{ fontSize: F.xs, color: C.dim }}>expected = opening balances + our fills</span>
            </div>
            {actionError && (
              <div style={{ marginBottom: 8, padding: '6px 10px', borderRadius: 4, background: tint(C.red, 10), color: C.red, fontSize: F.sm }}>
                {actionError}
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {recon.map((r) => {
                const st = RECON_STYLE[r.status]
                const diffs = r.items.filter((i) => i.status !== 'OK')
                return (
                  <div key={r.venue_id} style={{
                    padding: '10px 12px', borderRadius: 4, background: C.surface,
                    border: `1px solid ${r.status === 'BREAK' ? tint(C.red, 45) : C.border}`,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
                      <span style={{ fontWeight: 700, minWidth: 120 }}>
                        {r.name} <span style={{ color: C.dim, fontWeight: 400 }}>{r.venue_id}</span>
                      </span>
                      <span style={{
                        fontSize: F.xs, padding: '1px 7px', borderRadius: 3, fontWeight: 700,
                        background: tint(st.color, 14), color: st.color,
                      }}>{st.label}</span>
                      <span style={{ fontSize: F.xs, color: C.dim, flex: 1 }}>
                        {r.baseline_at
                          ? `opening balances ${fmtTime(r.baseline_at)}${r.baseline_set_by && r.baseline_set_by !== 'auto' ? ` (accepted by ${r.baseline_set_by})` : ''}`
                          : r.note}
                      </span>
                      {r.status === 'BREAK' && canRebaseline && (
                        <button
                          onClick={() => handleRebaseline(r.venue_id, r.name)}
                          disabled={rebasing !== null}
                          style={{
                            padding: '4px 10px', borderRadius: 4, border: `1px solid ${C.red}`,
                            background: tint(C.red, 10), color: C.red, fontSize: F.xs,
                            cursor: rebasing ? 'wait' : 'pointer', fontFamily: 'inherit',
                          }}
                        >
                          {rebasing === r.venue_id ? 'Accepting…' : 'Accept exchange balances'}
                        </button>
                      )}
                    </div>
                    {r.baseline_at && r.note && (
                      <div style={{ fontSize: F.xs, color: r.status === 'BREAK' ? C.red : C.muted, marginTop: 4 }}>{r.note}</div>
                    )}
                    {diffs.length > 0 && (
                      <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6, fontSize: F.sm }}>
                        <thead>
                          <tr>
                            <th style={th}>Asset</th>
                            <th style={{ ...th, textAlign: 'right' }}>Expected</th>
                            <th style={{ ...th, textAlign: 'right' }}>Exchange says</th>
                            <th style={{ ...th, textAlign: 'right' }}>Difference</th>
                          </tr>
                        </thead>
                        <tbody>
                          {diffs.map((i) => (
                            <tr key={i.asset} style={tr}>
                              <td style={{ padding: '4px 10px' }}>{i.asset}</td>
                              <td style={{ padding: '4px 10px', textAlign: 'right' }}>{fmtQty(i.expected)}</td>
                              <td style={{ padding: '4px 10px', textAlign: 'right' }}>{fmtQty(i.actual)}</td>
                              <td style={{ padding: '4px 10px', textAlign: 'right', color: C.red }}>
                                {(i.difference ?? 0) > 0 ? '+' : ''}{fmtQty(i.difference)}
                                {i.difference_usdt !== null && ` (${fmtPnl(i.difference_usdt)})`}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      </div>
                    )}
                  </div>
                )
              })}
              {recon.length === 0 && (
                <div style={{ padding: 12, textAlign: 'center', color: C.dim, fontSize: F.sm }}>Loading…</div>
              )}
            </div>
          </Card>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Per-exchange */}
          <Card>
            <CardTitle>BY EXCHANGE</CardTitle>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {exchanges.length === 0 && (
                <div style={{ padding: 12, textAlign: 'center', color: C.dim, fontSize: F.sm }}>No exchanges yet.</div>
              )}
              {exchanges.map((v) => {
                const lim = venueLimit[v.venue_id]
                return (
                  <div key={v.venue_id} style={{
                    padding: '10px 12px', borderRadius: 4, background: C.surface,
                    border: `1px solid ${v.error || lim?.breached ? tint(C.red, 40) : C.border}`,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
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
                      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
                        <span style={{ fontSize: F.lg, color: C.text, fontWeight: 700 }}>{fmtUsd(v.total_value_usdt)}</span>
                        {lim && (
                          <span style={{ fontSize: F.xs, color: lim.breached ? C.red : C.muted }}
                                title="Share of the portfolio held on this exchange (counterparty risk)">
                            {lim.share_pct.toFixed(1)}% of portfolio{lim.breached ? ` ⚠ > ${lim.limit_pct}%` : ''}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
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
                  const lim = assetLimit[a.asset]
                  return (
                    <div key={a.asset}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.sm, marginBottom: 4 }}>
                        <span style={{ color: C.muted }}>{a.asset}</span>
                        <span style={{ color: lim?.breached ? C.red : C.text, fontWeight: lim?.breached ? 700 : 400 }}>
                          {pct.toFixed(1)}%{lim?.breached ? ` — above the ${lim.limit_pct}% limit` : ''}
                        </span>
                      </div>
                      <div style={{ position: 'relative', height: 6, background: C.border, borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: lim?.breached ? C.red : BAR_COLORS[i % BAR_COLORS.length], borderRadius: 3 }} />
                        {lim && (
                          <div title={`limit ${lim.limit_pct}%`} style={{
                            position: 'absolute', top: 0, bottom: 0, left: `${lim.limit_pct}%`, width: 2, background: C.text, opacity: 0.6,
                          }} />
                        )}
                      </div>
                    </div>
                  )
                })}
                <div style={{ fontSize: F.xs, color: C.dim }}>
                  Tick = concentration limit (set in Risk Manager). USDT is cash and isn't limited.
                </div>
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
                    display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8,
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
