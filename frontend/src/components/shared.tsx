import React, { useEffect, useRef } from 'react'
import { C, F, tint, resolveColor } from '../theme'
import { useThemeStore } from '../stores/themeStore'
import type { VenueStatus, OrderState } from '../types'

// â”€â”€ Connection dot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function ConnDot({ status }: { status: VenueStatus }) {
  const color =
    status === 'Connected'    ? C.green  :
    status === 'Degraded'     ? C.orange : C.red
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: color, boxShadow: `0 0 5px ${color}99`, flexShrink: 0,
      animation: status === 'Degraded' ? 'pocPulse 1.5s infinite' : undefined,
    }} />
  )
}

// â”€â”€ Order state badge â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function StateBadge({ state }: { state: OrderState }) {
  const map: Record<OrderState, { bg: string; color: string }> = {
    Filled:          { bg: tint(C.green, 13), color: C.green  },
    Working:         { bg: tint(C.blue, 13), color: C.blue   },
    PartiallyFilled: { bg: tint(C.orange, 13), color: C.orange },
    Rejected:        { bg: tint(C.red, 13), color: C.red    },
    Cancelled:       { bg: tint(C.dim, 19), color: C.dim    },
  }
  const s = map[state]
  return (
    <span style={{ background: s.bg, color: s.color, padding: '1px 6px', borderRadius: 3, fontSize: F.xs, whiteSpace: 'nowrap' }}>
      {state}
    </span>
  )
}

// â”€â”€ Card wrapper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: C.surface2, border: `1px solid ${C.border}`,
      borderRadius: 6, padding: 14, ...style,
    }}>
      {children}
    </div>
  )
}

// â”€â”€ Card title â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 10, letterSpacing: '.05em', textTransform: 'uppercase' }}>
      {children}
    </div>
  )
}

// â”€â”€ Arc Gauge (canvas) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function ArcGauge({ value, max, label, unit, size = 110 }: {
  value: number; max: number; label: string; unit: string; size?: number
}) {
  const ref = useRef<HTMLCanvasElement>(null)
  const themeMode = useThemeStore((s) => s.mode)
  useEffect(() => {
    const c = ref.current; if (!c) return
    const ctx = c.getContext('2d')!
    const w = size, h = Math.round(size * 0.65)
    c.width = w; c.height = h
    const cx = w / 2, cy = h - 4, r = Math.min(w / 2 - 8, h - 12)
    const pct = Math.min(1, value / max)
    const color = pct > 0.8 ? C.red : pct > 0.6 ? C.orange : C.blue
    ctx.clearRect(0, 0, w, h)
    ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI, 2 * Math.PI)
    ctx.strokeStyle = resolveColor(C.border); ctx.lineWidth = 7; ctx.lineCap = 'round'; ctx.stroke()
    ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI, Math.PI + Math.PI * pct)
    ctx.strokeStyle = resolveColor(color); ctx.lineWidth = 7; ctx.lineCap = 'round'; ctx.stroke()
  }, [value, max, size, themeMode])
  return (
    <div style={{ textAlign: 'center' }}>
      <canvas ref={ref} style={{ display: 'block', margin: '0 auto' }} />
      <div style={{ fontSize: F.md, color: C.text, marginTop: 2 }}>
        {value.toLocaleString()} <span style={{ fontSize: F.xs, color: C.muted }}>{unit}</span>
      </div>
      <div style={{ fontSize: F.xs, color: C.muted }}>{label}</div>
    </div>
  )
}

// â”€â”€ Sparkline chart â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function Sparkline({ data }: { data: number[] }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const themeMode = useThemeStore((s) => s.mode)
  useEffect(() => {
    const c = ref.current; if (!c) return
    const ctx = c.getContext('2d')!
    const w = c.parentElement?.offsetWidth ?? 300, h = 80
    c.width = w; c.height = h
    const mn = 0, mx = 180
    const borderC = resolveColor(C.border)
    const blueC = resolveColor(C.blue)
    ctx.clearRect(0, 0, w, h)
    ;[0.25, 0.5, 0.75].forEach(f => {
      ctx.beginPath(); ctx.moveTo(0, h - f * h); ctx.lineTo(w, h - f * h)
      ctx.strokeStyle = borderC; ctx.lineWidth = 0.5; ctx.stroke()
    })
    ctx.beginPath()
    data.forEach((v, i) => {
      const x = (i / (data.length - 1)) * w
      const y = h - ((v - mn) / (mx - mn)) * h
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    })
    ctx.strokeStyle = blueC; ctx.lineWidth = 1.5; ctx.stroke()
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath()
    // canvas alpha fill — use rgba with 8% of the blue
    ctx.fillStyle = blueC; ctx.globalAlpha = 0.08; ctx.fill(); ctx.globalAlpha = 1
  }, [data, themeMode])
  return <canvas ref={ref} style={{ width: '100%', display: 'block' }} />
}

// â”€â”€ Latency chart â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function LatencyChart() {
  const ref = useRef<HTMLCanvasElement>(null)
  const themeMode = useThemeStore((s) => s.mode)
  useEffect(() => {
    const c = ref.current; if (!c) return
    const ctx = c.getContext('2d')!
    const w = c.parentElement?.offsetWidth ?? 600, h = 130
    c.width = w; c.height = h
    const pts = 30, maxVal = 1200
    const series = [
      { color: resolveColor(C.blue),   base: 142 },
      { color: resolveColor(C.green),  base: 98  },
      { color: resolveColor(C.purple), base: 215 },
    ]
    series.forEach(s => {
      const data = Array.from({ length: pts }, () => s.base * (1 + (Math.random() - .5) * .2))
      ctx.beginPath()
      data.forEach((v, i) => {
        const x = (i / (pts - 1)) * w, y = h - 10 - ((v / maxVal) * (h - 20))
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      })
      ctx.strokeStyle = s.color; ctx.lineWidth = 1.5; ctx.stroke()
    })
    const orangeC = resolveColor(C.orange)
    const bats = Array.from({ length: pts }, (_, i) => 300 + Math.random() * 100 + (i > 10 && i < 20 ? 700 : 0))
    ctx.beginPath()
    bats.forEach((v, i) => {
      const x = (i / (pts - 1)) * w, y = h - 10 - ((Math.min(v, maxVal) / maxVal) * (h - 20))
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    })
    ctx.strokeStyle = orangeC; ctx.lineWidth = 1.5; ctx.stroke()
    const mutedC = resolveColor(C.muted)
    const legs = [
      { n: 'NYSE',   c: resolveColor(C.blue) },
      { n: 'NASDAQ', c: resolveColor(C.green) },
      { n: 'BATS',   c: orangeC },
      { n: 'IEX',    c: resolveColor(C.purple) },
    ]
    legs.forEach((l, i) => {
      ctx.fillStyle = l.c; ctx.fillRect(i * 90 + 8, h - 14, 20, 2)
      ctx.fillStyle = mutedC; ctx.font = '11px Consolas,monospace'
      ctx.fillText(l.n, i * 90 + 32, h - 6)
    })
  }, [themeMode])
  return <canvas ref={ref} style={{ width: '100%', display: 'block' }} />
}

// â”€â”€ Format helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export function fmtAge(ms: number): string {
  const s = Math.floor(ms / 1000)
  return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ago`
}

export function fmtLatency(us: number): string {
  if (us === 0) return '—'
  if (us < 1000) return `${us} Âµs`
  return `${(us / 1000).toFixed(1)} ms`
}
