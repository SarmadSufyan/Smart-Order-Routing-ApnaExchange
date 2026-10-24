import React, { useCallback, useEffect, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle, PageHeader } from '../components/shared'
import { EquityChart, ResearchChart } from '../components/ResearchChart'
import { fmtPct, fmtPrice, fmtQty, fmtUsd, sentence } from '../format'
import { SYMBOLS } from '../mockData'
import { api } from '../services/api'
import type {
  BacktestResponse, BacktestRule, BookSignals, ForecastResponse, HorizonForecast, ResearchChart as ChartData,
  ResearchTf, ScoreboardRow, TechniqueStudy, TradePlanResponse,
} from '../services/api'
import { MQ, useMediaQuery } from '../hooks/useMediaQuery'
import { Loading } from '../components/Orb'

const TF_LABEL: Record<ResearchTf, string> = { '1m': '1 minute', '1h': '1 hour', '1d': '1 day' }
const TF_PLURAL: Record<ResearchTf, string> = { '1m': 'minutes', '1h': 'hours', '1d': 'days' }
const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`)
const signed = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`)

const RULES: { id: BacktestRule; label: string; hint: string; params: Record<string, number | string> }[] = [
  { id: 'buy_hold', label: 'Buy & hold', hint: 'The benchmark: buy once, never sell.', params: {} },
  { id: 'sma_cross', label: 'Moving-average cross', hint: 'In while the fast average is above the slow one (trend following).', params: { fast: 20, slow: 50 } },
  { id: 'rsi_reversion', label: 'RSI reversion', hint: 'Buy when RSI is oversold, sell when overbought (mean reversion).', params: { period: 14, buy_below: 30, sell_above: 70 } },
  { id: 'bollinger', label: 'Bollinger bands', hint: 'Reversion: buy under the lower band, sell at the middle. Breakout: the opposite.', params: { period: 20, k: 2, mode: 'reversion' } },
  { id: 'macd_cross', label: 'MACD cross', hint: 'In while MACD is above its signal line (momentum).', params: {} },
  { id: 'momentum', label: 'Time-series momentum', hint: 'In while the price is above where it was one lookback ago (Moskowitz, Ooi & Pedersen).', params: { lookback: 168 } },
  { id: 'donchian_breakout', label: 'Channel breakout (Turtle)', hint: 'Buy a close above the recent high, sell below the shorter recent low.', params: { period: 48, exit_period: 24 } },
  { id: 'model', label: 'Forecast model', hint: 'In while the out-of-sample forecast says P(up) > threshold.', params: { threshold: 0.55 } },
]

function Prob({ f }: { f: HorizonForecast }) {
  const p = f.p_up ?? 0.5
  const color = !f.edge ? C.dim : p >= 0.5 ? C.green : C.red
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: F.xxl, fontWeight: 700, color }}>{(p * 100).toFixed(1)}%</span>
        <span style={{ fontSize: F.sm, color: C.muted }}>chance it closes higher</span>
      </div>
      <div style={{ position: 'relative', height: 8, borderRadius: 4, background: `linear-gradient(90deg, ${tint(C.red, 40)}, ${tint(C.dim, 25)} 50%, ${tint(C.green, 40)})`, margin: '6px 0 4px' }}>
        <div style={{ position: 'absolute', left: '50%', top: -3, bottom: -3, width: 1, background: C.muted }} />
        <div style={{ position: 'absolute', left: `calc(${p * 100}% - 5px)`, top: -3, width: 10, height: 14, borderRadius: 3, background: color, border: `2px solid ${C.surface}` }} />
      </div>
    </div>
  )
}

