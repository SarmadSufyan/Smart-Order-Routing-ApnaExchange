import React, { useEffect, useMemo, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { TwapChart } from '../components/TwapChart'
import { OrderProofModal } from '../components/OrderProofModal'
import { useDataStore } from '../stores/dataStore'
import { SYMBOLS } from '../mockData'
import { baseAsset, fmtPrice, fmtQty, fmtUsd } from '../format'
import type { AlgoStatus, TwapState } from '../services/api'

const MIN_INTERVAL_S = 3        // mirrors the backend (a slice + balance refresh take ~1-2 s)
const TYPICAL_MIN_ORDER_USD = 5 // most venues' minimum order value

const STATUS_STYLE: Record<AlgoStatus, { color: string; label: string }> = {
  RUNNING:   { color: C.blue,   label: 'RUNNING' },
  PAUSED:    { color: C.orange, label: 'PAUSED' },
  COMPLETED: { color: C.green,  label: 'COMPLETED' },
  EXPIRED:   { color: C.orange, label: 'EXPIRED' },
  CANCELLED: { color: C.dim,    label: 'CANCELLED' },
  FAILED:    { color: C.red,    label: 'FAILED' },
}

function StatusChip({ status }: { status: AlgoStatus }) {
  const s = STATUS_STYLE[status]
  return (
    <span style={{
      fontSize: F.xs, padding: '1px 7px', borderRadius: 3, fontWeight: 700,
      background: tint(s.color, 14), color: s.color, whiteSpace: 'nowrap',
    }}>{s.label}</span>
  )
}

/** Execution cost in bps: + = worse than the benchmark (paid more / received less). */
function Bps({ v }: { v: number | null }) {
  if (v === null) return <span style={{ color: C.dim }}>—</span>
  const color = Math.abs(v) < 0.005 ? C.text : v > 0 ? C.red : C.green
  return <span style={{ color }}>{v > 0 ? '+' : ''}{v.toFixed(2)} bps</span>
}

function targetNow(a: TwapState, now: number): number {
  let t = 0
  for (const s of a.slices) if (new Date(s.scheduled_at).getTime() <= now) t = s.target_cumulative
  return t
}

export function AlgoOrders() {
  const algos      = useDataStore((s) => s.algos)
  const nbbos      = useDataStore((s) => s.nbbos)
  const orders     = useDataStore((s) => s.orders)
  const riskStatus = useDataStore((s) => s.riskStatus)
  const startTwap  = useDataStore((s) => s.startTwap)
  const controlAlgo = useDataStore((s) => s.controlAlgo)
  const fetchAlgos = useDataStore((s) => s.fetchAlgos)

  const [symbol, setSymbol]   = useState<string>(SYMBOLS[0])
  const [side, setSide]       = useState<'BUY' | 'SELL'>('BUY')
  const [qty, setQty]         = useState('')
  const [duration, setDuration] = useState('2')
  const [unit, setUnit]       = useState<'min' | 'sec'>('min')
  const [slices, setSlices]   = useState('10')
  const [limit, setLimit]     = useState('')
  const [randomize, setRandomize] = useState(false)
  const [busy, setBusy]       = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [proofOrderId, setProofOrderId] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())

  useEffect(() => { fetchAlgos() }, [fetchAlgos])
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const selected = algos.find((a) => a.id === selectedId) ?? algos[0] ?? null
  const killActive = riskStatus?.kill_switch_active ?? false
  const base = baseAsset(symbol)

  // Live preview of the schedule the backend will build.
  const preview = useMemo(() => {
    const n = nbbos[symbol]
    const mid = n && n.best_bid && n.best_ask ? (n.best_bid + n.best_ask) / 2 : null
    const q = parseFloat(qty)
    const k = parseInt(slices, 10)
    const secs = parseFloat(duration) * (unit === 'min' ? 60 : 1)
    if (!(q > 0) || !(k >= 2) || !(secs > 0)) return null
    const slice = q / k
    const interval = secs / k
    const warnings: string[] = []
    if (interval < MIN_INTERVAL_S) warnings.push(`One slice every ${interval.toFixed(1)} s — the minimum is ${MIN_INTERVAL_S} s. Use fewer slices or a longer window.`)
    if (mid && slice * mid < TYPICAL_MIN_ORDER_USD) warnings.push(`Each slice is only ~${fmtUsd(slice * mid)}; most venues need at least ${fmtUsd(TYPICAL_MIN_ORDER_USD)} per order.`)
    const algoLimit = riskStatus?.limits?.max_algo_notional_usdt
    if (mid && algoLimit && q * mid > algoLimit) warnings.push(`Total ~${fmtUsd(q * mid)} is above the algo limit ${fmtUsd(algoLimit)}.`)
    return { mid, slice, interval, secs, total: mid ? q * mid : null, warnings }
  }, [nbbos, symbol, qty, slices, duration, unit, riskStatus])

  async function submit() {
    if (!preview) return
    setBusy(true)
    setFormError(null)
    const [algo, err] = await startTwap({
      symbol, side, quantity: parseFloat(qty), duration_s: preview.secs, slices: parseInt(slices, 10),
      limit_price: limit ? parseFloat(limit) : null, jitter_pct: randomize ? 20 : 0,
    })
    setBusy(false)
    if (err) setFormError(err)
    if (algo) setSelectedId(algo.id)
  }

  async function control(id: string, action: 'pause' | 'resume' | 'cancel') {
    if (action === 'cancel' && !window.confirm(`Cancel ${id}? Slices already filled stay filled; the rest are not sent.`)) return
    setActionError(await controlAlgo(id, action))
  }

  const proofOrder = proofOrderId ? orders.find((o) => o.id === proofOrderId) ?? null : null

  const label: React.CSSProperties = { fontSize: F.xs, color: C.dim, letterSpacing: '.06em', marginBottom: 4 }
  const input: React.CSSProperties = {
    width: '100%', padding: '7px 9px', borderRadius: 4, background: C.surface,
    border: `1px solid ${C.border}`, color: C.text, fontSize: F.base, fontFamily: 'inherit', outline: 'none',
    boxSizing: 'border-box',
  }
  const th: React.CSSProperties = { padding: '5px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '6px 8px', fontSize: F.sm, color: C.text, borderBottom: `1px solid ${C.border}40`, whiteSpace: 'nowrap' }
  const right: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
  const tile: React.CSSProperties = { padding: '10px 12px', borderRadius: 4, background: C.surface, minWidth: 0 }

  return (
    <div>
      <div style={{ fontSize: F.lg, color: C.text, marginBottom: 6 }}>Algo Orders — TWAP</div>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 16, maxWidth: 980 }}>
        A TWAP spreads a large order evenly over time to reduce market impact. Every slice is an ordinary order
        through the same pre-trade risk checks, balance check and smart router as the order button — the bot has
        no other way to trade. A slice that fills short rolls into the next one (at most 2× a normal slice); the kill
        switch pauses every running TWAP, and a restart never resumes one on its own.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 340px) minmax(0, 1fr)', gap: 14, marginBottom: 14 }}>
        {/* New TWAP */}
        <Card>
          <CardTitle>NEW TWAP</CardTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div>
                <div style={label}>SYMBOL</div>
                <select value={symbol} onChange={(e) => setSymbol(e.target.value)} style={input}>
                  {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <div style={label}>SIDE</div>
                <div style={{ display: 'flex', gap: 4 }}>
                  {(['BUY', 'SELL'] as const).map((sd) => (
                    <button key={sd} onClick={() => setSide(sd)} style={{
                      flex: 1, padding: '7px 0', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit',
                      fontSize: F.sm, fontWeight: 700,
                      border: `1px solid ${side === sd ? (sd === 'BUY' ? C.green : C.red) : C.border}`,
                      background: side === sd ? tint(sd === 'BUY' ? C.green : C.red, 14) : C.surface,
                      color: side === sd ? (sd === 'BUY' ? C.green : C.red) : C.muted,
                    }}>{sd}</button>
                  ))}
                </div>
              </div>
            </div>
            <div>
              <div style={label}>TOTAL QUANTITY ({base})</div>
              <input value={qty} onChange={(e) => setQty(e.target.value)} placeholder="e.g. 0.002" style={input} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div>
                <div style={label}>OVER</div>
                <div style={{ display: 'flex', gap: 4 }}>
                  <input value={duration} onChange={(e) => setDuration(e.target.value)} style={{ ...input, width: '55%' }} />
                  <select value={unit} onChange={(e) => setUnit(e.target.value as 'min' | 'sec')} style={{ ...input, width: '45%' }}>
                    <option value="min">min</option>
                    <option value="sec">sec</option>
                  </select>
                </div>
              </div>
              <div>
                <div style={label}>SLICES</div>
                <input value={slices} onChange={(e) => setSlices(e.target.value)} style={input} />
              </div>
            </div>
            <div>
              <div style={label}>LIMIT PRICE (OPTIONAL — WORST PRICE FOR ANY SLICE)</div>
              <input value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="none = market, price-protected" style={input} />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: F.sm, color: C.muted, cursor: 'pointer' }}>
              <input type="checkbox" checked={randomize} onChange={(e) => setRandomize(e.target.checked)} />
              Randomise slice times (±20% of the interval) so the pattern is harder to spot
            </label>

            {preview && (
              <div style={{ padding: '8px 10px', borderRadius: 4, background: C.surface, fontSize: F.sm, color: C.muted, lineHeight: 1.6 }}>
                <div>
                  <span style={{ color: C.text, fontWeight: 600 }}>{slices}</span> slices of ~
                  <span style={{ color: C.text, fontWeight: 600 }}>{fmtQty(preview.slice, 8)} {base}</span>, one every{' '}
                  <span style={{ color: C.text, fontWeight: 600 }}>{preview.interval.toFixed(1)} s</span>
                </div>
                {preview.mid && (
                  <div>≈ {fmtUsd(preview.slice * preview.mid)} per slice · {fmtUsd(preview.total)} total at mid {fmtPrice(preview.mid)}</div>
                )}
                {preview.warnings.map((w) => <div key={w} style={{ color: C.orange }}>⚠ {w}</div>)}
              </div>
            )}
            {formError && (
              <div style={{ padding: '6px 10px', borderRadius: 4, background: tint(C.red, 10), color: C.red, fontSize: F.sm }}>{formError}</div>
            )}
            <button
              onClick={submit}
              disabled={busy || !preview || killActive}
              title={killActive ? 'Kill switch is active' : undefined}
              style={{
                padding: '10px 0', borderRadius: 4, border: 'none', fontFamily: 'inherit', fontSize: F.base, fontWeight: 700,
                background: side === 'BUY' ? C.green : C.red, color: '#fff',
                cursor: busy ? 'wait' : 'pointer', opacity: busy || !preview || killActive ? 0.45 : 1,
              }}
            >
              {killActive ? 'Kill switch active' : busy ? 'Starting…' : `Start TWAP — ${side} ${qty || '…'} ${base}`}
            </button>
          </div>
        </Card>

        {/* List */}
        <Card>
          <CardTitle>ALGO ORDERS</CardTitle>
          {algos.length === 0 ? (
            <div style={{ padding: 30, textAlign: 'center', color: C.dim, fontSize: F.sm }}>No TWAPs yet — start one on the left.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 520, overflowY: 'auto' }}>
              {algos.map((a) => {
                const pct = a.params.quantity > 0 ? (a.filled_quantity / a.params.quantity) * 100 : 0
                const tgt = a.status === 'RUNNING' ? targetNow(a, now) : null
                const isSel = selected?.id === a.id
                return (
                  <div key={a.id} onClick={() => setSelectedId(a.id)} style={{
                    padding: '9px 11px', borderRadius: 4, cursor: 'pointer', background: C.surface,
                    border: `1px solid ${isSel ? C.blue : C.border}`,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: F.sm }}>
                      <span style={{ fontWeight: 700 }}>{a.id}</span>
                      <span style={{ color: a.params.side === 'BUY' ? C.green : C.red, fontWeight: 700 }}>{a.params.side}</span>
                      <span>{fmtQty(a.params.quantity)} {a.params.symbol}</span>
                      <span style={{ color: C.dim }}>· {a.params.slices} × {a.interval_s.toFixed(0)} s · by {a.created_by}</span>
                      <span style={{ marginLeft: 'auto' }}><StatusChip status={a.status} /></span>
                    </div>
                    <div style={{ position: 'relative', height: 6, background: C.border, borderRadius: 3, overflow: 'hidden', marginTop: 7 }}>
                      <div style={{ height: '100%', width: `${Math.min(100, pct)}%`, background: C.blue, borderRadius: 3 }} />
                      {tgt !== null && (
                        <div title="where the schedule says it should be now" style={{
                          position: 'absolute', top: 0, bottom: 0, width: 2, background: C.text, opacity: 0.6,
                          left: `${Math.min(100, (tgt / a.params.quantity) * 100)}%`,
                        }} />
                      )}
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.xs, color: C.muted, marginTop: 4 }}>
                      <span>{fmtQty(a.filled_quantity)} / {fmtQty(a.params.quantity)} filled ({pct.toFixed(0)}%)</span>
                      <span>{a.filled_quantity > 0 ? `avg ${fmtPrice(a.avg_fill_price)}` : ''}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Card>
      </div>

      {/* Detail */}
      {selected && (() => {
        const a = selected
        const b = baseAsset(a.params.symbol)
        const next = a.status === 'RUNNING' ? a.slices.find((s) => s.status === 'PENDING') : undefined
        const nextIn = next ? Math.max(0, Math.round((new Date(next.scheduled_at).getTime() - now) / 1000)) : null
        const done = a.slices.filter((s) => s.sent_at).length
        return (
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{ fontSize: F.lg, fontWeight: 700 }}>{a.id}</span>
              <StatusChip status={a.status} />
              <span style={{ fontSize: F.sm, color: C.muted }}>
                {a.params.side} {fmtQty(a.params.quantity)} {a.params.symbol} · {a.params.slices} slices of ~{fmtQty(a.nominal_slice)} every {a.interval_s.toFixed(1)} s
                {a.params.limit_price ? ` · limit ${fmtPrice(a.params.limit_price)}` : ' · market (price-protected)'}
                {a.params.jitter_pct ? ` · ±${a.params.jitter_pct}% timing` : ''} · started by {a.created_by}
              </span>
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                {a.status === 'RUNNING' && <CtlButton onClick={() => control(a.id, 'pause')} color={C.orange}>Pause</CtlButton>}
                {a.status === 'PAUSED' && <CtlButton onClick={() => control(a.id, 'resume')} color={C.blue} disabled={killActive}>Resume</CtlButton>}
                {(a.status === 'RUNNING' || a.status === 'PAUSED') && <CtlButton onClick={() => control(a.id, 'cancel')} color={C.red}>Cancel</CtlButton>}
              </span>
            </div>
            {a.status_reason && (
              <div style={{ fontSize: F.sm, color: a.status === 'PAUSED' || a.status === 'FAILED' ? C.orange : C.muted, marginBottom: 8 }}>
                {a.status_reason}
              </div>
            )}
            {actionError && <div style={{ fontSize: F.sm, color: C.red, marginBottom: 8 }}>{actionError}</div>}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, minmax(0, 1fr))', gap: 8, marginBottom: 12 }}>
              <div style={tile}>
                <div style={label}>FILLED</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>{fmtQty(a.filled_quantity)} <span style={{ fontSize: F.xs, color: C.muted }}>/ {fmtQty(a.params.quantity)} {b}</span></div>
              </div>
              <div style={tile}>
                <div style={label}>AVG FILL PRICE</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>{a.filled_quantity > 0 ? fmtPrice(a.avg_fill_price) : '—'}</div>
              </div>
              <div style={tile} title="Implementation shortfall: average fill vs the market mid when the TWAP started. + = cost.">
                <div style={label}>VS ARRIVAL PRICE</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}><Bps v={a.arrival_slippage_bps} /></div>
                <div style={{ fontSize: F.xs, color: C.dim }}>mid at start {fmtPrice(a.arrival_mid)}</div>
              </div>
              <div style={tile} title="Average fill vs the average market mid at the slice times — the TWAP benchmark. + = cost.">
                <div style={label}>VS INTERVAL TWAP</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}><Bps v={a.vs_interval_twap_bps} /></div>
                <div style={{ fontSize: F.xs, color: C.dim }}>benchmark {fmtPrice(a.interval_twap)}</div>
              </div>
              <div style={tile}>
                <div style={label}>FEES</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>{fmtUsd(a.fees_usdt, a.fees_usdt < 1 ? 4 : 2)}</div>
              </div>
              <div style={tile}>
                <div style={label}>{a.status === 'RUNNING' ? 'NEXT SLICE' : 'SLICES SENT'}</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>
                  {a.status === 'RUNNING' && nextIn !== null ? `in ${nextIn} s` : `${done} / ${a.params.slices}`}
                </div>
              </div>
            </div>

            <TwapChart algo={a} now={now} />

            {a.notes.length > 0 && (
              <div style={{ marginTop: 8, fontSize: F.xs, color: C.muted }}>
                {a.notes.map((n) => <div key={n}>ⓘ {n}</div>)}
              </div>
            )}

            <div style={{ overflowX: 'auto', marginTop: 12 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={th}>#</th>
                    <th style={th}>Due</th>
                    <th style={th}>Sent</th>
                    <th style={{ ...th, textAlign: 'right' }}>Target (cum.)</th>
                    <th style={{ ...th, textAlign: 'right' }}>Asked</th>
                    <th style={{ ...th, textAlign: 'right' }}>Filled</th>
                    <th style={{ ...th, textAlign: 'right' }}>Avg price</th>
                    <th style={{ ...th, textAlign: 'right' }}>Market mid</th>
                    <th style={th}>Venues</th>
                    <th style={th}>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {a.slices.map((s) => {
                    const color = s.status === 'FILLED' ? C.green : s.status === 'PARTIAL' ? C.orange
                      : s.status === 'UNFILLED' ? C.red : C.dim
                    const clickable = !!s.order_id
                    return (
                      <tr key={s.index} onClick={() => clickable && setProofOrderId(s.order_id)}
                          style={{ cursor: clickable ? 'pointer' : 'default' }}
                          title={clickable ? 'Open this slice\'s routing proof and audit trail' : undefined}>
                        <td style={td}>{s.index}</td>
                        <td style={{ ...td, color: C.muted }}>{new Date(s.scheduled_at).toLocaleTimeString(undefined, { hour12: false })}</td>
                        <td style={{ ...td, color: C.muted }}>{s.sent_at ? new Date(s.sent_at).toLocaleTimeString(undefined, { hour12: false }) : '—'}</td>
                        <td style={right}>{fmtQty(s.target_cumulative)}</td>
                        <td style={right}>{s.quantity ? fmtQty(s.quantity) : '—'}</td>
                        <td style={right}>{s.sent_at ? fmtQty(s.filled) : '—'}</td>
                        <td style={right}>{s.filled > 0 ? fmtPrice(s.avg_price) : '—'}</td>
                        <td style={{ ...right, color: C.muted }}>{fmtPrice(s.mid_at_send)}</td>
                        <td style={td}>{s.venues.join(', ') || '—'}</td>
                        <td style={{ ...td, whiteSpace: 'normal', minWidth: 160 }}>
                          <span style={{ color, fontWeight: 700, fontSize: F.xs }}>{s.status}</span>
                          {s.note && <span style={{ color: C.muted, fontSize: F.xs }}> — {s.note}</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )
      })()}

      <OrderProofModal order={proofOrder} onClose={() => setProofOrderId(null)} />
    </div>
  )
}

function CtlButton({ children, onClick, color, disabled }: {
  children: React.ReactNode; onClick: () => void; color: string; disabled?: boolean
}) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: '5px 14px', borderRadius: 4, border: `1px solid ${color}`, background: tint(color, 12),
      color, fontSize: F.sm, fontWeight: 600, cursor: disabled ? 'not-allowed' : 'pointer',
      fontFamily: 'inherit', opacity: disabled ? 0.5 : 1,
    }}>{children}</button>
  )
}
