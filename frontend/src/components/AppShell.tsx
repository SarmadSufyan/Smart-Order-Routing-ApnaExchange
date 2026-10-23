import React, { useState, useEffect, useRef } from 'react'
import { Outlet, NavLink, Navigate, useNavigate, useLocation } from 'react-router-dom'
import {
  AlertOctagon, ArrowLeftRight, BookOpen, Bot, CandlestickChart, Check, ChevronLeft, ChevronRight,
  LayoutDashboard, LineChart, LogOut, Menu, Moon, Network, Palette, ScrollText, ShieldCheck, Sparkles, Sun,
  Wallet, Workflow, X, Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { C, F, tint } from '../theme'
import { ConnDot } from './shared'
import { CopilotPanel } from './CopilotPanel'
import { useAuthStore } from '../stores/authStore'
import { useDataStore } from '../stores/dataStore'
import { THEME_STYLES, useThemeStore } from '../stores/themeStore'
import { backendVenueToUi } from '../adapters'
import { MQ, useMediaQuery } from '../hooks/useMediaQuery'
import { API_BASE, SERVER_IS_REAL_MONEY, SERVER_LABEL } from '../services/api'

interface NavItem { path: string; label: string; icon: LucideIcon }

const NAV: { section: string; items: NavItem[] }[] = [
  { section: 'Trade', items: [
    { path: '/',            label: 'Dashboard',          icon: LayoutDashboard },
    { path: '/market-data', label: 'Market Data',        icon: CandlestickChart },
    { path: '/algos',       label: 'Algo Orders',        icon: Workflow },
    { path: '/bots',        label: 'Bots & Lab',         icon: Bot },
    { path: '/wallet',      label: 'Wallet',             icon: Wallet },
  ] },
  { section: 'Insight', items: [
    { path: '/research',    label: 'Research',           icon: LineChart },
    { path: '/arbitrage',   label: 'Arbitrage',          icon: ArrowLeftRight },
  ] },
  { section: 'Control', items: [
    { path: '/risk',        label: 'Risk Manager',       icon: ShieldCheck },
    { path: '/killswitch',  label: 'Kill Switch',        icon: AlertOctagon },
    { path: '/venues',      label: 'Venues',             icon: Network },
    { path: '/audit',       label: 'Audit Log',          icon: ScrollText },
  ] },
  { section: 'Learn', items: [
    { path: '/howitworks',  label: 'How It Works',       icon: BookOpen },
  ] },
]

const pill = (color: string, solid = false): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: 6, height: 26, padding: '0 10px', borderRadius: 999,
  fontSize: F.xs, fontWeight: 600, whiteSpace: 'nowrap',
  background: solid ? color : tint(color, 14), color: solid ? '#fff' : color,
  border: `1px solid ${solid ? color : tint(color, 30)}`,
})

const ghostButton: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, height: 32, minWidth: 32,
  padding: '0 10px', borderRadius: 10, border: `1px solid ${C.border}`, background: C.surface2,
  color: C.text, fontSize: F.sm, fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap',
}

