import React, { useState, useEffect } from 'react'
import { Outlet, NavLink, Navigate, useNavigate, useLocation } from 'react-router-dom'
import { C, F, tint } from '../theme'
import { ConnDot } from './shared'
import { useAuthStore } from '../stores/authStore'
import { useDataStore } from '../stores/dataStore'
import { useThemeStore } from '../stores/themeStore'
import { backendVenueToUi } from '../adapters'
import { MQ, useMediaQuery } from '../hooks/useMediaQuery'

const NAV = [
  { path: '/',            label: 'Dashboard'            },
  { path: '/market-data', label: 'Market Data'          },
  { path: '/arbitrage',   label: 'Arbitrage Detector'   },
  { path: '/research',    label: 'Research & Forecasts' },
  { path: '/algos',       label: 'Algo Orders'          },
  { path: '/bots',        label: 'Bots & Lab'           },
  { path: '/wallet',      label: 'Wallet'               },
  { path: '/howitworks',  label: 'How It Works'         },
  { path: '/venues',      label: 'Venue Connectivity'   },
  { path: '/risk',        label: 'Risk Manager'         },
  { path: '/killswitch',  label: 'Kill Switch & Alerts' },
  { path: '/audit',       label: 'Audit Log'            },
]

export function AppShell() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const user            = useAuthStore((s) => s.user)
  const logout          = useAuthStore((s) => s.logout)
  const navigate        = useNavigate()

  const venues       = useDataStore((s) => s.venues)
  const riskStatus   = useDataStore((s) => s.riskStatus)
  const fetchAll     = useDataStore((s) => s.fetchAll)
  const setupWS      = useDataStore((s) => s.setupWebSocket)

  const themeMode    = useThemeStore((s) => s.mode)
  const toggleTheme  = useThemeStore((s) => s.toggleMode)

  const [collapsed, setCollapsed] = useState(false)
  const [clock, setClock] = useState('')

  // Narrow screens: below 900 px the sidebar becomes a slide-in menu (☰) so
  // pages get the full width; below 600 px the top bar drops its labels.
  // The TESTNET badge and the KILL SWITCH button stay visible at every width.
  const isPhone   = useMediaQuery(MQ.phone)
  const isDrawer  = useMediaQuery(MQ.narrow)
  const isCompact = useMediaQuery(MQ.compact)
  const [menuOpen, setMenuOpen] = useState(false)
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
    const t = setInterval(() =>
      setClock(new Date().toISOString().replace('T', ' ').slice(0, 23)), 100)
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

  const venuesForDots = venues.length > 0
    ? venues.map((v) => backendVenueToUi(v))
    : []
  const exchangeMode = venues[0]?.mode
  const showLabels = isDrawer || !collapsed

  // Shown in the top bar on wide screens, inside the slide-in menu on narrow ones.
  const themeButton = (
    <button
      onClick={toggleTheme}
      title={`Switch to ${themeMode === 'dark' ? 'light' : 'dark'} mode`}
      style={{
        padding: '5px 10px', borderRadius: 3, cursor: 'pointer', fontFamily: 'inherit',
        border: `1px solid ${C.border}`, background: C.surface2,
        color: C.text, fontSize: F.base,
        display: 'flex', alignItems: 'center', gap: 6,
      }}
    >
      <span>{themeMode === 'dark' ? '☀' : '🌙'}</span>
      <span style={{ fontSize: F.xs, color: C.muted, letterSpacing: '.05em' }}>
        {themeMode === 'dark' ? 'LIGHT' : 'DARK'}
      </span>
    </button>
  )

  const userLabel = (
    <>
      <span style={{ fontSize: F.base, color: C.text }}>{user?.name || user?.username}</span>
      <span style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.05em' }}>{user?.role}</span>
    </>
  )

  const logoutButton = (
    <button
      onClick={handleLogout}
      style={{
        padding: '5px 12px', borderRadius: 3, border: `1px solid ${C.border}`,
        background: C.surface2, color: C.muted, fontSize: F.xs,
        cursor: 'pointer', fontFamily: 'inherit', letterSpacing: '.05em',
      }}
    >
      LOGOUT
    </button>
  )

  const iconButton: React.CSSProperties = {
    background: 'none', border: `1px solid ${C.border}`, borderRadius: 4,
    color: C.text, cursor: 'pointer', fontSize: F.lg, lineHeight: 1,
    padding: '5px 9px', fontFamily: 'inherit',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: C.bg, color: C.text }}>

      <style>{`
        @keyframes pocPulse { 0%,100%{opacity:1} 50%{opacity:.3} }
        @keyframes pocBlink  { 0%,100%{opacity:1} 50%{opacity:.5} }
      `}</style>

      {/* TOPBAR — wraps onto a second line rather than cutting anything off */}
      <header style={{
        minHeight: 52, background: C.surface, borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', columnGap: 12, rowGap: 6,
        padding: isDrawer ? '6px 12px' : '0 18px', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {isDrawer && (
            <button
              onClick={() => setMenuOpen(true)}
              aria-label="Open menu"
              aria-expanded={menuOpen}
              style={iconButton}
            >
              ☰
            </button>
          )}
          <span style={{ color: C.blue, fontSize: F.xl }}>⚡</span>
          {!isPhone && <span style={{ fontSize: F.md, fontWeight: 600 }}>ApnaSOR</span>}
          {exchangeMode && (
            <span
              title={exchangeMode === 'mainnet' ? 'Orders use REAL funds' : 'Exchange testnet — fake funds, real order books'}
              style={{
                borderRadius: 3, padding: '3px 9px', fontSize: F.xs, fontWeight: 700, letterSpacing: '.08em',
                background: tint(exchangeMode === 'mainnet' ? C.red : C.orange, 16),
                color: exchangeMode === 'mainnet' ? C.red : C.orange,
                border: `1px solid ${exchangeMode === 'mainnet' ? C.red : C.orange}`,
                whiteSpace: 'nowrap',
              }}
            >
              {exchangeMode === 'mainnet' ? 'MAINNET · REAL FUNDS' : 'TESTNET'}
            </span>
          )}
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', gap: isPhone ? 10 : 14,
          flexWrap: 'wrap', justifyContent: 'flex-end', marginLeft: 'auto',
        }}>
          {/* Venue dots (labels dropped on phones; the name is in the tooltip) */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {venuesForDots.map((v) => (
              <div key={v.id} title={`${v.name} — ${v.status}`} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                <ConnDot status={v.status} />
                {!isPhone && <span style={{ fontSize: F.xs, color: C.dim }}>{v.shortName}</span>}
              </div>
            ))}
          </div>

          {/* Clock */}
          {!isCompact && (
            <span style={{ fontSize: F.sm, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{clock}</span>
          )}

          {!isDrawer && themeButton}

          {/* Kill switch button */}
          <button
            onClick={() => navigate('/killswitch')}
            style={{
              padding: '5px 14px', borderRadius: 3, cursor: 'pointer', fontFamily: 'inherit',
              border: `1px solid ${killActive ? C.red : tint(C.red, 40)}`,
              background: killActive ? C.red : tint(C.red, 10),
              color: killActive ? '#fff' : C.red,
              fontSize: F.xs, letterSpacing: '.1em', fontWeight: 600, whiteSpace: 'nowrap',
            }}
          >
            ⚡ KILL SWITCH {killActive ? 'ACTIVE' : ''}
          </button>

          {/* User & logout */}
          {!isDrawer && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingLeft: 12, borderLeft: `1px solid ${C.border}` }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', lineHeight: 1.2 }}>
                {userLabel}
              </div>
              {logoutButton}
            </div>
          )}
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Backdrop behind the slide-in menu: a tap outside closes it */}
        {isDrawer && menuOpen && (
          <div
            onClick={() => setMenuOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 99, background: 'rgba(0, 0, 0, .5)' }}
          />
        )}

        {/* SIDEBAR — a fixed column on wide screens, a slide-in menu on narrow ones */}
        <nav
          aria-label="Pages"
          style={{
            background: C.surface, borderRight: `1px solid ${C.border}`,
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
            ...(isDrawer ? {
              position: 'fixed', top: 0, left: 0, bottom: 0, zIndex: 100,
              width: 260, maxWidth: '85vw',
              transform: menuOpen ? 'translateX(0)' : 'translateX(-100%)',
              // hidden once closed, so keyboard focus can't land on off-screen links
              visibility: menuOpen ? 'visible' : 'hidden',
              transition: 'transform .2s, visibility .2s',
              boxShadow: menuOpen ? '0 0 24px rgba(0, 0, 0, .45)' : 'none',
            } : {
              width: collapsed ? 56 : 240, flexShrink: 0, transition: 'width .2s',
            }),
          }}
        >
          {isDrawer && (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '10px 12px 10px 20px', borderBottom: `1px solid ${C.border}`,
            }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: F.md, fontWeight: 600 }}>
                <span style={{ color: C.blue, fontSize: F.xl }}>⚡</span> ApnaSOR
              </span>
              <button onClick={() => setMenuOpen(false)} aria-label="Close menu" style={iconButton}>✕</button>
            </div>
          )}

          <div style={{ flex: 1, padding: '8px 0', overflowY: 'auto' }}>
            {NAV.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === '/'}
                title={item.label}
                style={({ isActive }) => ({
                  display: 'flex', alignItems: 'center',
                  padding: '13px 20px', fontSize: F.md, textDecoration: 'none',
                  color: isActive ? C.text : C.muted,
                  background: isActive ? C.surface2 : undefined,
                  borderLeft: `3px solid ${isActive ? C.blue : 'transparent'}`,
                  whiteSpace: 'nowrap', transition: 'all .15s',
                  fontWeight: isActive ? 600 : 400,
                })}
              >
                {showLabels && <span>{item.label}</span>}
                {!showLabels && <span style={{ width: '100%', textAlign: 'center' }}>{item.label.charAt(0)}</span>}
              </NavLink>
            ))}
          </div>

          {isDrawer ? (
            <div style={{
              borderTop: `1px solid ${C.border}`, padding: '12px 16px 16px 20px',
              display: 'flex', flexDirection: 'column', gap: 10,
            }}>
              <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.3 }}>{userLabel}</div>
              <div style={{ display: 'flex', gap: 8 }}>
                {themeButton}
                {logoutButton}
              </div>
            </div>
          ) : (
            <button
              onClick={() => setCollapsed((c) => !c)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                padding: 12, borderTop: `1px solid ${C.border}`,
                background: 'none', border: 'none',
                color: C.dim, cursor: 'pointer', fontSize: F.lg,
              }}
            >
              {collapsed ? '▶' : '◀'}
            </button>
          )}
        </nav>

        {/* MAIN CONTENT */}
        <main style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: isPhone ? 12 : isDrawer ? 16 : 24, background: C.bg }}>
          <Outlet />
        </main>

      </div>
    </div>
  )
}
