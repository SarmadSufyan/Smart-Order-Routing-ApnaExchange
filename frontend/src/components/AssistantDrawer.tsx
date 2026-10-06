import React, { useEffect, useRef, useState } from 'react'
import { MessageSquare, Send, X, Trash2 } from 'lucide-react'
import { C, F, tint } from '../theme'
import { useAssistantStore, type ChatMessage } from '../stores/assistantStore'
import type { Proposal } from '../services/copilotApi'

/**
 * AssistantDrawer — "Ask ApnaSOR" order copilot
 * ─────────────────────────────────────────────
 * A floating button (bottom-right, every screen) that opens a side drawer.
 * The trader types an order in plain English; the backend answers with a
 * PROPOSAL. Nothing reaches an exchange until the trader presses Confirm on the
 * card, and a confirmed order still passes pre-trade risk, the kill switch and
 * the funding gate. Proposals expire after a couple of minutes.
 */

const SUGGESTIONS = [
  'Buy $50 of BTC over 2 minutes',
  'Sell 0.001 BTC at market',
  'Buy $20 of ETH',
  'Sell 0.01 ETH over 5 minutes',
]

const DRAWER_WIDTH = 400

/** Minimal inline formatting: **bold** and line breaks. No HTML is ever injected. */
function renderText(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : <React.Fragment key={i}>{part}</React.Fragment>,
  )
}

/** Backend datetimes may arrive without a zone; they are UTC. */
function parseTime(iso: string | null): number | null {
  if (!iso) return null
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(t) ? null : t
}

function fmtNum(n: number, max = 8): string {
  return String(Number(n.toFixed(max)))
}

function fmtDuration(s: number): string {
  if (s >= 3600) return `${fmtNum(s / 3600, 2)} h`
  if (s >= 60) return `${fmtNum(s / 60, 2)} min`
  return `${fmtNum(s, 1)} s`
}

function Countdown({ expiresAt, onExpired }: { expiresAt: number; onExpired?: () => void }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])
  const left = Math.max(0, Math.ceil((expiresAt - now) / 1000))
  useEffect(() => { if (left === 0) onExpired?.() }, [left, onExpired])
  if (left === 0) return <span style={{ color: C.red }}>Expired</span>
  const mm = Math.floor(left / 60)
  const ss = String(left % 60).padStart(2, '0')
  return <span>Expires in {mm}:{ss}</span>
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: F.sm, padding: '3px 0' }}>
      <span style={{ color: C.dim }}>{k}</span>
      <span style={{ color: C.text, textAlign: 'right' }}>{v}</span>
    </div>
  )
}

const cardBtn: React.CSSProperties = {
  flex: 1, height: 38, borderRadius: 12, fontSize: F.base, fontWeight: 600,
  cursor: 'pointer', fontFamily: 'inherit',
}

