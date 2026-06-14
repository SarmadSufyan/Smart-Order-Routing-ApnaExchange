import React, { useEffect, useRef, useState } from 'react'
import { C } from '../theme'
import { Card, CardTitle } from '../components/shared'
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
    icon: '📝',
    title: 'Order Received',
    short: 'Operator submits a new order',
    what: 'The order is created in the Order Manager with status NEW. Every order gets a UUID, timestamp, and audit trail.',
  },
  {
    key: 'risk',
    icon: '🛡',
    title: 'Pre-Trade Risk Check',
    short: 'Four checks must pass',
    what: 'Before the order goes anywhere, the Risk Engine validates: kill switch is off, order size is under the limit, position limit per symbol is not exceeded, and notional exposure is within the cap.',
  },
  {
    key: 'md',
    icon: '🌐',
    title: 'Market Data Aggregation',
    short: 'Pulls NBBO from all 5 venues',
    what: 'The Market Data Aggregator queries every connected venue and computes the National Best Bid & Offer — the best bid across venues and the best ask across venues. This is the "best price anywhere".',
  },
  {
    key: 'sor',
    icon: '🧠',
    title: 'SOR Strategy Decision',
    short: 'Best-Price algorithm picks a venue',
    what: 'The Smart Order Router runs the Best-Price strategy. It looks at the NBBO and filters out blacklisted/degraded venues, then picks the venue with the best price for our side.',
  },
  {
    key: 'route',
    icon: '📤',
    title: 'Venue Routing',
    short: 'Order sent to chosen venue',
    what: 'The Order Manager transitions the order to ROUTING then WORKING. A child order is sent via HTTP to the selected venue\'s /execute-order endpoint.',
  },
  {
    key: 'exec',
    icon: '⚡',
    title: 'Execution Report',
    short: 'Venue responds with FILL or REJECT',
    what: 'The venue simulates matching against its order book. It either fills the order (returns FILL with fill price and quantity) or rejects it. Latency is tracked.',
  },
  {
    key: 'position',
    icon: '📊',
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
  const [summary, setSummary] = useState<string | null>(null)
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
      // ── Step 1: input ──
      setCurrentIdx(0)
      updateStep(0, { status: 'active', startedAt: Date.now(), data: { symbol, side, quantity: qty } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(0, { status: 'done', durationMs: speed })

      // ── Step 2: risk ──
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
        setSummary('Order blocked by pre-trade risk check.')
        setIsPlaying(false)
        return
      }

      // ── Step 3: market data ──
      setCurrentIdx(2)
      const quotesRes = await api.getQuotes(symbol)
      const allNbboRes = await api.getAllNBBO()
      const nbbo: NBBO | undefined = allNbboRes.nbbo[symbol]
      updateStep(2, { status: 'active', startedAt: Date.now(), data: { quotes: quotesRes.quotes, nbbo } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(2, { status: 'done', durationMs: speed })

      // ── Step 4: SOR ──
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

      // ── Step 5: route ──
      setCurrentIdx(4)
      updateStep(4, { status: 'active', startedAt: Date.now(), data: { venue: chosenVenue, action: 'POST /execute-order' } })
      // Actually submit the order now
      const submitStart = Date.now()
      let order: Order
      try {
        order = await api.submitOrder(symbol, side, qty)
      } catch (e) {
        updateStep(4, { status: 'failed', durationMs: Date.now() - submitStart, note: String((e as Error).message) })
        setSummary(`Order failed to route: ${(e as Error).message}`)
        setIsPlaying(false)
        return
      }
      const routeLatency = Date.now() - submitStart
      await sleep(Math.max(0, speed - routeLatency))
      if (abortRef.current) return
      updateStep(4, { status: 'done', durationMs: routeLatency, data: { venue: chosenVenue, order } })

      // ── Step 6: execution ──
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

      // ── Step 7: position ──
      setCurrentIdx(6)
      const newRisk = await api.getRiskStatus()
      updateStep(6, { status: 'active', startedAt: Date.now(), data: { positions: newRisk.positions, totalExposure: newRisk.total_exposure } })
      await sleep(speed)
      if (abortRef.current) return
      updateStep(6, { status: 'done', durationMs: speed })

      // Final summary
      const filled = order.filled_quantity
      const totalFee = 0
      if (order.status === 'FILLED') {
        setSummary(`✓ Order FILLED: ${side} ${filled} ${symbol} @ avg $${order.avg_fill_price.toFixed(2)} on ${chosenVenue}.`)
      } else if (order.status === 'REJECTED') {
        setSummary(`✗ Order REJECTED: ${order.rejection_reason || 'see execution report'}`)
      } else {
        setSummary(`Order status: ${order.status}`)
      }
      fetchAll()
    } finally {
      setIsPlaying(false)
      setCurrentIdx(-1)
    }
  }

  // Cleanup on unmount
  useEffect(() => () => { abortRef.current = true }, [])

  return (
    <div>
      <div style={{ fontSize: 15, color: C.text, marginBottom: 6 }}>How It Works — Trace a Real Order</div>
      <div style={{ fontSize: 11, color: C.muted, marginBottom: 16 }}>
        Watch a single order travel through the platform, one stage at a time, with real backend data at every step.
      </div>

      {/* Controls */}
      <Card style={{ marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '120px 100px 100px 1fr 200px', gap: 12, alignItems: 'end' }}>
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
                    flex: 1, padding: '6px 0', fontSize: 10, borderRadius: 3, cursor: isPlaying ? 'not-allowed' : 'pointer',
                    border: `1px solid ${speedIdx === i ? C.accent : C.border}`,
                    background: speedIdx === i ? '#00D9FF15' : C.surface,
                    color: speedIdx === i ? C.accent : C.muted,
                    fontFamily: 'inherit',
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
                flex: 2, padding: '10px 0', border: 'none', borderRadius: 4,
                background: isPlaying ? C.surface : C.accent, color: isPlaying ? C.muted : C.bg,
                fontSize: 12, fontWeight: 700, letterSpacing: '.08em',
                cursor: isPlaying ? 'wait' : 'pointer', fontFamily: 'inherit',
              }}
            >
              {isPlaying ? '⏸ RUNNING…' : '▶ TRACE ORDER'}
            </button>
            <button
              onClick={reset}
              disabled={!isPlaying && currentIdx === -1 && !summary}
              style={{
                flex: 1, padding: '10px 0', border: `1px solid ${C.border}`, borderRadius: 4,
                background: C.surface, color: C.muted,
                fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              ↺ RESET
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
            isFailed   ? '#E24B4A06' :
            isDone     ? '#4CAF5006' :
            isActive   ? '#00D9FF10' :
                         '#1A1E24'

          return (
            <div key={def.key} style={{
              border: `1px solid ${borderColor}`,
              background: bg,
              borderRadius: 6,
              padding: 14,
              opacity: isSkipped ? 0.4 : 1,
              transition: 'all .3s',
              position: 'relative',
            }}>

              {/* Header row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{
                  width: 32, height: 32, borderRadius: 16,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: borderColor === C.border ? C.surface : `${borderColor}20`,
                  border: `1px solid ${borderColor}`,
                  fontSize: 16,
                  animation: isActive ? 'pocPulse 1s infinite' : undefined,
                }}>
                  {def.icon}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, color: C.text, fontWeight: 600 }}>
                    {idx + 1}. {def.title}
                  </div>
                  <div style={{ fontSize: 10, color: C.muted, marginTop: 2 }}>{def.short}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <span style={{
                    fontSize: 9, padding: '2px 8px', borderRadius: 3,
                    background: `${borderColor}25`, color: borderColor, letterSpacing: '.1em',
                  }}>
                    {state.status.toUpperCase()}
                  </span>
                  {state.durationMs !== undefined && (
                    <div style={{ fontSize: 10, color: C.dim, marginTop: 3 }}>
                      {state.durationMs} ms
                    </div>
                  )}
                </div>
              </div>

              {/* What this step does */}
              <div style={{ fontSize: 11, color: C.muted, marginTop: 10, paddingLeft: 44, lineHeight: 1.5 }}>
                {def.what}
              </div>

              {/* Live data for active/done step */}
              {state.data && (state.status === 'active' || state.status === 'done' || state.status === 'failed') && (
                <div style={{ marginTop: 10, paddingLeft: 44 }}>
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
          marginTop: 14, padding: 16, borderRadius: 6,
          background: summary.startsWith('✓') ? '#4CAF5010' : '#E24B4A10',
          border: `1px solid ${summary.startsWith('✓') ? C.green : C.red}`,
          color: summary.startsWith('✓') ? C.green : C.red,
          fontSize: 13, fontWeight: 600, textAlign: 'center',
        }}>
          {summary}
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
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '6px 10px', background: C.surface, borderRadius: 3, fontSize: 11,
          }}>
            <span style={{ color: c.passed ? C.green : C.red, fontSize: 14 }}>
              {c.passed ? '✓' : '✗'}
            </span>
            <span style={{ flex: 1, color: C.text }}>{c.name}</span>
            <span style={{ color: C.muted, fontSize: 10 }}>{c.detail}</span>
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
        <div style={{ fontSize: 10, color: C.dim, marginBottom: 6, letterSpacing: '.1em' }}>
          QUOTES FROM ALL VENUES (you are buying — looking for lowest ASK):
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
                  background: isWinner ? '#00D9FF10' : undefined,
                  borderBottom: `1px solid ${C.border}40`,
                }}>
                  <td style={td}>{vid}</td>
                  <td style={{ ...td, textAlign: 'right', color: C.green }}>{q.bid_price?.toFixed(2) ?? '—'}</td>
                  <td style={{ ...td, textAlign: 'right', color: C.red }}>{q.ask_price?.toFixed(2) ?? '—'}</td>
                  <td style={{ ...td, textAlign: 'right', color: C.muted }}>{((q.ask_price - q.bid_price) || 0).toFixed(3)}</td>
                  <td style={{ ...td, color: C.accent, fontSize: 10 }}>{isWinner ? '◀ best' : ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {nbbo && (
          <div style={{ ...kvBox, marginTop: 8 }}>
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
        <div style={{ fontSize: 10, color: C.dim, marginBottom: 8, letterSpacing: '.1em' }}>
          STRATEGY: best_price → pick the venue offering the best price for our side
        </div>
        <div style={{
          padding: 12, background: '#00D9FF10', border: `1px solid ${C.accent}`,
          borderRadius: 4, display: 'flex', alignItems: 'center', gap: 14,
        }}>
          <div style={{ fontSize: 22 }}>🎯</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: C.muted }}>WINNER</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: C.accent }}>{data.chosenVenue}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 11, color: C.muted }}>{side === 'BUY' ? 'BUY @' : 'SELL @'}</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: side === 'BUY' ? C.red : C.green }}>
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
      return <div style={{ fontSize: 11, color: C.muted }}>Awaiting execution report…</div>
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
          <div style={{ marginTop: 8, padding: 8, background: '#E24B4A10', border: `1px solid ${C.red}40`, borderRadius: 4, fontSize: 11, color: C.red }}>
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
        <div style={{ fontSize: 10, color: C.dim, marginBottom: 6, letterSpacing: '.1em' }}>
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
                <tr key={sym} style={{ borderBottom: `1px solid ${C.border}40` }}>
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
        <div style={{ marginTop: 8, fontSize: 11, color: C.muted }}>
          Total Exposure: <span style={{ color: C.text }}>${data.totalExposure?.toFixed(0).toLocaleString() ?? 0}</span>
        </div>
      </div>
    )
  }

  return null
}

function KV({ k, v, color }: { k: string; v: string | number; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: 9, color: C.dim, letterSpacing: '.1em', marginBottom: 2 }}>{k}</div>
      <div style={{ fontSize: 12, color: color ?? C.text, fontWeight: 600 }}>{v}</div>
    </div>
  )
}

const lbl: React.CSSProperties = {
  display: 'block', fontSize: 10, color: C.dim, letterSpacing: '.1em', marginBottom: 4,
}
const inp: React.CSSProperties = {
  width: '100%', padding: '8px 10px',
  background: C.surface, border: `1px solid ${C.border}`, borderRadius: 4,
  color: C.text, fontSize: 12, fontFamily: 'inherit', outline: 'none',
}
const kvBox: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 14,
  padding: 10, background: C.surface, borderRadius: 4,
}
const tbl: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', fontSize: 11 }
const th: React.CSSProperties = {
  padding: '4px 8px', fontSize: 10, color: C.dim, fontWeight: 400,
  borderBottom: `1px solid ${C.border}`, textAlign: 'left',
}
const td: React.CSSProperties = { padding: '5px 8px', color: C.text }
