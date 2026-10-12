import React, { useEffect, useRef } from 'react'
import { C, F, tint, resolveColor } from '../theme'
import { useThemeStore } from '../stores/themeStore'
import { useDataStore } from '../stores/dataStore'
import type { VenueStatus, OrderState } from '../types'

// ── Connection dot ─────────────────────────────────────────────
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

// ── Order state badge ──────────────────────────────────────────
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

// ── Card wrapper ───────────────────────────────────────────────
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

// ── Card title ─────────────────────────────────────────────────
export function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 10, letterSpacing: '.05em', textTransform: 'uppercase' }}>
      {children}
    </div>
  )
}

// ── Arc Gauge (canvas) ────────────────────────────────────────
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

// ── Sparkline chart ───────────────────────────────────────────
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

// ── Format helpers ────────────────────────────────────────────
export function fmtAge(ms: number): string {
  const s = Math.floor(ms / 1000)
  return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ago`
}

// ── Real money (EXCHANGE_MODE=mainnet) ─────────────────────────
/** True when the gateway trades real funds (any venue in mainnet mode). */
export function useRealMoney(): boolean {
  return useDataStore((s) => s.venues.some((v) => v.mode === 'mainnet'))
}

/** On real money, a one-click action that spends money asks first; on testnet it just runs. */
export function confirmRealMoney(realMoney: boolean, what: string): boolean {
  return !realMoney || window.confirm(`REAL MONEY: ${what}\n\nThis uses real funds on the exchange. Continue?`)
}

/** The tick box a real-money form needs before its button works (the form resets it when the order changes). */
export function RealMoneyCheck({ checked, onChange, what }: {
  checked: boolean
  onChange: (v: boolean) => void
  what: string
}) {
  return (
    <label style={{
      display: 'flex', gap: 8, alignItems: 'flex-start', padding: '8px 10px', marginBottom: 10, borderRadius: 4,
      border: `1px solid ${C.red}`, background: tint(C.red, 10), color: C.red, fontSize: F.sm, lineHeight: 1.4,
      cursor: 'pointer',
    }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 2 }} />
      <span><b>REAL MONEY</b> — {what}. I understand this uses real funds on the exchange.</span>
    </label>
  )
}
