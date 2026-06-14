import React, { useState, useEffect } from 'react'
import { Outlet, NavLink, Navigate, useNavigate } from 'react-router-dom'
import { C } from '../theme'
import { ConnDot } from './shared'
import { useAuthStore } from '../stores/authStore'
import { useDataStore } from '../stores/dataStore'
import { backendVenueToUi } from '../adapters'

const NAV = [
  { path: '/',           icon: '📊', label: 'Dashboard'            },
  { path: '/wallet',     icon: '💼', label: 'Wallet'               },
  { path: '/howitworks', icon: '🔬', label: 'How It Works'         },
  { path: '/venues',     icon: '🌐', label: 'Venue Connectivity'   },
  { path: '/risk',       icon: '🛡', label: 'Risk Manager'         },
  { path: '/killswitch', icon: '🔔', label: 'Kill Switch & Alerts' },
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
        height: 48, background: C.surface, borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 16px', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ color: C.blue, fontSize: 16 }}>⚡</span>
          <span style={{ fontSize: 13 }}>SOR</span>
          <span style={{ background: C.surface2, borderRadius: 3, padding: '2px 8px', fontSize: 10, color: C.muted }}>
            sor-prod-01
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {/* Venue dots */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {venuesForDots.map((v) => (
              <div key={v.id} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                <ConnDot status={v.status} />
                <span style={{ fontSize: 9, color: C.dim }}>{v.shortName}</span>
              </div>
            ))}
          </div>

          {/* Clock */}
          <span style={{ fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{clock}</span>

          {/* Kill switch button */}
          <button
            onClick={() => navigate('/killswitch')}
            style={{
              padding: '4px 12px', borderRadius: 3, cursor: 'pointer', fontFamily: 'inherit',
              border: `1px solid ${killActive ? C.red : '#E24B4A66'}`,
              background: killActive ? C.red : '#E24B4A15',
              color: killActive ? '#fff' : C.red,
              fontSize: 10, letterSpacing: '.1em',
            }}
          >
            ⚡ KILL SWITCH {killActive ? 'ACTIVE' : ''}
          </button>

          {/* User & logout */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingLeft: 12, borderLeft: `1px solid ${C.border}` }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', lineHeight: 1.2 }}>
              <span style={{ fontSize: 11, color: C.text }}>{user?.name || user?.username}</span>
              <span style={{ fontSize: 9, color: C.dim, letterSpacing: '.05em' }}>{user?.role}</span>
            </div>
            <button
              onClick={handleLogout}
              style={{
                padding: '4px 10px', borderRadius: 3, border: `1px solid ${C.border}`,
                background: C.surface2, color: C.muted, fontSize: 10,
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
          width: collapsed ? 56 : 220,
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
                style={({ isActive }) => ({
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '9px 16px', fontSize: 12, textDecoration: 'none',
                  color: isActive ? C.text : C.muted,
                  background: isActive ? C.surface2 : undefined,
                  borderLeft: `2px solid ${isActive ? C.blue : 'transparent'}`,
                  whiteSpace: 'nowrap', transition: 'all .15s',
                })}
              >
                <span style={{ fontSize: 15, flexShrink: 0, width: 18, textAlign: 'center' }}>
                  {item.icon}
                </span>
                {!collapsed && <span>{item.label}</span>}
              </NavLink>
            ))}
          </div>

          <button
            onClick={() => setCollapsed((c) => !c)}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              padding: 12, borderTop: `1px solid ${C.border}`,
              background: 'none', border: 'none',
              color: C.dim, cursor: 'pointer', fontSize: 16,
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