function ProposalCard({ m }: { m: ChatMessage }) {
  const confirm = useAssistantStore((s) => s.confirm)
  const cancel  = useAssistantStore((s) => s.cancel)
  const p: Proposal = m.proposal!
  const [expiredLocally, setExpiredLocally] = useState(false)

  const details = p.kind === 'twap' ? p.twap : p.order
  // CLARIFY / REFUSED carry no order: the message bubble is the whole answer.
  if (!details) return null

  const expiresAt = parseTime(p.expires_at)
  const live = p.status === 'PROPOSED' && !expiredLocally
  const sideColor = details.side === 'BUY' ? C.green : C.red

  return (
    <div style={{
      marginTop: 8, padding: 14, borderRadius: 18, maxWidth: '100%',
      background: C.surface2, border: `1px solid ${C.border}`,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <div style={{ fontSize: F.md, fontWeight: 700 }}>
          <span style={{ color: sideColor }}>{details.side}</span>{' '}
          {fmtNum(details.quantity)} {details.symbol}
        </div>
        <div style={{ fontSize: F.xs, color: C.dim, textTransform: 'uppercase', letterSpacing: '.06em' }}>
          {p.kind === 'twap' ? 'TWAP' : 'Order'}
        </div>
      </div>

      {p.kind === 'order' && p.order && (
        <>
          <Row k="Type" v={p.order.order_type} />
          {p.order.limit_price != null && <Row k="Limit price" v={fmtNum(p.order.limit_price)} />}
        </>
      )}
      {p.kind === 'twap' && p.twap && (
        <>
          <Row k="Window" v={fmtDuration(p.twap.duration_s)} />
          <Row k="Slices" v={p.twap.slices} />
          {p.twap.limit_price != null && <Row k="Worst price" v={fmtNum(p.twap.limit_price)} />}
        </>
      )}
      {p.reference_mid != null && <Row k="Reference mid" v={fmtNum(p.reference_mid, 6)} />}
      {p.estimated_notional_usdt != null && <Row k="Est. value" v={`≈ ${fmtNum(p.estimated_notional_usdt, 2)} USDT`} />}

      {p.conversion && (
        <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6, lineHeight: 1.45, wordBreak: 'break-word' }}>
          {p.conversion}
        </div>
      )}

      {p.warnings.length > 0 && (
        <div style={{
          marginTop: 10, padding: '8px 10px', borderRadius: 12, fontSize: F.sm, lineHeight: 1.45,
          background: tint(C.orange, 14), color: C.orange,
        }}>
          {p.warnings.map((w, i) => <div key={i}>• {w}</div>)}
        </div>
      )}

      {/* Footer: depends on where the proposal is in its life */}
      {p.status === 'PROPOSED' && (
        <>
          <div style={{ fontSize: F.xs, color: C.dim, marginTop: 10, textAlign: 'center' }}>
            {expiresAt != null
              ? <Countdown expiresAt={expiresAt} onExpired={() => setExpiredLocally(true)} />
              : 'Waiting for your confirmation'}
            {live && ' · nothing is sent until you confirm'}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button
              onClick={() => void cancel(m.id)}
              disabled={m.busy}
              style={{ ...cardBtn, background: 'none', border: `1px solid ${C.border}`, color: C.text,
                       opacity: m.busy ? 0.5 : 1 }}
            >
              Cancel
            </button>
            <button
              onClick={() => void confirm(m.id)}
              disabled={!live || m.busy}
              style={{
                ...cardBtn, border: 'none',
                background: live && !m.busy ? C.accent : C.surface2,
                color: live && !m.busy ? C.bg : C.dim,
                cursor: live && !m.busy ? 'pointer' : 'default',
              }}
            >
              {m.busy ? 'Working…' : p.kind === 'twap' ? 'Confirm TWAP' : 'Confirm order'}
            </button>
          </div>
        </>
      )}

      {p.status === 'CONFIRMED' && (
        <div style={{ marginTop: 10, fontSize: F.sm, color: C.green }}>
          ✓ Submitted{p.result_message ? ` — ${p.result_message}` : ''}
          {(p.result_order_id || p.result_algo_id) && (
            <span style={{ color: C.dim }}>
              {' '}· ref {(p.result_order_id ?? p.result_algo_id ?? '').slice(0, 8).toUpperCase()}
            </span>
          )}
        </div>
      )}
      {p.status === 'FAILED' && (
        <div style={{ marginTop: 10, fontSize: F.sm, color: C.red, lineHeight: 1.45 }}>
          Not sent — {p.result_message || 'stopped by a safety check'}
        </div>
      )}
      {p.status === 'CANCELLED' && (
        <div style={{ marginTop: 10, fontSize: F.sm, color: C.dim }}>Cancelled — nothing was sent.</div>
      )}
      {p.status === 'EXPIRED' && (
        <div style={{ marginTop: 10, fontSize: F.sm, color: C.dim }}>Expired — nothing was sent. Ask again for a fresh price.</div>
      )}

      {m.actionError && (
        <div style={{ marginTop: 8, fontSize: F.sm, color: C.red, lineHeight: 1.45 }}>{m.actionError}</div>
      )}
    </div>
  )
}

