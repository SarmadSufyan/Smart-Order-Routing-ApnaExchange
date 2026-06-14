import React, { useState } from 'react'
import { C } from '../theme'
import { Card, CardTitle, ConnDot } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { backendVenueToUi } from '../adapters'

function fmtMs(ms: number): string {
  if (!ms) return '—'
  if (ms < 10) return `${ms.toFixed(1)} ms`
  return `${ms.toFixed(0)} ms`
}

export function VenueHealth() {
  const backendVenues   = useDataStore((s) => s.venues)
  const blacklistVenue  = useDataStore((s) => s.blacklistVenue)
  const unblacklistVenue = useDataStore((s) => s.unblacklistVenue)

  const [busy, setBusy] = useState<string | null>(null)

  const venues = backendVenues.map(backendVenueToUi)

  async function handleBlacklist(venueId: string) {
    setBusy(venueId)
    await blacklistVenue(venueId, 'Manual operator action')
    setBusy(null)
  }

  async function handleUnblacklist(venueId: string) {
    setBusy(venueId)
    await unblacklistVenue(venueId)
    setBusy(null)
  }

  return (
    <div>
      <div style={{ fontSize: 15, color: C.text, marginBottom: 14 }}>Venue Connectivity</div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginBottom: 14 }}>
        {venues.map((v) => {
          const isOffline = v.status === 'Disconnected' || v.blacklisted
          const borderColor =
            v.status === 'Degraded'     ? C.orange :
            isOffline                    ? C.red    : C.border
          const bg =
            v.status === 'Degraded'     ? '#EF9F2706' :
            isOffline                    ? '#E24B4A06' : '#1A1E24'

          return (
            <div key={v.id} style={{ background: bg, border: `1px solid ${borderColor}`, borderRadius: 6, padding: 14 }}>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ConnDot status={v.status} />
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{v.name}</span>
                </div>
                <span style={{ fontSize: 10, color: C.muted, background: '#131619', padding: '2px 7px', borderRadius: 3 }}>
                  {v.protocol}
                </span>
              </div>

              {!isOffline ? (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 14px', marginBottom: 10, fontSize: 10 }}>
                    {[
                      { l: 'Latency',         v: fmtMs(v.latencyEma),    c: v.latencyEma > 50 ? C.orange : C.text },
                      { l: 'P99',             v: fmtMs(v.p99),           c: v.p99 > 100 ? C.orange : C.text       },
                      { l: 'Fill Rate',       v: `${v.fillRate}%`,        c: v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red },
                      { l: 'Reject Rate',     v: `${v.rejectRate}%`,      c: v.rejectRate > 5 ? C.red : C.text     },
                      { l: 'Health Score',    v: `${((v.backend.health_score ?? 0) * 100).toFixed(0)}%`, c: C.text          },
                      { l: 'Uptime',          v: `${((v.backend.uptime ?? 0) * 100).toFixed(0)}%`,    c: C.green         },
                    ].map((row) => (
                      <div key={row.l}>
                        <div style={{ color: C.dim }}>{row.l}</div>
                        <div style={{ color: row.c, fontWeight: 500 }}>{row.v}</div>
                      </div>
                    ))}
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      onClick={() => handleBlacklist(v.backend.venue_id)}
                      disabled={busy === v.backend.venue_id}
                      style={{
                        flex: 1, padding: '5px 0',
                        border: `1px solid ${C.border}`, borderRadius: 3,
                        background: '#131619', color: C.red,
                        fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
                        opacity: busy === v.backend.venue_id ? 0.5 : 1,
                      }}
                    >
                      {busy === v.backend.venue_id ? '…' : 'Blacklist'}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div style={{ textAlign: 'center', padding: '14px 0', color: C.red, fontSize: 12 }}>
                    ⚠ Venue Blacklisted
                    <div style={{ color: C.dim, fontSize: 10, marginTop: 4 }}>
                      Excluded from routing — no orders being sent
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 6, maxWidth: 240, margin: '0 auto' }}>
                    <button
                      onClick={() => handleUnblacklist(v.backend.venue_id)}
                      disabled={busy === v.backend.venue_id}
                      style={{
                        flex: 1, padding: '5px 0',
                        border: `1px solid ${C.border}`, borderRadius: 3,
                        background: '#131619', color: C.green,
                        fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
                        opacity: busy === v.backend.venue_id ? 0.5 : 1,
                      }}
                    >
                      {busy === v.backend.venue_id ? '…' : 'Unblacklist (Restore)'}
                    </button>
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>

      <Card>
        <CardTitle>VENUE COMPARISON</CardTitle>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr>
              {['Venue', 'Cloud', 'Status', 'Latency', 'Health', 'Fill %', 'Reject %', 'Uptime'].map((h) => (
                <th key={h} style={{ padding: '5px 8px', fontSize: 10, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }}>
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
                <td style={{ padding: '8px', color: v.latencyEma > 50 ? C.orange : C.text }}>{fmtMs(v.latencyEma)}</td>
                <td style={{ padding: '8px' }}>{((v.backend.health_score ?? 0) * 100).toFixed(0)}%</td>
                <td style={{ padding: '8px', color: v.fillRate > 90 ? C.green : v.fillRate > 75 ? C.orange : C.red }}>{v.fillRate}%</td>
                <td style={{ padding: '8px', color: v.rejectRate > 5 ? C.red : C.muted }}>{v.rejectRate}%</td>
                <td style={{ padding: '8px', color: C.green }}>{((v.backend.uptime ?? 0) * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
