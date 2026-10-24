import React from 'react'
import { C, F, tint } from '../theme'
import { baseAsset, fmtPrice, fmtQty, fmtSize, fmtUsd } from '../format'
import { routingStrategyLabel } from '../services/api'
import { venueName } from '../adapters'
import type { RoutingDecision } from '../services/api'

/**
 * RoutingProof
 * ────────────
 * The "show the panel WHY this is the best price" component.
 * Renders:
 *   1. A ranked table of every quoting venue with price, size, eligibility
 *   2. The winning venue(s) highlighted, with how much each received
 *   3. The expected (pre-trade) VWAP from walking the real order book
 *   4. Savings vs the worst venue, when more than one venue competes
 *   5. Plain-English notes explaining the routing decision
 */

interface Props {
  decision: RoutingDecision
  symbol: string
  /** Actual average fill price, to compare with the pre-trade estimate. */
  fillPrice?: number
}

// Local alias so the JSX below reads cleanly; `card` is just the surface.
const card = C.surface

export function RoutingProof({ decision, symbol, fillPrice }: Props) {
  const isBuy = decision.side === 'BUY'
  const unit = decision.base_asset || baseAsset(symbol)
  // Implementation shortfall: how much worse (+) or better (-) the real fill
  // was than the price the router expected from the order book.
  const expected = decision.blended_avg_price
  const shortfallBps = fillPrice && expected
    ? ((fillPrice - expected) / expected) * 10_000 * (isBuy ? 1 : -1)
    : null

  const th: React.CSSProperties = {
    padding: '6px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400,
    borderBottom: `1px solid ${C.border}`, textAlign: 'left', letterSpacing: '.05em',
  }
  const td: React.CSSProperties = { padding: '8px 10px', fontSize: F.base }
  // Decisions made before 2026-10-07 have no fee data: show the old columns only.
  const hasFees = decision.candidates.some((c) => (c.fee_bps ?? 0) > 0 || (c.extra_cost_bps ?? 0) > 0)
  const hasSmart = decision.candidates.some((c) => c.fill_ratio !== undefined && c.fill_ratio !== null)

  return (
    <div>

      {/* ── Header banner with savings highlight ── */}
      <div style={{
        background: decision.total_savings > 0 ? tint(C.green, 12) : card,
        border: `1px solid ${decision.total_savings > 0 ? C.green : C.border}`,
        borderRadius: 6, padding: '12px 14px', marginBottom: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div>
            <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 3 }}>
              SOR decision · {symbol} · {routingStrategyLabel(decision.strategy)}
            </div>
            <div style={{ fontSize: F.md, color: C.text, fontWeight: 600 }}>
              Routed {isBuy ? 'buy' : 'sell'} of {fmtQty(decision.total_allocated)} {unit}
              {' '}· expected VWAP{' '}
              <span style={{ color: C.green }}>{fmtPrice(decision.blended_avg_price)}</span>
            </div>
            <div style={{ fontSize: F.xs, color: C.muted, marginTop: 3 }}>
              {decision.levels_used ? `${decision.levels_used} price level${decision.levels_used === 1 ? '' : 's'} walked` : 'top of book'}
              {decision.expected_slippage_bps !== undefined && ` · ${decision.expected_slippage_bps.toFixed(2)} bps vs best price`}
              {' '}· pre-trade estimate from the live order book
            </div>
            {!!decision.all_in_avg_price && hasFees && (
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 3 }}>
                All-in {isBuy ? 'cost' : 'proceeds'} incl. fees{' '}
                <span style={{ color: C.text, fontWeight: 600 }}>{fmtPrice(decision.all_in_avg_price)}</span>
                {!!decision.expected_fees_quote && <> · expected fees {fmtUsd(decision.expected_fees_quote)}</>}
              </div>
            )}
            {decision.price_cap != null && (
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 3 }}>
                Price cap: no child may trade beyond{' '}
                <span style={{ color: C.orange, fontWeight: 600 }}>{fmtPrice(decision.price_cap)}</span> all-in
              </div>
            )}
            {shortfallBps !== null && (
              <div style={{ fontSize: F.xs, color: C.muted, marginTop: 3 }}>
                Actual fill <span style={{ color: C.text, fontWeight: 600 }}>{fmtPrice(fillPrice)}</span>
                {' '}· shortfall{' '}
                <span style={{ color: shortfallBps > 5 ? C.orange : C.green, fontWeight: 600 }}>
                  {shortfallBps >= 0 ? '+' : ''}{shortfallBps.toFixed(2)} bps
                </span>
                {' '}vs expected (decision uses the real-market book; execution venue may differ slightly)
              </div>
            )}
          </div>
          {decision.total_savings > 0 && (
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em' }}>Saved vs worst venue</div>
              <div style={{ fontSize: F.lg, color: C.green, fontWeight: 700 }}>
                +{fmtUsd(decision.total_savings)}
              </div>
              <div style={{ fontSize: F.xs, color: C.muted }}>
                ({fmtPrice(decision.savings_per_share)}/{unit} × {fmtQty(decision.total_allocated)})
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Split allocation bar (only if it was split) ── */}
      {decision.is_split && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.1em', marginBottom: 6 }}>
            Multi-venue split · order swept {decision.winning_venues.length} venues
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
                  fontSize: F.xs, color: C.bg, fontWeight: 600,
                  borderRight: i < decision.winning_venues.length - 1 ? `1px solid ${C.bg}` : undefined,
                  minWidth: pct < 8 ? 'auto' : undefined,
                }}>
                  {pct > 10 ? `${venueName(c.venue_id)} · ${fmtQty(c.allocated_qty, 5)}` : venueName(c.venue_id)}
                </div>
              )
            })}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.xs, color: C.muted, marginTop: 4 }}>
            <span>
              {decision.candidates.filter((c) => c.is_winner).map((c) =>
                `${fmtQty(c.allocated_qty, 5)} @ ${fmtPrice(c.price)}`
              ).join('  +  ')}
            </span>
            <span>= avg {fmtPrice(decision.blended_avg_price)}</span>
          </div>
        </div>
      )}

      {/* ── Ranked venue table ── */}
      <div style={{
        background: card, border: `1px solid ${C.border}`,
        borderRadius: 6, overflow: 'hidden', marginBottom: 12,
      }}>
        <div style={{ padding: '10px 12px', borderBottom: `1px solid ${C.border}`, fontSize: F.xs, color: C.dim, letterSpacing: '.1em' }}>
          Venue comparison · executable venues ranked by {hasFees ? `all-in ${isBuy ? 'cost (ask + fee)' : 'proceeds (bid − fee)'}` : isBuy ? 'lowest ask' : 'highest bid'}, then excluded venues
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, width: 32 }}>#</th>
              <th style={th}>Venue</th>
              <th style={{ ...th, textAlign: 'right' }}>{isBuy ? 'Ask' : 'Bid'}</th>
              {hasFees && <th style={{ ...th, textAlign: 'right' }}>Fee</th>}
              {hasSmart && <th style={{ ...th, textAlign: 'right' }} title="Share of what we sent this venue that it filled (recent orders)">Filled</th>}
              {hasFees && <th style={{ ...th, textAlign: 'right' }}>All-in</th>}
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
                    {c.rank}
                  </td>
                  <td style={{ ...td, color: C.text, fontWeight: winner ? 700 : 500 }}>
                    {venueName(c.venue_id)} <span style={{ color: C.dim, fontWeight: 400, fontSize: F.xs }}>{c.venue_id}</span>
                    {winner && <span style={{ marginLeft: 6, color: C.green, fontSize: F.xs, fontWeight: 700 }}>✓ Winner</span>}
                    {inelig && <span style={{ marginLeft: 6, color: C.red, fontSize: F.xs }}>Excluded</span>}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: c.eligible ? (isBuy ? C.red : C.green) : C.dim }}>
                    {fmtPrice(c.price)}
                  </td>
                  {hasFees && (
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}
                        title={c.extra_cost_bps ? `${c.fee_bps ?? 0} bps fee + ${c.extra_cost_bps.toFixed(2)} bps expected cost of missing` : undefined}>
                      {(c.fee_bps ?? 0).toFixed(0)} bps{c.extra_cost_bps ? ` +${c.extra_cost_bps.toFixed(1)}` : ''}
                    </td>
                  )}
                  {hasSmart && (
                    <td style={{ ...td, textAlign: 'right', color: c.fill_ratio != null && c.fill_ratio < 0.9 ? C.orange : C.muted }}>
                      {c.fill_ratio != null ? `${(c.fill_ratio * 100).toFixed(0)}%` : '—'}
                    </td>
                  )}
                  {hasFees && (
                    <td style={{ ...td, textAlign: 'right', color: c.eligible ? C.text : C.dim }}>
                      {c.effective_price ? fmtPrice(c.effective_price) : '—'}
                    </td>
                  )}
                  <td style={{ ...td, textAlign: 'right', color: c.eligible ? C.muted : C.dim }}>
                    {fmtSize(c.size)}
                  </td>
                  <td style={{ ...td, textAlign: 'right', color: winner ? C.green : C.dim, fontWeight: winner ? 700 : 400 }}>
                    {c.allocated_qty > 0 ? fmtQty(c.allocated_qty) : '—'}
                  </td>
                  <td style={{ ...td, color: C.muted, fontSize: F.sm }}>
                    {inelig
                      ? c.excluded_reason
                      : c.rank === 1
                        ? hasFees ? 'Best all-in' : `Best ${isBuy ? 'ask' : 'bid'}`
                        : winner
                          ? decision.strategy === 'liquidity_sweep'
                            ? 'Share of the in-cap liquidity'
                            : `Topped up after rank ${c.rank - 1} exhausted`
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
            Routing notes
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