function Bubble({ m }: { m: ChatMessage }) {
  const mine = m.role === 'user'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: mine ? 'flex-end' : 'flex-start' }}>
      <div style={{
        maxWidth: '86%', padding: '10px 14px', fontSize: F.base, lineHeight: 1.5,
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        borderRadius: mine ? '18px 18px 6px 18px' : '18px 18px 18px 6px',
        background: mine ? C.blue : C.surface2,
        color: mine ? '#fff' : m.error ? C.red : C.text,
        border: mine ? 'none' : `1px solid ${C.border2}`,
      }}>
        {renderText(m.content)}
      </div>
      {m.proposal && <div style={{ width: '100%' }}><ProposalCard m={m} /></div>}
    </div>
  )
}

function TypingDots() {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start' }} aria-label="Assistant is typing">
      <div style={{
        display: 'flex', gap: 5, padding: '14px 16px', borderRadius: '18px 18px 18px 6px',
        background: C.surface2, border: `1px solid ${C.border2}`,
      }}>
        {[0, 1, 2].map((i) => (
          <span key={i} style={{
            width: 7, height: 7, borderRadius: '50%', background: C.dim,
            animation: 'pocPulse 1.2s infinite', animationDelay: `${i * 0.18}s`,
          }} />
        ))}
      </div>
    </div>
  )
}

