// Number formatting for crypto. Prices span $0.0001 (XRP ticks) to ~$100k
// (BTC) and quantities go down to 0.00001 BTC, so fixed toFixed(2) /
// toFixed(0) formatting breaks at both ends.

export function fmtPrice(p: number | null | undefined): string {
  if (p === null || p === undefined || !isFinite(p) || p <= 0) return '—'
  const max = p >= 1000 ? 2 : p >= 1 ? 4 : 6
  return '$' + p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: max })
}

export function fmtQty(q: number | null | undefined, maxDigits = 8): string {
  if (q === null || q === undefined || !isFinite(q)) return '—'
  return q.toLocaleString('en-US', { maximumFractionDigits: maxDigits })
}

/** Book sizes in tight table cells: 64,808 -> 64.8K, 0.00872 -> 0.00872. */
export function fmtSize(s: number | null | undefined): string {
  if (s === null || s === undefined || !isFinite(s)) return '—'
  if (s >= 1_000_000) return `${(s / 1_000_000).toFixed(2)}M`
  if (s >= 10_000) return `${(s / 1_000).toFixed(1)}K`
  return fmtQty(s, s >= 1 ? 3 : 5)
}

export function fmtUsd(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  const sign = v < 0 ? '-' : ''
  return sign + '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** Signed P&L: +$12.30 / -$4.10 / $0.00. Hides float noise below half a cent. */
export function fmtPnl(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  if (Math.abs(v) < 0.005) return fmtUsd(0, digits)
  return (v > 0 ? '+' : '') + fmtUsd(v, digits)
}

export function spreadBps(bid: number, ask: number): number {
  const mid = (bid + ask) / 2
  return mid > 0 ? ((ask - bid) / mid) * 10_000 : 0
}

/** A fraction as a signed percent: 0.123 -> +12.3%, -0.05 -> -5.0%. */
export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  const fixed = (v * 100).toFixed(digits)
  return (Number(fixed) > 0 ? '+' : '') + (Number(fixed) === 0 ? (0).toFixed(digits) : fixed) + '%'
}

/** Signed basis points: +0.6 bps / -19.7 bps (1 bps = 0.01%). */
export function fmtBps(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  const fixed = v.toFixed(digits)
  // -0.0 reads as a loss that isn't there
  return (Number(fixed) > 0 ? '+' : '') + (Number(fixed) === 0 ? (0).toFixed(digits) : fixed) + ' bps'
}

export function baseAsset(symbol: string): string {
  return symbol.split('/')[0] || symbol
}
