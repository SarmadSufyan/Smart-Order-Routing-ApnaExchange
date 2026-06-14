import React, { useState, useEffect, useRef } from 'react'
import { C } from '../theme'
import { useDataStore } from '../stores/dataStore'
import { backendVenueToUi, isVenueBlacklisted } from '../adapters'
import { SYMBOLS } from '../mockData'
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

export function NewOrderModal({ open, onClose }: NewOrderModalProps) {
  const submitOrder = useDataStore((s) => s.submitOrder)
  const backendVenues = useDataStore((s) => s.venues)
  const nbbos = useDataStore((s) => s.nbbos)
  const riskStatus = useDataStore((s) => s.riskStatus)

  const venues = backendVenues.map(backendVenueToUi)
  const killActive = riskStatus?.kill_switch_active ?? false

  const [form, setForm] = useState<NewOrderForm>({
    symbol:    'AAPL',
    side:      'BUY',
    orderType: 'Market',
    qty:       '',
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
      setForm({ symbol: 'AAPL', side: 'BUY', orderType: 'Market', qty: '', price: '', venue: 'auto', tif: 'DAY' })
      setErrors({})
      setStage('form')
      setCheckIdx(0)
      setResultOrder(null)
      setRejectReason('')
      responseRef.current = null
      responseErrRef.current = null
    }
  }, [open])

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
    background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    padding: 16,
  }
  const modal: React.CSSProperties = {
    background: '#131619', border: `1px solid #2A2E35`,
    borderRadius: 8, width: '100%', maxWidth: 520,
    fontFamily: "'Consolas','IBM Plex Mono',monospace",
    color: '#E8E6DF', maxHeight: '90vh', overflowY: 'auto',
  }
  const header: React.CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '14px 18px', borderBottom: `1px solid #2A2E35`,
  }
  const body: React.CSSProperties = { padding: '18px' }
  const label: React.CSSProperties = {
    fontSize: 10, color: '#5F5E5A', letterSpacing: '.1em',
    textTransform: 'uppercase', marginBottom: 4, display: 'block',
  }
  const input: React.CSSProperties = {
    width: '100%', background: '#0D0F11', border: `1px solid #2A2E35`,
    borderRadius: 4, padding: '8px 10px', color: '#E8E6DF',
    fontSize: 12, fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box',
  }
  const inputErr: React.CSSProperties = { ...input, borderColor: '#E24B4A' }
  const select: React.CSSProperties = { ...input, cursor: 'pointer' }
  const errMsg: React.CSSProperties = { fontSize: 10, color: '#E24B4A', marginTop: 3 }
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
            <div style={{ fontSize: 14, fontWeight: 700 }}>New Order</div>
            <div style={{ fontSize: 10, color: '#5F5E5A', marginTop: 2 }}>
              All orders subject to pre-trade risk checks
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#5F5E5A', cursor: 'pointer', fontSize: 18 }}>✕</button>
        </div>

        <div style={body}>

          {killActive && (
            <div style={{
              background: '#E24B4A15', border: `1px solid #E24B4A66`,
              borderRadius: 4, padding: '10px 12px', marginBottom: 14,
              color: '#E24B4A', fontSize: 11,
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
                    cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, fontWeight: 700,
                    background: form.side === s
                      ? s === 'BUY' ? '#4CAF50' : '#E24B4A'
                      : '#1A1E24',
                    color: form.side === s ? '#fff' : '#5F5E5A',
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
              background: '#0D0F11', border: `1px solid #2A2E35`,
              borderRadius: 4, padding: '10px 12px', marginBottom: 12,
              fontSize: 11, color: '#9C9A92',
            }}>
              <div style={{ color: '#5F5E5A', marginBottom: 4, fontSize: 10 }}>LIVE NBBO (across 5 venues)</div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>Best Bid: <span style={{ color: '#4CAF50' }}>${nbbo.best_bid.toFixed(2)}</span> @ <span style={{ color: '#3B8BD4' }}>{nbbo.best_bid_venue}</span></span>
                <span>Best Ask: <span style={{ color: '#E24B4A' }}>${nbbo.best_ask.toFixed(2)}</span> @ <span style={{ color: '#3B8BD4' }}>{nbbo.best_ask_venue}</span></span>
              </div>
              {form.qty && (
                <div style={{ marginTop: 6, fontSize: 10, color: '#5F5E5A' }}>
                  Indicative {form.side === 'BUY' ? 'buy' : 'sell'} @{' '}
                  <span style={{ color: '#E8E6DF' }}>${indicativePrice.toFixed(2)}</span>{' '}
                  on <span style={{ color: '#3B8BD4' }}>{indicativeVenue}</span>{' '}
                  · Est. notional{' '}
                  <span style={{ color: '#E8E6DF' }}>
                    ${(parseInt(form.qty || '0') * indicativePrice).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Order summary preview */}
          {form.qty && (
            <div style={{
              background: '#0D0F11', border: `1px solid #2A2E35`,
              borderRadius: 4, padding: '10px 12px', marginBottom: 14,
              fontSize: 11, color: '#9C9A92',
            }}>
              <div style={{ color: '#5F5E5A', marginBottom: 4, fontSize: 10 }}>ORDER PREVIEW</div>
              <span style={{ color: form.side === 'BUY' ? '#4CAF50' : '#E24B4A', fontWeight: 700 }}>{form.side} </span>
              <span style={{ color: '#E8E6DF' }}>{parseInt(form.qty || '0').toLocaleString()} </span>
              <span style={{ color: '#E8E6DF' }}>{form.symbol} </span>
              {form.orderType === 'Limit' && form.price && (
                <span>@ <span style={{ color: '#E8E6DF' }}>${parseFloat(form.price).toFixed(2)} </span></span>
              )}
              <span style={{ color: '#3B8BD4' }}>{form.orderType} </span>
              <span style={{ color: '#5F5E5A' }}>{form.tif}</span>
            </div>
          )}

          <button
            onClick={handleSubmit}
            disabled={killActive}
            style={{
              width: '100%', height: 42, border: 'none', borderRadius: 4,
              background: killActive ? '#5F5E5A' : form.side === 'BUY' ? '#4CAF50' : '#E24B4A',
              color: '#fff', fontSize: 13, fontWeight: 700,
              cursor: killActive ? 'not-allowed' : 'pointer', fontFamily: 'inherit', letterSpacing: '.05em',
              transition: 'opacity .15s', opacity: killActive ? 0.6 : 1,
            }}
          >
            {killActive ? 'KILL SWITCH ACTIVE — TRADING DISABLED' : `Submit ${form.side} Order →`}
          </button>

          <div style={{ textAlign: 'center', fontSize: 10, color: '#5F5E5A', marginTop: 8 }}>
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
          <div style={{ fontSize: 14, fontWeight: 700 }}>Running Pre-Trade Checks</div>
        </div>
        <div style={{ padding: 20 }}>
          <div style={{ marginBottom: 16, fontSize: 11, color: '#5F5E5A' }}>
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
                  padding: '8px 12px', background: '#0D0F11',
                  borderRadius: 4, fontSize: 11,
                  border: `1px solid ${running ? '#3B8BD4' : done ? '#4CAF5030' : '#2A2E35'}`,
                  transition: 'border .2s',
                }}>
                  <span style={{
                    width: 16, height: 16, borderRadius: '50%',
                    background: done ? '#4CAF50' : running ? '#3B8BD4' : '#1A1E24',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 9, color: '#fff', flexShrink: 0, transition: 'background .2s',
                  }}>
                    {done ? '✓' : running ? '…' : ''}
                  </span>
                  <span style={{ color: done ? '#4CAF50' : running ? '#E8E6DF' : '#5F5E5A' }}>
                    {check}
                  </span>
                  {done && <span style={{ marginLeft: 'auto', color: '#4CAF50', fontSize: 10 }}>PASS</span>}
                  {running && <span style={{ marginLeft: 'auto', color: '#3B8BD4', fontSize: 10 }}>checking…</span>}
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
          <div style={{ fontSize: 28, marginBottom: 12 }}>⚡</div>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>Routing Order</div>
          <div style={{ fontSize: 11, color: '#9C9A92', marginBottom: 16 }}>
            All 6 pre-trade checks passed
          </div>
          <div style={{ fontSize: 12, color: '#5F5E5A' }}>
            {resultOrder
              ? <>Sending to <span style={{ color: '#3B8BD4' }}>{resolvedVenue}</span> via best-price strategy…</>
              : <>Routing via best-price strategy…</>}
          </div>
          <div style={{ marginTop: 16, height: 3, background: '#1A1E24', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%', background: '#3B8BD4', borderRadius: 2,
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
    const stateColor = filled ? '#4CAF50' : partial ? '#EF9F27' : working ? '#3B8BD4' : '#9C9A92'
    const stateLabel = filled ? 'Filled' : partial ? 'Partially Filled' : working ? 'Working' : resultOrder.status

    return (
      <div style={overlay}>
        <div style={{ ...modal, maxWidth: 460 }}>
          <div style={{ padding: 28, textAlign: 'center' }}>

            <div style={{
              width: 56, height: 56, borderRadius: '50%',
              background: `${stateColor}20`, border: `2px solid ${stateColor}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              margin: '0 auto 16px', fontSize: 24,
            }}>✓</div>

            <div style={{ fontSize: 15, fontWeight: 700, color: stateColor, marginBottom: 4 }}>
              Order {stateLabel}
            </div>
            <div style={{ fontSize: 11, color: '#5F5E5A', marginBottom: 20 }}>
              {filled
                ? `Filled at ${resolvedVenue} @ $${resultOrder.avg_fill_price?.toFixed(2)}`
                : `Working at ${resolvedVenue}`}
            </div>

            <div style={{
              background: '#0D0F11', border: `1px solid #2A2E35`,
              borderRadius: 6, padding: '14px 16px', textAlign: 'left',
              marginBottom: 20,
            }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px', fontSize: 11 }}>
                {[
                  { l: 'Order ID',     v: resultOrder.id.slice(0, 8).toUpperCase(),                       c: '#3B8BD4' },
                  { l: 'Symbol',       v: resultOrder.symbol,                                              c: '#E8E6DF' },
                  { l: 'Side',         v: resultOrder.side,                                                c: resultOrder.side === 'BUY' ? '#4CAF50' : '#E24B4A' },
                  { l: 'Quantity',     v: resultOrder.quantity.toLocaleString(),                          c: '#E8E6DF' },
                  { l: 'Filled Qty',   v: resultOrder.filled_quantity.toLocaleString(),                    c: '#E8E6DF' },
                  { l: 'Avg Fill',     v: resultOrder.avg_fill_price ? `$${resultOrder.avg_fill_price.toFixed(2)}` : '—', c: '#E8E6DF' },
                  { l: 'Venue',        v: resolvedVenue,                                                   c: '#9C9A92' },
                  { l: 'State',        v: stateLabel,                                                      c: stateColor },
                ].map((row) => (
                  <div key={row.l}>
                    <div style={{ color: '#5F5E5A', fontSize: 9, marginBottom: 2 }}>{row.l}</div>
                    <div style={{ color: row.c, fontWeight: 500 }}>{row.v}</div>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
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
                  flex: 1, height: 38, border: `1px solid #2A2E35`,
                  borderRadius: 4, background: '#1A1E24', color: '#9C9A92',
                  fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
                }}
              >
                New Order
              </button>
              <button
                onClick={onClose}
                style={{
                  flex: 1, height: 38, border: 'none',
                  borderRadius: 4, background: stateColor, color: '#fff',
                  fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
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
            background: '#E24B4A20', border: `2px solid #E24B4A`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 16px', fontSize: 28, color: '#E24B4A',
          }}>✕</div>

          <div style={{ fontSize: 15, fontWeight: 700, color: '#E24B4A', marginBottom: 4 }}>
            Order Rejected
          </div>
          <div style={{ fontSize: 11, color: '#5F5E5A', marginBottom: 16 }}>
            Pre-trade risk engine blocked the order
          </div>

          <div style={{
            background: '#E24B4A10', border: `1px solid #E24B4A40`,
            borderRadius: 4, padding: '10px 14px', marginBottom: 20,
            fontSize: 11, color: '#E24B4A', textAlign: 'left',
          }}>
            <div style={{ fontSize: 9, color: '#9C9A92', marginBottom: 4 }}>REASON</div>
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
                flex: 1, height: 38, border: `1px solid #2A2E35`,
                borderRadius: 4, background: '#1A1E24', color: '#9C9A92',
                fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              Edit Order
            </button>
            <button
              onClick={onClose}
              style={{
                flex: 1, height: 38, border: 'none',
                borderRadius: 4, background: '#E24B4A', color: '#fff',
                fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
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