export function AssistantDrawer() {
  const open     = useAssistantStore((s) => s.open)
  const loading  = useAssistantStore((s) => s.loading)
  const messages = useAssistantStore((s) => s.messages)
  const setOpen  = useAssistantStore((s) => s.setOpen)
  const send     = useAssistantStore((s) => s.send)
  const status   = useAssistantStore((s) => s.status)
  const loadStatus = useAssistantStore((s) => s.loadStatus)
  const clear    = useAssistantStore((s) => s.clear)

  const [draft, setDraft] = useState('')
  const listRef  = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Keep the newest message in view.
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, loading, open])

  // Focus the box when the drawer opens; Esc closes it.
  useEffect(() => {
    if (!open) return
    void loadStatus()
    inputRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen, loadStatus])

  const unavailable = status !== null && !status.available

  function submit(text: string) {
    const t = text.trim()
    if (!t || loading || unavailable) return
    setDraft('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
    void send(t)
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit(draft)
    }
  }

  function onInput(e: React.FormEvent<HTMLTextAreaElement>) {
    const el = e.currentTarget
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 110)}px`
  }

  return (
    <>
      <style>{`
        @media (prefers-reduced-motion: reduce) {
          .poc-assistant-drawer, .poc-assistant-fab { transition: opacity .2s ease !important; transform: none !important; }
        }
      `}</style>

      {/* Floating button */}
      <button
        className="glass poc-assistant-fab"
        onClick={() => setOpen(true)}
        aria-label="Open Ask ApnaSOR assistant"
        title="Ask ApnaSOR"
        style={{
          position: 'fixed', right: 24, bottom: 24, zIndex: 900,
          width: 56, height: 56, borderRadius: '50%', cursor: 'pointer',
          background: C.surface, border: `1px solid ${C.border}`, color: C.text,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          opacity: open ? 0 : 1, pointerEvents: open ? 'none' : 'auto',
          transform: open ? 'scale(.8)' : 'scale(1)',
          transition: 'opacity .2s ease, transform .3s cubic-bezier(.32,.72,0,1)',
        }}
      >
        <MessageSquare size={22} />
      </button>

      {/* Drawer */}
      <aside
        className="glass poc-assistant-drawer"
        role="dialog"
        aria-label="Ask ApnaSOR"
        aria-hidden={!open}
        style={{
          position: 'fixed', top: 64, right: 12, bottom: 12, zIndex: 1100,
          width: DRAWER_WIDTH, maxWidth: 'calc(100vw - 24px)',
          display: 'flex', flexDirection: 'column',
          // Mostly opaque so text underneath never shows through, even where blur is unavailable.
          background: 'color-mix(in srgb, var(--c-bg) 82%, transparent)', border: `1px solid ${C.border}`, borderRadius: 24,
          transform: open ? 'translateX(0)' : `translateX(${DRAWER_WIDTH + 40}px)`,
          visibility: open ? 'visible' : 'hidden',
          transition: `transform .35s cubic-bezier(.32,.72,0,1), visibility 0s linear ${open ? '0s' : '.35s'}`,
          color: C.text,
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '16px 18px 12px', borderBottom: `1px solid ${C.border2}`,
        }}>
          <div>
            <div style={{ fontSize: F.md, fontWeight: 700, letterSpacing: '-.01em' }}>Ask ApnaSOR</div>
            <div style={{ fontSize: F.xs, color: C.dim, marginTop: 2 }}>
              Nothing is sent until you confirm
            </div>
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            {messages.length > 0 && (
              <button
                onClick={clear}
                title="Clear conversation"
                aria-label="Clear conversation"
                style={iconBtn}
              >
                <Trash2 size={16} />
              </button>
            )}
            <button onClick={() => setOpen(false)} title="Close" aria-label="Close assistant" style={iconBtn}>
              <X size={18} />
            </button>
          </div>
        </div>

        {unavailable && (
          <div style={{
            margin: '10px 14px 0', padding: '8px 12px', borderRadius: 12, fontSize: F.sm, lineHeight: 1.45,
            background: tint(C.orange, 14), color: C.orange,
          }}>
            Copilot unavailable{status?.reason ? ` — ${status.reason}` : ''}.
          </div>
        )}

        {/* Messages */}
        <div ref={listRef} style={{
          flex: 1, overflowY: 'auto', padding: 16,
          display: 'flex', flexDirection: 'column', gap: 10,
        }}>
          {messages.length === 0 && (
            <div style={{ margin: 'auto 0', textAlign: 'center', padding: '0 6px' }}>
              <div style={{ fontSize: F.lg, fontWeight: 600, marginBottom: 6 }}>What would you like to trade?</div>
              <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 16, lineHeight: 1.5 }}>
                Describe an order in plain English. I'll show you exactly what would be
                sent, and nothing goes out until you press Confirm. Risk checks and the
                kill switch still apply.
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {SUGGESTIONS.map((q) => (
                  <button
                    key={q}
                    onClick={() => submit(q)}
                    style={{
                      textAlign: 'left', padding: '10px 14px', fontSize: F.sm, cursor: 'pointer',
                      borderRadius: 14, background: C.surface2, border: `1px solid ${C.border2}`,
                      color: C.text, fontFamily: 'inherit',
                    }}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => <Bubble key={m.id} m={m} />)}
          {loading && <TypingDots />}
        </div>

        {/* Composer */}
        <div style={{ padding: '10px 14px 14px', borderTop: `1px solid ${C.border2}` }}>
          <div style={{
            display: 'flex', alignItems: 'flex-end', gap: 8, padding: '6px 6px 6px 14px',
            borderRadius: 20, background: C.bg, border: `1px solid ${C.border}`,
          }}>
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              onInput={onInput}
              rows={1}
              placeholder={unavailable ? 'Copilot unavailable' : 'e.g. buy $50 of BTC over 2 minutes'}
              maxLength={500}
              disabled={unavailable}
              aria-label="Message to the assistant"
              style={{
                flex: 1, resize: 'none', background: 'none', border: 'none', outline: 'none',
                color: C.text, fontSize: F.base, fontFamily: 'inherit', lineHeight: 1.45,
                padding: '8px 0', maxHeight: 110,
              }}
            />
            <button
              onClick={() => submit(draft)}
              disabled={!draft.trim() || loading || unavailable}
              aria-label="Send"
              style={{
                width: 36, height: 36, borderRadius: '50%', border: 'none', flexShrink: 0,
                cursor: !draft.trim() || loading || unavailable ? 'default' : 'pointer',
                background: !draft.trim() || loading || unavailable ? C.surface2 : C.accent,
                color: !draft.trim() || loading || unavailable ? C.dim : C.bg,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >
              <Send size={16} />
            </button>
          </div>
          <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6, textAlign: 'center' }}>
            Enter to send · Shift+Enter for a new line
          </div>
        </div>
      </aside>
    </>
  )
}

const iconBtn: React.CSSProperties = {
  width: 32, height: 32, borderRadius: 10, border: 'none', cursor: 'pointer',
  background: 'none', color: C.muted,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
}