function ForecastCard({ f }: { f: HorizonForecast }) {
  const label: React.CSSProperties = { fontSize: F.xs, color: C.dim, letterSpacing: '.06em', marginTop: 8 }
  if (f.unavailable) {
    return (
      <Card>
        <CardTitle>{f.label.charAt(0).toUpperCase() + f.label.slice(1)}</CardTitle>
        <div style={{ fontSize: F.sm, color: C.muted }}>{f.unavailable}</div>
      </Card>
    )
  }
  const oos = f.model?.out_of_sample
  return (
    <Card style={{ border: `1px solid ${f.edge ? (f.lean === 'up' ? C.green : C.red) : C.border}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <CardTitle>{f.label.charAt(0).toUpperCase() + f.label.slice(1)}</CardTitle>
        <span style={{
          fontSize: F.xs, padding: '1px 7px', borderRadius: 3, fontWeight: 700,
          background: tint(f.edge ? C.green : C.dim, 14), color: f.edge ? C.green : C.muted,
        }}>{f.edge ? 'Small proven edge' : 'No proven edge'}</span>
      </div>
      <Prob f={f} />
      <div style={{ fontSize: F.sm, color: f.edge ? C.text : C.muted, lineHeight: 1.45 }}>{f.call}</div>

      <div style={label}>Likely range at the close</div>
      <div style={{ fontSize: F.sm, color: C.text }}>
        80%: {f.range80 ? `${fmtPrice(f.range80[0])} – ${fmtPrice(f.range80[1])}` : '—'}
      </div>
      <div style={{ fontSize: F.xs, color: C.muted }}>
        50%: {f.range50 ? `${fmtPrice(f.range50[0])} – ${fmtPrice(f.range50[1])}` : '—'} · now {fmtPrice(f.price)} · typical move ±{f.typical_move_pct?.toFixed(3)}%
      </div>

      <div style={label}>Track record</div>
      <div style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.6 }}>
        {oos ? <>
          Tested on {oos.n.toLocaleString()} past {TF_PLURAL[f.horizon]} it had not seen: right <b style={{ color: C.text }}>{pct(oos.accuracy)}</b> vs {pct(oos.baseline_accuracy)} for always guessing the commoner side
          {' '}(p = {oos.p_value.toFixed(3)}); Brier skill {oos.brier_skill >= 0 ? '+' : ''}{(oos.brier_skill * 100).toFixed(2)}%
        </> : 'Not enough history to test yet'}
        <br />
        Range: the 80% band held {pct(f.model?.range_coverage80 ?? null)} of the time out of sample
        <br />
        Live since this machine started logging: {f.live && f.live.n > 0
          ? <>{f.live.n} scored · right {pct(f.live.hit_rate)} · in range {pct(f.live.range_coverage80 ?? null)}</>
          : 'no scored forecasts yet'}
      </div>

      {f.drivers && f.drivers.length > 0 && <>
        <div style={label}>What moved this forecast most</div>
        {f.drivers.slice(0, 3).map((d) => (
          <div key={d.feature} style={{ fontSize: F.xs, color: C.muted }}>
            <span style={{ color: d.push >= 0 ? C.green : C.red }}>{d.push >= 0 ? '▲' : '▼'}</span> {d.text}
          </div>
        ))}
      </>}
    </Card>
  )
}

const day = (ts: number) => new Date(ts).toISOString().slice(0, 10)

/** Every classic technique on the coin's whole history (years of candles): what worked after fees, what each says now. */
function TechniquesCard({ symbol }: { symbol: string }) {
  const [tf, setTf] = useState<ResearchTf>('1h')
  const [study, setStudy] = useState<TechniqueStudy | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setStudy(null)
    setError(null)
    api.researchTechniques(symbol, tf)
      .then((s) => { if (live) setStudy(s) })
      .catch((e) => { if (live) setError((e as Error).message) })
    return () => { live = false }
  }, [symbol, tf])
  const verdictColor = (v: string) => (v === 'worked' ? C.green : v === 'mixed' ? C.orange : C.dim)
  const tab = (on: boolean): React.CSSProperties => ({
    padding: '4px 10px', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit', fontSize: F.xs, fontWeight: 700,
    border: `1px solid ${on ? C.blue : C.border}`, background: on ? tint(C.blue, 14) : C.surface, color: on ? C.blue : C.muted,
  })
  const th: React.CSSProperties = { padding: '6px 8px', fontSize: F.xs, color: C.dim, fontWeight: 600, borderBottom: `1px solid ${C.border}` }
  const td: React.CSSProperties = { padding: '6px 8px', borderBottom: `1px solid ${C.border2}` }
  return (
    <Card style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <CardTitle>Trading techniques on {symbol} — WHAT WORKED AFTER FEES, WHAT EACH SAYS NOW</CardTitle>
        <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
          {(['1h', '1d'] as ResearchTf[]).map((x) => (
            <button key={x} onClick={() => setTf(x)} style={tab(x === tf)}>{x === '1h' ? 'Hourly' : 'Daily'}</button>
          ))}
        </div>
      </div>
      {error ? <div style={{ fontSize: F.sm, color: C.red }}>{error}</div>
        : !study ? <div style={{ fontSize: F.sm, color: C.dim }}>Testing every technique on the full history…</div>
        : <>
          <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 8, lineHeight: 1.6 }}>
            {study.bars.toLocaleString()} {tf === '1h' ? 'hourly' : 'daily'} candles, {day(study.from_ts)} to {day(study.to_ts)};
            recent period from {day(study.recent_from_ts)}. Simply holding {symbol.split('/')[0]}: {fmtPct(study.hold.full.total_return)} over
            the whole period, {fmtPct(study.hold.recent.total_return)} recently. Every trade pays {study.fee_bps} bps fee
            + {study.slippage_bps} bps slippage.
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: F.sm }}>
              <thead>
                <tr>
                  {['Technique', 'Family', 'Says now', 'Whole period', 'Recent', 'Sharpe', 'Trades', 'Verdict'].map((h, i) => (
                    <th key={h} style={{ ...th, textAlign: i < 3 || i === 7 ? 'left' : 'right' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {study.techniques.map((r) => (
                  <tr key={r.key} title={`${r.idea}. ${r.why}`}>
                    <td style={{ ...td, color: C.text, fontWeight: 600 }}>{r.name}</td>
                    <td style={{ ...td, color: C.muted }}>{r.family}</td>
                    <td style={{ ...td, color: r.signal === 'in' ? C.green : C.muted, fontWeight: 700 }}>
                      {r.signal === 'in' ? 'IN' : 'OUT'}{r.fresh ? ' (new)' : ''}
                    </td>
                    <td style={{ ...td, textAlign: 'right', color: r.full.total_return >= 0 ? C.green : C.red }}>{fmtPct(r.full.total_return)}</td>
                    <td style={{ ...td, textAlign: 'right', color: r.recent.total_return >= 0 ? C.green : C.red }}>{fmtPct(r.recent.total_return)}</td>
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}>{r.full.sharpe?.toFixed(2) ?? '—'}</td>
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}>{r.full.trades}</td>
                    <td style={{ ...td, color: verdictColor(r.verdict), fontWeight: 700 }}>{r.verdict}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: F.xs, color: C.dim, marginTop: 8, lineHeight: 1.5 }}>
            "worked" = beat simply holding on risk-adjusted return over the whole period and recently, and made money recently.
            {' '}{study.note}
          </div>
        </>}
    </Card>
  )
}

export function Research() {
  const isNarrow = useMediaQuery(MQ.narrow)
  const [symbol, setSymbol] = useState<string>(SYMBOLS[0])
  const [tf, setTf] = useState<ResearchTf>('1h')
  const [forecast, setForecast] = useState<ForecastResponse | null>(null)
  const [chart, setChart] = useState<ChartData | null>(null)
  const [book, setBook] = useState<BookSignals | null>(null)
  const [board, setBoard] = useState<ScoreboardRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // backtest
  const [rule, setRule] = useState<BacktestRule>('sma_cross')
  const [ruleParams, setRuleParams] = useState<Record<string, number | string>>(RULES[1].params)
  const [btTf, setBtTf] = useState<ResearchTf>('1h')
  const [fee, setFee] = useState('10')
  const [slip, setSlip] = useState('2')
  const [bt, setBt] = useState<BacktestResponse | null>(null)
  const [btBusy, setBtBusy] = useState(false)
  const [btError, setBtError] = useState<string | null>(null)

  // trade plan
  const [planTf, setPlanTf] = useState<ResearchTf>('1h')
  const [equity, setEquity] = useState('1000')
  const [risk, setRisk] = useState('1')
  const [kAtr, setKAtr] = useState('1.5')
  const [rr, setRr] = useState('2')
  const [maxBars, setMaxBars] = useState('24')
  const [plan, setPlan] = useState<TradePlanResponse | null>(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)

  const loadForecast = useCallback(async () => {
    try {
      setForecast(await api.getResearchForecast(symbol))
      setError(null)
    } catch (e) { setError((e as Error).message) }
  }, [symbol])

  const loadChart = useCallback(async () => {
    try { setChart(await api.getResearchChart(symbol, tf, tf === '1d' ? 365 : 240)) } catch (e) { setError((e as Error).message) }
  }, [symbol, tf])

  useEffect(() => { setForecast(null); loadForecast() }, [loadForecast])
  useEffect(() => { loadChart() }, [loadChart])
  useEffect(() => {
    const t = setInterval(() => { loadForecast(); loadChart() }, 30_000)
    return () => clearInterval(t)
  }, [loadForecast, loadChart])
  useEffect(() => {
    let alive = true
    const tick = () => api.getResearchOrderbook(symbol).then((b) => { if (alive) setBook(b) }).catch(() => undefined)
    tick()
    const t = setInterval(tick, 3_000)
    return () => { alive = false; clearInterval(t) }
  }, [symbol])
  useEffect(() => {
    api.getResearchScoreboard().then(setBoard).catch(() => undefined)
    const t = setInterval(() => api.getResearchScoreboard().then(setBoard).catch(() => undefined), 60_000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => { setBt(null); setPlan(null) }, [symbol])

  async function runBacktest() {
    setBtBusy(true)
    setBtError(null)
    try {
      setBt(await api.runBacktest({ symbol, timeframe: btTf, strategy: rule, params: ruleParams, bars: 5000,
                                    fee_bps: parseFloat(fee), slippage_bps: parseFloat(slip) }))
    } catch (e) { setBt(null); setBtError((e as Error).message) }
    setBtBusy(false)
  }

  async function makePlan() {
    setPlanBusy(true)
    setPlanError(null)
    try {
      setPlan(await api.makeTradePlan({ symbol, timeframe: planTf, equity: parseFloat(equity), risk_pct: parseFloat(risk),
                                        k_atr: parseFloat(kAtr), rr: parseFloat(rr), max_bars: parseInt(maxBars, 10) }))
    } catch (e) { setPlan(null); setPlanError((e as Error).message) }
    setPlanBusy(false)
  }

  const horizon = forecast?.horizons.find((h) => h.horizon === tf)
  const label: React.CSSProperties = { fontSize: F.xs, color: C.dim, letterSpacing: '.06em', marginBottom: 4 }
  const input: React.CSSProperties = {
    width: '100%', padding: '6px 8px', borderRadius: 4, background: C.surface, border: `1px solid ${C.border}`,
    color: C.text, fontSize: F.sm, fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box',
  }
  const tile: React.CSSProperties = { padding: '8px 10px', borderRadius: 4, background: C.surface, minWidth: 0 }
  const pill = (on: boolean): React.CSSProperties => ({
    padding: '5px 12px', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit', fontSize: F.sm, fontWeight: 700,
    border: `1px solid ${on ? C.blue : C.border}`, background: on ? tint(C.blue, 14) : C.surface, color: on ? C.blue : C.muted,
  })
  const button = (color: string, busy: boolean): React.CSSProperties => ({
    padding: '7px 14px', borderRadius: 4, border: `1px solid ${color}`, background: tint(color, 12), color,
    fontFamily: 'inherit', fontSize: F.sm, fontWeight: 700, cursor: busy ? 'wait' : 'pointer',
  })
  const th: React.CSSProperties = { padding: '5px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '5px 8px', fontSize: F.sm, color: C.text, borderBottom: `1px solid ${C.border2}`, whiteSpace: 'nowrap' }
  const rule0 = RULES.find((r) => r.id === rule)!

  return (
    <div>
      <PageHeader
        title="Research"
        subtitle={<>
          Forecasts, indicators, a backtester and a trade planner on the real market's candles.{' '}
          <span style={{ color: C.orange }}>Research, not financial advice: nothing here places an order.</span>
        </>}
        about={<>
          Forecasts for the next minute, hour and day, the indicators behind them, a backtester and a trade planner —
          all on the real market's candles ({forecast?.horizons[0]?.model?.source === 'V1' ? 'Binance' : 'reference exchange'}).
          Every forecast is a <b>probability with its own track record</b>, tested only on data it had not seen.
          No one can reliably predict crypto prices.
        </>}
      />

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {SYMBOLS.map((s) => <button key={s} onClick={() => setSymbol(s)} style={pill(s === symbol)}>{s.replace('/USDT', '')}</button>)}
      </div>
      {error && <div style={{ fontSize: F.sm, color: C.orange, marginBottom: 10 }}>{error}</div>}

      {/* Forecasts */}
      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? 'minmax(0, 1fr)' : 'repeat(3, minmax(0, 1fr))', gap: 12, marginBottom: 12 }}>
        {forecast
          ? forecast.horizons.map((h) => <ForecastCard key={h.horizon} f={h} />)
          : (['1m', '1h', '1d'] as ResearchTf[]).map((h) => (
            <Card key={h}><CardTitle>Next {TF_LABEL[h].replace('1 ', '')}</CardTitle><Loading label="Fitting the forecast…" state="solving" /></Card>
          ))}
      </div>

      {/* Chart + signals */}
      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? 'minmax(0, 1fr)' : 'minmax(0, 1fr) minmax(0, 330px)', gap: 12, marginBottom: 12 }}>
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
            <CardTitle>{symbol} · {TF_LABEL[tf]} candles</CardTitle>
            <div style={{ display: 'flex', gap: 4 }}>
              {(['1m', '1h', '1d'] as ResearchTf[]).map((t) => <button key={t} onClick={() => setTf(t)} style={pill(t === tf)}>{t}</button>)}
            </div>
          </div>
          {chart && chart.symbol === symbol && chart.timeframe === tf ? <ResearchChart data={chart} />
            : <Loading block label="Loading the chart…" />}
        </Card>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          <Card>
            <CardTitle>What the indicators say ({TF_LABEL[tf]})</CardTitle>
            {horizon?.signals?.map((s) => (
              <div key={s.name} style={{ fontSize: F.sm, color: C.muted, padding: '4px 0', borderBottom: `1px solid ${C.border2}`, lineHeight: 1.4 }}>
                <span style={{ color: s.direction > 0 ? C.green : s.direction < 0 ? C.red : C.dim, fontWeight: 700 }}>
                  {s.direction > 0 ? '▲' : s.direction < 0 ? '▼' : '●'}
                </span>{' '}
                <b style={{ color: C.text }}>{s.name}</b> — {s.text}
              </div>
            )) ?? <div style={{ fontSize: F.sm, color: C.dim }}>—</div>}
            <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6 }}>
              These explain the picture; the probability comes from the tested model, not from counting arrows.
            </div>
          </Card>
          <Card>
            <CardTitle>Live order book</CardTitle>
            {book && book.available ? <>
              <div style={{ fontSize: F.sm, color: C.text, marginBottom: 6, lineHeight: 1.4 }}>{book.text}</div>
              <div style={{ position: 'relative', height: 10, borderRadius: 5, background: tint(C.red, 30), overflow: 'hidden', marginBottom: 6 }}>
                <div style={{ width: `${((book.imbalance_10bps ?? 0) + 1) * 50}%`, height: '100%', background: tint(C.green, 55) }} />
              </div>
              <div style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.6 }}>
                Bids {fmtUsd(book.bid_depth_10bps_usdt, 0)} vs asks {fmtUsd(book.ask_depth_10bps_usdt, 0)} within 0.1% of the price
                <br />Spread {book.spread_bps?.toFixed(2)} bps across {book.venues?.join(', ')}{(book.spread_bps ?? 0) < 0 ? ' (the exchanges\' prices overlap)' : ''}
                <br /><span style={{ color: C.dim }}>{book.note}</span>
              </div>
            </> : book?.text ? <div style={{ fontSize: F.sm, color: C.dim }}>{book.text}</div> : <Loading label="Reading the indicators…" />}
          </Card>
        </div>
      </div>

      <TechniquesCard symbol={symbol} />

      {/* Backtest + trade plan */}
      <div style={{ display: 'grid', gridTemplateColumns: isNarrow ? 'minmax(0, 1fr)' : 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12, marginBottom: 12 }}>
        <Card>
          <CardTitle>Backtest a rule on {symbol}</CardTitle>
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr', gap: 8 }}>
            <div>
              <div style={label}>Rule</div>
              <select value={rule} onChange={(e) => { const r = e.target.value as BacktestRule; setRule(r); setRuleParams(RULES.find((x) => x.id === r)!.params) }} style={input}>
                {RULES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
              </select>
            </div>
            <div>
              <div style={label}>Candles</div>
              <select value={btTf} onChange={(e) => setBtTf(e.target.value as ResearchTf)} style={input}>
                {(['1m', '1h', '1d'] as ResearchTf[]).map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div><div style={label}>Fee bps</div><input value={fee} onChange={(e) => setFee(e.target.value)} style={input} /></div>
            <div><div style={label}>Slippage</div><input value={slip} onChange={(e) => setSlip(e.target.value)} style={input} /></div>
          </div>
          <div style={{ fontSize: F.xs, color: C.dim, margin: '6px 0' }}>{rule0.hint}</div>
          {Object.keys(ruleParams).length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
              {Object.entries(ruleParams).map(([k, v]) => (
                <div key={k} style={{ width: 110 }}>
                  <div style={label}>{sentence(k)}</div>
                  {k === 'mode'
                    ? <select value={String(v)} onChange={(e) => setRuleParams({ ...ruleParams, mode: e.target.value })} style={input}>
                        <option value="reversion">reversion</option><option value="breakout">breakout</option>
                      </select>
                    : <input value={String(v)} onChange={(e) => setRuleParams({ ...ruleParams, [k]: parseFloat(e.target.value) || 0 })} style={input} />}
                </div>
              ))}
            </div>
          )}
          <button onClick={runBacktest} disabled={btBusy} style={button(C.blue, btBusy)}>{btBusy ? 'Testing…' : 'Run backtest'}</button>
          {btError && <div style={{ fontSize: F.sm, color: C.red, marginTop: 6 }}>{btError}</div>}
          {bt && (
            <div style={{ marginTop: 10 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 110px), 1fr))', gap: 6, marginBottom: 8 }}>
                <div style={tile}><div style={label}>Return</div><div style={{ fontSize: F.lg, fontWeight: 700, color: bt.total_return >= 0 ? C.green : C.red }}>{signed(bt.total_return)}</div></div>
                <div style={tile}><div style={label}>BUY &amp; HOLD</div><div style={{ fontSize: F.lg, fontWeight: 700 }}>{signed(bt.buy_hold_return)}</div></div>
                <div style={tile}><div style={label}>Max drawdown</div><div style={{ fontSize: F.lg, fontWeight: 700 }}>{pct(bt.max_drawdown)}</div><div style={{ fontSize: F.xs, color: C.dim }}>hold {pct(bt.buy_hold_max_drawdown)}</div></div>
                <div style={tile}><div style={label}>Sharpe</div><div style={{ fontSize: F.lg, fontWeight: 700 }}>{bt.sharpe?.toFixed(2) ?? '—'}</div></div>
                <div style={tile}><div style={label}>Trades</div><div style={{ fontSize: F.lg, fontWeight: 700 }}>{bt.trades}</div><div style={{ fontSize: F.xs, color: C.dim }}>won {pct(bt.win_rate, 0)}</div></div>
                <div style={tile}><div style={label}>Fees paid</div><div style={{ fontSize: F.lg, fontWeight: 700 }}>{fmtUsd(bt.fees_paid)}</div><div style={{ fontSize: F.xs, color: C.dim }}>on {fmtUsd(bt.capital, 0)}</div></div>
              </div>
              <EquityChart points={bt.equity_curve} />
              <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4 }}>
                {bt.bars.toLocaleString()} {TF_LABEL[bt.timeframe]} candles, {new Date(bt.start_ts).toLocaleDateString()} – {new Date(bt.end_ts).toLocaleDateString()} · in the market {pct(bt.exposure, 0)} of the time. {bt.note}
              </div>
            </div>
          )}
        </Card>

        <Card>
          <CardTitle>Plan a trade on {symbol} (BUY)</CardTitle>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
            <div>
              <div style={label}>Candles</div>
              <select value={planTf} onChange={(e) => setPlanTf(e.target.value as ResearchTf)} style={input}>
                {(['1m', '1h', '1d'] as ResearchTf[]).map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div><div style={label}>Money (USDT)</div><input value={equity} onChange={(e) => setEquity(e.target.value)} style={input} /></div>
            <div><div style={label}>Risk %</div><input value={risk} onChange={(e) => setRisk(e.target.value)} style={input} /></div>
            <div><div style={label}>Stop (× ATR)</div><input value={kAtr} onChange={(e) => setKAtr(e.target.value)} style={input} /></div>
            <div><div style={label}>Reward : risk</div><input value={rr} onChange={(e) => setRr(e.target.value)} style={input} /></div>
            <div><div style={label}>Give up after (candles)</div><input value={maxBars} onChange={(e) => setMaxBars(e.target.value)} style={input} /></div>
          </div>
          <div style={{ fontSize: F.xs, color: C.dim, margin: '6px 0' }}>
            Stop and target from volatility (ATR), size from the risk you accept, then this exact bracket is replayed at every past candle to see if it ever paid after fees.
          </div>
          <button onClick={makePlan} disabled={planBusy} style={button(C.purple, planBusy)}>{planBusy ? 'Measuring…' : 'Make a plan'}</button>
          {planError && <div style={{ fontSize: F.sm, color: C.red, marginTop: 6 }}>{planError}</div>}
          {plan && (
            <div style={{ marginTop: 10 }}>
              <div style={{
                padding: '8px 10px', borderRadius: 4, marginBottom: 8, fontSize: F.sm, fontWeight: 700,
                background: tint(plan.plan.verdict === 'positive' ? C.green : C.orange, 12),
                color: plan.plan.verdict === 'positive' ? C.green : C.orange,
              }}>
                {plan.plan.verdict === 'positive' ? 'Positive expectancy on this history' : 'No edge after fees on this history — skip or change the bracket'}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 110px), 1fr))', gap: 6, marginBottom: 8 }}>
                <div style={tile}><div style={label}>Entry (ask)</div><div style={{ fontSize: F.md, fontWeight: 700 }}>{fmtPrice(plan.plan.entry)}</div></div>
                <div style={tile}><div style={label}>Stop</div><div style={{ fontSize: F.md, fontWeight: 700, color: C.red }}>{fmtPrice(plan.plan.stop)}</div></div>
                <div style={tile}><div style={label}>Target</div><div style={{ fontSize: F.md, fontWeight: 700, color: C.green }}>{fmtPrice(plan.plan.target)}</div></div>
                <div style={tile}><div style={label}>Size</div><div style={{ fontSize: F.md, fontWeight: 700 }}>{fmtQty(plan.plan.quantity, 6)}</div><div style={{ fontSize: F.xs, color: C.dim }}>{fmtUsd(plan.plan.notional)}</div></div>
                <div style={tile}><div style={label}>Win rate</div><div style={{ fontSize: F.md, fontWeight: 700 }}>{pct(plan.history.win_rate)}</div><div style={{ fontSize: F.xs, color: C.dim }}>needs {pct(plan.history.break_even_win_rate)}</div></div>
                <div style={tile}><div style={label}>Per trade</div><div style={{ fontSize: F.md, fontWeight: 700 }}>{plan.history.expectancy_r !== null ? `${plan.history.expectancy_r >= 0 ? '+' : ''}${plan.history.expectancy_r.toFixed(3)} R` : '—'}</div><div style={{ fontSize: F.xs, color: C.dim }}>Kelly {plan.history.kelly_fraction !== null ? pct(plan.history.kelly_fraction) : '—'}</div></div>
              </div>
              {plan.plan.reasons.map((r) => <div key={r} style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.5 }}>• {r}</div>)}
              {plan.forecast && <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>• Forecast for the next {TF_LABEL[plan.timeframe].replace('1 ', '')}: {plan.forecast.call}</div>}
              <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4 }}>{plan.disclaimer}</div>
            </div>
          )}
        </Card>
      </div>

      {/* Scoreboard */}
      <Card>
        <CardTitle>Scoreboard — every coin × horizon, tested on data the model had not seen</CardTitle>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={th}>Coin</th><th style={th}>Horizon</th><th style={{ ...th, textAlign: 'right' }}>Right</th>
                <th style={{ ...th, textAlign: 'right' }}>Baseline</th><th style={{ ...th, textAlign: 'right' }}>Luck? (p)</th>
                <th style={{ ...th, textAlign: 'right' }}>Brier skill</th><th style={{ ...th, textAlign: 'right' }}>80% range held</th>
                <th style={th}>Verdict</th><th style={{ ...th, textAlign: 'right' }}>Live: scored / right</th>
              </tr>
            </thead>
            <tbody>
              {(board ?? []).map((r) => {
                const o = r.out_of_sample
                return (
                  <tr key={`${r.symbol}${r.horizon}`} style={{ background: r.symbol === symbol ? tint(C.blue, 5) : undefined }}>
                    <td style={td}>{r.symbol.replace('/USDT', '')}</td>
                    <td style={td}>{r.label}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{pct(o?.accuracy)}</td>
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}>{pct(o?.baseline_accuracy)}</td>
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}>{o ? o.p_value.toFixed(3) : '—'}</td>
                    <td style={{ ...td, textAlign: 'right', color: o && o.brier_skill > 0 ? C.green : C.muted }}>{o ? `${o.brier_skill >= 0 ? '+' : ''}${(o.brier_skill * 100).toFixed(2)}%` : '—'}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{pct(r.range_coverage80)}</td>
                    <td style={{ ...td, color: o?.edge ? C.green : C.dim, fontWeight: o?.edge ? 700 : 400 }}>{o ? (o.edge ? 'small edge' : 'no proven edge') : 'not trained yet'}</td>
                    <td style={{ ...td, textAlign: 'right', color: C.muted }}>{r.live.n > 0 ? `${r.live.n} / ${pct(r.live.hit_rate)}` : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6, lineHeight: 1.5 }}>
          "Baseline" = always guessing whichever side was more common. "Luck? (p)" = how likely the gain over the baseline is by pure chance (an edge needs p &lt; 0.05;
          with 15 models about one false edge is expected by chance, so watch the live column). An 80% range should hold about 80% of the time.
        </div>
      </Card>
    </div>
  )
}
