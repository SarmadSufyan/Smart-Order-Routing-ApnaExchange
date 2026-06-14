import React, { useEffect, useRef, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { api, type NBBO, type Order, type ExecutionReport } from '../services/api'
import { SYMBOLS } from '../mockData'
import type { OrderSide } from '../types'

type StepStatus = 'pending' | 'active' | 'done' | 'failed' | 'skipped'

interface StepState {
  key: string
  status: StepStatus
  startedAt?: number
  durationMs?: number
  data?: any
  note?: string
}

const STEP_DEFS = [
  {
    key: 'input',
    title: 'Order Received',
    short: 'Operator submits a new order',
    what: 'The order is created in the Order Manager with status NEW. Every order gets a UUID, timestamp, and audit trail.',
  },
  {
    key: 'risk',
    title: 'Pre-Trade Risk Check',
    short: 'Four checks must pass',
    what: 'Before the order goes anywhere, the Risk Engine validates: kill switch is off, order size is under the limit, position limit per symbol is not exceeded, and notional exposure is within the cap.',
  },
  {
    key: 'md',
    title: 'Market Data Aggregation',
    short: 'Pulls NBBO from all 5 venues',
    what: 'The Market Data Aggregator queries every connected venue and computes the National Best Bid & Offer — the best bid across venues and the best ask across venues. This is the "best price anywhere".',
  },
  {
    key: 'sor',
    title: 'SOR Strategy Decision',
    short: 'Best-Price algorithm picks a venue',
    what: 'The Smart Order Router runs the Best-Price strategy. It looks at the NBBO and filters out blacklisted/degraded venues, then picks the venue with the best price for our side.',
  },
  {
    key: 'route',
    title: 'Venue Routing',
    short: 'Order sent to chosen venue',
    what: 'The Order Manager transitions the order to ROUTING then WORKING. A child order is sent via HTTP to the selected venue\'s /execute-order endpoint.',
  },
  {
    key: 'exec',
    title: 'Execution Report',
    short: 'Venue responds with FILL or REJECT',
    what: 'The venue simulates matching against its order book. It either fills the order (returns FILL with fill price and quantity) or rejects it. Latency is tracked.',
  },
  {
    key: 'position',
    title: 'Position Update',
    short: 'Risk engine updates exposure',
    what: 'On a fill, the Position Tracker updates the net position, the avg entry price, and the total notional exposure. The audit log gets the execution report.',
  },
]

const SPEEDS = [
  { label: 'Very Slow', ms: 4000 },
  { label: 'Slow',      ms: 2500 },
  { label: 'Normal',    ms: 1200 },
  { label: 'Fast',      ms: 500  },
]

export function HowItWorks() {
  const fetchAll = useDataStore((s) => s.fetchAll)

  const [symbol, setSymbol] = useState<string>('AAPL')
  const [side, setSide]     = useState<OrderSide>('BUY')
  const [qty, setQty]       = useState<number>(50)
  const [speedIdx, setSpeedIdx] = useState<number>(1)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentIdx, setCurrentIdx] = useState<number>(-1)
  const [steps, setSteps] = useState<StepState[]>(STEP_DEFS.map((s) => ({ key: s.key, status: 'pending' as StepStatus })))
  const [summary, setSummary] = useState<{ kind: 'ok' | 'fail'; msg: string } | null>(null)
  const abortRef = useRef<boolean>(false)

  const speed = SPEEDS[speedIdx].ms

  function reset() {
    abortRef.current = true
    setSteps(STEP_DEFS.map((s) => ({ key: s.key, status: 'pending' as StepStatus })))
    setCurrentIdx(-1)
    setIsPlaying(false)
    setSummary(null)
  }

  function sleep(ms: number) {
    return new Promise<void>((resolve) => setTimeout(resolve, ms))
  }

  async function play() {
    if (isPlaying) return
    abortRef.current = false
    setSummary(null)
    setSteps(STEP_DEFS.map((s) => ({ key: s.key, status: 'pending' as StepStatus })))
    setCurrentIdx(-1)
    setIsPlaying(true)

    const trace: StepState[] = STEP_DEFS.map((s) => ({ key: s.key, status: 'pending' as StepStatus }))

    const updateStep = (idx: number, patch: Partial<StepState>) => {
      trace[idx] = { ...trace[idx], ...patch }
      setSteps([...trace])
    }

    try {
      // Step 1: input
      setCurrentIdx(0)
      updateStep(0, { status: 'active', startedAt: Date.now(), data: { symbol, side, quantity: qty } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(0, { status: 'done', durationMs: speed })

      // Step 2: risk
      setCurrentIdx(1)
      const risk = await api.getRiskStatus()
      const checks = [
        { name: 'Kill Switch',    passed: !risk.kill_switch_active, detail: risk.kill_switch_active ? `Reason: ${risk.kill_switch_reason}` : 'Inactive' },
        { name: 'Order Size',     passed: qty <= 10000, detail: `qty ${qty} ≤ 10,000 limit` },
        { name: 'Position Limit', passed: true, detail: 'Net position within per-symbol cap' },
        { name: 'Notional Limit', passed: true, detail: `Exposure ${risk.exposure_utilization_pct.toFixed(1)}% of cap` },
      ]
      const riskOk = checks.every((c) => c.passed)
      updateStep(1, { status: 'active', startedAt: Date.now(), data: { checks } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(1, { status: riskOk ? 'done' : 'failed', durationMs: speed })

      if (!riskOk) {
        for (let i = 2; i < STEP_DEFS.length; i++) updateStep(i, { status: 'skipped' })
        setSummary({ kind: 'fail', msg: 'Order blocked by pre-trade risk check.' })
        setIsPlaying(false)
        return
      }

      // Step 3: market data
      setCurrentIdx(2)
      const quotesRes = await api.getQuotes(symbol)
      const allNbboRes = await api.getAllNBBO()
      const nbbo: NBBO | undefined = allNbboRes.nbbo[symbol]
      updateStep(2, { status: 'active', startedAt: Date.now(), data: { quotes: quotesRes.quotes, nbbo } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(2, { status: 'done', durationMs: speed })

      // Step 4: SOR
      setCurrentIdx(3)
      const chosenVenue = side === 'BUY' ? nbbo?.best_ask_venue : nbbo?.best_bid_venue
      const chosenPrice = side === 'BUY' ? nbbo?.best_ask     : nbbo?.best_bid
      updateStep(3, {
        status: 'active', startedAt: Date.now(),
        data: { strategy: 'best_price', chosenVenue, chosenPrice, side },
      })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(3, { status: 'done', durationMs: speed })

      // Step 5: route
      setCurrentIdx(4)
      updateStep(4, { status: 'active', startedAt: Date.now(), data: { venue: chosenVenue, action: 'POST /execute-order' } })
      const submitStart = Date.now()
      let order: Order
      try {
        order = await api.submitOrder(symbol, side, qty)
      } catch (e) {
        updateStep(4, { status: 'failed', durationMs: Date.now() - submitStart, note: String((e as Error).message) })
        setSummary({ kind: 'fail', msg: `Order failed to route: ${(e as Error).message}` })
        setIsPlaying(false)
        return
      }
      const routeLatency = Date.now() - submitStart
      await sleep(Math.max(0, speed - routeLatency))
      if (abortRef.current) return
      updateStep(4, { status: 'done', durationMs: routeLatency, data: { venue: chosenVenue, order } })

      // Step 6: execution
      setCurrentIdx(5)
      const reports = (await api.getExecutionReports()).reports
      const myReport: ExecutionReport | undefined = reports.find((r) => r.order_id === order.id)
      updateStep(5, { status: 'active', startedAt: Date.now(), data: { report: myReport, order } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(5, {
        status: myReport?.exec_type === 'REJECT' ? 'failed' : 'done',
        durationMs: speed,
      })

      // Step 7: position
      setCurrentIdx(6)
      const newRisk = await api.getRiskStatus()
      updateStep(6, { status: 'active', startedAt: Date.now(), data: { positions: newRisk.positions, totalExposure: newRisk.total_exposure } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(6, { status: 'done', durationMs: speed })

      // Final summary
      const filled = order.filled_quantity
      if (order.status === 'FILLED') {
        setSummary({ kind: 'ok',   msg: `Order FILLED: ${side} ${filled} ${symbol} @ avg $${order.avg_fill_price.toFixed(2)} on ${chosenVenue}.` })
      } else if (order.status === 'REJECTED') {
        setSummary({ kind: 'fail', msg: `Order REJECTED: ${order.rejection_reason || 'see execution report'}` })
      } else {
        setSummary({ kind: 'ok',   msg: `Order status: ${order.status}` })
      }
      fetchAll()
    } finally {
      setIsPlaying(false)
      setCurrentIdx(-1)
    }
  }

  useEffect(() => () => { abortRef.current = true }, [])

  return (
    <div>
      <div style={{ fontSize: F.xl, color: C.text, marginBottom: 6, fontWeight: 600 }}>How It Works — Trace a Real Order</div>
      <div style={{ fontSize: F.base, color: C.muted, marginBottom: 16 }}>
        Watch a single order travel through the platform, one stage at a time, with real backend data at every step.
      </div>

      {/* Controls */}
      <Card style={{ marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '120px 100px 100px 1fr 220px', gap: 12, alignItems: 'end' }}>
          <div>
            <label style={lbl}>SYMBOL</label>
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)} disabled={isPlaying} style={inp}>
              {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label style={lbl}>SIDE</label>
            <select value={side} onChange={(e) => setSide(e.target.value as OrderSide)} disabled={isPlaying} style={inp}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>
          <div>
            <label style={lbl}>QTY</label>
            <input type="number" min={1} value={qty} onChange={(e) => setQty(parseInt(e.target.value) || 0)} disabled={isPlaying} style={inp} />
          </div>
          <div>
            <label style={lbl}>SPEED</label>
            <div style={{ display: 'flex', gap: 4 }}>
              {SPEEDS.map((s, i) => (
                <button
                  key={s.label}
                  onClick={() => setSpeedIdx(i)}
                  disabled={isPlaying}
                  style={{
                    flex: 1, padding: '8px 0', fontSize: F.sm, borderRadius: 3, cursor: isPlaying ? 'not-allowed' : 'pointer',
                    border: `1px solid ${speedIdx === i ? C.accent : C.border}`,
                    background: speedIdx === i ? tint(C.accent, 8) : C.surface,
                    color: speedIdx === i ? C.accent : C.muted,
                    fontFamily: 'inherit', fontWeight: speedIdx === i ? 600 : 400,
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={play}
              disabled={isPlaying}
              style={{
                flex: 2, padding: '12px 0', border: 'none', borderRadius: 4,
                background: isPlaying ? C.surface : C.accent, color: isPlaying ? C.muted : C.bg,
                fontSize: F.base, fontWeight: 700, letterSpacing: '.08em',
                cursor: isPlaying ? 'wait' : 'pointer', fontFamily: 'inherit',
              }}
            >
              {isPlaying ? 'RUNNING…' : 'TRACE ORDER'}
            </button>
            <button
              onClick={reset}
              disabled={!isPlaying && currentIdx === -1 && !summary}
              style={{
                flex: 1, padding: '12px 0', border: `1px solid ${C.border}`, borderRadius: 4,
                background: C.surface, color: C.muted,
                fontSize: F.base, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              RESET
            </button>
          </div>
        </div>
      </Card>

      {/* Steps */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {STEP_DEFS.map((def, idx) => {
          const state = steps[idx]
          const isActive = idx === currentIdx
          const isDone = state.status === 'done'
          const isFailed = state.status === 'failed'
          const isSkipped = state.status === 'skipped'

          const borderColor =
            isFailed   ? C.red    :
            isDone     ? C.green  :
            isActive   ? C.accent :
            isSkipped  ? C.border :
                         C.border

          const bg =
            isFailed   ? tint(C.red, 4) :
            isDone     ? tint(C.green, 4) :
            isActive   ? tint(C.accent, 8) :
                         C.surface2

          return (
            <div key={def.key} style={{
              border: `1px solid ${borderColor}`,
              background: bg,
              borderRadius: 6,
              padding: 16,
              opacity: isSkipped ? 0.4 : 1,
              transition: 'all .3s',
              position: 'relative',
            }}>

              {/* Header row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                {/* Step number badge — replaces the icon circle */}
                <div style={{
                  width: 36, height: 36, borderRadius: 18,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: borderColor === C.border ? C.surface : tint(borderColor, 16),
                  border: `1.5px solid ${borderColor}`,
                  fontSize: F.md, fontWeight: 700,
                  color: borderColor === C.border ? C.muted : borderColor,
                  animation: isActive ? 'pocPulse 1s infinite' : undefined,
                  flexShrink: 0,
                }}>
                  {idx + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: F.lg, color: C.text, fontWeight: 600 }}>
                    {def.title}
                  </div>
                  <div style={{ fontSize: F.sm, color: C.muted, marginTop: 2 }}>{def.short}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <span style={{
                    fontSize: F.xs, padding: '3px 10px', borderRadius: 3,
                    background: tint(borderColor, 20), color: borderColor, letterSpacing: '.1em',
                    fontWeight: 600,
                  }}>
                    {state.status.toUpperCase()}
                  </span>
                  {state.durationMs !== undefined && (
                    <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4 }}>
                      {state.durationMs} ms
                    </div>
                  )}
                </div>
              </div>

              {/* What this step does */}
              <div style={{ fontSize: F.base, color: C.muted, marginTop: 12, paddingLeft: 50, lineHeight: 1.55 }}>
                {def.what}
              </div>

              {/* Live data for active/done step */}
              {state.data && (state.status === 'active' || state.status === 'done' || state.status === 'failed') && (
                <div style={{ marginTop: 12, paddingLeft: 50 }}>
                  <StepDetails stepKey={def.key} data={state.data} side={side} />
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Summary */}
      {summary && (
        <div style={{
          marginTop: 16, padding: 18, borderRadius: 6,
          background: summary.kind === 'ok' ? tint(C.green, 8) : tint(C.red, 8),
          border: `1px solid ${summary.kind === 'ok' ? C.green : C.red}`,
          color: summary.kind === 'ok' ? C.green : C.red,
          fontSize: F.md, fontWeight: 600, textAlign: 'center',
        }}>
          {summary.msg}
        </div>
      )}
    </div>
  )
}

function StepDetails({ stepKey, data, side }: { stepKey: string; data: any; side: OrderSide }) {
  if (stepKey === 'input') {
    return (
      <div style={kvBox}>
        <KV k="Symbol"   v={data.symbol} />
        <KV k="Side"     v={data.side}    color={side === 'BUY' ? C.green : C.red} />
        <KV k="Quantity" v={data.quantity} />
        <KV k="Type"     v="MARKET" />
      </div>
    )
  }

  if (stepKey === 'risk') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {(data.checks || []).map((c: any) => (
          <div key={c.name} style={{
            display: 'flex', alignItems: 'center', gap: 12,
            padding: '8px 12px', background: C.surface, borderRadius: 3, fontSize: F.base,
          }}>
            <span style={{
              color: c.passed ? C.green : C.red, fontSize: F.lg, fontWeight: 700,
              width: 18, textAlign: 'center',
            }}>
              {c.passed ? '✓' : '✗'}
            </span>
            <span style={{ flex: 1, color: C.text, fontWeight: 500 }}>{c.name}</span>
            <span style={{ color: C.muted, fontSize: F.sm }}>{c.detail}</span>
          </div>
        ))}
      </div>
    )
  }

  if (stepKey === 'md') {
    const quotes = data.quotes || {}
    const nbbo: NBBO | undefined = data.nbbo
    const bestSideKey = side === 'BUY' ? 'best_ask_venue' : 'best_bid_venue'
    const winnerVenue = nbbo ? (nbbo as any)[bestSideKey] : null
    return (
      <div>
        <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 6, letterSpacing: '.1em' }}>
          QUOTES FROM ALL VENUES ({side === 'BUY' ? 'buying — looking for lowest ASK' : 'selling — looking for highest BID'}):
        </div>
        <table style={tbl}>
          <thead>
            <tr>
              <th style={th}>Venue</th>
              <th style={{ ...th, textAlign: 'right' }}>Bid</th>
              <th style={{ ...th, textAlign: 'right' }}>Ask</th>
              <th style={{ ...th, textAlign: 'right' }}>Spread</th>
              <th style={th}></th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(quotes).map(([vid, q]: any) => {
              const isWinner = vid === winnerVenue
              return (
                <tr key={vid} style={{
                  background: isWinner ? tint(C.accent, 8) : undefined,
                  borderBottom: `1px solid ${tint(C.border, 40)}`,
                }}>
                  <td style={td}>{vid}</td>
                  <td style={{ ...td, textAlign: 'right', color: C.green }}>{q.bid_price?.toFixed(2) ?? '—'}</td>
                  <td style={{ ...td, textAlign: 'right', color: C.red }}>{q.ask_price?.toFixed(2) ?? '—'}</td>
                  <td style={{ ...td, textAlign: 'right', color: C.muted }}>{((q.ask_price - q.bid_price) || 0).toFixed(3)}</td>
                  <td style={{ ...td, color: C.accent, fontSize: F.sm, fontWeight: 600 }}>{isWinner ? 'best' : ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {nbbo && (
          <div style={{ ...kvBox, marginTop: 10 }}>
            <KV k="Best Bid" v={`${nbbo.best_bid?.toFixed(2)} @ ${nbbo.best_bid_venue}`} color={C.green} />
            <KV k="Best Ask" v={`${nbbo.best_ask?.toFixed(2)} @ ${nbbo.best_ask_venue}`} color={C.red} />
            <KV k="Spread"   v={(nbbo.spread || 0).toFixed(3)} />
          </div>
        )}
      </div>
    )
  }

  if (stepKey === 'sor') {
    return (
      <div>
        <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 8, letterSpacing: '.1em' }}>
          STRATEGY: best_price — pick the venue offering the best price for our side
        </div>
        <div style={{
          padding: 14, background: tint(C.accent, 8), border: `1px solid ${C.accent}`,
          borderRadius: 4, display: 'flex', alignItems: 'center', gap: 16,
        }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: F.sm, color: C.muted, letterSpacing: '.1em' }}>WINNER</div>
            <div style={{ fontSize: F.xl, fontWeight: 700, color: C.accent, marginTop: 2 }}>{data.chosenVenue}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: F.sm, color: C.muted, letterSpacing: '.1em' }}>{side === 'BUY' ? 'BUY @' : 'SELL @'}</div>
            <div style={{ fontSize: F.xl, fontWeight: 700, color: side === 'BUY' ? C.red : C.green, marginTop: 2 }}>
              ${data.chosenPrice?.toFixed(2)}
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (stepKey === 'route') {
    return (
      <div style={kvBox}>
        <KV k="Venue"  v={data.venue} color={C.accent} />
        <KV k="Action" v={data.action || 'POST /execute-order'} />
        {data.order && (
          <>
            <KV k="Order ID" v={data.order.id.slice(0, 8).toUpperCase()} />
            <KV k="Status"   v={data.order.status} />
          </>
        )}
      </div>
    )
  }

  if (stepKey === 'exec') {
    const r = data.report
    const o: Order = data.order
    if (!r) {
      return <div style={{ fontSize: F.base, color: C.muted }}>Awaiting execution report…</div>
    }
    return (
      <div>
        <div style={kvBox}>
          <KV k="Exec Type" v={r.exec_type} color={r.exec_type === 'FILL' ? C.green : r.exec_type === 'REJECT' ? C.red : C.orange} />
          <KV k="Quantity"  v={r.quantity.toString()} />
          <KV k="Price"     v={r.price > 0 ? `$${r.price.toFixed(2)}` : '—'} />
          <KV k="Venue"     v={r.venue_id} />
          <KV k="Latency"   v={`${r.venue_latency_ms?.toFixed(1) ?? '?'} ms`} color={r.venue_latency_ms > 100 ? C.orange : C.muted} />
        </div>
        {o.rejection_reason && (
          <div style={{
            marginTop: 10, padding: 10, background: tint(C.red, 6),
            border: `1px solid ${tint(C.red, 30)}`, borderRadius: 4,
            fontSize: F.base, color: C.red,
          }}>
            Rejection: {o.rejection_reason}
          </div>
        )}
      </div>
    )
  }

  if (stepKey === 'position') {
    const positions = data.positions || {}
    return (
      <div>
        <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 6, letterSpacing: '.1em' }}>
          PORTFOLIO AFTER FILL:
        </div>
        <table style={tbl}>
          <thead>
            <tr>
              <th style={th}>Symbol</th>
              <th style={{ ...th, textAlign: 'right' }}>Net</th>
              <th style={{ ...th, textAlign: 'right' }}>Avg Price</th>
              <th style={{ ...th, textAlign: 'right' }}>Market Value</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(positions)
              .filter(([_, p]: any) => p.net_quantity !== 0)
              .map(([sym, p]: any) => (
                <tr key={sym} style={{ borderBottom: `1px solid ${tint(C.border, 40)}` }}>
                  <td style={td}>{sym}</td>
                  <td style={{ ...td, textAlign: 'right', color: p.net_quantity >= 0 ? C.green : C.red }}>
                    {p.net_quantity}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: C.muted }}>${p.avg_price?.toFixed(2) ?? '—'}</td>
                  <td style={{ ...td, textAlign: 'right' }}>${p.market_value?.toFixed(0) ?? 0}</td>
                </tr>
              ))}
          </tbody>
        </table>
        <div style={{ marginTop: 10, fontSize: F.base, color: C.muted }}>
          Total Exposure: <span style={{ color: C.text, fontWeight: 600 }}>${data.totalExposure?.toFixed(0).toLocaleString() ?? 0}</span>
        </div>
      </div>
    )
  }

  return null
}

function KV({ k, v, color }: { k: string; v: string | number; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 3 }}>{k}</div>
      <div style={{ fontSize: F.md, color: color ?? C.text, fontWeight: 600 }}>{v}</div>
    </div>
  )
}

const lbl: React.CSSProperties = {
  display: 'block', fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 4,
}
const inp: React.CSSProperties = {
  width: '100%', padding: '10px 12px',
  background: C.surface, border: `1px solid ${C.border}`, borderRadius: 4,
  color: C.text, fontSize: F.base, fontFamily: 'inherit', outline: 'none',
}
const kvBox: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 16,
  padding: 12, background: C.surface, borderRadius: 4,
}
const tbl: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: F.base }
const th: React.CSSProperties = {
  padding: '6px 10px', fontSize: F.xs, color: C.dim, fontWeight: 400,
  borderBottom: `1px solid ${C.border}`, textAlign: 'left',
}
const td: React.CSSProperties = { padding: '7px 10px', color: C.text }
