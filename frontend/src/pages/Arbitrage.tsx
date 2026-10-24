import React, { useEffect, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle, PageHeader, Pill } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { wsClient } from '../services/websocket'
import { baseAsset, fmtBps, fmtPnl, fmtPrice, fmtQty, fmtSize } from '../format'
import type { ArbSymbolStats, ArbWindow } from '../services/api'
import { Loading } from '../components/Orb'

// Display order of the bucket labels (backend: DURATION_BUCKETS / GROSS_BUCKETS
// in services/arbitrage/detector.py). The API sends them in first-seen order.
const DURATION_ORDER = ['<1 s', '1-5 s', '5-30 s', '30 s-5 min', '5 min+']
const GROSS_ORDER = ['0-1 bps', '1-2 bps', '2-5 bps', '5-10 bps', '10-20 bps', '20+ bps']

function edgeColor(bps: number | null | undefined): string {
  if (bps === null || bps === undefined || Math.abs(bps) < 0.05) return C.text
  return bps > 0 ? C.green : C.red
}

function fmtDuration(s: number): string {
  if (!isFinite(s) || s < 0) return '—'
  if (s < 10) return `${s.toFixed(1)} s`
  if (s < 60) return `${Math.round(s)} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ${Math.round(s % 60)} s`
  return `${Math.floor(m / 60)} h ${m % 60} min`
}

function fmtTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleTimeString() : '—'
}

// Local time with its zone: the backend's summary sentences are in UTC.
function fmtDateTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  }) : '—'
}

function pct(part: number, whole: number): string {
  if (whole <= 0) return '—'
  const p = Math.round((100 * part) / whole)
  return p === 0 && part > 0 ? '<1%' : `${p}%`
}

interface Totals {
  scans: number
  crossed: number
  windows: number
  profitableWindows: number
  sumGross: number
  sumNet: number
  duration: Record<string, number>
  gross: Record<string, number>
}

/** Statistics of several coins added together ("All coins"). */
function addUp(stats: ArbSymbolStats[]): Totals {
  const t: Totals = { scans: 0, crossed: 0, windows: 0, profitableWindows: 0, sumGross: 0, sumNet: 0, duration: {}, gross: {} }
  for (const st of stats) {
    t.scans += st.scans
    t.crossed += st.crossed_scans
    t.windows += st.windows
    t.profitableWindows += st.profitable_windows
    t.sumGross += st.sum_best_gross_bps
    t.sumNet += st.sum_best_net_bps
    for (const [k, n] of Object.entries(st.duration_buckets)) t.duration[k] = (t.duration[k] ?? 0) + n
    for (const [k, n] of Object.entries(st.peak_gross_buckets)) t.gross[k] = (t.gross[k] ?? 0) + n
  }
  return t
}

