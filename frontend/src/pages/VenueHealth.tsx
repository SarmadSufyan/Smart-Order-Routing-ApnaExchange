import React, { useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle, ConnDot, PageHeader } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { backendVenueToUi, fmtRate, LATENCY_WARN_MS, connectionLabel, dataFeedLabel } from '../adapters'
import { fmtUsd } from '../format'
import type { VenueGuard, VenueHealth as BackendVenue } from '../services/api'

function fmtMs(ms: number): string {
  if (!ms) return '—'
  if (ms < 10) return `${ms.toFixed(1)} ms`
  return `${ms.toFixed(0)} ms`
}

function fmtBps(bps: number): string {
  return `${bps >= 0 ? '+' : ''}${bps.toFixed(1)} bps`
}

/** Can money leave this exchange, and does its price still agree with the others? What the guard reads. */
function GuardFacts({ guard }: { guard: VenueGuard }) {
  const closed = guard.withdrawals_closed
  const shut = guard.transfers_known && closed >= guard.coins_checked / 2
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 14px', fontSize: F.xs, marginBottom: 10 }}>
      <div title={guard.transfer_source ? `From ${guard.transfer_source}` : 'This exchange publishes it only to a real-money key'}>
        <div style={{ color: C.dim }}>Withdrawals</div>
        <div style={{ color: !guard.transfers_known ? C.dim : shut ? C.red : C.green, fontWeight: 500 }}>
          {guard.transfers_known ? `${guard.coins_checked - closed}/${guard.coins_checked} coins open` : 'not published'}
        </div>
      </div>
      <div title="Median gap between this exchange's prices and the other exchanges'">
        <div style={{ color: C.dim }}>Price vs others</div>
        <div style={{ color: guard.wide_gap_coins.length ? C.orange : C.text, fontWeight: 500 }}>
          {guard.median_gap_bps === null ? '—' : fmtBps(guard.median_gap_bps)}
        </div>
      </div>
    </div>
  )
}

/** Shown at the top of the page while any exchange is at WATCH or GUARDED. */
function IncidentBanner({ venues }: { venues: BackendVenue[] }) {
  const open = venues.filter((v) => v.guard && v.guard.level !== 'NORMAL')
  if (open.length === 0) return null
  const parked = open.reduce((sum, v) => sum + (v.guard?.funds_usdt ?? 0), 0)
  const guarded = open.some((v) => v.guard?.level === 'GUARDED')
  const tone = guarded ? C.red : C.orange
  return (
    <div role="alert" style={{
      background: tint(tone, 8), border: `1px solid ${tint(tone, 45)}`, borderRadius: 6,
      padding: '12px 16px', marginBottom: 14,
    }}>
      <div style={{ fontSize: F.base, fontWeight: 700, color: tone, marginBottom: 6 }}>
        {guarded ? 'Exchange incident: routing around it' : 'Exchange warning'}
      </div>
      {open.map((v) => (
        <div key={v.venue_id} style={{ fontSize: F.sm, lineHeight: 1.5 }}>
          <strong>{v.name}</strong>: {v.guard?.reasons.join('; ')}
          {v.guard?.blocked && ' — no new orders go there.'}
        </div>
      ))}
      {parked > 0 && (
        <div style={{ fontSize: F.xs, color: C.muted, marginTop: 6 }}>
          {fmtUsd(parked)} of our {venues[0]?.mode === 'mainnet' ? '' : 'sandbox '}balance sits on{' '}
          {open.length === 1 ? 'that exchange' : 'those exchanges'}. A router can avoid an exchange, but it can't take
          money out of one: our keys can't withdraw, by design.
        </div>
      )}
    </div>
  )
}

