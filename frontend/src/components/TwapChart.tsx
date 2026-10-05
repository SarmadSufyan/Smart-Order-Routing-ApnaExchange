import React, { useEffect, useRef, useState } from 'react'
import { C, F } from '../theme'
import { fmtPrice, fmtQty } from '../format'
import type { TwapState } from '../services/api'

/**
 * TWAP execution chart: the schedule (cumulative target, muted dashed step)
 * against what actually filled (blue step + wash). One quantity axis; the
 * crosshair snaps to the nearest slice. The slice table on the page is the
 * table view of the same data.
 */

const H = 230
const PAD = { top: 16, right: 86, bottom: 28, left: 64 }
// Narrow enough for a 320 px phone's card; below this the plot would vanish.
const MIN_W = 240
// A clock label is ~50 px wide: three of them need this much plot width.
const MIN_PLOT_FOR_MID_TICK = 160

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(600)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(MIN_W, entry.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

const ms = (iso: string) => new Date(iso).getTime()
const clock = (t: number) => new Date(t).toLocaleTimeString(undefined, { hour12: false })

export function TwapChart({ algo, now }: { algo: TwapState; now: number }) {
  const [wrapRef, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)  // slice index (0-based)

  const qty = algo.params.quantity
  const base = algo.params.symbol.split('/')[0]
  const t0 = ms(algo.started_at ?? algo.created_at)
  const lastScheduled = Math.max(...algo.slices.map((s) => ms(s.scheduled_at)))
  // Running: show the whole planned window. Finished: up to when it ended.
  const endT = algo.ended_at ? Math.max(ms(algo.ended_at), lastScheduled) : lastScheduled + algo.interval_s * 1000
  const tEnd = Math.max(endT, t0 + 1000)

  const plotW = width - PAD.left - PAD.right
  const plotH = H - PAD.top - PAD.bottom
  const x = (t: number) => PAD.left + ((t - t0) / (tEnd - t0)) * plotW
  const y = (q: number) => PAD.top + plotH - (q / (qty * 1.05)) * plotH

  // Schedule: steps up at each slice's due time.
  const sched: string[] = [`M${x(t0)},${y(0)}`]
  let prevTarget = 0
  for (const s of algo.slices) {
    const t = ms(s.scheduled_at)
    sched.push(`L${x(t)},${y(prevTarget)}`, `L${x(t)},${y(s.target_cumulative)}`)
    prevTarget = s.target_cumulative
  }
  sched.push(`L${x(tEnd)},${y(prevTarget)}`)

  // Filled: steps up when each slice came back.
  const sent = algo.slices.filter((s) => s.sent_at)
  const fillPts: { t: number; cum: number; idx: number }[] = []
  let cum = 0
  const filled: string[] = [`M${x(t0)},${y(0)}`]
  for (const s of sent) {
    const t = ms(s.sent_at!)
    filled.push(`L${x(t)},${y(cum)}`)
    cum += s.filled
    filled.push(`L${x(t)},${y(cum)}`)
    fillPts.push({ t, cum, idx: s.index - 1 })
  }
  const lineEnd = algo.ended_at ? tEnd : Math.min(Math.max(now, t0), tEnd)
  filled.push(`L${x(lineEnd)},${y(cum)}`)
  const area = `${filled.join(' ')} L${x(lineEnd)},${y(0)} L${x(t0)},${y(0)} Z`

  const yTicks = [0, qty / 2, qty]
  const xTicks = plotW >= MIN_PLOT_FOR_MID_TICK ? [t0, t0 + (tEnd - t0) / 2, tEnd] : [t0, tEnd]

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - rect.left + PAD.left
    let best = 0
    let bestD = Infinity
    algo.slices.forEach((s, i) => {
      const d = Math.abs(x(ms(s.sent_at ?? s.scheduled_at)) - px)
      if (d < bestD) { bestD = d; best = i }
    })
    setHover(best)
  }

  const hs = hover !== null ? algo.slices[hover] : null
  const hsT = hs ? ms(hs.sent_at ?? hs.scheduled_at) : 0
  const cumAt = hs ? algo.slices.slice(0, hover! + 1).reduce((a, s) => a + s.filled, 0) : 0
  const tipLeft = hs ? Math.min(Math.max(x(hsT) + 12, 0), width - 230) : 0

  return (
    // minWidth 0: inside a flex/grid cell the SVG's own width would otherwise stop
    // the box from shrinking, so the observer would never see the narrower size.
    <div ref={wrapRef} style={{ position: 'relative', width: '100%', minWidth: 0 }}>
      {/* Legend: line keys mirror the marks */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 18px', fontSize: F.xs, color: C.muted, marginBottom: 4 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <svg width="18" height="4"><line x1="0" y1="2" x2="18" y2="2" style={{ stroke: C.blue, strokeWidth: 2 }} /></svg>
          Filled (cumulative)
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <svg width="18" height="4"><line x1="0" y1="2" x2="18" y2="2" style={{ stroke: C.muted, strokeWidth: 2, strokeDasharray: '4 3' }} /></svg>
          Schedule (target)
        </span>
      </div>

      <svg width={width} height={H} role="img"
           aria-label={`TWAP ${algo.id}: ${fmtQty(cum)} of ${fmtQty(qty)} ${base} filled against the schedule`}>
        {/* Recessive hairline grid + axes */}
        {yTicks.map((q) => (
          <g key={`y${q}`}>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={y(q)} y2={y(q)} style={{ stroke: C.border, strokeWidth: 1 }} />
            <text x={PAD.left - 8} y={y(q) + 4} textAnchor="end" style={{ fill: C.dim, fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
              {fmtQty(q)}
            </text>
          </g>
        ))}
        {xTicks.map((t, i) => (
          <text key={`x${i}`} x={x(t)} y={H - 8} textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}
                style={{ fill: C.dim, fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>
            {clock(t)}
          </text>
        ))}

        <path d={area} style={{ fill: C.blue, opacity: 0.1 }} />
        <path d={sched.join(' ')} style={{ fill: 'none', stroke: C.muted, strokeWidth: 2, strokeDasharray: '4 3', strokeLinejoin: 'round' }} />
        <path d={filled.join(' ')} style={{ fill: 'none', stroke: C.blue, strokeWidth: 2, strokeLinejoin: 'round', strokeLinecap: 'round' }} />
        {fillPts.map((p) => (
          <circle key={p.idx} cx={x(p.t)} cy={y(p.cum)} r={4}
                  style={{ fill: C.blue, stroke: C.surface2, strokeWidth: 2 }} />
        ))}

        {/* Direct label at the end of the filled line (text token, not the series color) */}
        <text x={x(lineEnd) + 8} y={y(cum) + 4} style={{ fill: C.text, fontSize: 12, fontWeight: 600 }}>
          {fmtQty(cum)} {base}
        </text>

        {hs && (
          <line x1={x(hsT)} x2={x(hsT)} y1={PAD.top} y2={PAD.top + plotH}
                style={{ stroke: C.dim, strokeWidth: 1 }} />
        )}
        {/* Hit area bigger than any mark: the whole plot */}
        <rect x={PAD.left} y={PAD.top} width={plotW} height={plotH} fill="transparent"
              onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>

      {hs && (
        <div style={{
          position: 'absolute', top: 30, left: tipLeft, width: 218, pointerEvents: 'none',
          background: C.surface, border: `1px solid ${C.border}`, borderRadius: 6,
          padding: '8px 10px', fontSize: F.xs, color: C.muted, boxShadow: '0 4px 14px rgba(0,0,0,.25)',
        }}>
          <div style={{ marginBottom: 4 }}>Slice {hs.index}/{algo.slices.length} · {clock(hsT)} · {hs.status}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <svg width="12" height="4"><line x1="0" y1="2" x2="12" y2="2" style={{ stroke: C.blue, strokeWidth: 2 }} /></svg>
            <span style={{ color: C.text, fontWeight: 700 }}>{fmtQty(cumAt)} {base}</span> filled
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <svg width="12" height="4"><line x1="0" y1="2" x2="12" y2="2" style={{ stroke: C.muted, strokeWidth: 2, strokeDasharray: '3 2' }} /></svg>
            <span style={{ color: C.text, fontWeight: 700 }}>{fmtQty(hs.target_cumulative)} {base}</span> scheduled
          </div>
          {hs.sent_at && (
            <div style={{ marginTop: 4 }}>
              This slice: {fmtQty(hs.filled)} of {fmtQty(hs.quantity)}
              {hs.filled > 0 && ` @ ${fmtPrice(hs.avg_price)}`}{hs.venues.length > 0 && ` (${hs.venues.join(', ')})`}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
