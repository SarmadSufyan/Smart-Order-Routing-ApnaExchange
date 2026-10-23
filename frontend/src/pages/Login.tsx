import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Lock, User, Zap } from 'lucide-react'
import { C, F, tint } from '../theme'
import { useAuthStore } from '../stores/authStore'
import { api, API_BASE, SERVER_LABEL } from '../services/api'
import type { GatewayHealth } from '../services/api'
import { environmentLabel } from '../adapters'
import { sentence } from '../format'
import { MQ, useMediaQuery } from '../hooks/useMediaQuery'

// Quick sign-in, shown only for users the gateway says still accept the sandbox login
// (password = user name; GET /api/auth/demo-logins) — none on a machine with real passwords.
const DEMO_ACCOUNTS = [
  { user: 'admin',    pass: 'admin',    role: 'Admin'        },
  { user: 'trader1',  pass: 'trader1',  role: 'Trader'       },
  { user: 'risk_mgr', pass: 'risk_mgr', role: 'Risk manager' },
]

export function Login() {
  const navigate = useNavigate()
  const login   = useAuthStore((s) => s.login)
  const error   = useAuthStore((s) => s.error)
  const loading = useAuthStore((s) => s.loading)
  const isNarrow = useMediaQuery(MQ.narrow)

  const [user, setUser]       = useState('')
  const [pass, setPass]       = useState('')
  const [demoUsers, setDemoUsers] = useState<string[]>([])
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
  const realMoney = health?.exchange_mode === 'mainnet'

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
    const tick = () => setClock(new Date().toISOString().slice(11, 19) + ' UTC')
    tick()
    const c = setInterval(tick, 1000)
    return () => clearInterval(c)
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!user || !pass) return
    const ok = await login(user, pass)
    if (ok) navigate('/')
  }

  const label: React.CSSProperties = { fontSize: F.sm, color: C.muted, fontWeight: 500, marginBottom: 6, display: 'block' }
  const field: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 10, height: 44, padding: '0 14px', marginBottom: 14,
    background: C.surface, border: `1px solid ${C.border}`, borderRadius: 4,
  }
  const input: React.CSSProperties = {
    flex: 1, minWidth: 0, background: 'none', border: 'none', outline: 'none', boxShadow: 'none',
    color: C.text, fontSize: F.md, fontFamily: 'inherit',
  }

  const envPill = health && (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 2,
      fontSize: F.xs, fontWeight: 600,
      background: realMoney ? C.red : tint(C.orange, 14), color: realMoney ? '#fff' : C.orange,
      border: `1px solid ${realMoney ? C.red : tint(C.orange, 35)}`,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: realMoney ? '#fff' : C.orange }} />
      {realMoney ? 'Real money' : sentence(environmentLabel(health.exchange_mode))}
    </span>
  )

  // LEFT — what this is, and the exchanges the gateway is connected to right now
  const hero = (
    <div style={{ flex: '1 1 420px', maxWidth: 520, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 28 }}>
        <span style={{
          width: 44, height: 44, borderRadius: 13, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          background: `linear-gradient(135deg, ${C.blue}, ${C.purple})`, boxShadow: `0 8px 24px ${tint(C.blue, 35)}`,
        }}>
          <Zap size={24} color="#fff" strokeWidth={2.2} fill="#fff" />
        </span>
        <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em' }}>ApnaSOR</span>
        {envPill}
      </div>
      <h1 style={{
        fontSize: isNarrow ? 30 : 42, lineHeight: 1.1, fontWeight: 700, letterSpacing: '-0.03em', margin: '0 0 14px',
      }}>
        One order.<br />Every exchange.<br /><span style={{ color: C.muted }}>The best price.</span>
      </h1>
      <p style={{ fontSize: F.md, color: C.muted, lineHeight: 1.6, margin: '0 0 28px', maxWidth: 440 }}>
        Apna Exchange's smart order router: live order books from real crypto exchanges, pre-trade risk on every
        order, and price-protected child orders split across venues.
      </p>

      {!isNarrow && (
        <div data-card="" style={{
          background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 6, padding: '6px 0', maxWidth: 440,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 16px 6px', fontSize: F.xs, color: C.dim }}>
            <span>Exchanges</span>
            <span>{clock}</span>
          </div>
          {exchanges.length === 0 ? (
            <div style={{ padding: '10px 16px', fontSize: F.sm, color: reachable === false ? C.red : C.muted }}>
              {reachable === false ? 'Gateway not reachable' : 'Connecting…'}
            </div>
          ) : exchanges.map(([vid, x]) => (
            <div key={vid} style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px', fontSize: F.sm,
              borderTop: `1px solid ${C.border2}`,
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: x.feed_connected ? C.green : C.red,
              }} />
              <span style={{ fontWeight: 600, width: 84 }}>{x.name}</span>
              <span style={{ color: x.feed_connected ? C.muted : C.red }}>
                {x.feed_connected ? `${x.books_synced} books live` : 'Offline'}
              </span>
              <span style={{ marginLeft: 'auto', color: C.dim, fontSize: F.xs }}>
                {x.trading_enabled ? `trades in ${environmentLabel(x.mode).toLowerCase()}` : 'market data only'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )

  // RIGHT — the sign-in card
  const card = (
    <div data-card="" style={{
      flex: '0 1 400px', width: '100%', maxWidth: 400, padding: 32,
      background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 6,
    }}>
      <div style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-0.02em', marginBottom: 6 }}>Sign in</div>
      <div style={{ fontSize: F.base, color: C.muted, marginBottom: 24 }}>Every session is recorded in the audit log.</div>

      <form onSubmit={handleSubmit}>
        <label style={label} htmlFor="login-user">User name</label>
        <div data-field="" style={field}>
          <User size={17} color={C.dim} />
          <input id="login-user" value={user} onChange={(e) => setUser(e.target.value)} style={input}
            autoComplete="username" spellCheck={false} />
        </div>

        <label style={label} htmlFor="login-pass">Password</label>
        <div data-field="" style={field}>
          <Lock size={17} color={C.dim} />
          <input id="login-pass" type="password" value={pass} onChange={(e) => setPass(e.target.value)} style={input}
            placeholder="Password" autoFocus autoComplete="current-password" />
        </div>

        {error && (
          <div role="alert" style={{
            background: tint(C.red, 10), border: `1px solid ${tint(C.red, 30)}`,
            borderRadius: 4, padding: '9px 12px', color: C.red, fontSize: F.sm, marginBottom: 14,
          }}>
            {error}
          </div>
        )}

        <button type="submit" disabled={loading || !user || !pass} style={{
          width: '100%', height: 44, border: 'none', borderRadius: 4, marginTop: 4,
          background: C.blue, color: '#fff', fontSize: F.md, fontWeight: 600, fontFamily: 'inherit',
          cursor: loading ? 'wait' : 'pointer', opacity: !user || !pass ? 0.5 : 1,
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
        }}>
          {loading ? 'Signing in…' : <>Continue <ArrowRight size={17} /></>}
        </button>

        {demoAccounts.length > 0 && <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '22px 0 12px' }}>
            <div style={{ flex: 1, height: 1, background: C.border }} />
            <span style={{ fontSize: F.xs, color: C.dim }}>Sandbox quick sign-in</span>
            <div style={{ flex: 1, height: 1, background: C.border }} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
            {demoAccounts.map((acct) => {
              const picked = user === acct.user
              return (
                <button key={acct.user} type="button" onClick={() => { setUser(acct.user); setPass(acct.pass) }} style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, padding: '9px 6px',
                  borderRadius: 4, fontFamily: 'inherit', cursor: 'pointer',
                  border: `1px solid ${picked ? C.blue : C.border}`, background: picked ? tint(C.blue, 12) : C.surface,
                }}>
                  <span style={{ fontSize: F.sm, fontWeight: 600, color: C.text }}>{acct.user}</span>
                  <span style={{ fontSize: F.xs, color: C.dim }}>{acct.role}</span>
                </button>
              )
            })}
          </div>
        </>}
      </form>
    </div>
  )

  return (
    <div style={{
      minHeight: '100vh', height: '100%', overflowY: 'auto', color: C.text,
      background: 'var(--page-bg, var(--c-bg))', backgroundAttachment: 'fixed',
      display: 'flex', flexDirection: 'column',
    }}>
      <main style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap',
        gap: isNarrow ? 32 : 72, padding: isNarrow ? '40px 20px' : '48px 40px',
      }}>
        {hero}
        {card}
      </main>
      <footer style={{
        display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8,
        padding: '14px 24px', fontSize: F.xs, color: C.dim,
      }}>
        <span>© 2026 ApnaSOR · Apna Exchange · UITU CS14</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: reachable === false ? C.red : C.green }} />
          {SERVER_LABEL ? `${SERVER_LABEL} · ` : ''}{reachable === false ? 'Not reachable:' : 'Connected to'} {new URL(API_BASE).host}
        </span>
      </footer>
    </div>
  )
}
