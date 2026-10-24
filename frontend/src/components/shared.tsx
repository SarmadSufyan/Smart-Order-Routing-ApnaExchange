import React, { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
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
      background: color, boxShadow: `0 0 6px ${tint(color, 60)}`, flexShrink: 0,
      animation: status === 'Degraded' ? 'pocPulse 1.5s infinite' : undefined,
    }} />
  )
}

// ── Order state badge ──────────────────────────────────────────
const STATE_LABEL: Record<OrderState, string> = {
  Filled: 'Filled', Working: 'Working', PartiallyFilled: 'Partly filled', Rejected: 'Rejected', Cancelled: 'Cancelled',
}

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
    <span style={{
      background: s.bg, color: s.color, padding: '2px 8px', borderRadius: 2, fontSize: F.xs, fontWeight: 500,
      whiteSpace: 'nowrap',
    }}>
      {STATE_LABEL[state]}
    </span>
  )
}

// ── Card wrapper ───────────────────────────────────────────────
// `data-card` lets a theme restyle every card at once (the Studio look frosts them; themeStyles.ts).
export function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div data-card="" style={{
      background: C.surface2, border: `1px solid ${C.border}`,
      borderRadius: 6, padding: 16, minWidth: 0, ...style,
    }}>
      {children}
    </div>
  )
}

// ── Card title ─────────────────────────────────────────────────
/** A card's heading; `right` sits at the far end of the line (a hint, a toggle, a count). */
export function CardTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  const title = (
    <div data-card-title="" style={{ fontSize: F.sm, color: C.muted, letterSpacing: '.05em', fontWeight: 600 }}>
      {children}
    </div>
  )
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, marginBottom: 12 }}>
      {title}
      {right && <div style={{ fontSize: F.xs, color: C.dim, textAlign: 'right' }}>{right}</div>}
    </div>
  )
}

// ── Page header ────────────────────────────────────────────────
/**
 * Every page's title line: a large title, one line saying what the page is for, and the page's actions.
 * `about` is the longer explanation, folded away behind "About this page" so the page opens on its data.
 */
export function PageHeader({ title, subtitle, actions, about, badge }: {
  title: React.ReactNode
  subtitle?: React.ReactNode
  actions?: React.ReactNode
  about?: React.ReactNode
  badge?: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <h1 data-page-title="" style={{ fontSize: F.xl, fontWeight: 600, color: C.text, margin: 0 }}>{title}</h1>
            {badge}
          </div>
          {subtitle && (
            <div style={{ fontSize: F.base, color: C.muted, marginTop: 6, maxWidth: 900, lineHeight: 1.5 }}>
              {subtitle}
              {about && (
                <button onClick={() => setOpen((o) => !o)} aria-expanded={open} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4, marginLeft: 8, padding: 0, border: 'none',
                  background: 'none', color: C.blue, fontSize: F.base, fontFamily: 'inherit', cursor: 'pointer',
                }}>
                  {open ? 'Less' : 'About this page'}
                  <ChevronDown size={14} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform .2s' }} />
                </button>
              )}
            </div>
          )}
        </div>
        {actions && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>{actions}</div>}
      </div>
      {about && open && (
        <div style={{
          marginTop: 12, padding: '12px 16px', borderRadius: 6, background: C.surface, border: `1px solid ${C.border2}`,
          fontSize: F.base, color: C.muted, lineHeight: 1.6, maxWidth: 980,
        }}>
          {about}
        </div>
      )}
    </div>
  )
}

/** A small rounded label next to a title or in a row: `tone` colours it. */
export function Pill({ tone, children, title }: { tone: string; children: React.ReactNode; title?: string }) {
  return (
    <span title={title} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 2,
      fontSize: F.xs, fontWeight: 600, whiteSpace: 'nowrap',
      background: tint(tone, 14), color: tone, border: `1px solid ${tint(tone, 35)}`,
    }}>
      {children}
    </span>
  )
}

// ── Buttons ────────────────────────────────────────────────────
type ButtonTone = 'primary' | 'secondary' | 'danger' | 'warning'

/** The app's button: primary (filled accent), secondary (quiet fill), danger, warning. */
export function Button({ tone = 'secondary', icon, children, style, ...rest }: {
  tone?: ButtonTone
  icon?: React.ReactNode
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const look: Record<ButtonTone, React.CSSProperties> = {
    primary:   { background: C.blue, color: C.onAccent, border: '1px solid transparent' },
    secondary: { background: C.surface2, color: C.text, border: `1px solid ${C.border}` },
    danger:    { background: C.red, color: '#fff', border: '1px solid transparent' },
    warning:   { background: tint(C.orange, 14), color: C.orange, border: `1px solid ${tint(C.orange, 40)}` },
  }
  return (
    <button
      {...rest}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, height: 36, padding: '0 16px',
        borderRadius: 4, fontSize: F.base, fontWeight: 600, fontFamily: 'inherit', whiteSpace: 'nowrap',
        cursor: rest.disabled ? 'not-allowed' : 'pointer', opacity: rest.disabled ? 0.5 : 1,
        ...look[tone], ...style,
      }}
    >
      {icon}
      {children}
    </button>
  )
}

// ── Stat tile ──────────────────────────────────────────────────
/** One number with its label (and an optional line under it), for the rows of key figures at the top of a page. */
export function Stat({ label, value, color, sub }: {
  label: React.ReactNode
  value: React.ReactNode
  color?: string
  sub?: React.ReactNode
}) {
  return (
    <Card style={{ padding: '14px 16px' }}>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: F.xxl, fontWeight: 600, color: color ?? C.text, lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6 }}>{sub}</div>}
    </Card>
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
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

// ── Real money (EXCHANGE_MODE=mainnet) ─────────────────────────
/** True when the gateway trades real funds (any venue in mainnet mode). */
export function useRealMoney(): boolean {
  return useDataStore((s) => s.venues.some((v) => v.mode === 'mainnet'))
}

/** On real money, a one-click action that spends money asks first; in the sandbox it just runs. */
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
