import React, { useState, useEffect } from 'react'
import { Outlet, NavLink, Navigate, useNavigate } from 'react-router-dom'
import { C, F, tint } from '../theme'
import { ConnDot } from './shared'
import { useAuthStore } from '../stores/authStore'
import { useDataStore } from '../stores/dataStore'
import { useThemeStore } from '../stores/themeStore'
import { backendVenueToUi } from '../adapters'

const NAV = [
  { path: '/',           label: 'Dashboard'            },
  { path: '/wallet',     label: 'Wallet'               },
  { path: '/howitworks', label: 'How It Works'         },
  { path: '/venues',     label: 'Venue Connectivity'   },
  { path: '/risk',       label: 'Risk Manager'         },
  { path: '/killswitch', label: 'Kill Switch & Alerts' },
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: C.bg, color: C.text }}>

      <style>{`
        @keyframes pocPulse { 0%,100%{opacity:1} 50%{opacity:.3} }
        @keyframes pocBlink  { 0%,100%{opacity:1} 50%{opacity:.5} }
      `}</style>

      {/* TOPBAR */}
      <header style={{
        height: 52, background: C.surface, borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 18px', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: C.blue, fontSize: F.xl }}>⚡</span>
          <span style={{ fontSize: F.md, fontWeight: 600 }}>SOR</span>
          <span style={{ background: C.surface2, borderRadius: 3, padding: '3px 9px', fontSize: F.xs, color: C.muted }}>
            sor-prod-01
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {/* Venue dots */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {venuesForDots.map((v) => (
              <div key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                <ConnDot status={v.status} />
                <span style={{ fontSize: F.xs, color: C.dim }}>{v.shortName}</span>
              </div>
            ))}
          </div>

          {/* Clock */}
          <span style={{ fontSize: F.sm, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{clock}</span>

          {/* Theme toggle */}
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

          {/* Kill switch button */}
          <button
            onClick={() => navigate('/killswitch')}
            style={{
              padding: '5px 14px', borderRadius: 3, cursor: 'pointer', fontFamily: 'inherit',
              border: `1px solid ${killActive ? C.red : tint(C.red, 40)}`,
              background: killActive ? C.red : tint(C.red, 10),
              color: killActive ? '#fff' : C.red,
              fontSize: F.xs, letterSpacing: '.1em', fontWeight: 600,
            }}
          >
            ⚡ KILL SWITCH {killActive ? 'ACTIVE' : ''}
          </button>

          {/* User & logout */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingLeft: 12, borderLeft: `1px solid ${C.border}` }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', lineHeight: 1.2 }}>
              <span style={{ fontSize: F.base, color: C.text }}>{user?.name || user?.username}</span>
              <span style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.05em' }}>{user?.role}</span>
            </div>
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
          </div>
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* SIDEBAR */}
        <nav style={{
          width: collapsed ? 56 : 240,
          background: C.surface, borderRight: `1px solid ${C.border}`,
          display: 'flex', flexDirection: 'column', flexShrink: 0,
          transition: 'width .2s', overflow: 'hidden',
        }}>
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
                {!collapsed && <span>{item.label}</span>}
                {collapsed && <span style={{ width: '100%', textAlign: 'center' }}>{item.label.charAt(0)}</span>}
              </NavLink>
            ))}
          </div>

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
        </nav>

        {/* MAIN CONTENT */}
        <main style={{ flex: 1, overflowY: 'auto', padding: 24, background: C.bg }}>
          <Outlet />
        </main>

      </div>
    </div>
  )
}
