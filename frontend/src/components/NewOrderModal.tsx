import React, { useState, useEffect, useRef } from 'react'
import { C, F, tint } from '../theme'
import { useDataStore } from '../stores/dataStore'
import { backendVenueToUi, isVenueBlacklisted } from '../adapters'
import { SYMBOLS } from '../mockData'
import { RoutingProof } from './RoutingProof'
import type { Order as BackendOrder } from '../services/api'
import type { OrderSide } from '../types'

interface NewOrderForm {
  symbol:    string
  side:      OrderSide
  orderType: 'Market' | 'Limit'
  qty:       string
  price:     string
  venue:     string
  tif:       'DAY' | 'GTC' | 'IOC' | 'FOK'
}

interface ValidationErrors {
  symbol?: string
  qty?:    string
  price?:  string
}

interface NewOrderModalProps {
  open:    boolean
  onClose: () => void
  /** Pre-fill the form (for the Demo Liquidity Sweep button). */
  initialSymbol?: string
  initialSide?: OrderSide
  initialQty?: string
  /** Custom label shown above the form (e.g. "DEMO: Liquidity Sweep"). */
  banner?: string
}

const RISK_CHECKS = [
  'KillSwitch check',
  'Symbol restriction check',
  'Fat finger check',
  'Position limit check',
  'Notional limit check',
  'Rate limit check',
]

type Stage = 'form' | 'checking' | 'routing' | 'done' | 'rejected'