function BucketBars({ order, counts, color }: { order: string[]; counts: Record<string, number>; color: string }) {
  const total = order.reduce((n, k) => n + (counts[k] ?? 0), 0)
  const max = Math.max(1, ...order.map((k) => counts[k] ?? 0))
  if (total === 0) {
    return <div style={{ padding: 20, textAlign: 'center', color: C.dim, fontSize: F.sm }}>No closed windows yet.</div>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {order.map((k) => {
        const n = counts[k] ?? 0
        return (
          <div key={k} style={{ display: 'grid', gridTemplateColumns: '84px minmax(0, 1fr) 96px', alignItems: 'center', gap: 10, fontSize: F.sm }}>
            <span style={{ color: C.muted, whiteSpace: 'nowrap' }}>{k}</span>
            <div style={{ height: 14, background: C.surface, borderRadius: 3, overflow: 'hidden' }}>
              <div style={{
                width: `${(n / max) * 100}%`, minWidth: n > 0 ? 3 : 0, height: '100%',
                background: color, borderRadius: 3, transition: 'width .3s',
              }} />
            </div>
            <span style={{ textAlign: 'right', color: C.text, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
              {n.toLocaleString()} <span style={{ color: C.dim }}>({pct(n, total)})</span>
            </span>
          </div>
        )
      })}
    </div>
  )
}

export function Arbitrage() {
  const arb             = useDataStore((s) => s.arbitrage)
  const arbWindows      = useDataStore((s) => s.arbWindows)
  const fetchArbitrage  = useDataStore((s) => s.fetchArbitrage)
  const fetchArbWindows = useDataStore((s) => s.fetchArbWindows)
  const [symbol, setSymbol]             = useState('')  // '' = all coins
  const [profitableOnly, setProfitableOnly] = useState(false)
  const [error, setError]               = useState<string | null>(null)

  // Status: refetched on every arb_update (once a second), plus a slow poll
  // in case the WebSocket is down. Skip a tick while a request is in flight.
  useEffect(() => {
    let busy = false
    let mounted = true
    const refresh = async () => {
      if (busy) return
      busy = true
      const err = await fetchArbitrage()
      busy = false
      if (mounted) setError(err)
    }
    refresh()
    const off = wsClient.subscribe('arb_update', refresh)
    const t = setInterval(refresh, 5000)
    return () => { mounted = false; off(); clearInterval(t) }
  }, [fetchArbitrage])

  // Windows open and close every few seconds, so the list is polled.
  useEffect(() => {
    const load = () => fetchArbWindows(symbol || undefined, profitableOnly)
    load()
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [symbol, profitableOnly, fetchArbWindows])

  const name = (vid: string) => arb?.venue_names[vid] ?? vid
  const symbols = arb?.symbols ?? []
  const all = addUp(symbols.map((s) => s.stats))
  const shown = symbol ? addUp(symbols.filter((s) => s.symbol === symbol).map((s) => s.stats)) : all
  const liveVenues = new Set(symbols.flatMap((s) => s.venues))
  const scansPerSecond = arb && arb.scan_interval_ms > 0 ? Math.round(1000 / arb.scan_interval_ms) : null
  const windowRows: ArbWindow[] = [...(arbWindows?.open ?? []), ...(arbWindows?.windows ?? [])]
  // A pair pays both exchanges' fees: cheapest pair .. dearest pair.
  const fees = Object.values(arb?.fees_bps ?? {}).sort((a, b) => a - b)
  const pairFees = fees.length >= 2 ? [fees[0] + fees[1], fees[fees.length - 1] + fees[fees.length - 2]] : null

  const th: React.CSSProperties = { padding: '6px 10px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left', whiteSpace: 'nowrap' }
  const thR: React.CSSProperties = { ...th, textAlign: 'right' }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${tint(C.border, 40)}` }
  const td: React.CSSProperties = { padding: '8px 10px', color: C.text, fontSize: F.base, whiteSpace: 'nowrap' }
  const tdR: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
  const sub: React.CSSProperties = { fontSize: F.xs, color: C.dim, marginTop: 2 }
  const statLabel: React.CSSProperties = { fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }
  const statSub: React.CSSProperties = { fontSize: F.xs, color: C.muted, marginTop: 4 }
  const tilePair: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 150px), 1fr))', gap: 12 }

  function chip(active: boolean): React.CSSProperties {
    return {
      padding: '5px 12px', borderRadius: 14, fontSize: F.xs, fontFamily: 'inherit', cursor: 'pointer',
      border: `1px solid ${active ? C.blue : C.border}`,
      background: active ? C.blue : C.surface, color: active ? C.onAccent : C.muted,
    }
  }

  const status = !arb ? null
    : !arb.enabled ? { color: C.dim, text: 'Off — ARB_ENABLED=false' }
    : arb.running ? { color: C.green, text: `Scanning · ${scansPerSecond ?? '?'}× a second` }
    : { color: C.orange, text: 'Stopped' }

  return (
    <div>
      <PageHeader
        title="Arbitrage"
        badge={<>
          <Pill tone={C.blue}>Detector only · nothing is traded</Pill>
          {status && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: F.sm, color: status.color }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: status.color }} />
              {status.text}
            </span>
          )}
        </>}
        subtitle="Price gaps between exchanges on the real market, measured after both exchanges' taker fees."
        about={<>
          When one exchange's best bid is above another exchange's best ask, the two order books are <b>crossed</b>:
          buying on one and selling on the other at the same moment would earn the gap. ApnaSOR checks every pair of
          exchanges, in both directions{scansPerSecond ? `, ${scansPerSecond} times a second` : ''}, and then subtracts
          both exchanges' taker fees, which is what doing it would actually cost. Prices are the real market.
          1 bps = 0.01%.
        </>}
      />

      {error && (
        <div style={{
          marginBottom: 14, padding: '10px 14px', borderRadius: 6, fontSize: F.sm, color: C.text,
          background: tint(C.red, 10), border: `1px solid ${tint(C.red, 45)}`,
        }}>
          Can't read the arbitrage detector: {error}{arb ? ' — showing the last data received.' : ''}
        </div>
      )}

      {!arb ? (
        <Card>
          {error ? (
            <div style={{ padding: 40, textAlign: 'center', color: C.dim, fontSize: F.base }}>No data from the detector yet.</div>
          ) : <Loading block label="Loading the arbitrage detector…" />}
        </Card>
      ) : !arb.enabled ? (
        <Card>
          <div style={{ padding: 30, textAlign: 'center', color: C.muted, fontSize: F.base }}>
            The arbitrage detector is switched off on this gateway. Set <code>ARB_ENABLED=true</code> in <code>.env</code> and
            restart the gateway.
          </div>
        </Card>
      ) : (
        <>
          {/* Headline */}
          <Card style={{ marginBottom: 14 }}>
            <div style={{ fontSize: F.md, color: C.text, lineHeight: 1.5, marginBottom: 14 }}>{arb.summary}</div>
            {/* Two pairs of tiles: 4 in a row, 2 x 2, or stacked — never 3 + 1 */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 400px), 1fr))', gap: 12 }}>
              <div style={tilePair}>
                <Card style={{ textAlign: 'center', padding: 16, background: C.surface }}>
                  <div style={statLabel}>Books crossed</div>
                  <div style={{ fontSize: F.xxl, fontWeight: 700, color: C.text }}>{pct(all.crossed, all.scans)}</div>
                  <div style={statSub}>of {all.scans.toLocaleString()} scans, all coins</div>
                </Card>
                <Card style={{ textAlign: 'center', padding: 16, background: C.surface }}>
                  <div style={statLabel}>Avg gap before fees</div>
                  <div style={{ fontSize: F.xxl, fontWeight: 700, color: C.text }}>
                    {all.scans ? fmtBps(all.sumGross / all.scans) : '—'}
                  </div>
                  <div style={statSub}>widest exchange pair, per scan</div>
                </Card>
              </div>
              <div style={tilePair}>
                <Card style={{ textAlign: 'center', padding: 16, background: C.surface }}>
                  <div style={statLabel}>Avg after taker fees</div>
                  <div style={{ fontSize: F.xxl, fontWeight: 700, color: all.scans ? edgeColor(all.sumNet / all.scans) : C.text }}>
                    {all.scans ? fmtBps(all.sumNet / all.scans) : '—'}
                  </div>
                  <div style={statSub}>best pair once both fees are paid</div>
                </Card>
                <Card style={{ textAlign: 'center', padding: 16, background: C.surface }}>
                  <div style={statLabel}>Profitable windows</div>
                  <div style={{ fontSize: F.xxl, fontWeight: 700, color: all.profitableWindows > 0 ? C.green : C.text }}>
                    {all.profitableWindows.toLocaleString()}
                    <span style={{ fontSize: F.md, color: C.muted, fontWeight: 400 }}> of {all.windows.toLocaleString()}</span>
                  </div>
                  <div style={statSub}>crossings with money left after fees</div>
                </Card>
              </div>
            </div>
            <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
              Statistics since {fmtDateTime(arb.stats_since)} · they survive gateway restarts.
            </div>
          </Card>

          {/* Right now */}
          <Card style={{ marginBottom: 14 }}>
            <CardTitle>Right now — best direction per coin</CardTitle>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={th}>Coin</th>
                    <th style={th}>Buy on</th>
                    <th style={th}>Sell on</th>
                    <th style={thR}>Gross gap</th>
                    <th style={thR}>Fees</th>
                    <th style={thR}>Net</th>
                    <th style={thR}>Size (top of books)</th>
                    <th style={thR}>Net on that size</th>
                    <th style={thR}>We could fund</th>
                    <th style={th}>Crossed now</th>
                  </tr>
                </thead>
                <tbody>
                  {symbols.map((s) => {
                    const e = s.best
                    const base = baseAsset(s.symbol)
                    const longestOpen = Math.max(0, ...s.open_windows.map((w) => w.duration_s))
                    if (!e) {
                      return (
                        <tr key={s.symbol} style={tr}>
                          <td style={{ ...td, fontWeight: 700 }}>{base}</td>
                          <td colSpan={9} style={{ ...td, color: C.dim, fontSize: F.sm }}>
                            Waiting for live quotes from at least two exchanges
                            {s.venues.length === 1 ? ` (only ${name(s.venues[0])} is live)` : ''}.
                          </td>
                        </tr>
                      )
                    }
                    return (
                      <tr key={s.symbol} style={tr}>
                        <td style={{ ...td, fontWeight: 700 }}>{base}</td>
                        <td style={td}>
                          {name(e.buy_venue)}
                          <div style={sub}>ask {fmtPrice(e.buy_price)}</div>
                        </td>
                        <td style={td}>
                          {name(e.sell_venue)}
                          <div style={sub}>bid {fmtPrice(e.sell_price)}</div>
                        </td>
                        <td style={{ ...tdR, color: edgeColor(e.gross_bps) }}>{fmtBps(e.gross_bps, 2)}</td>
                        <td style={{ ...tdR, color: C.muted }}>{fmtBps(-e.fee_bps, 0)}</td>
                        <td style={{ ...tdR, color: edgeColor(e.net_bps), fontWeight: 700 }}>{fmtBps(e.net_bps, 2)}</td>
                        <td style={tdR}>{fmtSize(e.size)} {base}</td>
                        <td style={{ ...tdR, color: edgeColor(e.net_bps) }}>{fmtPnl(e.net_usdt)}</td>
                        <td style={{ ...tdR, whiteSpace: 'normal', minWidth: 150 }} title={e.limited_by.join('\n') || undefined}>
                          {e.fundable_size === null ? <span style={{ color: C.dim }}>unknown</span> : `${fmtQty(e.fundable_size, 6)} ${base}`}
                          {e.limited_by.length > 0 && <div style={sub}>{e.limited_by[0]}</div>}
                        </td>
                        <td style={td}>
                          {s.crossed_directions > 0
                            ? `${s.crossed_directions} direction${s.crossed_directions === 1 ? '' : 's'}`
                            : <span style={{ color: C.dim }}>none</span>}
                          {s.open_windows.length > 0 && (
                            <div style={sub}>{longestOpen < 1 ? 'just opened' : `longest open ${fmtDuration(longestOpen)}`}</div>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim, lineHeight: 1.5 }}>
              Net = gross gap minus both taker fees. Red means paying the fees would turn the gap into a loss.
              "We could fund" uses our sandbox balances: the USDT on the buy exchange and the coins on the sell
              exchange.
            </div>
          </Card>

          {/* Coin filter for the charts and the windows list */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: F.xs, color: C.dim, marginRight: 4 }}>Show</span>
            <button onClick={() => setSymbol('')} style={chip(symbol === '')}>All coins</button>
            {symbols.map((s) => (
              <button key={s.symbol} onClick={() => setSymbol(s.symbol)} style={chip(symbol === s.symbol)}>
                {baseAsset(s.symbol)}
              </button>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))', gap: 14, marginBottom: 14 }}>
            <Card>
              <CardTitle>How long crossings lasted</CardTitle>
              <BucketBars order={DURATION_ORDER} counts={shown.duration} color={C.blue} />
              <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
                {shown.windows.toLocaleString()} closed windows · {pct(
                  (shown.duration['<1 s'] ?? 0) + (shown.duration['1-5 s'] ?? 0), shown.windows,
                )} lasted under 5 s
              </div>
            </Card>
            <Card>
              <CardTitle>How wide they got (peak gap before fees)</CardTitle>
              <BucketBars order={GROSS_ORDER} counts={shown.gross} color={C.purple} />
              {pairFees && (
                <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
                  Paying both taker fees costs {pairFees[0]}–{pairFees[1]} bps depending on the exchange pair, so a gap
                  has to be wider than that to make money.
                </div>
              )}
            </Card>
          </div>

          {/* Per coin */}
          <Card style={{ marginBottom: 14 }}>
            <CardTitle>Per coin — since {fmtDateTime(arb.stats_since)}</CardTitle>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={th}>Coin</th>
                    <th style={thR}>Scans</th>
                    <th style={thR}>Crossed</th>
                    <th style={thR}>Avg gap</th>
                    <th style={thR}>Avg after fees</th>
                    <th style={thR}>Windows</th>
                    <th style={thR}>Profitable</th>
                    <th style={th}>Best after fees seen</th>
                  </tr>
                </thead>
                <tbody>
                  {symbols.map((s) => {
                    const st = s.stats
                    const best = st.max_net
                    return (
                      <tr key={s.symbol} style={tr}>
                        <td style={{ ...td, fontWeight: 700 }}>{baseAsset(s.symbol)}</td>
                        <td style={tdR}>{st.scans.toLocaleString()}</td>
                        <td style={tdR}>{pct(st.crossed_scans, st.scans)}</td>
                        <td style={tdR}>{st.scans ? fmtBps(st.sum_best_gross_bps / st.scans) : '—'}</td>
                        <td style={{ ...tdR, color: st.scans ? edgeColor(st.sum_best_net_bps / st.scans) : C.text }}>
                          {st.scans ? fmtBps(st.sum_best_net_bps / st.scans) : '—'}
                        </td>
                        <td style={tdR}>{st.windows.toLocaleString()}</td>
                        <td style={{ ...tdR, color: st.profitable_windows > 0 ? C.green : C.text }}>
                          {st.profitable_windows.toLocaleString()}
                        </td>
                        <td style={td}>
                          {best ? (
                            <>
                              <span style={{ color: edgeColor(best.net_bps), fontWeight: 600 }}>{fmtBps(best.net_bps, 2)}</span>
                              <div style={sub}>
                                buy {name(best.buy_venue)} → sell {name(best.sell_venue)} · {fmtTime(st.max_net_at)}
                              </div>
                            </>
                          ) : <span style={{ color: C.dim }}>—</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {symbols.map((s) => (
                <div key={s.symbol} style={{ fontSize: F.sm, color: C.muted, lineHeight: 1.5 }}>{s.summary}</div>
              ))}
            </div>
          </Card>

          {/* Recent windows */}
          <Card style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
              <CardTitle>Recent windows{symbol ? ` — ${baseAsset(symbol)}` : ''}</CardTitle>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: F.xs, color: C.muted, cursor: 'pointer' }}>
                <input type="checkbox" checked={profitableOnly} onChange={(e) => setProfitableOnly(e.target.checked)} />
                Profitable only
              </label>
            </div>
            {windowRows.length === 0 ? (
              !arbWindows ? <Loading block label="Loading the windows…" /> : (
                <div style={{ padding: 24, textAlign: 'center', color: C.dim, fontSize: F.sm }}>
                  {profitableOnly ? 'No profitable window seen yet.' : 'No windows in the last few minutes.'}
                </div>
              )
            ) : (
              <div style={{ overflow: 'auto', maxHeight: 440 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead style={{ position: 'sticky', top: 0, background: C.surface2 }}>
                    <tr>
                      <th style={th}>Opened</th>
                      <th style={th}>Coin</th>
                      <th style={th}>Direction</th>
                      <th style={thR}>Lasted</th>
                      <th style={thR}>Scans</th>
                      <th style={thR}>Peak gap</th>
                      <th style={thR}>Best net</th>
                      <th style={th}>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {windowRows.map((w) => {
                      const open = w.closed_at === null
                      return (
                        <tr key={`${w.symbol}-${w.buy_venue}-${w.sell_venue}-${w.opened_at}`} style={tr}>
                          <td style={{ ...td, color: C.muted }}>{fmtTime(w.opened_at)}</td>
                          <td style={{ ...td, fontWeight: 700 }}>{baseAsset(w.symbol)}</td>
                          <td style={td}>buy {name(w.buy_venue)} → sell {name(w.sell_venue)}</td>
                          <td style={tdR}>
                            {open && (
                              <span style={{
                                marginRight: 6, padding: '1px 6px', borderRadius: 3, fontSize: F.xs,
                                background: tint(C.blue, 14), color: C.blue,
                              }}>Open</span>
                            )}
                            {fmtDuration(w.duration_s)}
                          </td>
                          <td style={tdR}>{w.scans.toLocaleString()}</td>
                          <td style={{ ...tdR, color: edgeColor(w.peak_gross_bps) }}>{fmtBps(w.peak_gross_bps, 2)}</td>
                          <td style={{ ...tdR, color: edgeColor(w.peak.net_bps), fontWeight: 600 }}>{fmtBps(w.peak.net_bps, 2)}</td>
                          <td style={td}>
                            {w.profitable
                              ? <span style={{ color: C.green, fontWeight: 700 }}>Profitable</span>
                              : <span style={{ color: C.dim }}>loss after fees</span>}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div style={{ marginTop: 10, fontSize: F.xs, color: C.dim }}>
              A window is one direction that stayed crossed over consecutive scans. Crossings flicker, so the full list
              only covers the last few minutes; profitable windows are kept longer and written to the Audit Log.
            </div>
          </Card>

          {/* Fees and the honest note */}
          <Card>
            <CardTitle>Taker fees used</CardTitle>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              {Object.entries(arb.fees_bps).map(([vid, bps]) => {
                const live = liveVenues.has(vid)
                return (
                  <span key={vid} title={live ? 'Live quotes right now' : 'No live quote right now'} style={{
                    padding: '5px 10px', borderRadius: 4, fontSize: F.sm,
                    border: `1px solid ${C.border}`, background: C.surface, color: live ? C.text : C.dim,
                  }}>
                    {name(vid)} <b>{bps} bps</b>{live ? '' : ' · no live quote'}
                  </span>
                )
              })}
            </div>
            <div style={{ fontSize: F.xs, color: C.dim, lineHeight: 1.5 }}>
              Each exchange's base-tier spot taker fee (both legs would be immediate-or-cancel orders, so both pay
              taker). "Profitable" means more than {arb.min_net_bps} bps left after both fees. ⓘ {arb.note}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