export function VenueHealth() {
  const backendVenues   = useDataStore((s) => s.venues)
  const blacklistVenue  = useDataStore((s) => s.blacklistVenue)
  const unblacklistVenue = useDataStore((s) => s.unblacklistVenue)
  const degradeVenue    = useDataStore((s) => s.degradeVenue)
  const recoverVenue    = useDataStore((s) => s.recoverVenue)
  const incidentDrill   = useDataStore((s) => s.incidentDrill)

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
    const err = await degradeVenue(venueId)
    setActionError(err ? { venueId, text: err } : null)
    setBusy(null)
  }

  async function handleRecover(venueId: string) {
    setBusy(venueId)
    const err = await recoverVenue(venueId)
    setActionError(err ? { venueId, text: err } : null)
    setBusy(null)
  }

  async function handleDrill(venueId: string, on: boolean) {
    setBusy(venueId)
    const err = await incidentDrill(venueId, on)
    setActionError(err ? { venueId, text: err } : null)
    setBusy(null)
  }

  return (
    <div>
      <PageHeader
        title="Venues"
        subtitle={<>
          Order books from {dataFeedLabel(backendVenues[0]?.data_mode)} feeds · orders execute in{' '}
          {backendVenues[0]?.mode === 'mainnet' ? 'mainnet (real funds)' : "each exchange's sandbox environment"}.
        </>}
        about={<>
          Latency is the measured REST round-trip. “Inject latency” adds a fixed delay to our own connection to that
          exchange (fault injection), so the health monitor's response can be observed on a live venue; the exchange
          is not affected. The incident guard reads whether money can leave each exchange and whether its prices still
          agree with the others: an exchange in trouble leaves routing automatically, and only a person lets it back.
          “Incident drill” simulates that in the sandbox.
        </>}
      />

      <IncidentBanner venues={backendVenues} />

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
            <div key={v.id} data-card={borderColor === C.border ? '' : undefined}
              style={{ background: bg, border: `1px solid ${borderColor}`, borderRadius: 6, padding: 16 }}>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ConnDot status={v.status} />
                  <span style={{ fontSize: F.md, fontWeight: 600 }}>{v.name}</span>
                  {v.dataOnly && (
                    <span title={b.execution_note ?? ''} style={{
                      fontSize: F.xs, padding: '1px 6px', borderRadius: 3, fontWeight: 700,
                      background: tint(C.blue, 14), color: C.blue, border: `1px solid ${tint(C.blue, 40)}`,
                    }}>Data only</span>
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
                      { l: 'Fill rate',       v: fmtRate(v, v.fillRate),   c: !v.hasOrders ? C.dim : v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red },
                      { l: 'Reject rate',     v: fmtRate(v, v.rejectRate), c: !v.hasOrders ? C.dim : v.rejectRate > 5 ? C.red : C.text },
                      { l: 'Health score',    v: `${((v.backend.health_score ?? 0) * 100).toFixed(0)}%`, c: C.text          },
                      { l: 'Uptime',          v: `${((v.backend.uptime ?? 0) * 100).toFixed(0)}%`,    c: C.green         },
                    ].map((row) => (
                      <div key={row.l}>
                        <div style={{ color: C.dim }}>{row.l}</div>
                        <div style={{ color: row.c, fontWeight: 500 }}>{row.v}</div>
                      </div>
                    ))}
                  </div>
                  {b.guard && <GuardFacts guard={b.guard} />}
                  {b.execution_coherence && Object.keys(b.execution_coherence).length > 0 && (
                    <div style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 4 }}>
                        {b.mode === 'mainnet' ? 'Execution book' : 'Sandbox book'} vs live market
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
                  {b.key_note && v.canTrade && (
                    <div style={{
                      fontSize: F.xs, color: C.green, background: tint(C.green, 8), borderRadius: 3,
                      padding: '6px 8px', marginBottom: 10, lineHeight: 1.4,
                    }}>
                      🔒 {b.key_note}
                    </div>
                  )}
                  {b.fee_note && (
                    <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 10, lineHeight: 1.4 }}>
                      {b.fee_note}
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
                        {busy === v.backend.venue_id ? '…' : 'Inject latency'}
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
                  {b.mode !== 'mainnet' && (
                    <button
                      onClick={() => handleDrill(v.backend.venue_id, !b.guard?.drill)}
                      disabled={busy === v.backend.venue_id}
                      title="Simulates this exchange closing its withdrawals: the block, the alert and the order routed around it all run for real, in the sandbox"
                      style={{
                        width: '100%', marginTop: 6, padding: '5px 0',
                        border: `1px solid ${C.border}`, borderRadius: 3,
                        background: C.surface, color: C.muted,
                        fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
                        opacity: busy === v.backend.venue_id ? 0.5 : 1,
                      }}
                    >
                      {busy === v.backend.venue_id ? '…' : b.guard?.drill ? 'End incident drill' : 'Incident drill'}
                    </button>
                  )}
                </>
              ) : (
                <>
                  <div style={{ textAlign: 'center', padding: '14px 0', color: C.red, fontSize: F.base }}>
                    {b.guard?.blocked ? 'Blocked by the incident guard' : v.blacklisted ? 'Venue blacklisted' : 'Feed disconnected'}
                    <div style={{ color: C.dim, fontSize: F.xs, marginTop: 4, lineHeight: 1.5 }}>
                      {b.guard?.blocked
                        ? (b.guard.reasons.join('; ') || 'The warning signs have cleared') + ' — orders route through the other exchanges until a person lifts this'
                        : v.blacklisted
                        ? 'Excluded from routing by an operator — no orders being sent'
                        : 'Reconnecting and re-syncing order books — excluded from routing until healthy'}
                    </div>
                  </div>
                  {b.guard && v.blacklisted && <GuardFacts guard={b.guard} />}
                  {b.guard?.funds_usdt != null && b.guard.blocked && (
                    <div style={{ fontSize: F.xs, color: C.muted, textAlign: 'center', marginBottom: 10 }}>
                      Our {b.mode === 'mainnet' ? '' : 'sandbox '}balance there: {fmtUsd(b.guard.funds_usdt)}
                    </div>
                  )}
                  {v.blacklisted && <div style={{ display: 'flex', gap: 6, maxWidth: 240, margin: '0 auto' }}>
                    {b.guard?.drill && (
                      <button
                        onClick={() => handleDrill(v.backend.venue_id, false)}
                        disabled={busy === v.backend.venue_id}
                        style={{
                          flex: 1, padding: '5px 0',
                          border: `1px solid ${C.border}`, borderRadius: 3,
                          background: C.surface, color: C.muted,
                          fontSize: F.xs, cursor: 'pointer', fontFamily: 'inherit',
                          opacity: busy === v.backend.venue_id ? 0.5 : 1,
                        }}
                      >
                        {busy === v.backend.venue_id ? '…' : 'End drill'}
                      </button>
                    )}
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
                      {busy === v.backend.venue_id ? '…' : b.guard?.blocked ? 'Lift the block' : 'Unblacklist (Restore)'}
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
        <CardTitle>Venue comparison</CardTitle>
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
              <tr key={v.id} style={{ borderBottom: `1px solid ${C.border2}` }}>
                <td style={{ padding: '8px', fontWeight: 600 }}>{v.name}</td>
                <td style={{ padding: '8px', color: C.muted }}>{connectionLabel(v.backend)}</td>
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
