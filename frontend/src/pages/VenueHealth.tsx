import React, { useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle, ConnDot } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { backendVenueToUi, fmtRate, LATENCY_WARN_MS } from '../adapters'

function fmtMs(ms: number): string {
  if (!ms) return '—'
  if (ms < 10) return `${ms.toFixed(1)} ms`
  return `${ms.toFixed(0)} ms`
}

export function VenueHealth() {
  const backendVenues   = useDataStore((s) => s.venues)
  const blacklistVenue  = useDataStore((s) => s.blacklistVenue)
  const unblacklistVenue = useDataStore((s) => s.unblacklistVenue)
  const degradeVenue    = useDataStore((s) => s.degradeVenue)
  const recoverVenue    = useDataStore((s) => s.recoverVenue)

  const [busy, setBusy] = useState<string | null>(null)
  // e.g. un-blacklisting is refused while RAFT has no majority
  const [actionError, setActionError] = useState<{ venueId: string; text: string } | null>(null)

  const venues = backendVenues.map(backendVenueToUi)

  async function handleBlacklist(venueId: string) {
    setBusy(venueId)
    const err = await blacklistVenue(venueId, 'Manual operator action')
    setActionError(err ? { venueId, text: err } : null)
    setBusy(null)
  }

  async function handleUnblacklist(venueId: string) {
    setBusy(venueId)
    const err = await unblacklistVenue(venueId)
    setActionError(err ? { venueId, text: err } : null)
    setBusy(null)
  }

  async function handleDegrade(venueId: string) {
    setBusy(venueId)
    await degradeVenue(venueId)
    setBusy(null)
  }

  async function handleRecover(venueId: string) {
    setBusy(venueId)
    await recoverVenue(venueId)
    setBusy(null)
  }

  return (
    <div>
      <div style={{ fontSize: F.lg, color: C.text, marginBottom: 4 }}>Venue Connectivity</div>
      <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 14 }}>
        Order books: {backendVenues[0]?.data_mode ?? '—'} feeds · orders execute on {backendVenues[0]?.mode ?? '—'}.
        Latency = measured REST round-trip. “Degrade (Test)” adds artificial latency to our
        connection so the health monitor can be demonstrated on a real venue.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 14, marginBottom: 14 }}>
        {venues.map((v) => {
          const isOffline = v.status === 'Disconnected' || v.blacklisted
          const b = v.backend
          const borderColor =
            v.status === 'Degraded'     ? C.orange :
            isOffline                    ? C.red    : C.border
          const bg =
            v.status === 'Degraded'     ? tint(C.orange, 2) :
            isOffline                    ? tint(C.red, 2) : C.surface2

          return (
            <div key={v.id} style={{ background: bg, border: `1px solid ${borderColor}`, borderRadius: 6, padding: 14 }}>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ConnDot status={v.status} />
                  <span style={{ fontSize: F.md, fontWeight: 600 }}>{v.name}</span>
                  {v.dataOnly && (
                    <span title={b.execution_note ?? ''} style={{
                      fontSize: F.xs, padding: '1px 6px', borderRadius: 3, fontWeight: 700,
                      background: tint(C.blue, 14), color: C.blue, border: `1px solid ${tint(C.blue, 40)}`,
                    }}>DATA ONLY</span>
                  )}
                </div>
                <span style={{ fontSize: F.xs, color: C.muted, background: C.surface, padding: '2px 7px', borderRadius: 3 }}>
                  {v.protocol}
                </span>
              </div>

              {!isOffline ? (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 14px', marginBottom: 10, fontSize: F.xs }}>
                    {[
                      { l: 'Latency (p50)',   v: fmtMs(v.latencyEma),    c: v.latencyEma > LATENCY_WARN_MS ? C.orange : C.text },
                      { l: 'P99',             v: fmtMs(v.p99),           c: v.p99 > LATENCY_WARN_MS * 1.5 ? C.orange : C.text },
                      { l: 'Order books',     v: `${b.books_synced ?? 0}/${b.books_total ?? 0} synced`, c: b.books_synced === b.books_total ? C.green : C.orange },
                      { l: 'Resyncs',         v: `${b.resyncs ?? 0} gap · ${b.reconnects ?? 0} reconnect`, c: C.muted },
                      { l: 'Fill Rate',       v: fmtRate(v, v.fillRate),   c: !v.hasOrders ? C.dim : v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red },
                      { l: 'Reject Rate',     v: fmtRate(v, v.rejectRate), c: !v.hasOrders ? C.dim : v.rejectRate > 5 ? C.red : C.text },
                      { l: 'Health Score',    v: `${((v.backend.health_score ?? 0) * 100).toFixed(0)}%`, c: C.text          },
                      { l: 'Uptime',          v: `${((v.backend.uptime ?? 0) * 100).toFixed(0)}%`,    c: C.green         },
                    ].map((row) => (
                      <div key={row.l}>
                        <div style={{ color: C.dim }}>{row.l}</div>
                        <div style={{ color: row.c, fontWeight: 500 }}>{row.v}</div>
                      </div>
                    ))}
                  </div>
                  {b.execution_coherence && Object.keys(b.execution_coherence).length > 0 && (
                    <div style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 4 }}>
                        Execution book vs real market
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {Object.entries(b.execution_coherence).map(([sym, st]) => {
                          const ok = st.status === 'OK' || st.status === 'SAME_BOOK'
                          const color = ok ? C.green : st.status === 'UNKNOWN' ? C.dim : C.red
                          const label = st.status === 'EMPTY' ? 'empty'
                            : st.status === 'UNKNOWN' ? '…'
                            : st.divergence_bps !== null ? `${st.divergence_bps >= 0 ? '+' : ''}${st.divergence_bps.toFixed(0)}bp` : '='
                          return (
                            <span
                              key={sym}
                              title={ok ? 'Tracks the real market - routable' : 'Excluded from routing: execution book does not reflect the real market'}
                              style={{
                                fontSize: F.xs, padding: '1px 6px', borderRadius: 3, fontWeight: 600,
                                background: tint(color, 12), color, border: `1px solid ${tint(color, 35)}`,
                              }}
                            >
                              {sym.split('/')[0]} {label} {ok ? '✓' : st.status === 'UNKNOWN' ? '' : '✗'}
                            </span>
                          )
                        })}
                      </div>
                    </div>
                  )}
                  {!v.canTrade && b.execution_note && (
                    <div style={{
                      fontSize: F.xs, color: C.muted, background: C.surface, borderRadius: 3,
                      padding: '6px 8px', marginBottom: 10, lineHeight: 1.4,
                    }}>
                      ⓘ Not routable: {b.execution_note}. Its real order book still counts in the NBBO.
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 6 }}>
                    {v.status === 'Degraded' ? (
                      <button
                        onClick={() => handleRecover(v.backend.venue_id)}
                        disabled={busy === v.backend.venue_id}
                        style={{
                          flex: 1, padding: '5px 0',
                          border: `1px solid ${C.green}`, borderRadius: 3,
                          background: tint(C.green, 8), color: C.green,
                          fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
                          opacity: busy === v.backend.venue_id ? 0.5 : 1,
                        }}
                      >
                        {busy === v.backend.venue_id ? '…' : 'Recover'}
                      </button>
                    ) : (
                      <button
                        onClick={() => handleDegrade(v.backend.venue_id)}
                        disabled={busy === v.backend.venue_id}
                        style={{
                          flex: 1, padding: '5px 0',
                          border: `1px solid ${C.border}`, borderRadius: 3,
                          background: C.surface, color: C.orange,
                          fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
                          opacity: busy === v.backend.venue_id ? 0.5 : 1,
                        }}
                      >
                        {busy === v.backend.venue_id ? '…' : 'Degrade (Test)'}
                      </button>
                    )}
                    <button
                      onClick={() => handleBlacklist(v.backend.venue_id)}
                      disabled={busy === v.backend.venue_id}
                      style={{
                        flex: 1, padding: '5px 0',
                        border: `1px solid ${C.border}`, borderRadius: 3,
                        background: C.surface, color: C.red,
                        fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
                        opacity: busy === v.backend.venue_id ? 0.5 : 1,
                      }}
                    >
                      {busy === v.backend.venue_id ? '…' : 'Blacklist'}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div style={{ textAlign: 'center', padding: '14px 0', color: C.red, fontSize: F.base }}>
                    {v.blacklisted ? 'Venue Blacklisted' : 'Feed Disconnected'}
                    <div style={{ color: C.dim, fontSize: F.xs, marginTop: 4 }}>
                      {v.blacklisted
                        ? 'Excluded from routing by an operator — no orders being sent'
                        : 'Reconnecting and re-syncing order books — excluded from routing until healthy'}
                    </div>
                  </div>
                  {v.blacklisted && <div style={{ display: 'flex', gap: 6, maxWidth: 240, margin: '0 auto' }}>
                    <button
                      onClick={() => handleUnblacklist(v.backend.venue_id)}
                      disabled={busy === v.backend.venue_id}
                      style={{
                        flex: 1, padding: '5px 0',
                        border: `1px solid ${C.border}`, borderRadius: 3,
                        background: C.surface, color: C.green,
                        fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
                        opacity: busy === v.backend.venue_id ? 0.5 : 1,
                      }}
                    >
                      {busy === v.backend.venue_id ? '…' : 'Unblacklist (Restore)'}
                    </button>
                  </div>}
                </>
              )}
              {actionError?.venueId === v.backend.venue_id && (
                <div style={{
                  marginTop: 8, padding: '6px 10px', borderRadius: 4, fontSize: F.xs,
                  background: tint(C.red, 10), border: `1px solid ${tint(C.red, 45)}`, color: C.red,
                }}>
                  {actionError.text}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <Card>
        <CardTitle>VENUE COMPARISON</CardTitle>
        <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
          <thead>
            <tr>
              {['Venue', 'Connection', 'Status', 'Latency (p50)', 'Health', 'Fill %', 'Reject %', 'Uptime'].map((h) => (
                <th key={h} style={{ padding: '5px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {venues.map((v) => (
              <tr key={v.id} style={{ borderBottom: `1px solid ${C.border}40` }}>
                <td style={{ padding: '8px', fontWeight: 600 }}>{v.name}</td>
                <td style={{ padding: '8px', color: C.muted }}>{v.backend.cloud}</td>
                <td style={{ padding: '8px' }}><ConnDot status={v.status} /></td>
                <td style={{ padding: '8px', color: v.latencyEma > LATENCY_WARN_MS ? C.orange : C.text }}>{fmtMs(v.latencyEma)}</td>
                <td style={{ padding: '8px' }}>{((v.backend.health_score ?? 0) * 100).toFixed(0)}%</td>
                <td style={{ padding: '8px', color: !v.hasOrders ? C.dim : v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red }}>{fmtRate(v, v.fillRate)}</td>
                <td style={{ padding: '8px', color: !v.hasOrders ? C.dim : v.rejectRate > 5 ? C.red : C.muted }}>{fmtRate(v, v.rejectRate)}</td>
                <td style={{ padding: '8px', color: C.green }}>{((v.backend.uptime ?? 0) * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </Card>
    </div>
  )
}