function initials(name: string): string {
  return name.split(/[\s_]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join('') || '?'
}

/** The palette menu: the look (Studio / Liquid Glass / Terminal) and dark or light. */
function ThemeMenu({ compact }: { compact: boolean }) {
  const { mode, style, setMode, setStyle } = useThemeStore()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} title="Theme" style={ghostButton}>
        <Palette size={16} strokeWidth={1.8} />
        {!compact && <span>Theme</span>}
      </button>
      {open && (
        <div role="menu" data-glass="float" style={{
          position: 'absolute', top: 40, right: 0, zIndex: 200, width: 260, padding: 8, borderRadius: 14,
          background: C.surface, border: `1px solid ${C.border}`, boxShadow: '0 18px 48px rgba(0,0,0,.35)',
          WebkitBackdropFilter: 'saturate(180%) blur(28px)', backdropFilter: 'saturate(180%) blur(28px)',
        }}>
          <div style={{ fontSize: F.xs, color: C.dim, fontWeight: 600, padding: '6px 10px 4px' }}>Style</div>
          {THEME_STYLES.map((s) => (
            <button key={s.id} role="menuitemradio" aria-checked={style === s.id} onClick={() => setStyle(s.id)} style={{
              display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '8px 10px', borderRadius: 10,
              border: 'none', background: style === s.id ? tint(C.blue, 14) : 'transparent', cursor: 'pointer',
              textAlign: 'left', fontFamily: 'inherit', color: C.text,
            }}>
              <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 1 }}>
                <span style={{ fontSize: F.base, fontWeight: 600 }}>{s.name}</span>
                <span style={{ fontSize: F.xs, color: C.muted }}>{s.by}</span>
              </span>
              {style === s.id && <Check size={16} color={C.blue} />}
            </button>
          ))}
          <div style={{ fontSize: F.xs, color: C.dim, fontWeight: 600, padding: '10px 10px 6px' }}>Appearance</div>
          <div style={{ display: 'flex', gap: 4, padding: 3, margin: '0 6px 6px', borderRadius: 10, background: C.surface2 }}>
            {(['dark', 'light'] as const).map((m) => {
              const Icon = m === 'dark' ? Moon : Sun
              return (
                <button key={m} onClick={() => setMode(m)} aria-pressed={mode === m} style={{
                  flex: 1, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, height: 30,
                  borderRadius: 8, border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: F.sm, fontWeight: 600,
                  background: mode === m ? C.surface : 'transparent', color: mode === m ? C.text : C.muted,
                  boxShadow: mode === m ? '0 1px 3px rgba(0,0,0,.18)' : 'none',
                }}>
                  <Icon size={14} /> {m === 'dark' ? 'Dark' : 'Light'}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export function AppShell() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const user            = useAuthStore((s) => s.user)
  const logout          = useAuthStore((s) => s.logout)
  const navigate        = useNavigate()

  const venues       = useDataStore((s) => s.venues)
  const riskStatus   = useDataStore((s) => s.riskStatus)
  const fetchAll     = useDataStore((s) => s.fetchAll)
  const setupWS      = useDataStore((s) => s.setupWebSocket)

  const [collapsed, setCollapsed] = useState(false)
  const [clock, setClock] = useState('')

  // Narrow screens: below 900 px the sidebar becomes a slide-in menu so pages get the full width; below 600 px
  // the top bar drops its labels. The environment badge and the kill switch stay visible at every width.
  const isPhone   = useMediaQuery(MQ.phone)
  const isDrawer  = useMediaQuery(MQ.narrow)
  const isCompact = useMediaQuery(MQ.compact)
  const [menuOpen, setMenuOpen] = useState(false)
  const [copilotOpen, setCopilotOpen] = useState(false)
  const { pathname } = useLocation()

  // Close the menu after picking a page, and when the screen grows past drawer width.
  useEffect(() => { setMenuOpen(false) }, [pathname, isDrawer])

  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  // Boot data + WS + polling (runs only after auth)
  useEffect(() => {
    if (!isAuthenticated) return
    setupWS()
    fetchAll()
    const t = setInterval(() => fetchAll(), 3000)
    return () => clearInterval(t)
  }, [isAuthenticated, setupWS, fetchAll])

  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().slice(11, 19))
    tick()
    const t = setInterval(tick, 1000)
    return () => clearInterval(t)
  }, [])

  // Auth guard — must come after all hooks
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  const killActive = riskStatus?.kill_switch_active ?? false

  function handleLogout() {
    logout()
    navigate('/login')
  }

  const venuesForDots = venues.length > 0 ? venues.map((v) => backendVenueToUi(v)) : []
  const exchangeMode = venues[0]?.mode
  const realMoney = exchangeMode === 'mainnet'
  const showLabels = isDrawer || !collapsed
  const displayName = user?.name || user?.username || ''

  const brand = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
      <span style={{
        width: 30, height: 30, borderRadius: 9, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: `linear-gradient(135deg, ${C.blue}, ${C.purple})`, boxShadow: `0 4px 14px ${tint(C.blue, 35)}`,
      }}>
        <Zap size={17} color="#fff" strokeWidth={2.2} fill="#fff" />
      </span>
      {!isPhone && <span style={{ fontSize: F.md, fontWeight: 650, letterSpacing: '-0.01em' }}>ApnaSOR</span>}
    </span>
  )

  const userBlock = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
      <span title={displayName} style={{
        width: 32, height: 32, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: tint(C.blue, 20), color: C.blue, fontSize: F.xs, fontWeight: 700,
      }}>
        {initials(displayName)}
      </span>
      <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
        <span style={{ fontSize: F.sm, fontWeight: 600, color: C.text }}>{displayName}</span>
        <span style={{ fontSize: F.xs, color: C.muted }}>{(user?.role || '').replace('_', ' ').toLowerCase()}</span>
      </span>
    </span>
  )

  const logoutButton = (
    <button onClick={handleLogout} title="Sign out" style={ghostButton}>
      <LogOut size={15} strokeWidth={1.8} />
      {isDrawer && <span>Sign out</span>}
    </button>
  )

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100vh',
      background: 'var(--page-bg, var(--c-bg))', backgroundAttachment: 'fixed', color: C.text,
    }}>

      <style>{`
        @keyframes pocPulse { 0%,100%{opacity:1} 50%{opacity:.3} }
        @keyframes pocBlink  { 0%,100%{opacity:1} 50%{opacity:.5} }
      `}</style>

      {/* TOP BAR — wraps onto a second line rather than cutting anything off */}
      <header data-glass="chrome" style={{
        minHeight: 56, background: C.surface, borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', columnGap: 12, rowGap: 6, position: 'relative', zIndex: 20,
        padding: isDrawer ? '8px 12px' : '0 20px', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {isDrawer && (
            <button onClick={() => setMenuOpen(true)} aria-label="Open menu" aria-expanded={menuOpen} style={ghostButton}>
              <Menu size={18} />
            </button>
          )}
          {brand}
          {exchangeMode && (
            <span
              title={realMoney ? 'Orders use REAL funds'
                : 'Orders execute in the exchanges\' sandbox environments; order books are live market data'}
              style={pill(realMoney ? C.red : C.orange, realMoney)}
            >
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: realMoney ? '#fff' : C.orange }} />
              {realMoney ? 'Real money' : 'Sandbox'}
            </span>
          )}
          {SERVER_LABEL && (
            <span title={`This page shows the gateway at ${API_BASE}, not the one on this machine`}
              style={pill(SERVER_IS_REAL_MONEY ? C.red : C.blue)}>
              {SERVER_LABEL}
            </span>
          )}
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', gap: isPhone ? 8 : 10,
          flexWrap: 'wrap', justifyContent: 'flex-end', marginLeft: 'auto',
        }}>
          {/* Exchanges: a dot each (names on wide screens; the status is in the tooltip) */}
          {venuesForDots.length > 0 && (
            <div style={{
              display: 'inline-flex', alignItems: 'center', gap: isCompact ? 8 : 12, height: 32, padding: '0 12px',
              borderRadius: 999, background: C.surface2, border: `1px solid ${C.border2}`,
            }}>
              {venuesForDots.map((v) => (
                <span key={v.id} title={`${v.name} — ${v.status}`}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <ConnDot status={v.status} />
                  {!isCompact && <span style={{ fontSize: F.xs, color: C.muted, fontWeight: 500 }}>{v.name}</span>}
                </span>
              ))}
            </div>
          )}

          {!isCompact && (
            <span title="Coordinated universal time" style={{
              fontSize: F.sm, color: C.muted, fontVariantNumeric: 'tabular-nums', minWidth: 92, textAlign: 'right',
            }}>
              {clock} UTC
            </span>
          )}

          {!isDrawer && <ThemeMenu compact={isCompact} />}

          {/* The copilot: ask, analyze, propose — every action waits for a confirm */}
          <button onClick={() => setCopilotOpen((o) => !o)} aria-expanded={copilotOpen} style={{
            ...ghostButton, border: `1px solid ${tint(C.blue, 40)}`,
            background: copilotOpen ? C.blue : tint(C.blue, 12), color: copilotOpen ? '#fff' : C.blue, fontWeight: 600,
          }}>
            <Sparkles size={15} strokeWidth={2} />
            {!isPhone && <span>Copilot</span>}
          </button>

          <button onClick={() => navigate('/killswitch')} title="Kill switch" style={{
            ...ghostButton, fontWeight: 600,
            border: `1px solid ${killActive ? C.red : tint(C.red, 40)}`,
            background: killActive ? C.red : tint(C.red, 10), color: killActive ? '#fff' : C.red,
            animation: killActive ? 'pocBlink 1.6s infinite' : undefined,
          }}>
            <AlertOctagon size={15} strokeWidth={2} />
            {!isPhone && <span>{killActive ? 'Kill switch on' : 'Kill switch'}</span>}
          </button>

          {!isDrawer && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingLeft: 12, borderLeft: `1px solid ${C.border}` }}>
              {!isCompact ? userBlock : null}
              {logoutButton}
            </div>
          )}
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Backdrop behind the slide-in menu: a tap outside closes it */}
        {isDrawer && menuOpen && (
          <div onClick={() => setMenuOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 99, background: 'var(--modal-overlay)' }} />
        )}

        {/* SIDEBAR — a fixed column on wide screens, a slide-in menu on narrow ones */}
        <nav aria-label="Pages" data-glass={isDrawer ? 'float' : 'chrome'} style={{
          background: C.surface, borderRight: `1px solid ${C.border}`,
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          ...(isDrawer ? {
            position: 'fixed', top: 0, left: 0, bottom: 0, zIndex: 100, width: 272, maxWidth: '86vw',
            transform: menuOpen ? 'translateX(0)' : 'translateX(-100%)',
            // hidden once closed, so keyboard focus can't land on off-screen links
            visibility: menuOpen ? 'visible' : 'hidden',
            transition: 'transform .22s cubic-bezier(.2,.8,.2,1), visibility .22s',
            boxShadow: menuOpen ? '0 0 40px rgba(0, 0, 0, .35)' : 'none',
          } : {
            width: collapsed ? 68 : 232, flexShrink: 0, transition: 'width .2s cubic-bezier(.2,.8,.2,1)',
          }),
        }}>
          {isDrawer && (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '12px 12px 12px 16px', borderBottom: `1px solid ${C.border}`,
            }}>
              {brand}
              <button onClick={() => setMenuOpen(false)} aria-label="Close menu" style={ghostButton}><X size={16} /></button>
            </div>
          )}

          <div style={{ flex: 1, padding: '10px 0', overflowY: 'auto' }}>
            {NAV.map((group) => (
              <div key={group.section} style={{ marginBottom: 6 }}>
                {showLabels ? (
                  <div style={{ fontSize: F.xs, color: C.dim, fontWeight: 600, padding: '10px 22px 6px' }}>
                    {group.section}
                  </div>
                ) : (
                  <div style={{ height: 1, background: C.border2, margin: '10px 16px' }} />
                )}
                {group.items.map((item) => {
                  const Icon = item.icon
                  return (
                    <NavLink
                      key={item.path}
                      to={item.path}
                      end={item.path === '/'}
                      title={item.label}
                      style={({ isActive }) => ({
                        display: 'flex', alignItems: 'center', gap: 12, margin: '2px 10px',
                        padding: showLabels ? '8px 12px' : '9px 0', justifyContent: showLabels ? 'flex-start' : 'center',
                        borderRadius: 10, fontSize: F.base, textDecoration: 'none',
                        color: isActive ? C.text : C.muted,
                        background: isActive ? tint(C.blue, 15) : 'transparent',
                        fontWeight: isActive ? 600 : 500, whiteSpace: 'nowrap', transition: 'background-color .15s, color .15s',
                      })}
                    >
                      {({ isActive }) => (
                        <>
                          <Icon size={18} strokeWidth={isActive ? 2.1 : 1.8} color={isActive ? 'var(--c-blue)' : 'currentColor'} />
                          {showLabels && <span>{item.label}</span>}
                        </>
                      )}
                    </NavLink>
                  )
                })}
              </div>
            ))}
          </div>

          {isDrawer ? (
            <div style={{
              borderTop: `1px solid ${C.border}`, padding: '12px 16px 16px',
              display: 'flex', flexDirection: 'column', gap: 12,
            }}>
              {userBlock}
              <div style={{ display: 'flex', gap: 8 }}>
                <ThemeMenu compact={false} />
                {logoutButton}
              </div>
            </div>
          ) : (
            <button
              onClick={() => setCollapsed((c) => !c)}
              aria-label={collapsed ? 'Expand menu' : 'Collapse menu'}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, height: 44,
                borderTop: `1px solid ${C.border}`, borderLeft: 'none', borderRight: 'none', borderBottom: 'none',
                background: 'none', color: C.dim, cursor: 'pointer', fontFamily: 'inherit', fontSize: F.xs,
              }}
            >
              {collapsed ? <ChevronRight size={16} /> : <><ChevronLeft size={16} /> Collapse</>}
            </button>
          )}
        </nav>

        {/* MAIN CONTENT */}
        <main style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: isPhone ? 12 : isDrawer ? 16 : 24 }}>
          <Outlet />
        </main>

        {/* Stays mounted while you move between pages, so the conversation is kept */}
        <CopilotPanel open={copilotOpen} onClose={() => setCopilotOpen(false)} top={isDrawer ? 0 : 57} />

      </div>
    </div>
  )
}
