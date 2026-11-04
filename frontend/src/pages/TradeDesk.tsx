import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle, PageHeader, Pill } from '../components/shared'
import { fmtPrice, fmtQty, fmtUsd } from '../format'
import { api } from '../services/api'
import type { ScanHit, ScanResponse, TimeMachineOdds, TradeDeskPlan, TradeDeskStatus, TradeDeskTf } from '../services/api'
import { Loading } from '../components/Orb'

const TF_WORD: Record<TradeDeskTf, string> = { '5m': '5-minute', '1h': 'hourly', '1d': 'daily' }
const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`)
const signedR = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(3)} R`)
const ago = (ms: number) => {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 90) return `${s}s ago`
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  if (s < 172800) return `${(s / 3600).toFixed(1)} h ago`
  return `${Math.round(s / 86400)} d ago`
}
const when = (ts: number) => new Date(ts).toISOString().slice(5, 16).replace('T', ' ') + ' UTC'

const STATE_COLOR = { triggered: C.green, waiting: C.orange, expired: C.dim } as const
const HISTORY_COLOR = { positive: C.green, too_few: C.dim, negative: C.red } as const
const HISTORY_WORD = { positive: 'Paid before', too_few: 'Too few fires', negative: 'No edge' } as const
const VERDICT = {
  favourable: { color: C.green, word: 'Favourable on this history' },
  mixed: { color: C.orange, word: 'Mixed evidence' },
  coin_flip: { color: C.dim, word: 'A coin flip' },
  skip: { color: C.red, word: 'Skip, or change the bracket' },
} as const

const th: React.CSSProperties = { padding: '6px 8px', fontSize: F.xs, color: C.dim, fontWeight: 600, borderBottom: `1px solid ${C.border}`, textAlign: 'left', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 8px', borderBottom: `1px solid ${C.border2}`, fontSize: F.sm, verticalAlign: 'top' }
const label: React.CSSProperties = { fontSize: F.xs, color: C.dim, letterSpacing: '.06em', marginBottom: 2 }
const tile: React.CSSProperties = { padding: '8px 10px', borderRadius: 4, background: tint(C.dim, 6), minWidth: 0 }
const input: React.CSSProperties = { width: '100%', padding: '6px 8px', borderRadius: 4, border: `1px solid ${C.border}`, background: C.surface, color: C.text, fontFamily: 'inherit', fontSize: F.sm }
const chip = (on: boolean, color: string = C.blue): React.CSSProperties => ({
  padding: '4px 10px', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit', fontSize: F.xs, fontWeight: 700,
  border: `1px solid ${on ? color : C.border}`, background: on ? tint(color, 14) : C.surface, color: on ? color : C.muted,
})
const button = (color: string, disabled: boolean): React.CSSProperties => ({
  padding: '7px 14px', borderRadius: 4, border: 'none', cursor: disabled ? 'default' : 'pointer', fontFamily: 'inherit',
  fontSize: F.sm, fontWeight: 700, background: disabled ? tint(color, 20) : color, color: C.onAccent, opacity: disabled ? 0.7 : 1,
})

/** The time machine's fan: where 10,000 replays put the price, bar by bar, against the bracket. */
function FanChart({ odds }: { odds: TimeMachineOdds }) {
  const W = 600
  const H = 170
  const pad = 6
  const ys = odds.bands.flatMap((b) => [b.p5, b.p95]).concat([odds.stop, odds.target, odds.entry])
  const lo = Math.min(...ys)
  const hi = Math.max(...ys)
  const y = (v: number) => H - pad - ((v - lo) / Math.max(hi - lo, 1e-9)) * (H - 2 * pad)
  const x = (i: number) => pad + (i / Math.max(odds.bands.length - 1, 1)) * (W - 2 * pad)
  const band = (lowKey: 'p5' | 'p25', highKey: 'p95' | 'p75') =>
    odds.bands.map((b, i) => `${x(i)},${y(b[lowKey])}`).concat(odds.bands.map((b, i) => `${x(i)},${y(b[highKey])}`).reverse()).join(' ')
  const line = odds.bands.map((b, i) => `${x(i)},${y(b.p50)}`).join(' ')
  const rule = (v: number, color: string) => <line x1={pad} x2={W - pad} y1={y(v)} y2={y(v)} stroke={color} strokeWidth={1.5} strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: 170, display: 'block', borderRadius: 4, background: tint(C.dim, 5) }}>
        <polygon points={band('p5', 'p95')} fill={tint(C.blue, 14)} stroke="none" />
        <polygon points={band('p25', 'p75')} fill={tint(C.blue, 26)} stroke="none" />
        <polyline points={line} fill="none" stroke={C.blue} strokeWidth={2} vectorEffect="non-scaling-stroke" />
        {rule(odds.target, C.green)}
        {rule(odds.entry, C.muted)}
        {rule(odds.stop, C.red)}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: F.xs, color: C.dim, marginTop: 4, flexWrap: 'wrap', gap: 6 }}>
        <span><span style={{ color: C.green }}>— target {fmtPrice(odds.target)}</span> · <span style={{ color: C.muted }}>— entry {fmtPrice(odds.entry)}</span> · <span style={{ color: C.red }}>— stop {fmtPrice(odds.stop)}</span></span>
        <span>bands: 5–95% and 25–75% of replays; line: the median · {odds.bars} bars</span>
      </div>
    </div>
  )
}

