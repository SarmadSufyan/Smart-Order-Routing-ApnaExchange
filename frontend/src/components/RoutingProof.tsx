import React from 'react'
import { C, F, tint } from '../theme'
import type { RoutingDecision } from '../services/api'

/**
 * RoutingProof
 * ────────────
 * The "show the panel WHY this is the best price" component.
 * Renders:
 *   1. A ranked table of all 5 venues with their prices, sizes, eligibility
 *   2. The winning venue(s) highlighted, with how many shares each got
 *   3. The blended avg fill price + savings vs the worst venue
 *   4. A stacked bar visualizing the split allocation
 *   5. Plain-English notes explaining the routing decision
 */

interface Props {
  decision: RoutingDecision
  symbol: string
}

// Local alias so the JSX below reads cleanly; `card` is just the surface.
const card = C.surface

export function RoutingProof({ decision, symbol }: Props) {
  const isBuy = decision.side === 'BUY'

  const th: React.CSSProperties = {
    padding: '6px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400,
    borderBottom: `1px solid ${C.border}`, textAlign: 'left', letterSpacing: '.05em',
  }
  const td: React.CSSProperties = { padding: '8px 10px', fontSize: F.base }

  return (
    <div style={{ fontFamily: "'Consolas','IBM Plex Mono',monospace" }}>

      {/* ── Header banner with savings highlight ── */}
      <div style={{
        background: decision.total_savings > 0 ? tint(C.green, 12) : card,
        border: `1px solid ${decision.total_savings > 0 ? C.green : C.border}`,
        borderRadius: 6, padding: '12px 14px', marginBottom: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div>
            <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 3 }}>
              SOR DECISION · {symbol}
            </div>
            <div style={{ fontSize: F.md, color: C.text, fontWeight: 600 }}>
              {isBuy ? 'Bought' : 'Sold'} {decision.total_allocated.toFixed(0)} shares
              {' '}@ blended avg{' '}
              <span style={{ color: C.green }}>${decision.blended_avg_price.toFixed(4)}</span>
            </div>
          </div>
          {decision.total_savings > 0 && (
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em' }}>SAVED vs WORST VENUE</div>
              <div style={{ fontSize: F.lg, color: C.green, fontWeight: 700 }}>
                +${decision.total_savings.toFixed(2)}
              </div>
              <div style={{ fontSize: F.xs, color: C.muted }}>
                (${decision.savings_per_share.toFixed(4)}/sh × {decision.total_allocated.toFixed(0)})
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Split allocation bar (only if it was split) ── */}
      {decision.is_split && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>
            ⚡ MULTI-VENUE SPLIT · order swept {decision.winning_venues.length} venues
          </div>
          <div style={{
            display: 'flex', height: 28, borderRadius: 4, overflow: 'hidden',
            border: `1px solid ${C.border}`, background: C.bg,
          }}>
            {decision.candidates.filter((c) => c.is_winner).map((c, i) => {
              const pct = (c.allocated_qty / decision.total_allocated) * 100
              const colors = [C.green, C.blue, C.orange, C.gold, C.purple]
              return (
                <div key={c.venue_id} style={{
                  width: `${pct}%`, background: colors[i % colors.length],
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: F.xs, color: '#fff', fontWeight: 600,
                  borderRight: i < decision.winning_venues.length - 1 ? `1px solid ${C.bg}` : undefined,
                  minWidth: pct < 8 ? 'auto' : undefined,
                }}>
                  {pct > 10 ? `${c.venue_id} · ${c.allocated_qty.toFixed(0)}` : c.venue_id}
                </div>
              )
            })}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.xs, color: C.muted, marginTop: 4 }}>
            <span>
              {decision.candidates.filter((c) => c.is_winner).map((c) =>
                `${c.allocated_qty.toFixed(0)}@$${c.price.toFixed(2)}`
              ).join('  +  ')}
            </span>
            <span>= avg ${decision.blended_avg_price.toFixed(4)}</span>
          </div>
        </div>
      )}

      {/* ── Ranked venue table ── */}
      <div style={{
        background: card, border: `1px solid ${C.border}`,
        borderRadius: 6, overflow: 'hidden', marginBottom: 12,
      }}>
        <div style={{ padding: '10px 12px', borderBottom: `1px solid ${C.border}`, fontSize: F.xs, color: C.dim, letterSpacing: '.1em' }}>
          VENUE COMPARISON · ranked by {isBuy ? 'lowest ask' : 'highest bid'}
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, width: 32 }}>#</th>
              <th style={th}>Venue</th>
              <th style={{ ...th, textAlign: 'right' }}>{isBuy ? 'Ask' : 'Bid'}</th>
              <th style={{ ...th, textAlign: 'right' }}>Avail</th>
              <th style={{ ...th, textAlign: 'right' }}>Allocated</th>
              <th style={th}>Note</th>
            </tr>
          </thead>
          <tbody>
            {decision.candidates.map((c) => {
              const winner = c.is_winner
              const inelig = !c.eligible
              const bg = winner ? tint(C.green, 10) : inelig ? tint(C.red, 6) : undefined
              const rankColor = c.rank === 1 ? C.gold : C.dim
              return (
                <tr key={c.venue_id} style={{
                  background: bg,
                  borderBottom: `1px solid ${tint(C.border, 40)}`,
                }}>
                  <td style={{ ...td, color: rankColor, fontWeight: c.rank === 1 ? 700 : 400 }}>
                    {c.rank === 1 ? '🥇' : c.rank}
                  </td>
                  <td style={{ ...td, color: C.text, fontWeight: winner ? 700 : 500 }}>
                    {c.venue_id}
                    {winner && <span style={{ marginLeft: 6, color: C.green, fontSize: F.xs, fontWeight: 700 }}>✓ WINNER</span>}
                    {inelig && <span style={{ marginLeft: 6, color: C.red, fontSize: F.xs }}>EXCLUDED</span>}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: c.eligible ? (isBuy ? C.red : C.green) : C.dim }}>
                    {c.price > 0 ? `$${c.price.toFixed(2)}` : '—'}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: c.eligible ? C.muted : C.dim }}>
                    {c.size.toFixed(0)}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: winner ? C.green : C.dim, fontWeight: winner ? 700 : 400 }}>
                    {c.allocated_qty > 0 ? c.allocated_qty.toFixed(0) : '—'}
                  </td>
                  <td style={{ ...td, color: C.muted, fontSize: F.sm }}>
                    {inelig
                      ? c.excluded_reason
                      : c.rank === 1
                        ? `Best ${isBuy ? 'ask' : 'bid'}`
                        : winner
                          ? `Topped up after rank ${c.rank - 1} exhausted`
                          : 'Not needed'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ── Plain-English explanation ── */}
      {decision.notes.length > 0 && (
        <div style={{
          background: C.bg, border: `1px solid ${C.border}`,
          borderRadius: 6, padding: '10px 12px',
        }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>
            ROUTING NOTES
          </div>
          {decision.notes.map((n, i) => (
            <div key={i} style={{ fontSize: F.base, color: C.text, lineHeight: 1.6 }}>
              <span style={{ color: C.blue, marginRight: 6 }}>›</span>{n}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
