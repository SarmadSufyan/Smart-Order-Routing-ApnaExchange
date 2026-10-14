import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { C, F, tint } from '../theme'
import { BOOT_LINES } from '../mockData'
import { useAuthStore } from '../stores/authStore'
import { api, API_BASE, SERVER_LABEL } from '../services/api'
import type { GatewayHealth } from '../services/api'
import { environmentLabel } from '../adapters'

// Quick sign-in, shown only for users the gateway says still accept the sandbox login
// (password = user name; GET /api/auth/demo-logins) — none on a machine with real passwords.
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

  const [user, setUser]       = useState('')
  const [pass, setPass]       = useState('')
  const [demoUsers, setDemoUsers] = useState<string[]>([])
  const [bootIdx, setBootIdx] = useState(0)
  const [clock, setClock]     = useState('')
  const [health, setHealth]   = useState<GatewayHealth | null>(null)
  const [reachable, setReachable] = useState<boolean | null>(null)

  // The gateway's public health check: each exchange's feed and the environment, before anyone logs in.
  useEffect(() => {
    let stopped = false
    const load = () => api.health()
      .then((h) => { if (!stopped) { setHealth(h); setReachable(true) } })
      .catch(() => { if (!stopped) setReachable(false) })
    load()
    const t = setInterval(load, 5000)
    return () => { stopped = true; clearInterval(t) }
  }, [])
  const exchanges = Object.entries(health?.exchanges ?? {})

  useEffect(() => {
    // The gateway may still be starting when this page opens: ask again until it answers.
    let retry: ReturnType<typeof setTimeout> | undefined
    let stopped = false
    const ask = () => api.getDemoLogins()
      .then((r) => {
        if (stopped) return
        setDemoUsers(r.demo_logins)
        if (r.demo_logins.includes('admin')) {  // pre-fill only while admin / admin really works
          setUser((u) => u || 'admin')
          setPass((p) => p || 'admin')
        }
      })
      .catch(() => { if (!stopped) retry = setTimeout(ask, 2000) })
    ask()
    return () => { stopped = true; clearTimeout(retry) }
  }, [])
  const demoAccounts = DEMO_ACCOUNTS.filter((a) => demoUsers.includes(a.user))

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
    color: C.text, fontSize: F.base, fontFamily: 'inherit',
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
          <span style={{ color: C.accent, fontSize: 18 }}></span>
          <span style={{ fontSize: F.md, letterSpacing: '.08em' }}>SOR // COMMAND CENTER</span>
        </div>

        <div>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.15em', marginBottom: 8 }}>
            // SYSTEM BOOT
          </div>
          <div style={{ minHeight: 90 }}>
            {BOOT_LINES.slice(0, bootIdx).map((line, i) => (
              <div key={i} style={{
                fontSize: F.sm, lineHeight: 1.8,
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
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.15em', marginBottom: 6 }}>
            // VENUE GATEWAYS
          </div>
          <div style={{ border: `1px solid ${C.border}`, borderRadius: 4, overflow: 'hidden' }}>
            {exchanges.length === 0 ? (
              <div style={{ padding: '6px 12px', background: C.surface, fontSize: F.sm, color: reachable === false ? C.red : C.muted }}>
                {reachable === false ? 'Gateway not reachable' : 'Connecting…'}
              </div>
            ) : exchanges.map(([vid, x]) => {
              const dotColor = x.feed_connected ? C.green : C.red
              return (
                <div key={vid} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '6px 12px', borderBottom: `1px solid ${C.border2}`,
                  background: C.surface, fontSize: F.sm,
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: dotColor, display: 'inline-block' }} />
                  <span style={{ width: 90 }}>{x.name}</span>
                  <span style={{ color: dotColor }}>{x.feed_connected ? 'CONNECTED' : 'OFFLINE'}</span>
                  <span style={{ marginLeft: 'auto', color: C.muted }}>
                    {x.books_synced} books · {x.trading_enabled ? `exec: ${environmentLabel(x.mode)}` : 'market data only'}
                  </span>
                </div>
              )
            })}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 16, fontSize: F.xs, color: C.dim }}>
          <span>ENV {health ? environmentLabel(health.exchange_mode).toUpperCase() : '—'}</span>
          <span>GATEWAY {new URL(API_BASE).host}</span>
          <span style={{ marginLeft: 'auto', color: C.muted }}>{clock}</span>
        </div>
      </div>

      {/* RIGHT — auth form */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32 }}>
        <div style={{ width: '100%', maxWidth: 360 }}>

          <div style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <span style={{ color: C.accent }}></span>
              <span style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.15em' }}>SECURE ACCESS</span>
            </div>
            <div style={{ fontSize: F.xxl, letterSpacing: '-.3px', marginBottom: 4 }}>Sign in to continue</div>
            <div style={{ fontSize: F.base, color: C.muted }}>
              Operator credentials required. All sessions are audited.
            </div>
          </div>

          <form onSubmit={handleSubmit}>
            <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 4 }}>OPERATOR ID</div>
            <div style={fieldStyle}>
              <span style={{ color: C.dim }}></span>
              <input
                value={user}
                onChange={(e) => setUser(e.target.value)}
                style={inputStyle}
                autoComplete="username"
              />
            </div>

            <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 4 }}>PASSPHRASE</div>
            <div style={fieldStyle}>
              <span style={{ color: C.dim }}></span>
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
                background: tint(C.red, 8), border: `1px solid #E24B4A40`,
                borderRadius: 4, padding: '8px 12px', color: C.red, fontSize: F.sm, marginBottom: 10,
              }}>
                 {error}
              </div>
            )}

            <button type="submit" disabled={loading} style={{
              width: '100%', height: 40, border: 'none', borderRadius: 4,
              background: loading ? C.surface : C.accent,
              color: loading ? C.muted : C.bg,
              fontSize: F.base, letterSpacing: '.1em', cursor: loading ? 'wait' : 'pointer', fontFamily: 'inherit',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}>
              {loading ? 'AUTHENTICATING…' : 'CONTINUE >'}
            </button>

            {demoAccounts.length > 0 && <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '14px 0' }}>
                <div style={{ flex: 1, height: 1, background: C.border }} />
                <span style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.15em' }}>SANDBOX QUICK SIGN-IN</span>
                <div style={{ flex: 1, height: 1, background: C.border }} />
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {demoAccounts.map((acct) => (
                  <button
                    key={acct.user}
                    type="button"
                    onClick={() => fillDemo(acct.user, acct.pass)}
                    style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '7px 12px', border: `1px solid ${C.border}`,
                      borderRadius: 4, background: C.surface, color: C.text,
                      fontSize: F.sm, fontFamily: 'inherit', cursor: 'pointer',
                    }}
                  >
                    <span style={{ color: C.text }}>{acct.user}</span>
                    <span style={{ color: C.dim, fontSize: F.xs }}>{acct.role}</span>
                  </button>
                ))}
              </div>
            </>}
          </form>

          <div style={{
            marginTop: 28, paddingTop: 12, borderTop: `1px solid ${C.border2}`,
            display: 'flex', justifyContent: 'space-between', fontSize: F.xs, color: C.dim,
          }}>
            <span>© 2026 APNASOR · APNA EXCHANGE</span>
            <span>
              <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: reachable === false ? C.red : C.green, marginRight: 4 }} />
              {SERVER_LABEL ? `${SERVER_LABEL} · ` : ''}{reachable === false ? 'Not reachable:' : 'Connected to'} {new URL(API_BASE).host}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
