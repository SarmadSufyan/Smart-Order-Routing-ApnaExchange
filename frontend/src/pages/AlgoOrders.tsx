import React, { useEffect, useMemo, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { TwapChart } from '../components/TwapChart'
import { OrderProofModal } from '../components/OrderProofModal'
import { useDataStore } from '../stores/dataStore'
import { SYMBOLS } from '../mockData'
import { baseAsset, fmtPrice, fmtQty, fmtUsd } from '../format'
import { ALGO_KINDS, ROUTING_STRATEGIES, algoKindLabel, api, routingStrategyLabel, PREFLIGHT_MAX_ORDERS } from '../services/api'
import type { AlgoKind, AlgoParams, AlgoRecommendation, AlgoStatus, PreflightResponse, RoutingStrategyName, TwapState, Urgency } from '../services/api'
import { MQ, useMediaQuery } from '../hooks/useMediaQuery'

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

/** "REJECTED_POSITION_LIMIT" -> "position limit". */
function checkLabel(result: string): string {
  return result === 'APPROVED' ? 'pass' : result.replace(/^REJECTED_/, '').replace(/_/g, ' ').toLowerCase()
}

function signedQty(q: number): string {
  return (q > 0 ? '+' : '') + fmtQty(q, 8)
}

/** The pre-flight answer: one verdict, the stress picture, then every slice. */
function PreflightResult({ result, error, base, side, onClose }: {
  result: PreflightResponse | null
  error: string | null
  base: string
  side: 'BUY' | 'SELL'
  onClose: () => void
}) {
  const th: React.CSSProperties = { padding: '5px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '5px 8px', fontSize: F.sm, color: C.text, borderBottom: `1px solid ${tint(C.border, 40)}`, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }
  const right: React.CSSProperties = { ...td, textAlign: 'right' }

  const rows = result?.results ?? []
  const firstBad = rows.find((r) => r.result !== 'APPROVED') ?? null
  const hasStress = rows.some((r) => r.stress)
  const last = rows[rows.length - 1] ?? null  // the position once every slice has filled
  const ms = result ? (result.elapsed_ms < 10 ? result.elapsed_ms.toFixed(2) : result.elapsed_ms.toFixed(0)) : ''

  return (
    <Card style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
        <CardTitle>PRE-FLIGHT — NOTHING WAS SENT</CardTitle>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {result && (
            <span style={{ fontSize: F.xs, color: C.dim }} title="Where the checks ran, and how long they took">
              {result.executor} · {ms} ms
            </span>
          )}
          <button onClick={onClose} aria-label="Close the pre-flight result" style={{
            background: 'none', border: `1px solid ${C.border}`, borderRadius: 4, color: C.muted,
            cursor: 'pointer', fontFamily: 'inherit', fontSize: F.sm, padding: '2px 8px',
          }}>✕</button>
        </div>
      </div>

      {error && (
        <div style={{ padding: '8px 10px', borderRadius: 4, background: tint(C.red, 10), color: C.red, fontSize: F.sm }}>
          Pre-flight failed: {error}
        </div>
      )}

      {result && (
        <>
          <div style={{
            padding: '10px 12px', borderRadius: 4, fontSize: F.base, fontWeight: 600, lineHeight: 1.5,
            background: tint(result.all_approved ? C.green : C.red, 10),
            border: `1px solid ${tint(result.all_approved ? C.green : C.red, 45)}`,
            color: result.all_approved ? C.green : C.red,
          }}>
            {result.all_approved
              ? `✓ All ${rows.length} slices pass pre-trade risk at today's limits.`
              : `✗ ${result.rejected} of ${rows.length} slices would be rejected — first: slice ${(firstBad?.index ?? 0) + 1} (${checkLabel(firstBad?.result ?? '')})${firstBad?.message ? `: ${firstBad.message}` : ''}. Start would be refused.`}
            {last && (
              <div style={{ fontSize: F.sm, fontWeight: 400, color: C.text, marginTop: 2 }}>
                Position after the last slice: {signedQty(last.position_after)} {base}
              </div>
            )}
          </div>

          {last?.stress && (
            <div style={{ marginTop: 10, padding: '10px 12px', borderRadius: 4, background: C.surface, fontSize: F.sm, color: C.muted, lineHeight: 1.6 }}>
              <span style={{ color: C.text, fontWeight: 600 }}>Stress test, if every slice fills ({side} → {signedQty(last.stress.position_after)} {base}): </span>
              on 99% of {last.stress.paths.toLocaleString()} simulated price paths the loss within {last.stress.horizon_min} min stays
              under <span style={{ color: C.text, fontWeight: 600 }}>{fmtUsd(last.stress.var_99_usdt)}</span> (VaR 99%); the worst 1% of
              paths lose <span style={{ color: C.text, fontWeight: 600 }}>{fmtUsd(last.stress.expected_shortfall_usdt)}</span> on
              average (expected shortfall). Volatility {last.stress.sigma_per_min_pct.toFixed(3)}% per minute, measured from our own
              live prices.
            </div>
          )}

          <div style={{ overflow: 'auto', maxHeight: 280, marginTop: 10 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead style={{ position: 'sticky', top: 0, background: C.surface2 }}>
                <tr>
                  <th style={th}>Slice</th>
                  <th style={{ ...th, textAlign: 'right' }}>Quantity</th>
                  <th style={{ ...th, textAlign: 'right' }}>Position after</th>
                  <th style={th}>Pre-trade risk</th>
                  {hasStress && <th style={{ ...th, textAlign: 'right' }}>VaR 99%</th>}
                  {hasStress && <th style={{ ...th, textAlign: 'right' }}>Exp. shortfall</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.index}>
                    <td style={{ ...td, color: C.muted }}>{r.index + 1}</td>
                    <td style={right}>{fmtQty(r.quantity, 8)} {base}</td>
                    <td style={right}>{signedQty(r.position_after)}</td>
                    <td style={{ ...td, whiteSpace: 'normal', minWidth: 140 }} title={r.message ?? undefined}>
                      {r.result === 'APPROVED'
                        ? <span style={{ color: C.green }}>✓ pass</span>
                        : <span style={{ color: C.red, fontWeight: 600 }}>✗ {checkLabel(r.result)}</span>}
                    </td>
                    {hasStress && <td style={right}>{r.stress ? fmtUsd(r.stress.var_99_usdt) : '—'}</td>}
                    {hasStress && <td style={right}>{r.stress ? fmtUsd(r.stress.expected_shortfall_usdt) : '—'}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.notes.length > 0 && (
            <div style={{ marginTop: 8, fontSize: F.xs, color: C.muted, lineHeight: 1.6 }}>
              {result.notes.map((n) => <div key={n}>ⓘ {n}</div>)}
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: F.xs, color: C.dim, lineHeight: 1.6 }}>
            Each slice is checked against the position it would meet if every earlier slice had filled. Slices here are an
            even split of the total; the TWAP itself rounds each to the exchange's lot size and runs this same check again
            when you press Start. Every pre-flight is recorded in the Audit Log.
          </div>
        </>
      )}
    </Card>
  )
}

// IS urgency -> how front-loaded (kappa*T), mirrors backend URGENCY_KAPPA_T.
const URGENCY_KAPPA_T: Record<Urgency, number> = { LOW: 0.5, MEDIUM: 2, HIGH: 5 }
const SCHEDULED: AlgoKind[] = ['TWAP', 'VWAP', 'IS']

/** Almgren–Chriss slice sizes (the same curve the backend plans). */
function isSliceSizes(q: number, n: number, k: number): number[] {
  const x = Array.from({ length: n + 1 }, (_, i) => q * Math.sinh(k * (1 - i / n)) / Math.sinh(k))
  return x.slice(1).map((v, i) => x[i] - v)
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export function AlgoOrders() {
  const algos      = useDataStore((s) => s.algos)
  const nbbos      = useDataStore((s) => s.nbbos)
  const orders     = useDataStore((s) => s.orders)
  const riskStatus = useDataStore((s) => s.riskStatus)
  const startAlgo  = useDataStore((s) => s.startTwap)
  const submitOrder = useDataStore((s) => s.submitOrder)
  const controlAlgo = useDataStore((s) => s.controlAlgo)
  const fetchAlgos = useDataStore((s) => s.fetchAlgos)

  const [kind, setKind]       = useState<AlgoKind>('TWAP')
  const [symbol, setSymbol]   = useState<string>(SYMBOLS[0])
  const [side, setSide]       = useState<'BUY' | 'SELL'>('BUY')
  const [qty, setQty]         = useState('')
  const [duration, setDuration] = useState('2')
  const [unit, setUnit]       = useState<'min' | 'sec'>('min')
  const [slices, setSlices]   = useState('10')
  const [limit, setLimit]     = useState('')
  const [randomize, setRandomize] = useState(false)
  const [routing, setRouting] = useState<RoutingStrategyName>('best_price')
  const [volumeDays, setVolumeDays] = useState('5')
  const [urgency, setUrgency] = useState<Urgency>('MEDIUM')
  const [participation, setParticipation] = useState('10')
  const [checkEvery, setCheckEvery] = useState('10')
  const [display, setDisplay] = useState('')
  const [refresh, setRefresh] = useState('5')
  const [variance, setVariance] = useState('20')
  const [busy, setBusy]       = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [proofOrderId, setProofOrderId] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
  const isNarrow = useMediaQuery(MQ.narrow)  // the form goes above the list
  // Pre-flight: a dry run of the planned children through pre-trade risk (nothing is sent).
  const [withStress, setWithStress] = useState(false)
  const [checking, setChecking]     = useState(false)
  const [preflight, setPreflight]   = useState<PreflightResponse | null>(null)
  const [preflightError, setPreflightError] = useState<string | null>(null)
  // Auto: the engine's recommendation for this order (nothing is sent).
  const [rec, setRec] = useState<AlgoRecommendation | null>(null)
  const [recBusy, setRecBusy] = useState(false)
  const [recError, setRecError] = useState<string | null>(null)
  const [singleResult, setSingleResult] = useState<string | null>(null)

  // A result only describes the order it was run for.
  useEffect(() => { setPreflight(null); setPreflightError(null) }, [kind, symbol, side, qty, slices, urgency, display])
  useEffect(() => { setRec(null); setRecError(null); setSingleResult(null) }, [symbol, side, qty])

  useEffect(() => { fetchAlgos() }, [fetchAlgos])
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const selected = algos.find((a) => a.id === selectedId) ?? algos[0] ?? null
  const killActive = riskStatus?.kill_switch_active ?? false
  const base = baseAsset(symbol)
  const scheduled = SCHEDULED.includes(kind)

  // Live preview of what the backend will do.
  const preview = useMemo(() => {
    const n = nbbos[symbol]
    const mid = n && n.best_bid && n.best_ask ? (n.best_bid + n.best_ask) / 2 : null
    const q = parseFloat(qty)
    const k = parseInt(slices, 10)
    const secs = parseFloat(duration) * (unit === 'min' ? 60 : 1)
    if (!(q > 0) || !(secs > 0)) return null
    if (scheduled && !(k >= 2)) return null
    if (kind === 'POV' && !(parseFloat(participation) > 0)) return null
    if (kind === 'ICEBERG' && !(parseFloat(display) > 0 && parseFloat(limit) > 0)) return null
    const warnings: string[] = []
    const slice = scheduled ? q / k : kind === 'ICEBERG' ? parseFloat(display) : null
    const interval = scheduled ? secs / k : kind === 'POV' ? parseFloat(checkEvery) : parseFloat(refresh)
    if (interval < MIN_INTERVAL_S) warnings.push(`One step every ${interval.toFixed(1)} s — the minimum is ${MIN_INTERVAL_S} s.`)
    if (mid && slice !== null && slice * mid < TYPICAL_MIN_ORDER_USD) warnings.push(`Each ${kind === 'ICEBERG' ? 'piece' : 'slice'} is only ~${fmtUsd(slice * mid)}; most venues need at least ${fmtUsd(TYPICAL_MIN_ORDER_USD)} per order.`)
    const algoLimit = riskStatus?.limits?.max_algo_notional_usdt
    if (mid && algoLimit && q * mid > algoLimit) warnings.push(`Total ~${fmtUsd(q * mid)} is above the algo limit ${fmtUsd(algoLimit)}.`)
    if (kind === 'ICEBERG' && parseFloat(display) > q) warnings.push('The display size is larger than the whole quantity.')
    return { mid, slice, interval, secs, total: mid ? q * mid : null, warnings }
  }, [nbbos, symbol, qty, slices, duration, unit, riskStatus, kind, scheduled, participation, checkEvery, display, refresh, limit])

  function params(): AlgoParams {
    const p: AlgoParams = {
      kind, symbol, side, quantity: parseFloat(qty), duration_s: preview!.secs, routing,
      limit_price: limit ? parseFloat(limit) : null,
    }
    if (scheduled) p.slices = parseInt(slices, 10)
    if (kind === 'TWAP' || kind === 'VWAP') p.jitter_pct = randomize ? 20 : 0
    if (kind === 'VWAP') p.volume_days = parseInt(volumeDays, 10) || 5
    if (kind === 'IS') p.urgency = urgency
    if (kind === 'POV') { p.participation_pct = parseFloat(participation); p.check_interval_s = parseFloat(checkEvery) }
    if (kind === 'ICEBERG') {
      p.display_qty = parseFloat(display); p.refresh_s = parseFloat(refresh); p.size_variance_pct = parseFloat(variance) || 0
    }
    return p
  }

  async function submit() {
    if (!preview) return
    setBusy(true)
    setFormError(null)
    const [algo, err] = await startAlgo(params())
    setBusy(false)
    if (err) setFormError(err)
    if (algo) setSelectedId(algo.id)
  }

  // The children the algo plans (approximate for VWAP/POV: the exact plan is checked again at Start).
  function sliceSizes(): number[] {
    const q = parseFloat(qty)
    const k = parseInt(slices, 10)
    if (kind === 'IS') return isSliceSizes(q, k, URGENCY_KAPPA_T[urgency])
    if (kind === 'POV') return Array.from({ length: 10 }, () => q / 10)
    if (kind === 'ICEBERG') {
      const d = parseFloat(display)
      const n = Math.max(1, Math.ceil(q / d))
      return Array.from({ length: n }, (_, i) => (i === n - 1 ? q - d * (n - 1) : d))
    }
    const targets = Array.from({ length: k }, (_, i) => (i === k - 1 ? q : (q * (i + 1)) / k))
    return targets.map((t, i) => t - (i === 0 ? 0 : targets[i - 1]))
  }

  async function runPreflight() {
    if (!preview) return
    setChecking(true)
    setPreflightError(null)
    try {
      setPreflight(await api.preflight(sliceSizes().map((quantity) => ({ symbol, side, quantity })), withStress))
    } catch (e) {
      setPreflight(null)
      setPreflightError((e as Error).message)
    }
    setChecking(false)
  }

  async function recommend() {
    const q = parseFloat(qty)
    if (!(q > 0)) { setRecError('Enter a quantity first'); return }
    setRecBusy(true)
    setRecError(null)
    setSingleResult(null)
    try {
      const secs = parseFloat(duration) * (unit === 'min' ? 60 : 1)
      setRec(await api.recommendAlgo({ symbol, side, quantity: q, urgency, routing,
                                        max_duration_s: Math.min(86_400, Math.max(60, secs || 3600)) }))
    } catch (e) {
      setRec(null)
      setRecError((e as Error).message)
    }
    setRecBusy(false)
  }

  function applyRecommendation(r: AlgoRecommendation) {
    const p = r.params
    if (!p || !p.kind) return
    setKind(p.kind)
    const secs = p.duration_s
    if (secs >= 120 && secs % 60 === 0) { setDuration(String(secs / 60)); setUnit('min') }
    else { setDuration(String(Math.round(secs))); setUnit('sec') }
    if (p.slices) setSlices(String(p.slices))
    if (p.participation_pct) setParticipation(String(p.participation_pct))
    if (p.urgency) setUrgency(p.urgency)
    setRec(null)
  }

  async function placeSingle() {
    const order = await submitOrder(symbol, side, parseFloat(qty), limit ? 'LIMIT' : 'MARKET',
                                    limit ? parseFloat(limit) : undefined, routing)
    setSingleResult(order ? `Order ${order.status}: ${fmtQty(order.filled_quantity)} ${base} filled` +
                            (order.avg_fill_price ? ` @ ${fmtPrice(order.avg_fill_price)}` : '') : 'Order failed — see the order list')
  }

  async function control(id: string, action: 'pause' | 'resume' | 'cancel') {
    if (action === 'cancel' && !window.confirm(`Cancel ${id}? Orders already filled stay filled; the rest are not sent.`)) return
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
  const two: React.CSSProperties = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }

  return (
    <div>
      <div style={{ fontSize: F.lg, color: C.text, marginBottom: 6 }}>Algo Orders — execution algorithms</div>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 16, maxWidth: 980 }}>
        An execution algorithm works a large order over time to reduce its market impact: TWAP (even slices), VWAP
        (the market's usual volume), implementation shortfall (front-loaded), POV (a share of live volume) or iceberg
        (small pieces at a limit) — or let <b>Auto</b> pick one and explain why. Every child is an ordinary order through
        the same pre-trade risk checks, balance check and smart router as the order button; the algo has no other way
        to trade. The kill switch pauses every running algo, and a restart never resumes one on its own.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? 'minmax(0, 1fr)' : 'minmax(0, 360px) minmax(0, 1fr)', gap: 14, marginBottom: 14 }}>
        {/* New algo */}
        <Card>
          <CardTitle>NEW ALGO ORDER</CardTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div>
              <div style={label}>ALGORITHM</div>
              <select value={kind} onChange={(e) => setKind(e.target.value as AlgoKind)} style={input}>
                {ALGO_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
              </select>
              <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4, lineHeight: 1.5 }}>
                {ALGO_KINDS.find((k) => k.id === kind)?.hint}
              </div>
            </div>
            <div style={two}>
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
            <div style={two}>
              <div>
                <div style={label}>{scheduled ? 'OVER' : 'AT MOST FOR'}</div>
                <div style={{ display: 'flex', gap: 4 }}>
                  <input value={duration} onChange={(e) => setDuration(e.target.value)} style={{ ...input, width: '55%' }} />
                  <select value={unit} onChange={(e) => setUnit(e.target.value as 'min' | 'sec')} style={{ ...input, width: '45%' }}>
                    <option value="min">min</option>
                    <option value="sec">sec</option>
                  </select>
                </div>
              </div>
              {scheduled && (
                <div>
                  <div style={label}>SLICES</div>
                  <input value={slices} onChange={(e) => setSlices(e.target.value)} style={input} />
                </div>
              )}
              {kind === 'POV' && (
                <div>
                  <div style={label}>% OF MARKET VOLUME</div>
                  <input value={participation} onChange={(e) => setParticipation(e.target.value)} style={input} />
                </div>
              )}
              {kind === 'ICEBERG' && (
                <div>
                  <div style={label}>SHOW AT MOST ({base})</div>
                  <input value={display} onChange={(e) => setDisplay(e.target.value)} placeholder="display size" style={input} />
                </div>
              )}
            </div>
            {kind === 'VWAP' && (
              <div>
                <div style={label}>VOLUME HISTORY (DAYS OF THE SAME WINDOW)</div>
                <input value={volumeDays} onChange={(e) => setVolumeDays(e.target.value)} style={input} />
              </div>
            )}
            {(kind === 'IS') && (
              <div>
                <div style={label}>URGENCY</div>
                <select value={urgency} onChange={(e) => setUrgency(e.target.value as Urgency)} style={input}>
                  <option value="LOW">Low — close to even, more price risk</option>
                  <option value="MEDIUM">Medium</option>
                  <option value="HIGH">High — front-loaded, more impact</option>
                </select>
              </div>
            )}
            {kind === 'POV' && (
              <div>
                <div style={label}>CHECK THE VOLUME EVERY (S)</div>
                <input value={checkEvery} onChange={(e) => setCheckEvery(e.target.value)} style={input} />
              </div>
            )}
            {kind === 'ICEBERG' && (
              <div style={two}>
                <div>
                  <div style={label}>LOOK EVERY (S)</div>
                  <input value={refresh} onChange={(e) => setRefresh(e.target.value)} style={input} />
                </div>
                <div>
                  <div style={label}>SIZE VARIES ± %</div>
                  <input value={variance} onChange={(e) => setVariance(e.target.value)} style={input} />
                </div>
              </div>
            )}
            <div>
              <div style={label}>{kind === 'ICEBERG' ? 'LIMIT PRICE (REQUIRED — ONLY AT THIS PRICE OR BETTER)' : 'LIMIT PRICE (OPTIONAL — WORST PRICE FOR ANY CHILD)'}</div>
              <input value={limit} onChange={(e) => setLimit(e.target.value)} placeholder={kind === 'ICEBERG' ? 'e.g. a bit below the ask' : 'none = market, price-protected'} style={input} />
            </div>
            <div>
              <div style={label}>ROUTING OF EACH CHILD</div>
              <select value={routing} onChange={(e) => setRouting(e.target.value as RoutingStrategyName)} style={input}>
                {ROUTING_STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </div>
            {(kind === 'TWAP' || kind === 'VWAP') && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: F.sm, color: C.muted, cursor: 'pointer' }}>
                <input type="checkbox" checked={randomize} onChange={(e) => setRandomize(e.target.checked)} />
                Randomise slice times (±20% of the interval) so the pattern is harder to spot
              </label>
            )}

            {preview && (
              <div style={{ padding: '8px 10px', borderRadius: 4, background: C.surface, fontSize: F.sm, color: C.muted, lineHeight: 1.6 }}>
                {scheduled && (
                  <div>
                    <span style={{ color: C.text, fontWeight: 600 }}>{slices}</span> slices{kind === 'TWAP' ? ' of ~' : ', ~'}
                    <span style={{ color: C.text, fontWeight: 600 }}>{fmtQty(preview.slice ?? 0, 8)} {base}</span>
                    {kind === 'TWAP' ? '' : ' on average'}, one every{' '}
                    <span style={{ color: C.text, fontWeight: 600 }}>{preview.interval.toFixed(1)} s</span>
                    {kind === 'VWAP' && ' — sized by the market\'s volume profile'}
                    {kind === 'IS' && ` — front-loaded (${urgency.toLowerCase()} urgency)`}
                  </div>
                )}
                {kind === 'POV' && (
                  <div>Trades <b style={{ color: C.text }}>{participation}%</b> of the real market volume, checking every {checkEvery} s, for at most {(preview.secs / 60).toFixed(1)} min</div>
                )}
                {kind === 'ICEBERG' && (
                  <div>Shows ~<b style={{ color: C.text }}>{display} {base}</b> (±{variance}%) at a time, only at {limit ? fmtPrice(parseFloat(limit)) : '—'} or better, looking every {refresh} s, for at most {(preview.secs / 60).toFixed(1)} min</div>
                )}
                {preview.mid && (
                  <div>≈ {preview.slice !== null ? `${fmtUsd(preview.slice * preview.mid)} per ${kind === 'ICEBERG' ? 'piece' : 'slice'} · ` : ''}{fmtUsd(preview.total)} total at mid {fmtPrice(preview.mid)}</div>
                )}
                {preview.warnings.map((w) => <div key={w} style={{ color: C.orange }}>⚠ {w}</div>)}
              </div>
            )}

            {/* Auto */}
            <div style={{ padding: '8px 10px', borderRadius: 4, border: `1px dashed ${C.border}` }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <button onClick={recommend} disabled={recBusy} style={{
                  padding: '6px 12px', borderRadius: 4, border: `1px solid ${C.purple}`, background: tint(C.purple, 12),
                  color: C.purple, fontFamily: 'inherit', fontSize: F.sm, fontWeight: 700, cursor: recBusy ? 'wait' : 'pointer',
                }}>{recBusy ? 'Thinking…' : '✦ Auto — recommend'}</button>
                <select value={urgency} onChange={(e) => setUrgency(e.target.value as Urgency)} style={{ ...input, width: 'auto', padding: '5px 7px', fontSize: F.sm }}>
                  <option value="LOW">low urgency</option>
                  <option value="MEDIUM">medium urgency</option>
                  <option value="HIGH">high urgency</option>
                </select>
                <span style={{ fontSize: F.xs, color: C.dim }}>within the time above · sends nothing</span>
              </div>
              {recError && <div style={{ fontSize: F.sm, color: C.red, marginTop: 6 }}>{recError}</div>}
              {rec && (
                <div style={{ marginTop: 8, fontSize: F.sm, color: C.muted, lineHeight: 1.55 }}>
                  <div style={{ color: C.text, fontWeight: 700 }}>
                    {rec.choice === 'SINGLE' ? 'One order now' : rec.choice === 'NONE' ? 'Can\'t be done as asked' : algoKindLabel(rec.choice)}
                    {rec.params && ` · ${rec.params.slices ? `${rec.params.slices} slices over ` : ''}${(rec.params.duration_s / 60).toFixed(1)} min`}
                    {rec.params?.participation_pct ? ` at ${rec.params.participation_pct}% of volume` : ''}
                  </div>
                  {rec.reasons.map((r) => <div key={r}>✓ {r}</div>)}
                  {rec.warnings.map((w) => <div key={w} style={{ color: C.orange }}>⚠ {w}</div>)}
                  {rec.alternatives.length > 0 && (
                    <div style={{ color: C.dim, fontSize: F.xs, marginTop: 2 }}>Others: {rec.alternatives.join(' · ')}</div>
                  )}
                  <div style={{ color: C.dim, fontSize: F.xs, marginTop: 2 }}>
                    spread {num(rec.metrics.spread_bps)?.toFixed(2) ?? '—'} bps · all at once {num(rec.metrics.impact_now_bps)?.toFixed(1) ?? 'not fillable'} {num(rec.metrics.impact_now_bps) !== null ? 'bps' : ''}
                    {num(rec.metrics.volume_per_min) !== null && ` · market ${fmtQty(rec.metrics.volume_per_min as number, 4)} ${base}/min (${(rec.metrics.volume_venues ?? []).join(', ')})`}
                  </div>
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    {rec.params && <CtlButton onClick={() => applyRecommendation(rec)} color={C.purple}>Use this</CtlButton>}
                    {rec.choice === 'SINGLE' && !killActive && <CtlButton onClick={placeSingle} color={side === 'BUY' ? C.green : C.red}>Place as one order</CtlButton>}
                  </div>
                  {singleResult && <div style={{ marginTop: 6, color: C.text }}>{singleResult}</div>}
                </div>
              )}
            </div>

            {formError && (
              <div style={{ padding: '6px 10px', borderRadius: 4, background: tint(C.red, 10), color: C.red, fontSize: F.sm }}>{formError}</div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <button
                onClick={runPreflight}
                disabled={!preview || checking || sliceSizes().length > PREFLIGHT_MAX_ORDERS}
                title="Runs the planned children through the pre-trade risk checks without sending anything"
                style={{
                  padding: '8px 0', borderRadius: 4, fontFamily: 'inherit', fontSize: F.sm, fontWeight: 600,
                  border: `1px solid ${C.blue}`, background: tint(C.blue, 10), color: C.blue,
                  cursor: checking ? 'wait' : 'pointer',
                  opacity: !preview || checking ? 0.45 : 1,
                }}
              >
                {checking ? 'Checking every child…' : 'Pre-flight check — sends nothing'}
              </button>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: F.xs, color: C.muted, cursor: 'pointer' }}>
                <input type="checkbox" checked={withStress} onChange={(e) => setWithStress(e.target.checked)} />
                With stress test (Monte Carlo price paths, on worker processes when worth it)
              </label>
              {(kind === 'VWAP' || kind === 'POV') && (
                <div style={{ fontSize: F.xs, color: C.dim }}>
                  {kind === 'VWAP' ? 'VWAP slice sizes come from the volume profile at Start; the pre-flight uses even slices.'
                    : 'POV sizes follow live volume; the pre-flight checks ten equal pieces.'} Start checks the real plan again.
                </div>
              )}
            </div>
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
              {killActive ? 'Kill switch active' : busy ? 'Starting…' : `Start ${algoKindLabel(kind)} — ${side} ${qty || '…'} ${base}`}
            </button>
          </div>
        </Card>

        {/* List */}
        <Card>
          <CardTitle>ALGO ORDERS</CardTitle>
          {algos.length === 0 ? (
            <div style={{ padding: 30, textAlign: 'center', color: C.dim, fontSize: F.sm }}>No algo orders yet — start one {isNarrow ? 'above' : 'on the left'}.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 620, overflowY: 'auto' }}>
              {algos.map((a) => {
                const pct = a.params.quantity > 0 ? (a.filled_quantity / a.params.quantity) * 100 : 0
                const tgt = a.status === 'RUNNING' && SCHEDULED.includes((a.params.kind ?? 'TWAP') as AlgoKind) ? targetNow(a, now) : null
                const isSel = selected?.id === a.id
                const k = (a.params.kind ?? 'TWAP') as AlgoKind
                return (
                  <div key={a.id} onClick={() => setSelectedId(a.id)} style={{
                    padding: '9px 11px', borderRadius: 4, cursor: 'pointer', background: C.surface,
                    border: `1px solid ${isSel ? C.blue : C.border}`,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, fontSize: F.sm }}>
                      <span style={{ fontWeight: 700 }}>{a.id}</span>
                      <span style={{ color: a.params.side === 'BUY' ? C.green : C.red, fontWeight: 700 }}>{a.params.side}</span>
                      <span>{fmtQty(a.params.quantity)} {a.params.symbol}</span>
                      <span style={{ color: C.dim }}>
                        · {SCHEDULED.includes(k) ? `${a.params.slices} × ${a.interval_s.toFixed(0)} s`
                          : k === 'POV' ? `${a.params.participation_pct}% of volume` : `≤ ${fmtQty(a.nominal_slice)} at ${fmtPrice(a.params.limit_price)}`}
                        {' '}· by {a.created_by}
                      </span>
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

      {(preflight || preflightError) && (
        <PreflightResult
          result={preflight}
          error={preflightError}
          base={base}
          side={side}
          onClose={() => { setPreflight(null); setPreflightError(null) }}
        />
      )}

      {/* Detail */}
      {selected && (() => {
        const a = selected
        const b = baseAsset(a.params.symbol)
        const k = (a.params.kind ?? 'TWAP') as AlgoKind
        const isSched = SCHEDULED.includes(k)
        const next = a.status === 'RUNNING' && isSched ? a.slices.find((s) => s.status === 'PENDING') : undefined
        const nextIn = next ? Math.max(0, Math.round((new Date(next.scheduled_at).getTime() - now) / 1000)) : null
        const deadlineIn = a.deadline_at && a.status === 'RUNNING' ? Math.max(0, Math.round((new Date(a.deadline_at).getTime() - now) / 1000)) : null
        const done = a.slices.filter((s) => s.sent_at).length
        const plan = a.plan ?? {}
        const profile = Array.isArray(plan.profile) ? (plan.profile as number[]) : null
        return (
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
              <span style={{ fontSize: F.lg, fontWeight: 700 }}>{a.id}</span>
              <span style={{ fontSize: F.xs, padding: '1px 7px', borderRadius: 3, background: tint(C.purple, 14), color: C.purple, fontWeight: 700 }}>{algoKindLabel(k)}</span>
              <StatusChip status={a.status} />
              <span style={{ fontSize: F.sm, color: C.muted }}>
                {a.params.side} {fmtQty(a.params.quantity)} {a.params.symbol}
                {isSched && ` · ${a.params.slices} slices, ~${fmtQty(a.nominal_slice)} each, every ${a.interval_s.toFixed(1)} s`}
                {k === 'IS' && a.params.urgency && ` · ${a.params.urgency.toLowerCase()} urgency`}
                {k === 'POV' && ` · ${a.params.participation_pct}% of market volume, checked every ${a.params.check_interval_s ?? 10} s`}
                {k === 'ICEBERG' && ` · shows ~${fmtQty(a.nominal_slice)} at a time`}
                {a.params.limit_price ? ` · limit ${fmtPrice(a.params.limit_price)}` : ' · market (price-protected)'}
                {a.params.routing && a.params.routing !== 'best_price' ? ` · children: ${routingStrategyLabel(a.params.routing).toLowerCase()}` : ''}
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
            {a.status === 'RUNNING' && a.waiting_reason && (
              <div style={{ fontSize: F.sm, color: C.blue, marginBottom: 8 }}>⏳ {a.waiting_reason}</div>
            )}
            {actionError && <div style={{ fontSize: F.sm, color: C.red, marginBottom: 8 }}>{actionError}</div>}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 130px), 1fr))', gap: 8, marginBottom: 12 }}>
              <div style={tile}>
                <div style={label}>FILLED</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>{fmtQty(a.filled_quantity)} <span style={{ fontSize: F.xs, color: C.muted }}>/ {fmtQty(a.params.quantity)} {b}</span></div>
              </div>
              <div style={tile}>
                <div style={label}>AVG FILL PRICE</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>{a.filled_quantity > 0 ? fmtPrice(a.avg_fill_price) : '—'}</div>
              </div>
              <div style={tile} title="Implementation shortfall: average fill vs the market mid when the algo started. + = cost.">
                <div style={label}>VS ARRIVAL PRICE</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}><Bps v={a.arrival_slippage_bps} /></div>
                <div style={{ fontSize: F.xs, color: C.dim }}>mid at start {fmtPrice(a.arrival_mid)}</div>
              </div>
              {a.market_vwap ? (
                <div style={tile} title="Average fill vs the real market's VWAP over the same time (all exchanges we watch). + = cost.">
                  <div style={label}>VS MARKET VWAP</div>
                  <div style={{ fontSize: F.lg, fontWeight: 700 }}><Bps v={a.vs_market_vwap_bps ?? null} /></div>
                  <div style={{ fontSize: F.xs, color: C.dim }}>market {fmtPrice(a.market_vwap)}</div>
                </div>
              ) : (
                <div style={tile} title="Average fill vs the average market mid at the slice times — the TWAP benchmark. + = cost.">
                  <div style={label}>VS INTERVAL TWAP</div>
                  <div style={{ fontSize: F.lg, fontWeight: 700 }}><Bps v={a.vs_interval_twap_bps} /></div>
                  <div style={{ fontSize: F.xs, color: C.dim }}>benchmark {fmtPrice(a.interval_twap)}</div>
                </div>
              )}
              {k === 'POV' && (
                <div style={tile} title="Our fills as a share of the real market volume since the start">
                  <div style={label}>PARTICIPATION</div>
                  <div style={{ fontSize: F.lg, fontWeight: 700 }}>{a.participation_actual_pct != null ? `${a.participation_actual_pct < 0.1 ? a.participation_actual_pct.toPrecision(2) : a.participation_actual_pct.toFixed(2)}%` : '—'}</div>
                  <div style={{ fontSize: F.xs, color: C.dim }}>target {a.params.participation_pct}% · market {a.market_volume != null ? fmtQty(a.market_volume, 4) : '—'} {b}</div>
                </div>
              )}
              {k === 'IS' && num(plan.expected_cost_usdt) !== null && (
                <div style={tile} title="Almgren–Chriss: expected impact cost ± 1 σ price risk, measured on the live book and volatility">
                  <div style={label}>EXPECTED COST ± RISK</div>
                  <div style={{ fontSize: F.lg, fontWeight: 700 }}>{fmtUsd(num(plan.expected_cost_usdt) ?? 0, 4)}{num(plan.risk_usdt) !== null && <span style={{ fontSize: F.sm, color: C.muted }}> ±{fmtUsd(num(plan.risk_usdt) ?? 0, 4)}</span>}</div>
                  <div style={{ fontSize: F.xs, color: C.dim }}>even TWAP {fmtUsd(num(plan.twap_cost_usdt) ?? 0, 4)}{num(plan.twap_risk_usdt) !== null ? ` ±${fmtUsd(num(plan.twap_risk_usdt) ?? 0, 4)}` : ''}</div>
                </div>
              )}
              <div style={tile}>
                <div style={label}>FEES</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>{fmtUsd(a.fees_usdt, a.fees_usdt < 1 ? 4 : 2)}</div>
              </div>
              <div style={tile}>
                <div style={label}>{a.status === 'RUNNING' ? (isSched ? 'NEXT SLICE' : 'ENDS IN') : 'CHILDREN SENT'}</div>
                <div style={{ fontSize: F.lg, fontWeight: 700 }}>
                  {a.status === 'RUNNING' && nextIn !== null ? `in ${nextIn} s`
                    : a.status === 'RUNNING' && deadlineIn !== null ? `${Math.floor(deadlineIn / 60)}:${String(deadlineIn % 60).padStart(2, '0')}`
                    : isSched ? `${done} / ${a.params.slices}` : `${done}`}
                </div>
              </div>
            </div>

            {profile && (
              <div style={{ marginBottom: 10 }}>
                <div style={label}>VOLUME PROFILE — SHARE OF THE WINDOW'S VOLUME PER SLICE {plan.profile_fallback ? '(NO HISTORY: EVEN)' : `(LAST ${String(plan.profile_days ?? '?')} DAYS)`}</div>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 46 }}>
                  {profile.map((w, i) => (
                    <div key={i} title={`slice ${i + 1}: ${(w * 100).toFixed(1)}%`} style={{
                      flex: 1, minWidth: 2, height: `${Math.max(4, (w / Math.max(...profile)) * 100)}%`,
                      background: tint(C.purple, 60), borderRadius: 2,
                    }} />
                  ))}
                </div>
              </div>
            )}

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
                          title={clickable ? 'Open this child\'s routing proof and audit trail' : undefined}>
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
