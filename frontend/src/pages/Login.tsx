import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { C } from '../theme'
import { BOOT_LINES, MOCK_VENUES } from '../mockData'
import { useAuthStore } from '../stores/authStore'

const DEMO_ACCOUNTS = [
  { user: 'admin',    pass: 'admin',    role: 'ADMIN'        },
  { user: 'trader1',  pass: 'trader1',  role: 'TRADER'       },
  { user: 'risk_mgr', pass: 'risk_mgr', role: 'RISK_MANAGER' },
]

export function Login() {
  const navigate = useNavigate()
  const login   = useAuthStore((s) => s.login)
  const error   = useAuthStore((s) => s.error)
  const loading = useAuthStore((s) => s.loading)

  const [user, setUser]       = useState('admin')
  const [pass, setPass]       = useState('admin')
  const [bootIdx, setBootIdx] = useState(0)
  const [clock, setClock]     = useState('')

  useEffect(() => {
    const b = setInterval(() => setBootIdx((i) => Math.min(i + 1, BOOT_LINES.length)), 420)
    const c = setInterval(() => setClock(new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC'), 1000)
    return () => { clearInterval(b); clearInterval(c) }
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!user || !pass) return
    const ok = await login(user, pass)
    if (ok) navigate('/')
  }

  function fillDemo(u: string, p: string) {
    setUser(u)
    setPass(p)
  }

  const fieldStyle: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 8,
    background: C.surface, border: `1px solid ${C.border}`,
    borderRadius: 4, padding: '0 12px', height: 40, marginBottom: 12,
  }
  const inputStyle: React.CSSProperties = {
    flex: 1, background: 'none', border: 'none', outline: 'none',
    color: C.text, fontSize: 12, fontFamily: 'inherit',
  }

  return (
    <div style={{ display: 'flex', height: '100vh', background: C.bg, color: C.text }}>

      {/* LEFT — terminal panel */}
      <div style={{
        width: '46%', background: '#0A0C0E',
        borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column',
        justifyContent: 'space-between', padding: 32,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: C.accent, fontSize: 18 }}>⚡</span>
          <span style={{ fontSize: 13, letterSpacing: '.08em' }}>SOR // COMMAND CENTER</span>
          <span style={{ marginLeft: 'auto', fontSize: 10, color: C.dim }}>v4.12.3</span>
        </div>

        <div>
          <div style={{ fontSize: 10, color: C.dim, letterSpacing: '.15em', marginBottom: 8 }}>
            // SYSTEM BOOT
          </div>
          <div style={{ minHeight: 90 }}>
            {BOOT_LINES.slice(0, bootIdx).map((line, i) => (
              <div key={i} style={{
                fontSize: 11, lineHeight: 1.8,
                color: i === bootIdx - 1 ? C.accent : C.muted,
              }}>
                {line}
              </div>
            ))}
            {bootIdx < BOOT_LINES.length && (
              <span style={{ display: 'inline-block', width: 8, height: 12, background: C.accent }} />
            )}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 10, color: C.dim, letterSpacing: '.15em', marginBottom: 6 }}>
            // VENUE GATEWAYS
          </div>
          <div style={{ border: `1px solid ${C.border}`, borderRadius: 4, overflow: 'hidden' }}>
            {MOCK_VENUES.map((v) => {
              const dotColor = v.status === 'Connected' ? C.green : v.status === 'Degraded' ? C.orange : C.red
              const label = v.status === 'Connected' ? 'OK' : v.status.toUpperCase()
              return (
                <div key={v.id} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '6px 12px', borderBottom: `1px solid ${C.border2}`,
                  background: C.surface, fontSize: 11,
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: dotColor, display: 'inline-block' }} />
                  <span style={{ width: 90 }}>{v.name}</span>
                  <span style={{ color: dotColor }}>{label}</span>
                  <span style={{ marginLeft: 'auto', color: C.muted }}>
                    {v.latencyEma > 0 ? `${v.latencyEma}ms` : '—'}
                  </span>
                </div>
              )
            })}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 16, fontSize: 10, color: C.dim }}>
          <span>REGION local</span>
          <span>NODE sor-p01</span>
          <span style={{ marginLeft: 'auto', color: C.muted }}>{clock}</span>
        </div>
      </div>

      {/* RIGHT — auth form */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32 }}>
        <div style={{ width: '100%', maxWidth: 360 }}>

          <div style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <span style={{ color: C.accent }}>🔒</span>
              <span style={{ fontSize: 10, color: C.dim, letterSpacing: '.15em' }}>SECURE ACCESS</span>
            </div>
            <div style={{ fontSize: 22, letterSpacing: '-.3px', marginBottom: 4 }}>Sign in to continue</div>
            <div style={{ fontSize: 12, color: C.muted }}>
              Operator credentials required. All sessions are audited.
            </div>
          </div>

          <form onSubmit={handleSubmit}>
            <div style={{ fontSize: 10, color: C.dim, letterSpacing: '.1em', marginBottom: 4 }}>OPERATOR ID</div>
            <div style={fieldStyle}>
              <span style={{ color: C.dim }}>👤</span>
              <input
                value={user}
                onChange={(e) => setUser(e.target.value)}
                style={inputStyle}
                autoComplete="username"
              />
            </div>

            <div style={{ fontSize: 10, color: C.dim, letterSpacing: '.1em', marginBottom: 4 }}>PASSPHRASE</div>
            <div style={fieldStyle}>
              <span style={{ color: C.dim }}>🔑</span>
              <input
                type="password"
                value={pass}
                onChange={(e) => setPass(e.target.value)}
                placeholder="••••••••••••"
                autoFocus
                style={{ ...inputStyle, letterSpacing: 3 }}
                autoComplete="current-password"
              />
            </div>

            {error && (
              <div style={{
                background: '#E24B4A15', border: `1px solid #E24B4A40`,
                borderRadius: 4, padding: '8px 12px', color: C.red, fontSize: 11, marginBottom: 10,
              }}>
                ⚠ {error}
              </div>
            )}

            <button type="submit" disabled={loading} style={{
              width: '100%', height: 40, border: 'none', borderRadius: 4,
              background: loading ? C.surface : C.accent,
              color: loading ? C.muted : C.bg,
              fontSize: 12, letterSpacing: '.1em', cursor: loading ? 'wait' : 'pointer', fontFamily: 'inherit',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}>
              {loading ? 'AUTHENTICATING…' : 'CONTINUE →'}
            </button>

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '14px 0' }}>
              <div style={{ flex: 1, height: 1, background: C.border }} />
              <span style={{ fontSize: 9, color: C.dim, letterSpacing: '.15em' }}>DEMO ACCOUNTS</span>
              <div style={{ flex: 1, height: 1, background: C.border }} />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {DEMO_ACCOUNTS.map((acct) => (
                <button
                  key={acct.user}
                  type="button"
                  onClick={() => fillDemo(acct.user, acct.pass)}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '7px 12px', border: `1px solid ${C.border}`,
                    borderRadius: 4, background: C.surface, color: C.text,
                    fontSize: 11, fontFamily: 'inherit', cursor: 'pointer',
                  }}
                >
                  <span style={{ color: C.text }}>{acct.user}</span>
                  <span style={{ color: C.dim, fontSize: 10 }}>{acct.role}</span>
                </button>
              ))}
            </div>
          </form>

          <div style={{
            marginTop: 28, paddingTop: 12, borderTop: `1px solid ${C.border2}`,
            display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim,
          }}>
            <span>© 2026 DEIRCP PLATFORM</span>
            <span>
              <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: C.green, marginRight: 4 }} />
              Connected to localhost:8000
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
