import React, { useEffect, useRef, useState } from 'react'
import { C, F, tint } from '../theme'
import { fmtPrice } from '../format'
import type { ResearchChart as ChartData, ResearchTf } from '../services/api'

/**
 * Research chart: price (close) with the 20 / 50 averages and the Bollinger band, volume bars,
 * then RSI (with 30 / 70) and MACD (histogram + lines) in their own panels. One shared time
 * axis; the crosshair snaps to a candle and lists its values. Gaps (warm-up) are left blank.
 */

const MIN_W = 260
const PAD = { left: 8, right: 70 }
const H_PRICE = 230
const H_VOL = 40
const H_RSI = 70
const H_MACD = 70
const GAP = 10

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(700)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(MIN_W, entry.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

function stamp(t: number, tf: ResearchTf): string {
  const d = new Date(t)
  if (tf === '1d') return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' })
  if (tf === '1h') return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', hour12: false })
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
}

/** SVG path through the non-null points (a null breaks the line). */
function path(values: (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
  let d = ''
  let pen = false
  values.forEach((v, i) => {
    if (v === null) { pen = false; return }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    pen = true
  })
  return d
}

function range(...series: (number | null)[][]): [number, number] {
  let lo = Infinity
  let hi = -Infinity
  for (const s of series) for (const v of s) if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v) }
  if (!Number.isFinite(lo)) return [0, 1]
  const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.01 || 1
  return [lo - pad, hi + pad]
}

