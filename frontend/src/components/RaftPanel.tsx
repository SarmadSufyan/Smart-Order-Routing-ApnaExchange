import React, { useEffect, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from './shared'
import { useDataStore } from '../stores/dataStore'
import type { ConsensusNode } from '../services/api'

// The gateway re-reads the cluster every 0.5 s (RAFT_POLL_INTERVAL_S);
// there is no WebSocket channel for it, so the panel polls while it is shown.
const POLL_MS = 2000

function roleStyle(n: ConsensusNode): { label: string; color: string } {
  if (!n.reachable) return { label: 'UNREACHABLE', color: C.red }
  if (n.role === 'LEADER') return { label: 'LEADER', color: C.blue }
  if (n.role === 'CANDIDATE') return { label: 'CANDIDATE', color: C.orange }
  return { label: n.role ?? '—', color: C.muted }
}

function fmtIndex(i: number | null | undefined): string {
  return i === null || i === undefined ? '—' : `#${i.toLocaleString()}`
}

/**
 * Live state of the 3-node RAFT cluster that replicates the kill switch, risk
 * limits and venue blacklist (GET /api/consensus/status). Real data only: with
 * RAFT off it says so instead of showing nodes. `compact` = one row per node
 * (the Dashboard); otherwise the full panel (Kill Switch & Alerts).
 */
export function RaftPanel({ style, compact = false }: { style?: React.CSSProperties; compact?: boolean }) {
  const status         = useDataStore((s) => s.consensus)
  const fetchConsensus = useDataStore((s) => s.fetchConsensus)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    const load = async () => {
      const err = await fetchConsensus()
      if (mounted) setError(err)
    }
    load()
    const t = setInterval(load, POLL_MS)
    return () => { mounted = false; clearInterval(t) }
  }, [fetchConsensus])

  const nodes = status?.nodes ?? []
  const reachable = nodes.filter((n) => n.reachable).length
  const majority = Math.floor(nodes.length / 2) + 1

  const chip = (color: string): React.CSSProperties => ({
    fontSize: F.xs, padding: '2px 8px', borderRadius: 3, fontWeight: 700, letterSpacing: '.05em',
    background: tint(color, 14), color, border: `1px solid ${tint(color, 45)}`, whiteSpace: 'nowrap',
  })
  const label: React.CSSProperties = { color: C.dim, fontSize: F.xs }
  const value: React.CSSProperties = { color: C.text, fontSize: F.sm, fontVariantNumeric: 'tabular-nums' }

  if (compact) {
    return (
      <Card style={style}>
        <CardTitle>RAFT CONSENSUS NODES</CardTitle>
        {!status ? (
          <div style={{ padding: 10, textAlign: 'center', color: C.dim, fontSize: F.sm }}>{error ? 'No status yet.' : 'Loading…'}</div>
        ) : !status.enabled ? (
          <div style={{ fontSize: F.sm, color: C.muted, lineHeight: 1.5 }}>{status.message}</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {nodes.map((n) => {
              const role = roleStyle(n)
              return (
                <div key={n.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 10px', background: C.surface, borderRadius: 4,
                }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: n.reachable ? C.green : C.red, flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: F.sm }}>Node {n.id}</span>
                  <span style={chip(role.color)}>{role.label}</span>
                  <span style={{ fontSize: F.xs, color: C.dim, fontVariantNumeric: 'tabular-nums' }}>
                    {n.reachable ? `term ${n.term ?? '—'} · ${fmtIndex(n.commit_index)}` : '—'}
                  </span>
                </div>
              )
            })}
            <div style={{ fontSize: F.xs, color: status.majority_reachable ? C.dim : C.red, textAlign: 'center', marginTop: 2 }}>
              {status.majority_reachable
                ? `Majority up · ${reachable} of ${nodes.length} nodes${status.signed ? ' · messages signed' : ''}`
                : `No majority · ${reachable} of ${nodes.length} nodes up`}
            </div>
          </div>
        )}
        {error && status && (
          <div style={{ marginTop: 6, fontSize: F.xs, color: C.red, textAlign: 'center' }}>Can't read the cluster status — showing the last one</div>
        )}
      </Card>
    )
  }

  return (
    <Card style={style}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
        <CardTitle>RAFT cluster — replicated controls</CardTitle>
        {status?.enabled && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span style={chip(status.majority_reachable ? C.green : C.red)}>
              {status.majority_reachable ? `MAJORITY · ${reachable} OF ${nodes.length} UP` : `NO MAJORITY · ${reachable} OF ${nodes.length} UP`}
            </span>
            {status.signed && <span style={chip(C.blue)} title="Every RAFT message carries an HMAC-SHA256 signature">SIGNED</span>}
          </div>
        )}
      </div>

      {error && (
        <div style={{ marginBottom: 10, padding: '6px 10px', borderRadius: 4, background: tint(C.red, 10), color: C.red, fontSize: F.sm }}>
          Can't read the cluster status: {error}{status ? ' — showing the last status received.' : ''}
        </div>
      )}

      {!status ? (
        <div style={{ padding: 20, textAlign: 'center', color: C.dim, fontSize: F.sm }}>{error ? 'No status yet.' : 'Loading…'}</div>
      ) : !status.enabled ? (
        <div style={{ fontSize: F.sm, color: C.muted, lineHeight: 1.6 }}>
          {status.message}
          <div style={{ marginTop: 6, fontSize: F.xs, color: C.dim }}>
            To run the cluster: set <code>RAFT_ENABLED=true</code> and your own <code>RAFT_SHARED_SECRET</code> in
            <code> .env</code>, then <code>python start_all.py</code> starts 3 node processes before the gateway.
          </div>
        </div>
      ) : (
        <>
          <div style={{ fontSize: F.sm, color: status.majority_reachable ? C.text : C.red, marginBottom: 12, lineHeight: 1.5 }}>
            {status.message}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 170px), 1fr))', gap: 10, marginBottom: 12 }}>
            {nodes.map((n) => {
              const role = roleStyle(n)
              return (
                <div key={n.id} style={{
                  padding: '10px 12px', borderRadius: 4, background: C.surface,
                  border: `1px solid ${n.reachable ? (n.role === 'LEADER' ? tint(C.blue, 60) : C.border) : tint(C.red, 50)}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6, marginBottom: 8 }}>
                    <span style={{ fontWeight: 700, fontSize: F.base }}>Node {n.id}</span>
                    <span style={chip(role.color)}>{role.label}</span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 10, rowGap: 3 }}>
                    <span style={label}>Address</span><span style={value}>{n.address}</span>
                    {n.reachable && (
                      <>
                        <span style={label}>Term</span><span style={value}>{n.term ?? '—'}</span>
                        <span style={label}>Committed</span><span style={value}>{fmtIndex(n.commit_index)}</span>
                        <span style={label}>Applied</span><span style={value}>{fmtIndex(n.applied_index)}</span>
                        {(n.rejected_messages ?? 0) > 0 && (
                          <>
                            <span style={label}>Refused</span>
                            <span style={{ ...value, color: C.orange }} title="Forged, too old or replayed messages this node refused">
                              {n.rejected_messages} msg
                            </span>
                          </>
                        )}
                      </>
                    )}
                  </div>
                  {!n.reachable && n.mode && (
                    <div style={{ marginTop: 6, fontSize: F.xs, color: C.dim }}>answers, but runs in {n.mode} mode</div>
                  )}
                </div>
              )
            })}
          </div>

          {status.pending_writes.length > 0 && (
            <div style={{ marginBottom: 10, padding: '6px 10px', borderRadius: 4, background: tint(C.orange, 10), color: C.text, fontSize: F.sm }}>
              Waiting for the cluster to confirm: {status.pending_writes.join(', ')}
            </div>
          )}

          <div style={{ fontSize: F.xs, color: C.dim, lineHeight: 1.6 }}>
            This gateway has adopted log entry {fmtIndex(status.synced_index)}
            {status.last_sync ? ` (checked ${new Date(status.last_sync).toLocaleTimeString()})` : ''}.
            The kill switch, risk limits and venue blacklist are written through the leader and count once {majority} of{' '}
            {nodes.length} nodes have stored them. Without a majority, tightening (engaging the kill switch, blacklisting a
            venue) still happens at once; releasing it, changing limits or un-blacklisting is refused.
          </div>
        </>
      )}
    </Card>
  )
}
