import React, { useEffect, useRef, useState } from 'react'
import { C, F, tint } from '../theme'
import { confirmRealMoney, useRealMoney } from './shared'
import { api } from '../services/api'
import type { AnalysisCard, CopilotStatus, CopilotTurn } from '../services/api'
import { SYMBOLS } from '../mockData'
import { fmtPct, fmtPrice, fmtQty, fmtUsd } from '../format'

// The copilot: ask in plain English. Answers and analyses come from the system's own numbers (the language model
// only words them); every order, algorithm or bot it proposes waits for Confirm — except, in the sandbox with Auto
// on, a small proposal without warnings. Real money always asks.

const EXAMPLES = [
  'Should I buy BTC?',
  'Any good trade right now?',
  'How is my portfolio doing?',
  'Which techniques worked on ETH?',
  'Buy $20 of ETH over 2 minutes',
  'What is RSI?',
]

type Entry = { id: string; question: string; turn: CopilotTurn | null; error?: string; analysisOnly?: AnalysisCard }

const STATUS_COLOR: Record<string, string> = {
  PROPOSED: C.blue, ANSWERED: C.green, CLARIFY: C.orange, REFUSED: C.red, CONFIRMED: C.green,
  FAILED: C.red, CANCELLED: C.dim, EXPIRED: C.dim,
}

function Verdict({ a }: { a: AnalysisCard }) {
  const color = a.verdict === 'BUY' ? C.green : a.verdict === 'REDUCE' ? C.red : C.muted
  const t = a.trade
  return (
    <div style={{ border: `1px solid ${tint(color, 45)}`, borderRadius: 4, padding: 8, marginTop: 8, background: tint(color, 5) }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, color, fontSize: F.base }}>{a.verdict.replace('_', ' ')} {a.symbol.split('/')[0]}</span>
        <span style={{ fontSize: F.xs, color: C.muted }}>evidence {a.score > 0 ? '+' : ''}{a.score} · confidence {a.confidence}</span>
      </div>
      {t && t.stop !== undefined && (
        <div style={{ fontSize: F.xs, color: C.text, marginTop: 4 }}>
          Buy ~{fmtQty(t.quantity)} ({fmtUsd(t.notional_usdt)}) at ~{fmtPrice(t.entry)} · stop {fmtPrice(t.stop)} · target {fmtPrice(t.target)}
        </div>
      )}
      {a.for.length > 0 && (
        <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: F.xs, color: C.green, lineHeight: 1.5 }}>
          {a.for.map((r) => <li key={r}><span style={{ color: C.text }}>{r}</span></li>)}
        </ul>
      )}
      {a.against.length > 0 && (
        <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: F.xs, color: C.red, lineHeight: 1.5 }}>
          {a.against.map((r) => <li key={r}><span style={{ color: C.text }}>{r}</span></li>)}
        </ul>
      )}
      {a.notes.length > 0 && (
        <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4, lineHeight: 1.5 }}>{a.notes.join(' · ')}</div>
      )}
      {a.techniques && a.techniques.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.xs, marginTop: 6 }}>
          <tbody>
            {a.techniques.map((r) => (
              <tr key={r.name} title={r.why}>
                <td style={{ padding: '2px 4px', color: C.text }}>{r.name}</td>
                <td style={{ padding: '2px 4px', color: r.signal === 'in' ? C.green : C.muted }}>{r.signal.toUpperCase()}</td>
                <td style={{ padding: '2px 4px', textAlign: 'right', color: C.muted }}>{fmtPct(r.return_recent)} recent</td>
                <td style={{ padding: '2px 4px', color: r.verdict === 'worked' ? C.green : r.verdict === 'mixed' ? C.orange : C.dim }}>{r.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {a.overview && a.overview.length > 1 && (
        <div style={{ fontSize: F.xs, color: C.muted, marginTop: 6, lineHeight: 1.5 }}>
          {a.overview.map((c) => `${c.symbol.split('/')[0]}: ${c.verdict.replace('_', ' ')}`).join(' · ')}
        </div>
      )}
      <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6 }}>{a.disclaimer}</div>
    </div>
  )
}