export function ResearchChart({ data }: { data: ChartData }) {
  const [wrapRef, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const n = data.close.length
  const plotW = width - PAD.left - PAD.right
  const x = (i: number) => PAD.left + (n <= 1 ? 0 : (i / (n - 1)) * plotW)
  const step = n > 1 ? plotW / (n - 1) : plotW

  const [pLo, pHi] = range(data.close, data.bb_upper, data.bb_lower, data.sma50)
  const yP = (v: number) => H_PRICE - ((v - pLo) / (pHi - pLo)) * (H_PRICE - 12) - 6
  const vMax = Math.max(1e-12, ...data.volume.map((v) => v ?? 0))
  const top2 = H_PRICE + 4
  const yV = (v: number) => top2 + H_VOL - (v / vMax) * H_VOL
  const top3 = top2 + H_VOL + GAP
  const yR = (v: number) => top3 + H_RSI - (v / 100) * H_RSI
  const top4 = top3 + H_RSI + GAP
  const [mLo, mHi] = range(data.macd, data.macd_signal, data.macd_hist)
  const mAbs = Math.max(Math.abs(mLo), Math.abs(mHi))
  const yM = (v: number) => top4 + H_MACD / 2 - (v / (mAbs || 1)) * (H_MACD / 2)
  const H = top4 + H_MACD + 20

  // Bollinger band as one filled shape (upper left-to-right, lower right-to-left).
  const bandIdx = data.bb_upper.map((v, i) => (v !== null && data.bb_lower[i] !== null ? i : -1)).filter((i) => i >= 0)
  const band = bandIdx.length > 1
    ? `M${bandIdx.map((i) => `${x(i).toFixed(1)},${yP(data.bb_upper[i] as number).toFixed(1)}`).join('L')}`
      + `L${[...bandIdx].reverse().map((i) => `${x(i).toFixed(1)},${yP(data.bb_lower[i] as number).toFixed(1)}`).join('L')}Z`
    : ''

  const last = data.close[n - 1]
  const priceTicks = [pLo + (pHi - pLo) * 0.15, (pLo + pHi) / 2, pLo + (pHi - pLo) * 0.85]
  const timeTicks = n > 2 ? [0, Math.floor((n - 1) / 2), n - 1] : [0]

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const i = Math.round(((e.clientX - rect.left - PAD.left) / plotW) * (n - 1))
    setHover(Math.max(0, Math.min(n - 1, i)))
  }

  const h = hover
  const val = (s: (number | null)[], digits = 2) => (h !== null && s[h] !== null ? (s[h] as number).toFixed(digits) : '—')

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%', minWidth: 0 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 16px', fontSize: F.xs, color: C.muted, marginBottom: 4 }}>
        <span style={{ color: C.text }}>— close</span>
        <span style={{ color: C.blue }}>— SMA 20</span>
        <span style={{ color: C.orange }}>— SMA 50</span>
        <span style={{ color: C.purple }}>▮ Bollinger (20, 2σ)</span>
        <span>RSI 14 · MACD 12/26/9</span>
      </div>
      <svg width={width} height={H} style={{ display: 'block', touchAction: 'none' }}>
        {/* grid + axes labels */}
        {priceTicks.map((v) => (
          <g key={`p${v}`}>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={yP(v)} y2={yP(v)} style={{ stroke: C.border, strokeDasharray: '2 4' }} />
            <text x={PAD.left + plotW + 6} y={yP(v) + 4} style={{ fill: C.dim, fontSize: 11 }}>{fmtPrice(v)}</text>
          </g>
        ))}
        {band && <path d={band} style={{ fill: tint(C.purple, 12), stroke: 'none' }} />}
        <path d={path(data.sma50, x, yP)} style={{ fill: 'none', stroke: C.orange, strokeWidth: 1.2 }} />
        <path d={path(data.sma20, x, yP)} style={{ fill: 'none', stroke: C.blue, strokeWidth: 1.2 }} />
        <path d={path(data.close, x, yP)} style={{ fill: 'none', stroke: C.text, strokeWidth: 1.6 }} />
        {last !== null && (
          <g>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={yP(last)} y2={yP(last)} style={{ stroke: C.accent, strokeDasharray: '3 3', opacity: 0.6 }} />
            <rect x={PAD.left + plotW + 2} y={yP(last) - 9} width={PAD.right - 4} height={18} rx={3} style={{ fill: C.accent }} />
            <text x={PAD.left + plotW + 6} y={yP(last) + 4} style={{ fill: C.bg, fontSize: 11, fontWeight: 700 }}>{fmtPrice(last)}</text>
          </g>
        )}

        {/* volume */}
        {data.volume.map((v, i) => v !== null && (
          <rect key={`v${i}`} x={x(i) - Math.max(1, step * 0.35)} y={yV(v)} width={Math.max(1, step * 0.7)}
                height={top2 + H_VOL - yV(v)}
                style={{ fill: (data.close[i] ?? 0) >= (data.open[i] ?? 0) ? tint(C.green, 45) : tint(C.red, 45) }} />
        ))}
        <text x={PAD.left + plotW + 6} y={top2 + 12} style={{ fill: C.dim, fontSize: 10 }}>volume</text>

        {/* RSI */}
        <rect x={PAD.left} y={yR(70)} width={plotW} height={yR(30) - yR(70)} style={{ fill: tint(C.blue, 6) }} />
        {[30, 70].map((lvl) => (
          <g key={`r${lvl}`}>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={yR(lvl)} y2={yR(lvl)} style={{ stroke: C.border, strokeDasharray: '2 4' }} />
            <text x={PAD.left + plotW + 6} y={yR(lvl) + 4} style={{ fill: C.dim, fontSize: 10 }}>{lvl}</text>
          </g>
        ))}
        <path d={path(data.rsi, x, yR)} style={{ fill: 'none', stroke: C.purple, strokeWidth: 1.3 }} />
        <text x={PAD.left + plotW + 6} y={top3 + 10} style={{ fill: C.dim, fontSize: 10 }}>RSI</text>

        {/* MACD */}
        <line x1={PAD.left} x2={PAD.left + plotW} y1={yM(0)} y2={yM(0)} style={{ stroke: C.border }} />
        {data.macd_hist.map((v, i) => v !== null && (
          <rect key={`m${i}`} x={x(i) - Math.max(0.5, step * 0.3)} y={Math.min(yM(v), yM(0))}
                width={Math.max(1, step * 0.6)} height={Math.abs(yM(v) - yM(0))}
                style={{ fill: v >= 0 ? tint(C.green, 60) : tint(C.red, 60) }} />
        ))}
        <path d={path(data.macd, x, yM)} style={{ fill: 'none', stroke: C.blue, strokeWidth: 1.1 }} />
        <path d={path(data.macd_signal, x, yM)} style={{ fill: 'none', stroke: C.orange, strokeWidth: 1.1 }} />
        <text x={PAD.left + plotW + 6} y={top4 + 10} style={{ fill: C.dim, fontSize: 10 }}>MACD</text>

        {/* time axis */}
        {timeTicks.map((i, k) => (
          <text key={`t${i}`} x={x(i)} y={H - 4} textAnchor={k === 0 ? 'start' : k === timeTicks.length - 1 ? 'end' : 'middle'}
                style={{ fill: C.dim, fontSize: 11 }}>{stamp(data.ts[i], data.timeframe)}</text>
        ))}

        {/* crosshair */}
        {h !== null && (
          <line x1={x(h)} x2={x(h)} y1={0} y2={top4 + H_MACD} style={{ stroke: C.muted, strokeDasharray: '3 3' }} />
        )}
        <rect x={PAD.left} y={0} width={plotW} height={top4 + H_MACD} style={{ fill: 'transparent', cursor: 'crosshair' }}
              onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {h !== null && (
        <div style={{
          position: 'absolute', top: 22, left: Math.min(Math.max(x(h) + 12, 0), width - 210), width: 196,
          pointerEvents: 'none', background: C.surface, border: `1px solid ${C.border}`, borderRadius: 4,
          padding: '6px 8px', fontSize: F.xs, color: C.muted, lineHeight: 1.6, boxShadow: '0 2px 8px rgba(0,0,0,.25)',
        }}>
          <div style={{ color: C.text, fontWeight: 700 }}>{stamp(data.ts[h], data.timeframe)}</div>
          <div>close <span style={{ color: C.text }}>{data.close[h] !== null ? fmtPrice(data.close[h]) : '—'}</span></div>
          <div>SMA20 {data.sma20[h] !== null ? fmtPrice(data.sma20[h]) : '—'} · SMA50 {data.sma50[h] !== null ? fmtPrice(data.sma50[h]) : '—'}</div>
          <div>RSI {val(data.rsi, 1)} · ATR {data.atr[h] !== null ? fmtPrice(data.atr[h]) : '—'}</div>
          <div>MACD hist {val(data.macd_hist, 4)}</div>
        </div>
      )}
    </div>
  )
}