export function NewOrderModal({
  open, onClose,
  initialSymbol = 'AAPL',
  initialSide = 'BUY',
  initialQty = '',
  banner,
}: NewOrderModalProps) {
  const submitOrder = useDataStore((s) => s.submitOrder)
  const backendVenues = useDataStore((s) => s.venues)
  const nbbos = useDataStore((s) => s.nbbos)
  const riskStatus = useDataStore((s) => s.riskStatus)

  const venues = backendVenues.map(backendVenueToUi)
  const killActive = riskStatus?.kill_switch_active ?? false

  const [form, setForm] = useState<NewOrderForm>({
    symbol:    initialSymbol,
    side:      initialSide,
    orderType: 'Market',
    qty:       initialQty,
    price:     '',
    venue:     'auto',
    tif:       'DAY',
  })

  const [errors, setErrors] = useState<ValidationErrors>({})
  const [stage, setStage] = useState<Stage>('form')
  const [checkIdx, setCheckIdx] = useState(0)
  const [resultOrder, setResultOrder] = useState<BackendOrder | null>(null)
  const [rejectReason, setRejectReason] = useState<string>('')

  // Keep the API call's response so the animation can finish before showing it
  const responseRef = useRef<BackendOrder | null>(null)
  const responseErrRef = useRef<string | null>(null)

  useEffect(() => {
    if (open) {
      setForm({
        symbol: initialSymbol, side: initialSide, orderType: 'Market',
        qty: initialQty, price: '', venue: 'auto', tif: 'DAY',
      })
      setErrors({})
      setStage('form')
      setCheckIdx(0)
      setResultOrder(null)
      setRejectReason('')
      responseRef.current = null
      responseErrRef.current = null
    }
  }, [open, initialSymbol, initialSide, initialQty])

  // Drive the animated check sequence
  useEffect(() => {
    if (stage !== 'checking') return
    if (checkIdx < RISK_CHECKS.length) {
      const t = setTimeout(() => setCheckIdx((i) => i + 1), 280)
      return () => clearTimeout(t)
    }
    // All animated checks done → show routing while we wait for backend (if still pending)
    const t = setTimeout(() => {
      const err = responseErrRef.current
      const order = responseRef.current
      if (err) {
        setRejectReason(err)
        setStage('rejected')
      } else if (order) {
        setResultOrder(order)
        if (order.status === 'REJECTED') {
          setRejectReason(order.rejection_reason || 'Rejected by risk engine')
          setStage('rejected')
        } else {
          setStage('routing')
          setTimeout(() => setStage('done'), 1200)
        }
      } else {
        // Backend still pending — give it more time
        setStage('routing')
      }
    }, 350)
    return () => clearTimeout(t)
  }, [stage, checkIdx])

  // When in 'routing' but no response yet, keep waiting
  useEffect(() => {
    if (stage !== 'routing') return
    if (resultOrder) return
    const interval = setInterval(() => {
      const err = responseErrRef.current
      const order = responseRef.current
      if (err) {
        clearInterval(interval)
        setRejectReason(err)
        setStage('rejected')
      } else if (order) {
        clearInterval(interval)
        setResultOrder(order)
        if (order.status === 'REJECTED') {
          setRejectReason(order.rejection_reason || 'Rejected by risk engine')
          setStage('rejected')
        } else {
          setTimeout(() => setStage('done'), 1000)
        }
      }
    }, 200)
    return () => clearInterval(interval)
  }, [stage, resultOrder])

  function validate(): boolean {
    const errs: ValidationErrors = {}
    if (!form.symbol) errs.symbol = 'Symbol is required'
    const qty = parseInt(form.qty)
    if (!form.qty || isNaN(qty) || qty <= 0) errs.qty = 'Enter a valid quantity'
    if (qty > 100000) errs.qty = 'Quantity exceeds fat finger limit (100,000)'
    if (form.orderType === 'Limit') {
      const price = parseFloat(form.price)
      if (!form.price || isNaN(price) || price <= 0) errs.price = 'Enter a valid price for limit order'
      if (price > 100000) errs.price = 'Price exceeds fat finger limit'
    }
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  async function handleSubmit() {
    if (!validate()) return

    setStage('checking')
    setCheckIdx(0)
    responseRef.current = null
    responseErrRef.current = null

    // Fire the real backend call in parallel with the animation
    try {
      const order = await submitOrder(
        form.symbol,
        form.side,
        parseInt(form.qty),
        form.orderType === 'Market' ? 'MARKET' : 'LIMIT',
        form.orderType === 'Limit' ? parseFloat(form.price) : undefined,
      )
      if (order) {
        responseRef.current = order
      } else {
        responseErrRef.current = 'Order submission failed (no response)'
      }
    } catch (e) {
      responseErrRef.current = (e as Error).message
    }
  }

  function update<K extends keyof NewOrderForm>(field: K, val: NewOrderForm[K]) {
    setForm((prev) => ({ ...prev, [field]: val }))
    if (errors[field as keyof ValidationErrors]) {
      setErrors((prev) => ({ ...prev, [field]: undefined }))
    }
  }

  if (!open) return null

  // Indicative price from live NBBO (best ask for BUY, best bid for SELL)
  const nbbo = nbbos[form.symbol]
  const indicativePrice = nbbo
    ? form.side === 'BUY' ? nbbo.best_ask : nbbo.best_bid
    : 0
  const indicativeVenue = nbbo
    ? form.side === 'BUY' ? nbbo.best_ask_venue : nbbo.best_bid_venue
    : '—'

  // Resolved venue label after fill
  const resolvedVenue =
    resultOrder?.child_orders?.[0]?.venue_id
      ? venues.find((v) => v.shortName === resultOrder!.child_orders[0].venue_id)?.name
        ?? resultOrder!.child_orders[0].venue_id
      : '—'

  // ── Styles ──
  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, zIndex: 1000,
    background: 'var(--modal-overlay)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    padding: 16,
  }
  const modal: React.CSSProperties = {
    background: C.surface, border: `1px solid ${C.border}`,
    borderRadius: 8, width: '100%', maxWidth: 520,
    fontFamily: "'Consolas','IBM Plex Mono',monospace",
    color: C.text, maxHeight: '90vh', overflowY: 'auto',
  }
  const header: React.CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '14px 18px', borderBottom: `1px solid ${C.border}`,
  }
  const body: React.CSSProperties = { padding: '18px' }
  const label: React.CSSProperties = {
    fontSize: F.xs, color: C.dim, letterSpacing: '.1em',
    textTransform: 'uppercase', marginBottom: 4, display: 'block',
  }
  const input: React.CSSProperties = {
    width: '100%', background: C.bg, border: `1px solid ${C.border}`,
    borderRadius: 4, padding: '8px 10px', color: C.text,
    fontSize: F.base, fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box',
  }
  const inputErr: React.CSSProperties = { ...input, borderColor: C.red }
  const select: React.CSSProperties = { ...input, cursor: 'pointer' }
  const errMsg: React.CSSProperties = { fontSize: F.xs, color: C.red, marginTop: 3 }
  const row2: React.CSSProperties = {
    display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12,
  }
  const fieldWrap: React.CSSProperties = { marginBottom: 12 }

  // ── FORM ──
  if (stage === 'form') return (
    <div style={overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div style={modal}>
        <div style={header}>
          <div>
            <div style={{ fontSize: F.md, fontWeight: 700 }}>New Order</div>
            <div style={{ fontSize: F.xs, color: C.dim, marginTop: 2 }}>
              All orders subject to pre-trade risk checks
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: C.dim, cursor: 'pointer', fontSize: F.xl }}>✕</button>
        </div>

        <div style={body}>

          {banner && (
            <div style={{
              background: tint(C.blue, 14), border: `1px solid ${C.blue}`,
              borderRadius: 4, padding: '8px 12px', marginBottom: 14,
              color: C.blue, fontSize: F.sm, letterSpacing: '.05em',
            }}>
              ⚡ {banner}
            </div>
          )}

          {killActive && (
            <div style={{
              background: tint(C.red, 10), border: `1px solid ${tint(C.red, 50)}`,
              borderRadius: 4, padding: '10px 12px', marginBottom: 14,
              color: C.red, fontSize: F.sm,
            }}>
              ⚠ Kill switch is ACTIVE — orders will be rejected
            </div>
          )}

          {/* BUY / SELL toggle */}
          <div style={fieldWrap}>
            <span style={label}>Side</span>
            <div style={{ display: 'flex', gap: 8 }}>
              {(['BUY', 'SELL'] as OrderSide[]).map((s) => (
                <button
                  key={s} onClick={() => update('side', s)}
                  style={{
                    flex: 1, height: 40, border: 'none', borderRadius: 4,
                    cursor: 'pointer', fontFamily: 'inherit', fontSize: F.md, fontWeight: 700,
                    background: form.side === s
                      ? s === 'BUY' ? C.green : C.red
                      : C.surface2,
                    color: form.side === s ? '#fff' : C.dim,
                    transition: 'all .15s',
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {/* Symbol + Order Type */}
          <div style={row2}>
            <div>
              <span style={label}>Symbol</span>
              <select value={form.symbol} onChange={(e) => update('symbol', e.target.value)} style={select}>
                {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              {errors.symbol && <div style={errMsg}>{errors.symbol}</div>}
            </div>
            <div>
              <span style={label}>Order Type</span>
              <select value={form.orderType} onChange={(e) => update('orderType', e.target.value as NewOrderForm['orderType'])} style={select}>
                <option value="Market">Market</option>
                <option value="Limit">Limit</option>
              </select>
            </div>
          </div>

          {/* Qty + Price */}
          <div style={row2}>
            <div>
              <span style={label}>Quantity (shares)</span>
              <input
                type="number" min="1" max="100000"
                placeholder="e.g. 500"
                value={form.qty}
                onChange={(e) => update('qty', e.target.value)}
                style={errors.qty ? inputErr : input}
              />
              {errors.qty && <div style={errMsg}>{errors.qty}</div>}
            </div>
            <div>
              <span style={label}>
                {form.orderType === 'Market' ? 'Market (auto-price)' : 'Limit Price (USD)'}
              </span>
              <input
                type="number" min="0.01" step="0.01"
                placeholder={form.orderType === 'Market' ? `~ ${indicativePrice.toFixed(2)}` : 'e.g. 182.50'}
                value={form.price}
                disabled={form.orderType === 'Market'}
                onChange={(e) => update('price', e.target.value)}
                style={{
                  ...(errors.price ? inputErr : input),
                  opacity: form.orderType === 'Market' ? 0.5 : 1,
                }}
              />
              {errors.price && <div style={errMsg}>{errors.price}</div>}
            </div>
          </div>

          {/* Venue + TIF */}
          <div style={row2}>
            <div>
              <span style={label}>Venue routing</span>
              <select value={form.venue} onChange={(e) => update('venue', e.target.value)} style={select}>
                <option value="auto">⚡ Auto — Best Price (SOR)</option>
                {venues.filter((v) => !v.blacklisted).map((v) => (
                  <option key={v.id} value={v.shortName}>
                    {v.name} {v.status === 'Degraded' ? '⚠ Degraded' : '● Connected'}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <span style={label}>Time in Force</span>
              <select value={form.tif} onChange={(e) => update('tif', e.target.value as NewOrderForm['tif'])} style={select}>
                <option value="DAY">DAY</option>
                <option value="GTC">GTC</option>
                <option value="IOC">IOC</option>
                <option value="FOK">FOK</option>
              </select>
            </div>
          </div>

          {/* Indicative pricing from live NBBO */}
          {nbbo && (
            <div style={{
              background: C.bg, border: `1px solid ${C.border}`,
              borderRadius: 4, padding: '10px 12px', marginBottom: 12,
              fontSize: F.sm, color: C.muted,
            }}>
              <div style={{ color: C.dim, marginBottom: 4, fontSize: 10 }}>LIVE NBBO (across 5 venues)</div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>Best Bid: <span style={{ color: C.green }}>${nbbo.best_bid.toFixed(2)}</span> @ <span style={{ color: C.blue }}>{nbbo.best_bid_venue}</span></span>
                <span>Best Ask: <span style={{ color: C.red }}>${nbbo.best_ask.toFixed(2)}</span> @ <span style={{ color: C.blue }}>{nbbo.best_ask_venue}</span></span>
              </div>
              {form.qty && (
                <div style={{ marginTop: 6, fontSize: F.xs, color: C.dim }}>
                  Indicative {form.side === 'BUY' ? 'buy' : 'sell'} @{' '}
                  <span style={{ color: C.text }}>${indicativePrice.toFixed(2)}</span>{' '}
                  on <span style={{ color: C.blue }}>{indicativeVenue}</span>{' '}
                  · Est. notional{' '}
                  <span style={{ color: C.text }}>
                    ${(parseInt(form.qty || '0') * indicativePrice).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Order summary preview */}
          {form.qty && (
            <div style={{
              background: C.bg, border: `1px solid ${C.border}`,
              borderRadius: 4, padding: '10px 12px', marginBottom: 14,
              fontSize: F.sm, color: C.muted,
            }}>
              <div style={{ color: C.dim, marginBottom: 4, fontSize: 10 }}>ORDER PREVIEW</div>
              <span style={{ color: form.side === 'BUY' ? C.green : C.red, fontWeight: 700 }}>{form.side} </span>
              <span style={{ color: C.text }}>{parseInt(form.qty || '0').toLocaleString()} </span>
              <span style={{ color: C.text }}>{form.symbol} </span>
              {form.orderType === 'Limit' && form.price && (
                <span>@ <span style={{ color: C.text }}>${parseFloat(form.price).toFixed(2)} </span></span>
              )}
              <span style={{ color: C.blue }}>{form.orderType} </span>
              <span style={{ color: C.dim }}>{form.tif}</span>
            </div>
          )}

          <button
            onClick={handleSubmit}
            disabled={killActive}
            style={{
              width: '100%', height: 42, border: 'none', borderRadius: 4,
              background: killActive ? C.dim : form.side === 'BUY' ? C.green : C.red,
              color: '#fff', fontSize: F.md, fontWeight: 700,
              cursor: killActive ? 'not-allowed' : 'pointer', fontFamily: 'inherit', letterSpacing: '.05em',
              transition: 'opacity .15s', opacity: killActive ? 0.6 : 1,
            }}
          >
            {killActive ? 'KILL SWITCH ACTIVE — TRADING DISABLED' : `Submit ${form.side} Order →`}
          </button>

          <div style={{ textAlign: 'center', fontSize: F.xs, color: C.dim, marginTop: 8 }}>
            Order will pass 6 pre-trade risk checks before routing
          </div>
        </div>
      </div>
    </div>
  )

  // ── CHECKING ──
  if (stage === 'checking') return (
    <div style={overlay}>
      <div style={{ ...modal, maxWidth: 400 }}>
        <div style={header}>
          <div style={{ fontSize: F.md, fontWeight: 700 }}>Running Pre-Trade Checks</div>
        </div>
        <div style={{ padding: 20 }}>
          <div style={{ marginBottom: 16, fontSize: F.sm, color: C.dim }}>
            {form.side} {parseInt(form.qty).toLocaleString()} {form.symbol}
            {form.orderType === 'Limit' && ` @ $${parseFloat(form.price).toFixed(2)}`}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {RISK_CHECKS.map((check, i) => {
              const done = i < checkIdx
              const running = i === checkIdx
              return (
                <div key={check} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 12px', background: C.bg,
                  borderRadius: 4, fontSize: F.sm,
                  border: `1px solid ${running ? C.blue : done ? tint(C.green, 24) : C.border}`,
                  transition: 'border .2s',
                }}>
                  <span style={{
                    width: 16, height: 16, borderRadius: '50%',
                    background: done ? C.green : running ? C.blue : C.surface2,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: F.xs, color: '#fff', flexShrink: 0, transition: 'background .2s',
                  }}>
                    {done ? '✓' : running ? '…' : ''}
                  </span>
                  <span style={{ color: done ? C.green : running ? C.text : C.dim }}>
                    {check}
                  </span>
                  {done && <span style={{ marginLeft: 'auto', color: C.green, fontSize: 10 }}>PASS</span>}
                  {running && <span style={{ marginLeft: 'auto', color: C.blue, fontSize: 10 }}>checking…</span>}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )

  // ── ROUTING ──
  if (stage === 'routing') return (
    <div style={overlay}>
      <div style={{ ...modal, maxWidth: 400 }}>
        <div style={{ padding: 32, textAlign: 'center' }}>
          <div style={{ fontSize: F.xxl, marginBottom: 12 }}>⚡</div>
          <div style={{ fontSize: F.md, fontWeight: 700, marginBottom: 6 }}>Routing Order</div>
          <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 16 }}>
            All 6 pre-trade checks passed
          </div>
          <div style={{ fontSize: F.base, color: C.dim }}>
            {resultOrder
              ? <>Sending to <span style={{ color: C.blue }}>{resolvedVenue}</span> via best-price strategy…</>
              : <>Routing via best-price strategy…</>}
          </div>
          <div style={{ marginTop: 16, height: 3, background: C.surface2, borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%', background: C.blue, borderRadius: 2,
              animation: 'pocProgress 1.2s ease-out forwards',
            }} />
          </div>
          <style>{`@keyframes pocProgress { from{width:0%} to{width:100%} }`}</style>
        </div>
      </div>
    </div>
  )

  // ── DONE ──
  if (stage === 'done' && resultOrder) {
    const filled = resultOrder.status === 'FILLED'
    const partial = resultOrder.status === 'PARTIALLY_FILLED'
    const working = resultOrder.status === 'WORKING'
    const stateColor = filled ? C.green : partial ? C.orange : working ? C.blue : C.muted
    const stateLabel = filled ? 'Filled' : partial ? 'Partially Filled' : working ? 'Working' : resultOrder.status

    return (
      <div style={overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div style={{ ...modal, maxWidth: 720 }}>
          <div style={{ ...header, padding: '12px 18px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{
                width: 32, height: 32, borderRadius: '50%',
                background: `${stateColor}20`, border: `2px solid ${stateColor}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: F.lg, color: stateColor,
              }}>✓</div>
              <div>
                <div style={{ fontSize: F.md, color: stateColor, fontWeight: 700 }}>Order {stateLabel}</div>
                <div style={{ fontSize: F.xs, color: C.dim }}>
                  {resultOrder.id.slice(0, 8).toUpperCase()} · {resultOrder.symbol} · {resultOrder.side} {resultOrder.quantity.toFixed(0)}
                </div>
              </div>
            </div>
            <button onClick={onClose} style={{ background: 'none', border: 'none', color: C.dim, cursor: 'pointer', fontSize: F.xl }}>✕</button>
          </div>

          <div style={{ padding: '14px 18px' }}>

            {resultOrder.routing_decision ? (
              <RoutingProof decision={resultOrder.routing_decision} symbol={resultOrder.symbol} />
            ) : (
              <div style={{ padding: 20, textAlign: 'center', color: C.dim, fontSize: 11 }}>
                No routing decision attached to this order.
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button
                onClick={() => {
                  setStage('form')
                  setCheckIdx(0)
                  setResultOrder(null)
                  setForm((prev) => ({ ...prev, qty: '', price: '' }))
                  responseRef.current = null
                  responseErrRef.current = null
                }}
                style={{
                  flex: 1, height: 38, border: `1px solid ${C.border}`,
                  borderRadius: 4, background: C.surface2, color: C.muted,
                  fontSize: F.base, cursor: 'pointer', fontFamily: 'inherit',
                }}
              >
                New Order
              </button>
              <button
                onClick={onClose}
                style={{
                  flex: 1, height: 38, border: 'none',
                  borderRadius: 4, background: stateColor, color: '#fff',
                  fontSize: F.base, cursor: 'pointer', fontFamily: 'inherit',
                }}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── REJECTED ──
  if (stage === 'rejected') return (
    <div style={overlay}>
      <div style={{ ...modal, maxWidth: 420 }}>
        <div style={{ padding: 28, textAlign: 'center' }}>

          <div style={{
            width: 56, height: 56, borderRadius: '50%',
            background: tint(C.red, 14), border: `2px solid ${C.red}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 16px', fontSize: F.xxl, color: C.red,
          }}>✕</div>

          <div style={{ fontSize: F.lg, fontWeight: 700, color: C.red, marginBottom: 4 }}>
            Order Rejected
          </div>
          <div style={{ fontSize: F.sm, color: C.dim, marginBottom: 16 }}>
            Pre-trade risk engine blocked the order
          </div>

          <div style={{
            background: tint(C.red, 8), border: `1px solid ${tint(C.red, 30)}`,
            borderRadius: 4, padding: '10px 14px', marginBottom: 20,
            fontSize: F.sm, color: C.red, textAlign: 'left',
          }}>
            <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 4 }}>REASON</div>
            {rejectReason}
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => {
                setStage('form')
                setCheckIdx(0)
                setRejectReason('')
                responseRef.current = null
                responseErrRef.current = null
              }}
              style={{
                flex: 1, height: 38, border: `1px solid ${C.border}`,
                borderRadius: 4, background: C.surface2, color: C.muted,
                fontSize: F.base, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              Edit Order
            </button>
            <button
              onClick={onClose}
              style={{
                flex: 1, height: 38, border: 'none',
                borderRadius: 4, background: C.red, color: '#fff',
                fontSize: F.base, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  )

  return null
}
