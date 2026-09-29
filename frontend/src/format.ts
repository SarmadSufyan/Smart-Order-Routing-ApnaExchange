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

export function spreadBps(bid: number, ask: number): number {
  const mid = (bid + ask) / 2
  return mid > 0 ? ((ask - bid) / mid) * 10_000 : 0
}

export function baseAsset(symbol: string): string {
  return symbol.split('/')[0] || symbol
}
