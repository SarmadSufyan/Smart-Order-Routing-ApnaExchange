import React, { useEffect, useRef, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { api, NBBO, VenueQuote } from '../services/api'
import { useDataStore } from '../stores/dataStore'
import { venueName } from '../adapters'
import { SYMBOLS } from '../mockData'
import { fmtPrice, fmtSize, spreadBps } from '../format'

type FlashMap = Record<string, 'up' | 'down' | null>

function fmtSpread(n: NBBO): string {
  const abs = Math.abs(n.spread)
  const dollars = abs > 0 ? fmtPrice(abs) : '$0'
  return `${n.spread < 0 ? '-' : ''}${dollars} · ${spreadBps(n.best_bid, n.best_ask).toFixed(2)} bps`
}

function priceChange(curr: number, prev: number): 'up' | 'down' | null {
  if (!prev || prev === curr) return null
  return curr > prev ? 'up' : 'down'
}

export function MarketData() {
  const [allNbbo, setAllNbbo] = useState<Record<string, NBBO>>({})
  const [prevNbbo, setPrevNbbo] = useState<Record<string, NBBO>>({})
  const [selectedSymbol, setSelectedSymbol] = useState<string>(SYMBOLS[0])
  const venues = useDataStore((s) => s.venues)
  const venueIds = venues.map((v) => v.venue_id)
  const [flash, setFlash] = useState<FlashMap>({})
  const [lastUpdate, setLastUpdate] = useState<string>('—')
  const [paused, setPaused] = useState(false)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    let mounted = true

    async function fetchAll() {
      try {
        const res = await api.getAllNBBO()
        if (!mounted) return
        const next = res.nbbo
        // Compute flash for any symbol whose mid price changed
        setPrevNbbo(allNbbo)
        const newFlash: FlashMap = {}
        Object.entries(next).forEach(([sym, nbbo]) => {
          const prev = allNbbo[sym]
          if (!prev) return
          const currMid = (nbbo.best_bid + nbbo.best_ask) / 2
          const prevMid = (prev.best_bid + prev.best_ask) / 2
          newFlash[sym] = priceChange(currMid, prevMid)
        })
        setFlash(newFlash)
        setAllNbbo(next)
        setLastUpdate(new Date().toLocaleTimeString())
        // Clear flash after a moment
        setTimeout(() => setFlash({}), 400)
      } catch (e) {
        // silent — keep last data
      }
    }

    if (!paused) {
      fetchAll()
      pollRef.current = window.setInterval(fetchAll, 1000)
    }

    return () => {
      mounted = false
      if (pollRef.current) window.clearInterval(pollRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused])

  const symbols = Object.keys(allNbbo).sort()
  const selectedNbbo = allNbbo[selectedSymbol]
  const venueQuotes: [string, VenueQuote][] = selectedNbbo
    ? Object.entries(selectedNbbo.venue_quotes).sort((a, b) => a[0].localeCompare(b[0]))
    : []

  const th: React.CSSProperties = {
    padding: '6px 8px', fontSize: F.xs, color: C.dim, fontWeight: 500,
    borderBottom: `1px solid ${C.border}`, textAlign: 'left',
  }
  const td: React.CSSProperties = {
    padding: '8px', fontSize: F.sm, borderBottom: `1px solid ${C.border}40`,
  }

  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 14,
      }}>
        <div>
          <div style={{ fontSize: F.lg, color: C.text }}>Market Data Aggregation</div>
          <div style={{ fontSize: F.xs, color: C.dim, marginTop: 2 }}>
            Local order books kept in sync from each exchange's {venues[0]?.data_mode ?? ''} WebSocket feed
            {venues[0]?.mode ? ` · orders execute on ${venues[0].mode}` : ''}
            {venues.some((v) => v.execution_available === false) ? ' · DATA ONLY venues count in the NBBO but receive no orders' : ''}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: F.xs, color: C.dim }}>
            {paused ? 'Paused' : 'Live'} · Last update: <span style={{ color: C.text }}>{lastUpdate}</span>
          </span>
          <button
            onClick={() => setPaused((p) => !p)}
            style={{
              padding: '5px 12px',
              border: `1px solid ${paused ? C.green : C.border}`,
              borderRadius: 4,
              background: paused ? tint(C.green, 12) : C.surface,
              color: paused ? C.green : C.muted,
              fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
            }}
          >
            {paused ? '▶ Resume' : '❚❚ Pause'}
          </button>
        </div>
      </div>

      {/* SUMMARY STRIP — one card per symbol */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${Math.max(symbols.length, 1)}, 1fr)`,
        gap: 10, marginBottom: 14,
      }}>
        {symbols.map((sym) => {
          const nbbo = allNbbo[sym]
          const mid = (nbbo.best_bid + nbbo.best_ask) / 2
          const isSel = sym === selectedSymbol
          const f = flash[sym]
          const accent = f === 'up' ? C.green : f === 'down' ? C.red : (isSel ? C.blue : C.border)
          const bg = isSel ? tint(C.blue, 10) : f === 'up' ? tint(C.green, 8) : f === 'down' ? tint(C.red, 8) : C.surface2
          return (
            <button
              key={sym}
              onClick={() => setSelectedSymbol(sym)}
              style={{
                padding: '12px 14px',
                background: bg,
                border: `1px solid ${accent}`,
                borderRadius: 6,
                textAlign: 'left',
                cursor: 'pointer',
                fontFamily: 'inherit',
                color: C.text,
                transition: 'background .2s, border-color .2s',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: F.md, fontWeight: 700 }}>{sym}</span>
                <span style={{
                  fontSize: F.xs, color: f === 'up' ? C.green : f === 'down' ? C.red : C.dim,
                  fontWeight: 600,
                }}>
                  {f === 'up' ? '▲' : f === 'down' ? '▼' : '·'}
                </span>
              </div>
              <div style={{ fontSize: F.xl, fontWeight: 700, color: C.text }}>
                {fmtPrice(mid)}
              </div>
              <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4 }}>
                Spread: <span style={{ color: nbbo.spread < 0 ? C.red : C.text }}>{spreadBps(nbbo.best_bid, nbbo.best_ask).toFixed(2)} bps</span>
              </div>
            </button>
          )
        })}
        {symbols.length === 0 && (
          <div style={{
            gridColumn: '1 / -1', padding: 30, textAlign: 'center',
            color: C.dim, fontSize: F.sm,
          }}>
            Waiting for market data… the exchange order books sync a few seconds after the gateway starts.
          </div>
        )}
      </div>

      {/* SELECTED SYMBOL — NBBO HIGHLIGHT */}
      {selectedNbbo && (
        <Card style={{ marginBottom: 14 }}>
          <CardTitle>NBBO — {selectedSymbol}</CardTitle>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12,
            marginTop: 8,
          }}>
            <div style={{
              padding: 12, borderRadius: 5,
              background: tint(C.green, 8), border: `1px solid ${tint(C.green, 30)}`,
            }}>
              <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 4 }}>Best Bid</div>
              <div style={{ fontSize: F.xl, fontWeight: 700, color: C.green }}>
                {fmtPrice(selectedNbbo.best_bid)}
              </div>
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
                {fmtSize(selectedNbbo.best_bid_size)} @ <span style={{ color: C.text, fontWeight: 600 }}>{selectedNbbo.best_bid_venue}</span>
              </div>
            </div>
            <div style={{
              padding: 12, borderRadius: 5,
              background: tint(C.red, 8), border: `1px solid ${tint(C.red, 30)}`,
            }}>
              <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 4 }}>Best Ask</div>
              <div style={{ fontSize: F.xl, fontWeight: 700, color: C.red }}>
                {fmtPrice(selectedNbbo.best_ask)}
              </div>
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
                {fmtSize(selectedNbbo.best_ask_size)} @ <span style={{ color: C.text, fontWeight: 600 }}>{selectedNbbo.best_ask_venue}</span>
              </div>
            </div>
            <div style={{
              padding: 12, borderRadius: 5,
              background: C.surface2, border: `1px solid ${C.border}`,
            }}>
              <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 4 }}>Mid Price</div>
              <div style={{ fontSize: F.xl, fontWeight: 700, color: C.text }}>
                {fmtPrice((selectedNbbo.best_bid + selectedNbbo.best_ask) / 2)}
              </div>
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>Across all venues</div>
            </div>
            <div style={{
              padding: 12, borderRadius: 5,
              background: C.surface2, border: `1px solid ${C.border}`,
            }}>
              <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 4 }}>Spread</div>
              <div style={{
                fontSize: F.xl, fontWeight: 700,
                color: selectedNbbo.spread < 0 ? C.orange : C.text,
              }}>
                {fmtSpread(selectedNbbo)}
              </div>
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>
                {selectedNbbo.spread < 0 ? 'Crossed market' : 'Bid-Ask difference'}
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* PER-VENUE QUOTES — main table */}
      <Card style={{ marginBottom: 14 }}>
        <CardTitle>PER-VENUE QUOTES — {selectedSymbol}</CardTitle>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={th}>Venue</th>
              <th style={{ ...th, textAlign: 'right' }}>Bid Price</th>
              <th style={{ ...th, textAlign: 'right' }}>Bid Size</th>
              <th style={{ ...th, textAlign: 'right' }}>Ask Price</th>
              <th style={{ ...th, textAlign: 'right' }}>Ask Size</th>
              <th style={{ ...th, textAlign: 'right' }}>Last</th>
              <th style={{ ...th, textAlign: 'right' }}>Volume</th>
              <th style={{ ...th, textAlign: 'center' }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {venueQuotes.length === 0 ? (
              <tr><td colSpan={8} style={{ ...td, textAlign: 'center', color: C.dim, padding: 24 }}>
                No quotes yet for {selectedSymbol}.
              </td></tr>
            ) : venueQuotes.map(([vid, q]) => {
              const isBestBid = vid === selectedNbbo?.best_bid_venue
              const isBestAsk = vid === selectedNbbo?.best_ask_venue
              const prevQ = prevNbbo[selectedSymbol]?.venue_quotes?.[vid]
              const bidChange = prevQ ? priceChange(q.bid_price, prevQ.bid_price) : null
              const askChange = prevQ ? priceChange(q.ask_price, prevQ.ask_price) : null
              return (
                <tr key={vid} style={{
                  background: q.is_stale ? tint(C.orange, 5) : undefined,
                  opacity: q.is_stale ? 0.6 : 1,
                }}>
                  <td style={{ ...td, fontWeight: 600 }}>
                    {venueName(vid, venues)} <span style={{ color: C.dim, fontWeight: 400 }}>{vid}</span>
                    {venues.find((v) => v.venue_id === vid)?.execution_available === false && (
                      <span style={{ marginLeft: 6, fontSize: F.xs, color: C.blue, fontWeight: 700 }}>DATA ONLY</span>
                    )}
                  </td>
                  <td style={{
                    ...td, textAlign: 'right',
                    color: isBestBid ? C.green : C.text,
                    fontWeight: isBestBid ? 700 : 400,
                    background: bidChange === 'up' ? tint(C.green, 12) : bidChange === 'down' ? tint(C.red, 12) : undefined,
                  }}>
                    {fmtPrice(q.bid_price)} {isBestBid && <span style={{ fontSize: F.xs, color: C.green }}>★</span>}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: C.muted }}>
                    {fmtSize(q.bid_size)}
                  </td>
                  <td style={{
                    ...td, textAlign: 'right',
                    color: isBestAsk ? C.red : C.text,
                    fontWeight: isBestAsk ? 700 : 400,
                    background: askChange === 'up' ? tint(C.green, 12) : askChange === 'down' ? tint(C.red, 12) : undefined,
                  }}>
                    {fmtPrice(q.ask_price)} {isBestAsk && <span style={{ fontSize: F.xs, color: C.red }}>★</span>}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: C.muted }}>
                    {fmtSize(q.ask_size)}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: C.text }}>
                    {fmtPrice(q.last_price)}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: C.muted }}>
                    {fmtSize(q.volume)}
                  </td>
                  <td style={{ ...td, textAlign: 'center' }}>
                    {q.is_stale ? (
                      <span style={{
                        fontSize: F.xs, padding: '2px 6px', borderRadius: 3,
                        background: tint(C.orange, 15), color: C.orange,
                      }}>STALE</span>
                    ) : (
                      <span style={{
                        fontSize: F.xs, padding: '2px 6px', borderRadius: 3,
                        background: tint(C.green, 15), color: C.green,
                      }}>LIVE</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
          ★ = venue providing the National Best Bid/Offer. Highlighted price cells flashed because the price changed since the last poll.
        </div>
      </Card>

      {/* ALL SYMBOLS × ALL VENUES MATRIX */}
      <Card>
        <CardTitle>FULL MATRIX — ALL SYMBOLS × ALL VENUES (ASK PRICE / SIZE)</CardTitle>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={th}>Symbol</th>
              {venueIds.map((v) => (
                <th key={v} style={{ ...th, textAlign: 'right' }}>{venueName(v, venues)}</th>
              ))}
              <th style={{ ...th, textAlign: 'right' }}>Best Ask</th>
            </tr>
          </thead>
          <tbody>
            {symbols.map((sym) => {
              const nbbo = allNbbo[sym]
              return (
                <tr key={sym}>
                  <td style={{ ...td, fontWeight: 700, color: C.text }}>{sym}</td>
                  {venueIds.map((vid) => {
                    const q = nbbo.venue_quotes[vid]
                    if (!q) {
                      return <td key={vid} style={{ ...td, textAlign: 'right', color: C.dim }}>—</td>
                    }
                    const isBest = vid === nbbo.best_ask_venue
                    return (
                      <td key={vid} style={{
                        ...td, textAlign: 'right',
                        color: isBest ? C.green : C.text,
                        fontWeight: isBest ? 700 : 400,
                        background: isBest ? tint(C.green, 8) : undefined,
                      }}>
                        <div>{fmtPrice(q.ask_price)}</div>
                        <div style={{ fontSize: F.xs, color: C.dim }}>{fmtSize(q.ask_size)}</div>
                      </td>
                    )
                  })}
                  <td style={{ ...td, textAlign: 'right', color: C.green, fontWeight: 700 }}>
                    {fmtPrice(nbbo.best_ask)} <span style={{ fontSize: F.xs, color: C.dim, fontWeight: 400 }}>@ {nbbo.best_ask_venue}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
          Green cell = venue currently offering the best (lowest) ask price for that symbol. This is what the SOR uses to route BUY orders.
        </div>
      </Card>
    </div>
  )
}
