import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { C, F, tint } from '../theme'
import { Card, CardTitle, ArcGauge } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { useAuthStore } from '../stores/authStore'
import { backendRiskToPositions } from '../adapters'
import { SYMBOLS } from '../mockData'
import { fmtPnl, fmtPrice, fmtUsd } from '../format'

const PORTFOLIO_LIMITS = [
  { key: 'max_trading_loss_usdt', label: 'Trading loss limit', unit: 'USDT', hint: '0 = off. Breach engages the kill switch.' },
  { key: 'max_asset_concentration_pct', label: 'Max share of one asset', unit: '%', hint: 'Non-stable assets only.' },
  { key: 'max_venue_concentration_pct', label: 'Max share on one exchange', unit: '%', hint: 'Counterparty risk.' },
  { key: 'max_algo_notional_usdt', label: 'Max TWAP order value', unit: 'USDT', hint: 'Whole parent order, so slicing can\'t dodge the per-order limit.' },
] as const

type PortfolioLimitKey = typeof PORTFOLIO_LIMITS[number]['key']

export function RiskManager() {
  const navigate = useNavigate()
  const riskStatus = useDataStore((s) => s.riskStatus)
  const orders     = useDataStore((s) => s.orders)
  const routingStats = useDataStore((s) => s.routingStats)
  const updateRiskLimits = useDataStore((s) => s.updateRiskLimits)
  const isAdmin = useAuthStore((s) => s.user?.role) === 'ADMIN'

  const [search, setSearch] = useState('')
  const [draft, setDraft] = useState<Partial<Record<PortfolioLimitKey, string>>>({})
  const [saving, setSaving] = useState(false)
  const [limitMsg, setLimitMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const portfolio = riskStatus?.portfolio ?? null
  const limits = riskStatus?.limits ?? null
  const lossUsed = Math.max(0, -(portfolio?.trading_pnl ?? 0))

  async function saveLimits() {
    if (!limits) return
    const next = { ...limits }
    for (const { key } of PORTFOLIO_LIMITS) {
      const raw = draft[key]
      if (raw === undefined || raw === '') continue
      const val = Number(raw)
      if (!isFinite(val) || val < 0) {
        setLimitMsg({ ok: false, text: `Invalid value for ${key}` })
        return
      }
      next[key] = val
    }
    setSaving(true)
    const err = await updateRiskLimits(next)
    setSaving(false)
    setLimitMsg(err ? { ok: false, text: err } : { ok: true, text: 'Saved — applies immediately and survives restarts.' })
    if (!err) setDraft({})
  }

  const positions = backendRiskToPositions(riskStatus)
  const filtered = positions.filter((p) => !search || p.sym.includes(search.toUpperCase()))

  const openPositions = positions.length  // units of different assets can't be summed
  const notional = riskStatus?.total_exposure ?? 0
  const notionalLimit = riskStatus?.exposure_limit || 1_000_000
  const ordersToday = routingStats?.orders_routed_today ?? 0

  // Derive pre-trade check stats from recent_checks + orders
  const recent = riskStatus?.recent_checks ?? []
  const totalChecks = orders.length
  const approved = recent.filter((c) => c.result === 'APPROVED').length
  const rejected = recent.filter((c) => c.result !== 'APPROVED').length
  const avgMs = recent.length > 0
    ? (recent.reduce((s, c) => s + c.check_duration_ms, 0) / recent.length).toFixed(2)
    : '—'

  const checks = [
    {
      name: 'Kill Switch',
      total: totalChecks,
      passed: orders.filter((o) => o.status !== 'REJECTED' || !o.rejection_reason?.toLowerCase().includes('kill')).length,
      rejected: orders.filter((o) => o.rejection_reason?.toLowerCase().includes('kill')).length,
    },
    {
      name: 'Position Limit',
      total: totalChecks,
      passed: orders.filter((o) => !o.rejection_reason?.toLowerCase().includes('position')).length,
      rejected: orders.filter((o) => o.rejection_reason?.toLowerCase().includes('position')).length,
    },
    {
      name: 'Notional Limit',
      total: totalChecks,
      passed: orders.filter((o) => !o.rejection_reason?.toLowerCase().includes('notional') && !o.rejection_reason?.toLowerCase().includes('exposure')).length,
      rejected: orders.filter((o) => o.rejection_reason?.toLowerCase().includes('notional') || o.rejection_reason?.toLowerCase().includes('exposure')).length,
    },
    {
      name: 'Symbol Check',
      total: totalChecks,
      passed: orders.filter((o) => !o.rejection_reason?.toLowerCase().includes('symbol')).length,
      rejected: orders.filter((o) => o.rejection_reason?.toLowerCase().includes('symbol')).length,
    },
  ]

  const th: React.CSSProperties = { padding: '3px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }

  return (
    <div>
      <div style={{ fontSize: F.lg, color: C.text, marginBottom: 14 }}>Risk Manager Console</div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
        <Card style={{ textAlign: 'center' }}>
          <ArcGauge value={openPositions} max={SYMBOLS.length} label="Open Positions" unit="symbols" size={120} />
        </Card>
        <Card style={{ textAlign: 'center' }}>
          <ArcGauge value={Math.round(notional)} max={notionalLimit} label="Notional" unit="USD" size={120} />
        </Card>
        <Card style={{ textAlign: 'center' }}>
          <ArcGauge value={ordersToday} max={500} label="Orders Routed" unit="today" size={120} />
        </Card>
        <Card style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: F.xxl, fontWeight: 700, color: notional > 0 ? C.green : C.muted }}>
              ${notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </div>
            <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>Exposure Used</div>
            <div style={{ fontSize: F.xs, color: C.dim, marginTop: 2 }}>
              {riskStatus ? `${riskStatus.exposure_utilization_pct.toFixed(1)}% of limit` : '—'}
            </div>
          </div>
        </Card>
      </div>

      <Card style={{ marginBottom: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{
            width: 12, height: 12, borderRadius: '50%',
            background: riskStatus?.kill_switch_active ? C.red : C.green,
            boxShadow: `0 0 8px ${riskStatus?.kill_switch_active ? C.red : C.green}`,
            display: 'inline-block', flexShrink: 0,
          }} />
          <span style={{ fontSize: F.lg, color: riskStatus?.kill_switch_active ? C.red : C.green, fontWeight: 600 }}>
            KILL SWITCH {riskStatus?.kill_switch_active ? 'ACTIVE' : 'INACTIVE'}
          </span>
          {riskStatus?.kill_switch_active && riskStatus.kill_switch_reason && (
            <span style={{ fontSize: F.sm, color: C.muted, marginLeft: 8 }}>
              Reason: {riskStatus.kill_switch_reason}
            </span>
          )}
        </div>
        <button
          onClick={() => navigate('/killswitch')}
          style={{
            padding: '8px 20px', borderRadius: 4, border: 'none',
            background: C.red, color: '#fff', fontSize: F.base,
            cursor: 'pointer', fontFamily: 'inherit',
          }}
        >
          {riskStatus?.kill_switch_active ? 'Manage Kill Switch' : 'Activate Kill Switch'}
        </button>
      </Card>

      {/* Portfolio controls — watched continuously by the risk monitor */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr 1.2fr', gap: 12, marginBottom: 12 }}>
        <Card style={{ textAlign: 'center' }}>
          <CardTitle>TRADING LOSS LIMIT</CardTitle>
          {portfolio && portfolio.loss_limit_usdt > 0 ? (
            <>
              <ArcGauge value={Math.round(lossUsed * 100) / 100} max={portfolio.loss_limit_usdt}
                        label={`of ${fmtUsd(portfolio.loss_limit_usdt, 0)} limit`} unit="USDT lost" size={130} />
              <div style={{ fontSize: F.sm, marginTop: 6 }}>
                Trading P&L{' '}
                <span style={{ color: (portfolio.trading_pnl ?? 0) < 0 ? C.red : C.green, fontWeight: 700 }}>
                  {fmtPnl(portfolio.trading_pnl)}
                </span>
              </div>
              <div style={{ fontSize: F.xs, color: portfolio.loss_limit_breached ? C.red : C.dim, marginTop: 4 }}>
                {portfolio.loss_limit_breached
                  ? 'BREACHED — kill switch engaged automatically'
                  : 'A breach engages the kill switch automatically'}
              </div>
            </>
          ) : (
            <div style={{ fontSize: F.sm, color: C.dim, padding: '24px 0' }}>
              {portfolio ? 'Loss limit is off (0).' : 'Waiting for the risk monitor…'}
            </div>
          )}
        </Card>

        <Card>
          <CardTitle>CONCENTRATION</CardTitle>
          {(portfolio?.concentration ?? []).length === 0 ? (
            <div style={{ fontSize: F.sm, color: C.dim, textAlign: 'center', padding: '20px 0' }}>
              Waiting for wallet balances…
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
              {portfolio!.concentration.filter((c) => c.share_pct >= 1 || c.breached).map((c) => (
                <div key={`${c.kind}-${c.key}`}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.xs, marginBottom: 3 }}>
                    <span style={{ color: C.muted }}>
                      {c.kind === 'venue' ? 'Exchange' : 'Asset'} · <span style={{ color: C.text }}>{c.name}</span>
                    </span>
                    <span style={{ color: c.breached ? C.red : C.text, fontWeight: c.breached ? 700 : 400 }}>
                      {c.share_pct.toFixed(1)}% / {c.limit_pct}%
                    </span>
                  </div>
                  <div style={{ position: 'relative', height: 5, background: C.border, borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${Math.min(100, c.share_pct)}%`, background: c.breached ? C.red : C.blue }} />
                    <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${c.limit_pct}%`, width: 2, background: C.text, opacity: 0.6 }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <CardTitle>PORTFOLIO LIMITS</CardTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {PORTFOLIO_LIMITS.map((l) => (
              <div key={l.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: F.sm, color: C.text }}>{l.label}</div>
                  <div style={{ fontSize: F.xs, color: C.dim }}>{l.hint}</div>
                </div>
                {isAdmin ? (
                  <input
                    value={draft[l.key] ?? (limits ? String(limits[l.key]) : '')}
                    onChange={(e) => setDraft((d) => ({ ...d, [l.key]: e.target.value }))}
                    style={{
                      width: 90, padding: '4px 6px', borderRadius: 3, textAlign: 'right',
                      background: C.surface, border: `1px solid ${C.border}`, color: C.text,
                      fontSize: F.sm, fontFamily: 'inherit', outline: 'none',
                    }}
                  />
                ) : (
                  <span style={{ fontSize: F.sm, color: C.text }}>{limits ? limits[l.key].toLocaleString() : '—'}</span>
                )}
                <span style={{ fontSize: F.xs, color: C.dim, width: 34 }}>{l.unit}</span>
              </div>
            ))}
            {isAdmin ? (
              <button onClick={saveLimits} disabled={saving || !limits || Object.keys(draft).length === 0} style={{
                marginTop: 4, padding: '6px 12px', borderRadius: 4, border: 'none',
                background: C.blue, color: '#fff', fontSize: F.sm, fontFamily: 'inherit',
                cursor: saving ? 'wait' : 'pointer', opacity: Object.keys(draft).length === 0 ? 0.5 : 1,
              }}>
                {saving ? 'Saving…' : 'Save limits'}
              </button>
            ) : (
              <div style={{ fontSize: F.xs, color: C.dim }}>Only an admin can change limits.</div>
            )}
            {limitMsg && (
              <div style={{ fontSize: F.xs, color: limitMsg.ok ? C.green : C.red }}>{limitMsg.text}</div>
            )}
          </div>
        </Card>
      </div>

      <Card style={{ marginBottom: 12 }}>
        <CardTitle>PRE-TRADE CHECK BREAKDOWN</CardTitle>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
          <thead>
            <tr>
              <th style={th}>Check</th>
              <th style={{ ...th, textAlign: 'right' }}>Total</th>
              <th style={{ ...th, textAlign: 'right' }}>Passed</th>
              <th style={{ ...th, textAlign: 'right' }}>Rejected</th>
              <th style={{ ...th, textAlign: 'right' }}>Rej %</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((c) => {
              const rejPct = c.total > 0 ? (c.rejected / c.total) * 100 : 0
              return (
                <tr key={c.name} style={tr}>
                  <td style={{ padding: '7px 8px' }}>{c.name}</td>
                  <td style={{ padding: '7px 8px', textAlign: 'right', color: C.muted }}>{c.total}</td>
                  <td style={{ padding: '7px 8px', textAlign: 'right', color: C.green }}>{c.passed}</td>
                  <td style={{ padding: '7px 8px', textAlign: 'right', color: c.rejected > 0 ? C.red : C.muted }}>{c.rejected}</td>
                  <td style={{ padding: '7px 8px', textAlign: 'right', color: rejPct > 30 ? C.orange : C.muted }}>{rejPct.toFixed(1)}%</td>
                </tr>
              )
            })}
            <tr style={{ ...tr, background: C.surface }}>
              <td style={{ padding: '7px 8px', color: C.muted, fontSize: F.xs }} colSpan={5}>
                Recent checks: <span style={{ color: C.text }}>{recent.length}</span> · approved: <span style={{ color: C.green }}>{approved}</span> · rejected: <span style={{ color: C.red }}>{rejected}</span> · avg time: <span style={{ color: C.text }}>{avgMs} ms</span>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>

        <Card>
          <CardTitle>VENUE ALLOCATION (LAST ROUTED)</CardTitle>
          {routingStats && Object.keys(routingStats.venue_allocation).length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {Object.entries(routingStats.venue_allocation).map(([vid, share]) => {
                const pct = share * 100
                return (
                  <div key={vid}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.xs, marginBottom: 3 }}>
                      <span style={{ color: C.muted }}>{vid}</span>
                      <span style={{ color: C.text }}>{pct.toFixed(1)}%</span>
                    </div>
                    <div style={{ height: 6, background: C.border, borderRadius: 3, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: C.blue, borderRadius: 3, transition: 'width .5s' }} />
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div style={{ fontSize: F.sm, color: C.dim, textAlign: 'center', padding: '20px 0' }}>
              No routing data yet — place an order.
            </div>
          )}
        </Card>

        <Card>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <CardTitle>NET TRADED SINCE OPENING BALANCES</CardTitle>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search symbol…"
              style={{
                background: C.surface, border: `1px solid ${C.border}`, borderRadius: 3,
                padding: '3px 8px', color: C.text, fontSize: F.xs, fontFamily: 'inherit',
                width: 120, outline: 'none',
              }}
            />
          </div>
          {filtered.length === 0 ? (
            <div style={{ fontSize: F.sm, color: C.dim, textAlign: 'center', padding: '20px 0' }}>
              Nothing traded since the opening balances. Holdings are on the Wallet page.
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
              <thead>
                <tr>
                  <th style={th}>Sym</th>
                  <th style={{ ...th, textAlign: 'right' }}>Net</th>
                  <th style={{ ...th, textAlign: 'right' }}>Avg Price</th>
                  <th style={{ ...th, textAlign: 'right' }}>% Used</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((p) => {
                  const pct = ((Math.abs(p.net) / p.max) * 100).toFixed(1)
                  const bg  = +pct > 80 ? tint(C.red, 6) : +pct > 60 ? tint(C.orange, 6) : undefined
                  const pc  = +pct > 80 ? C.red : +pct > 60 ? C.orange : C.muted
                  return (
                    <tr key={p.sym} style={{ ...tr, background: bg }}>
                      <td style={{ padding: '6px' }}>{p.sym}</td>
                      <td style={{ padding: '6px', textAlign: 'right', color: p.net >= 0 ? C.green : C.red }}>
                        {p.net.toLocaleString()}
                      </td>
                      <td style={{ padding: '6px', textAlign: 'right', color: C.muted }}>
                        {fmtPrice(p.price)}
                      </td>
                      <td style={{ padding: '6px', textAlign: 'right', color: pc }}>{pct}%</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </Card>

      </div>
    </div>
  )
}