/** `top`: where the panel starts — below the top bar on wide screens, so the kill switch stays in view. */
export function CopilotPanel({ open, onClose, top = 0 }: { open: boolean; onClose: () => void; top?: number }) {
  const realMoney = useRealMoney()
  const [status, setStatus] = useState<CopilotStatus | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [auto, setAuto] = useState(true)
  const [quickCoin, setQuickCoin] = useState<string>(SYMBOLS[0])
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    api.copilotStatus().then(setStatus).catch(() => setStatus(null))
  }, [open])
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }) }, [entries])

  const update = (id: string, patch: Partial<Entry>) =>
    setEntries((list) => list.map((e) => (e.id === id ? { ...e, ...patch } : e)))

  async function send(message: string) {
    const q = message.trim()
    if (!q || busy) return
    const id = `${Date.now()}`
    setEntries((list) => [...list, { id, question: q, turn: null }])
    setText('')
    setBusy(true)
    try {
      update(id, { turn: await api.copilotPropose(q, auto && !!status?.auto_allowed) })
    } catch (e) {
      update(id, { error: (e as Error).message })
    }
    setBusy(false)
  }

  async function quickAnalysis() {
    const id = `${Date.now()}`
    setEntries((list) => [...list, { id, question: `Quick analysis of ${quickCoin} (no AI)`, turn: null }])
    setBusy(true)
    try {
      update(id, { analysisOnly: await api.copilotAnalysis(quickCoin) })
    } catch (e) {
      update(id, { error: (e as Error).message })
    }
    setBusy(false)
  }

  async function act(entry: Entry, confirm: boolean) {
    const t = entry.turn!
    if (confirm && !confirmRealMoney(realMoney, t.message)) return
    try {
      update(entry.id, { turn: confirm ? await api.copilotConfirm(t.id) : await api.copilotCancel(t.id) })
    } catch (e) {
      update(entry.id, { error: (e as Error).message })
    }
  }

  if (!open) return null
  const available = status?.available ?? false
  const button = (color: string): React.CSSProperties => ({
    padding: '5px 12px', borderRadius: 4, border: `1px solid ${color}`, background: tint(color, 12), color,
    fontFamily: 'inherit', fontSize: F.sm, fontWeight: 700, cursor: 'pointer',
  })

  return (
    <aside aria-label="Copilot" style={{
      position: 'fixed', top, right: 0, bottom: 0, zIndex: 120, width: 440, maxWidth: '100vw',
      background: C.surface, borderLeft: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column',
      boxShadow: '-8px 0 24px rgba(0, 0, 0, .35)',
    }}>
      <div style={{ padding: '12px 14px', borderBottom: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: C.blue, fontSize: F.lg }}>✦</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: F.md, fontWeight: 700 }}>Copilot</div>
          <div style={{ fontSize: F.xs, color: available ? C.muted : C.orange, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
               title={status?.reason ?? ''}>
            {!status ? 'Connecting…' : available ? `${status.provider} · ${status.model} · ${status.environment}`
              : `AI off: ${status.reason}`}
          </div>
        </div>
        <button onClick={onClose} aria-label="Close the copilot" style={{ ...button(C.muted), padding: '3px 9px' }}>✕</button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 12 }}>
        {entries.length === 0 && (
          <div style={{ fontSize: F.sm, color: C.muted, lineHeight: 1.6 }}>
            Ask about a coin, your portfolio, risk, the research or a trading idea; or tell it to trade
            ({!status || available ? 'it proposes, you confirm' : 'needs an AI key — the quick analysis below works without one'}).
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
              {EXAMPLES.map((ex) => (
                <button key={ex} disabled={!available || busy} onClick={() => send(ex)}
                        style={{ ...button(C.blue), fontWeight: 400, fontSize: F.xs, opacity: available ? 1 : 0.5 }}>{ex}</button>
              ))}
            </div>
          </div>
        )}
        {entries.map((e) => {
          const t = e.turn
          return (
            <div key={e.id} style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <div style={{ background: tint(C.blue, 14), color: C.text, borderRadius: 6, padding: '6px 10px', fontSize: F.sm, maxWidth: '85%' }}>
                  {e.question}
                </div>
              </div>
              <div style={{ marginTop: 6, background: C.surface2, border: `1px solid ${C.border}`, borderRadius: 6, padding: 10 }}>
                {e.error ? <div style={{ fontSize: F.sm, color: C.red }}>{e.error}</div>
                  : e.analysisOnly ? <Verdict a={e.analysisOnly} />
                  : !t ? <div style={{ fontSize: F.sm, color: C.dim }}>Thinking — checking the live data…</div>
                  : <>
                    <div style={{ fontSize: F.xs, fontWeight: 700, color: STATUS_COLOR[t.status] ?? C.muted, letterSpacing: '.06em' }}>
                      {t.status}{t.auto_executed ? ' · RAN AUTOMATICALLY (SANDBOX)' : ''}
                    </div>
                    {t.answer && <div style={{ fontSize: F.sm, color: C.text, whiteSpace: 'pre-wrap', lineHeight: 1.55, marginTop: 4 }}>{t.answer}</div>}
                    {(!t.answer || t.kind) && <div style={{ fontSize: F.sm, color: C.text, marginTop: 4, lineHeight: 1.5 }}>{t.message}</div>}
                    {t.analysis && <Verdict a={t.analysis} />}
                    {t.conversion && <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4 }}>{t.conversion}</div>}
                    {t.warnings.map((w) => <div key={w} style={{ fontSize: F.xs, color: C.orange, marginTop: 4 }}>⚠ {w}</div>)}
                    {t.result_message && (
                      <div style={{ fontSize: F.xs, marginTop: 6, color: t.status === 'FAILED' ? C.red : C.green }}>{t.result_message}</div>
                    )}
                    {t.status === 'PROPOSED' && (
                      <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                        <button onClick={() => act(e, true)} style={button(realMoney ? C.red : C.green)}>
                          {realMoney ? 'Confirm (REAL MONEY)' : 'Confirm'}
                        </button>
                        <button onClick={() => act(e, false)} style={button(C.muted)}>Cancel</button>
                      </div>
                    )}
                  </>}
              </div>
            </div>
          )
        })}
        <div ref={bottom} />
      </div>

      <div style={{ borderTop: `1px solid ${C.border}`, padding: 10 }}>
        {status?.analysis_available && (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: F.xs, color: C.dim }}>Quick analysis (no AI):</span>
            <select value={quickCoin} onChange={(ev) => setQuickCoin(ev.target.value)}
                    style={{ background: C.surface, color: C.text, border: `1px solid ${C.border}`, borderRadius: 4, fontFamily: 'inherit', fontSize: F.xs, padding: '3px 6px' }}>
              {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button disabled={busy} onClick={quickAnalysis} style={{ ...button(C.blue), padding: '3px 10px', fontSize: F.xs }}>Analyze</button>
          </div>
        )}
        <form onSubmit={(ev) => { ev.preventDefault(); send(text) }} style={{ display: 'flex', gap: 6 }}>
          <input value={text} onChange={(ev) => setText(ev.target.value)} disabled={!available || busy} maxLength={500}
                 placeholder={!status ? 'Connecting…' : available ? 'Ask or instruct… e.g. "should I buy SOL?"' : 'Add an AI key in .env to chat'}
                 style={{ flex: 1, padding: '8px 10px', borderRadius: 4, background: C.bg, border: `1px solid ${C.border}`,
                          color: C.text, fontFamily: 'inherit', fontSize: F.sm, outline: 'none' }} />
          <button type="submit" disabled={!available || busy || !text.trim()} style={button(C.blue)}>{busy ? '…' : 'Send'}</button>
        </form>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: F.xs,
                        color: status?.auto_allowed ? C.muted : C.dim }}
               title={status?.auto_allowed ? '' : 'Auto never runs with real money'}>
          <input type="checkbox" checked={auto && !!status?.auto_allowed} disabled={!status?.auto_allowed}
                 onChange={(ev) => setAuto(ev.target.checked)} />
          {status?.auto_allowed
            ? `Auto (sandbox): run small proposals up to ${fmtUsd(status.auto_max_usdt, 0)} without the Confirm click`
            : 'Auto is off with real money: every action waits for your Confirm'}
        </label>
      </div>
    </aside>
  )
}