function HistoryCell({ hit }: { hit: ScanHit }) {
  const h = hit.history
  const color = HISTORY_COLOR[hit.history_verdict]
  if (h.samples === 0) return <span style={{ color: C.dim }}>never fired before on this coin</span>
  return (
    <span title={hit.history_text}>
      <b style={{ color }}>{h.wins}W {h.losses}L {h.timeouts}T</b>
      <span style={{ color: C.muted }}> of {h.samples} · {pct(h.win_rate)} vs {pct(h.break_even_win_rate)} needed · {signedR(h.expectancy_r)}</span>
    </span>
  )
}

export function TradeDesk() {
  const [scan, setScan] = useState<ScanResponse | null>(null)
  const [status, setStatus] = useState<TradeDeskStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tf, setTf] = useState<'all' | TradeDeskTf>('all')
  const [setupFilter, setSetupFilter] = useState('all')
  const [onlyTraded, setOnlyTraded] = useState(false)
  const [selected, setSelected] = useState<ScanHit | null>(null)
  const [plan, setPlan] = useState<TradeDeskPlan | null>(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)
  const [equity, setEquity] = useState('1000')
  const [risk, setRisk] = useState('1')
  const [kAtr, setKAtr] = useState('1')
  const [rr, setRr] = useState('1.5')
  const [stopRule, setStopRule] = useState<'atr' | 'range_mid'>('atr')
  const [rescanning, setRescanning] = useState(false)

  const load = useCallback(async () => {
    try {
      const st = await api.getTradeDeskStatus()
      setStatus(st)
      try {
        setScan(await api.getTradeDeskScan())
      } catch (e) {
        if (!(e as Error).message.includes('No scan yet')) throw e
      }
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])

  const rescan = async () => {
    setRescanning(true)
    try {
      setScan(await api.tradeDeskRescan())
      setStatus(await api.getTradeDeskStatus())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRescanning(false)
    }
  }

  const hits = useMemo(() => (scan?.hits ?? []).filter((h) =>
    (tf === 'all' || h.timeframe === tf) && (setupFilter === 'all' || h.setup === setupFilter) && (!onlyTraded || h.traded_here)),
  [scan, tf, setupFilter, onlyTraded])

  const pick = (hit: ScanHit) => {
    setSelected(hit)
    setPlan(null)
    setPlanError(null)
    setStopRule(hit.setup === 'range_breakout' ? 'range_mid' : 'atr')
  }

  const makePlan = async () => {
    if (!selected) return
    setPlanBusy(true)
    setPlanError(null)
    try {
      setPlan(await api.makeTradeDeskPlan({
        symbol: selected.symbol, timeframe: selected.timeframe, setup: selected.setup, equity: Number(equity),
        risk_pct: Number(risk), k_atr: Number(kAtr), rr: Number(rr), stop_rule: stopRule,
      }))
    } catch (e) {
      setPlanError((e as Error).message)
    } finally {
      setPlanBusy(false)
    }
  }

  const setups = scan?.setups ?? status?.setups ?? []
  const now = Date.now()
  const odds = plan?.time_machine.odds

  return (
    <div>
      <PageHeader
        title="Trade Desk"
        badge={status && (
          <Pill tone={status.scanning || rescanning ? C.blue : scan ? C.green : C.dim}>
            {status.scanning || rescanning ? 'Scanning…' : scan ? `Scanned ${ago(now - scan.as_of_ts)}` : 'No scan yet'}
          </Pill>
        )}
        subtitle={status
          ? `A scanner over ${status.universe.coins} coins (${status.universe.source}), every setup measured against its own history before anyone acts. Nothing here sends an order.`
          : 'A scanner over every coin the exchanges share, with the odds measured. Nothing here sends an order.'}
        about={<>
          Every {status ? Math.round(status.interval_s / 60) : 5} minutes the desk refreshes small candle histories for every coin and looks for eight
          classic setups: channel breakouts, a reclaimed 20-bar average, oversold bounces, volume surges, pull-backs in an uptrend, bull flags, volatility
          squeezes, and opening-range breakouts after the {status?.session.opens_utc.join(', ')} UTC session opens (the first {status?.session.range_minutes}
          minutes' range, entries within {status ? status.session.entry_window_minutes / 60 : 4} hours). A setup is a pattern, not a forecast, so each hit shows what
          happened <b>every past time the same setup fired on the same coin</b> with the desk's bracket (stop 1 ATR below, target 1.5 × that above, a time limit
          of a day of hours, ten days, or four hours of 5-minute bars), after fees and slippage. "Plan" sizes the trade and runs the <b>time machine</b>:
          {' '}{status?.time_machine.replays.toLocaleString()} replays of the next bars drawn from the coin's own bars at the same time of day over the last
          {' '}{status?.time_machine.days} days, the stop checked before the target. Past results and replays of them: never a promise.
        </>}
        actions={<button onClick={rescan} disabled={rescanning || !!status?.scanning} style={button(C.blue, rescanning || !!status?.scanning)}>{rescanning ? 'Scanning…' : 'Scan now'}</button>}
      />

      {error && <div style={{ fontSize: F.sm, color: C.red, marginBottom: 10 }}>{error}</div>}

      <Card style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <CardTitle>
            Live setups — {hits.length} of {scan?.hits.length ?? 0}
            {scan && <span style={{ fontWeight: 400, color: C.dim }}> · {scan.universe.with_data} of {scan.universe.coins} coins had candles · took {scan.took_s}s</span>}
          </CardTitle>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
            {(['all', '5m', '1h', '1d'] as const).map((x) => <button key={x} onClick={() => setTf(x)} style={chip(tf === x)}>{x === 'all' ? 'All bars' : TF_WORD[x]}</button>)}
            <button onClick={() => setOnlyTraded((v) => !v)} style={chip(onlyTraded, C.purple)}>Coins we trade</button>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 10 }}>
          <button onClick={() => setSetupFilter('all')} style={chip(setupFilter === 'all')}>Every setup</button>
          {setups.map((s) => <button key={s.name} onClick={() => setSetupFilter(s.name)} style={chip(setupFilter === s.name)} title={s.idea}>{s.label}</button>)}
        </div>
        {!scan ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: F.sm, color: C.dim }}><Loading /> The first scan runs shortly after start-up: candles for every coin are being collected.</div>
        ) : hits.length === 0 ? (
          <div style={{ fontSize: F.sm, color: C.dim }}>No live setup matches these filters right now. Quiet is an answer too.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>Coin</th><th style={th}>Setup</th><th style={th}>Bars</th><th style={th}>Fired</th>
                  <th style={{ ...th, textAlign: 'right' }}>Price</th><th style={{ ...th, textAlign: 'right' }}>Trigger</th>
                  <th style={th}>Entry timer</th><th style={th}>Every past fire, this bracket</th><th style={th}>History says</th>
                  <th style={{ ...th, textAlign: 'right' }}>Traded / day</th><th style={th}></th>
                </tr>
              </thead>
              <tbody>
                {hits.map((h) => {
                  const key = `${h.symbol}|${h.setup}|${h.timeframe}`
                  const on = selected && `${selected.symbol}|${selected.setup}|${selected.timeframe}` === key
                  return (
                    <tr key={key} style={{ background: on ? tint(C.blue, 8) : undefined }}>
                      <td style={td}><b>{h.symbol.replace('/USDT', '')}</b>{h.traded_here && <span style={{ color: C.purple, fontSize: F.xs }} title="In the trading universe: a plan can route it"> ●</span>}</td>
                      <td style={td} title={h.idea}>{h.label}<div style={{ fontSize: F.xs, color: C.dim }}>{h.kind === 'watch' ? 'watch: trigger ahead' : 'entry: the bar is the trigger'}</div></td>
                      <td style={td}>{TF_WORD[h.timeframe]}</td>
                      <td style={td} title={when(h.bar_ts)}>{h.age_bars === 0 ? 'latest bar' : `${h.age_bars} bar${h.age_bars > 1 ? 's' : ''} ago`}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{fmtPrice(h.live_price ?? h.price)}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{fmtPrice(h.trigger)}</td>
                      <td style={td}><span style={{ color: STATE_COLOR[h.state], fontWeight: 700 }}>{h.state}</span><div style={{ fontSize: F.xs, color: C.dim, maxWidth: 220 }}>{h.state_text}</div></td>
                      <td style={td}><HistoryCell hit={h} /></td>
                      <td style={td}><span style={{ fontSize: F.xs, padding: '1px 7px', borderRadius: 3, fontWeight: 700, background: tint(HISTORY_COLOR[h.history_verdict], 14), color: HISTORY_COLOR[h.history_verdict] }}>{HISTORY_WORD[h.history_verdict]}</span></td>
                      <td style={{ ...td, textAlign: 'right' }}>{fmtUsd(h.dollar_volume, 0)}</td>
                      <td style={td}><button onClick={() => pick(h)} style={chip(!!on, C.purple)}>Plan</button></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {scan && Object.keys(scan.errors).length > 0 && (
          <div style={{ fontSize: F.xs, color: C.dim, marginTop: 8 }}>Candles missing for {Object.keys(scan.errors).length} coin-timeframes (an exchange did not answer); they are retried at the next scan.</div>
        )}
      </Card>

      {selected && (
        <Card style={{ marginBottom: 12 }}>
          <CardTitle>Plan — {selected.symbol} · {selected.label} on {TF_WORD[selected.timeframe]} bars</CardTitle>
          <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 8 }}>{selected.idea}. {Object.entries(selected.details).map(([k, v]) => `${k.replace(/_/g, ' ')} ${typeof v === 'number' ? (Math.abs(v) >= 1e9 ? when(v) : v.toLocaleString(undefined, { maximumFractionDigits: 6 })) : v ?? '—'}`).join(' · ')}</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 120px), 1fr))', gap: 8, marginBottom: 8 }}>
            <div><div style={label}>Money (USDT)</div><input value={equity} onChange={(e) => setEquity(e.target.value)} style={input} /></div>
            <div><div style={label}>Risk %</div><input value={risk} onChange={(e) => setRisk(e.target.value)} style={input} /></div>
            <div><div style={label}>Stop (× ATR)</div><input value={kAtr} onChange={(e) => setKAtr(e.target.value)} style={input} disabled={stopRule === 'range_mid'} /></div>
            <div><div style={label}>Reward : risk</div><input value={rr} onChange={(e) => setRr(e.target.value)} style={input} /></div>
            {selected.setup === 'range_breakout' && (
              <div><div style={label}>Stop rule</div>
                <select value={stopRule} onChange={(e) => setStopRule(e.target.value as 'atr' | 'range_mid')} style={input}>
                  <option value="range_mid">Opening range's midpoint</option><option value="atr">ATR below the entry</option>
                </select>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button onClick={makePlan} disabled={planBusy} style={button(C.purple, planBusy)}>{planBusy ? 'Replaying…' : 'Size it and run the time machine'}</button>
            <span style={{ fontSize: F.xs, color: C.dim }}>Stop and target from the rule, size from the risk you accept, then every past fire and {status?.time_machine.replays.toLocaleString()} replays. Nothing is sent.</span>
          </div>
          {planError && <div style={{ fontSize: F.sm, color: C.red, marginTop: 6 }}>{planError}</div>}
          {plan && odds && (
            <div style={{ marginTop: 12 }}>
              <div style={{ padding: '8px 10px', borderRadius: 4, marginBottom: 10, fontSize: F.sm, fontWeight: 700, background: tint(VERDICT[plan.verdict].color, 12), color: VERDICT[plan.verdict].color }}>
                {VERDICT[plan.verdict].word} — {plan.verdict_text}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 110px), 1fr))', gap: 6, marginBottom: 10 }}>
                <div style={tile}><div style={label}>Entry</div><div style={{ fontSize: F.md, fontWeight: 700 }}>{fmtPrice(plan.plan.entry)}</div><div style={{ fontSize: F.xs, color: C.dim }}>{plan.entry_source}</div></div>
                <div style={tile}><div style={label}>Stop</div><div style={{ fontSize: F.md, fontWeight: 700, color: C.red }}>{fmtPrice(plan.plan.stop)}</div><div style={{ fontSize: F.xs, color: C.dim }}>−{fmtUsd(plan.plan.risk_amount)} if hit</div></div>
                <div style={tile}><div style={label}>Target</div><div style={{ fontSize: F.md, fontWeight: 700, color: C.green }}>{fmtPrice(plan.plan.target)}</div><div style={{ fontSize: F.xs, color: C.dim }}>+{fmtUsd(plan.plan.reward_amount)} if hit</div></div>
                <div style={tile}><div style={label}>Size</div><div style={{ fontSize: F.md, fontWeight: 700 }}>{fmtQty(plan.plan.quantity, 6)}</div><div style={{ fontSize: F.xs, color: C.dim }}>{fmtUsd(plan.plan.notional)} · fees {fmtUsd(plan.plan.fees_round_trip)}</div></div>
                <div style={tile}><div style={label}>History</div><div style={{ fontSize: F.md, fontWeight: 700, color: HISTORY_COLOR[plan.history_verdict] }}>{pct(plan.history.win_rate)}</div><div style={{ fontSize: F.xs, color: C.dim }}>needs {pct(plan.history.break_even_win_rate)} · {plan.history.samples} fires · {signedR(plan.history.expectancy_r)}</div></div>
                <div style={tile}><div style={label}>Time machine</div><div style={{ fontSize: F.md, fontWeight: 700, color: odds.verdict === 'favourable' ? C.green : odds.verdict === 'unfavourable' ? C.red : C.muted }}>{pct(odds.p_target)} target first</div><div style={{ fontSize: F.xs, color: C.dim }}>{pct(odds.p_stop)} stop first · {pct(odds.p_neither)} neither · {signedR(odds.avg_r)}</div></div>
              </div>
              <FanChart odds={odds} />
              <div style={{ marginTop: 10 }}>
                {plan.reasons.map((r) => <div key={r} style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.6 }}>• {r}</div>)}
              </div>
              {plan.history.recent.length > 0 && (
                <div style={{ marginTop: 8, fontSize: F.xs, color: C.dim }}>
                  Last fires: {plan.history.recent.map((r) => (
                    <span key={r.ts} style={{ marginRight: 10, color: r.outcome === 'target' ? C.green : r.outcome === 'stop' ? C.red : C.muted }}>
                      {when(r.ts)} {r.outcome} ({r.r >= 0 ? '+' : ''}{r.r.toFixed(2)} R in {r.bars} bars)
                    </span>
                  ))}
                </div>
              )}
              <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6 }}>{plan.time_machine.note} Pool: {plan.time_machine.pool.bars.toLocaleString()} bars, about {plan.time_machine.pool.sessions} per time-of-day slot. {plan.disclaimer}</div>
            </div>
          )}
        </Card>
      )}

      <Card>
        <CardTitle>The setups the desk looks for</CardTitle>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 8 }}>
          {setups.map((s) => (
            <div key={s.name} style={tile}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                <b style={{ fontSize: F.sm }}>{s.label}</b>
                <span style={{ fontSize: F.xs, color: C.dim }}>{s.timeframes.map((t) => TF_WORD[t]).join(', ')} · {s.kind}</span>
              </div>
              <div style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.5, marginTop: 3 }}>{s.idea}</div>
            </div>
          ))}
        </div>
        {scan && <div style={{ fontSize: F.xs, color: C.dim, marginTop: 8 }}>{scan.note}</div>}
      </Card>
    </div>
  )
}