/** Backtest equity: the rule vs simply holding, from the same start. */
export function EquityChart({ points }: { points: { ts: number; equity: number; buy_hold: number | null }[] }) {
  const [wrapRef, width] = useWidth<HTMLDivElement>()
  const H = 170
  const n = points.length
  const plotW = width - 70
  const eq = points.map((p) => p.equity)
  const bh = points.map((p) => p.buy_hold)
  const [lo, hi] = range(eq, bh)
  const x = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * plotW)
  const y = (v: number) => H - 18 - ((v - lo) / (hi - lo)) * (H - 30)
  return (
    <div ref={wrapRef} style={{ width: '100%', minWidth: 0 }}>
      <div style={{ display: 'flex', gap: 16, fontSize: F.xs, color: C.muted, marginBottom: 4 }}>
        <span style={{ color: C.blue }}>— this rule</span>
        <span style={{ color: C.dim }}>— buy &amp; hold</span>
      </div>
      <svg width={width} height={H} style={{ display: 'block' }}>
        {[lo + (hi - lo) * 0.15, (lo + hi) / 2, lo + (hi - lo) * 0.85].map((v) => (
          <g key={v}>
            <line x1={0} x2={plotW} y1={y(v)} y2={y(v)} style={{ stroke: C.border, strokeDasharray: '2 4' }} />
            <text x={plotW + 6} y={y(v) + 4} style={{ fill: C.dim, fontSize: 11 }}>{v.toFixed(0)}</text>
          </g>
        ))}
        <path d={path(bh, x, y)} style={{ fill: 'none', stroke: C.dim, strokeWidth: 1.3 }} />
        <path d={path(eq, x, y)} style={{ fill: 'none', stroke: C.blue, strokeWidth: 1.8 }} />
      </svg>
    </div>
  )
}
