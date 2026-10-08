import React, { useCallback, useEffect, useState } from 'react'
import { C, F, tint } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { baseAsset, fmtBps, fmtPnl, fmtPrice, fmtQty, fmtUsd } from '../format'
import { SYMBOLS } from '../mockData'
import { api } from '../services/api'
import { wsClient } from '../services/websocket'
import type { AdvisorParams, AdvisorProposal, BotState, CycleParams, FundView, LabReport, Lot } from '../services/api'
import { MQ, useMediaQuery } from '../hooks/useMediaQuery'

// Bots & Lab (Phase C): the automation fund, the profit-cycle bot, the advisor agent and the
// practice lab. Every bot order goes through the same order path as the order button.

const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`)
const ago = (iso: string | null) => {
  if (!iso) return '—'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  return s < 90 ? `${s.toFixed(0)} s` : s < 5400 ? `${(s / 60).toFixed(0)} min` : s < 172800 ? `${(s / 3600).toFixed(1)} h` : `${(s / 86400).toFixed(1)} d`
}
const until = (iso: string) => {
  const s = (new Date(iso).getTime() - Date.now()) / 1000
  return s <= 0 ? 'now' : s < 90 ? `${s.toFixed(0)} s` : s < 5400 ? `${(s / 60).toFixed(0)} min` : `${(s / 3600).toFixed(1)} h`
}
const dur = (fromIso: string, toIso: string | null) => {
  const s = Math.max(0, ((toIso ? new Date(toIso).getTime() : Date.now()) - new Date(fromIso).getTime()) / 1000)
  return s < 90 ? `${s.toFixed(0)} s` : s < 5400 ? `${(s / 60).toFixed(0)} min` : s < 172800 ? `${(s / 3600).toFixed(1)} h` : `${(s / 86400).toFixed(1)} d`
}
const STATUS_COLOR: Record<string, string> = { RUNNING: C.green, PAUSED: C.orange, STOPPED: C.dim, FAILED: C.red }

const label: React.CSSProperties = { fontSize: F.xs, color: C.dim, letterSpacing: '.06em', marginBottom: 4 }
const input: React.CSSProperties = {
  width: '100%', padding: '6px 8px', borderRadius: 4, background: C.surface, border: `1px solid ${C.border}`,
  color: C.text, fontSize: F.sm, fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box',
}
const tile: React.CSSProperties = { padding: '8px 10px', borderRadius: 4, background: C.surface, minWidth: 0 }
const th: React.CSSProperties = { padding: '5px 8px', fontSize: F.xs, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: F.sm, color: C.text, borderBottom: `1px solid ${C.border}40`, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }
const pill = (on: boolean): React.CSSProperties => ({
  padding: '5px 12px', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit', fontSize: F.sm, fontWeight: 700,
  border: `1px solid ${on ? C.blue : C.border}`, background: on ? tint(C.blue, 14) : C.surface, color: on ? C.blue : C.muted,
})
const button = (color: string, busy = false): React.CSSProperties => ({
  padding: '6px 12px', borderRadius: 4, border: `1px solid ${color}`, background: tint(color, 12), color,
  fontFamily: 'inherit', fontSize: F.sm, fontWeight: 700, cursor: busy ? 'wait' : 'pointer',
})

function Stat({ name, value, color, hint }: { name: string; value: React.ReactNode; color?: string; hint?: string }) {
  return (
    <div style={tile} title={hint}>
      <div style={{ fontSize: F.xs, color: C.dim, letterSpacing: '.05em' }}>{name}</div>
      <div style={{ fontSize: F.md, color: color ?? C.text, fontWeight: 700, marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )
}

function Num({ name, value, set, step = 'any', min }: { name: string; value: number; set: (v: number) => void; step?: string; min?: number }) {
  return (
    <div>
      <div style={label}>{name}</div>
      <input type="number" value={Number.isFinite(value) ? value : ''} step={step} min={min} style={input}
        onChange={(e) => set(parseFloat(e.target.value))} />
    </div>
  )
}

// ── the automation fund ─────────────────────────────────────────────
function FundCard({ fund, onChange }: { fund: FundView | null; onChange: () => void }) {
  const [amount, setAmount] = useState(100)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const narrow = useMediaQuery(MQ.narrow)
  const move = async (direction: 'deposit' | 'withdraw') => {
    setBusy(true); setError(null)
    try { await api.moveFund(direction, amount); onChange() } catch (e) { setError((e as Error).message) }
    setBusy(false)
  }
  const pnlColor = (v: number | null) => (v === null ? C.text : v >= 0 ? C.green : C.red)
  return (
    <Card>
      <CardTitle>AUTOMATION FUND</CardTitle>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 10, lineHeight: 1.5 }}>
        Money you set aside for bots. Each bot gets its own pocket from it and can never spend more; manual
        orders can't use what the fund holds. {fund?.note.split('. ').slice(2).join('. ')}
      </div>
      {fund ? <>
        <div style={{ display: 'grid', gridTemplateColumns: narrow ? 'repeat(2, minmax(0, 1fr))' : 'repeat(6, minmax(0, 1fr))', gap: 8 }}>
          <Stat name="PUT IN" value={fmtUsd(fund.allocated_usdt)} hint="Moved from the wallet into the fund, net of what was moved out" />
          <Stat name="UNASSIGNED" value={fmtUsd(fund.unassigned_usdt)} hint="In the fund, not given to any bot" />
          <Stat name="BOTS' CASH" value={fmtUsd(fund.bots_cash_usdt)} />
          <Stat name="BOTS' COINS" value={fund.coins_value_usdt === null ? '—' : fmtUsd(fund.coins_value_usdt)}
            hint={Object.entries(fund.coins).map(([a, q]) => `${fmtQty(q)} ${a}`).join(', ') || 'none'} />
          <Stat name="VALUE NOW" value={fund.value_usdt === null ? '—' : fmtUsd(fund.value_usdt)} />
          <Stat name="P&L (REAL-MARKET FEES)" value={fmtPnl(fund.pnl_market_usdt, 4)} color={pnlColor(fund.pnl_market_usdt)}
            hint={`Fees as the test exchanges charged them: ${fmtPnl(fund.pnl_usdt, 4)}. Binance's spot testnet charges no fees, so results are shown at each exchange's real taker fee.`} />
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', marginTop: 10 }}>
          <div style={{ width: 140 }}><Num name="AMOUNT (USDT)" value={amount} set={setAmount} min={0} /></div>
          <button disabled={busy} onClick={() => move('deposit')} style={button(C.blue, busy)}>Move into the fund</button>
          <button disabled={busy} onClick={() => move('withdraw')} style={button(C.muted, busy)}>Move back to the wallet</button>
          <div style={{ fontSize: F.xs, color: C.dim }}>
            Free for manual trading now: {fmtUsd(fund.manual_free.USDT ?? 0)} USDT
            {Object.entries(fund.reserved).filter(([a, q]) => a !== 'USDT' && q > 0).map(([a, q]) => ` · ${fmtQty(q)} ${a} held by bots`)}
          </div>
        </div>
        {error && <div style={{ fontSize: F.sm, color: C.orange, marginTop: 6 }}>{error}</div>}
        {fund.warnings.map((w) => <div key={w} style={{ fontSize: F.xs, color: C.orange, marginTop: 6 }}>⚠ {w}</div>)}
      </> : <div style={{ fontSize: F.sm, color: C.dim }}>Loading…</div>}
    </Card>
  )
}

// ── starting a bot ──────────────────────────────────────────────────
const CYCLE_DEFAULTS: CycleParams = {
  symbol: 'BTC/USDT', budget_usdt: 50, buy_usdt: 10, threshold_pct: 0.3, spacing_pct: 0.5, cooldown_s: 60,
  max_drawdown_pct: 10,
}
const ADVISOR_DEFAULTS: AdvisorParams = {
  symbols: [...SYMBOLS], budget_usdt: 50, trade_usdt: 10, mode: 'ASK', min_probability: 0.55, require_edge: false,
  min_expectancy_r: 0, k_atr: 1.5, rr: 2, max_hold_hours: 12, max_open: 2, max_trades_per_day: 6,
  scan_interval_s: 300, proposal_ttl_s: 600,
}

function StartBot({ mainnet, onStarted }: { mainnet: boolean; onStarted: () => void }) {
  const [kind, setKind] = useState<'cycle' | 'advisor'>('cycle')
  const [cycle, setCycle] = useState<CycleParams>(CYCLE_DEFAULTS)
  const [adv, setAdv] = useState<AdvisorParams>(ADVISOR_DEFAULTS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const narrow = useMediaQuery(MQ.narrow)
  const cols = narrow ? 'repeat(2, minmax(0, 1fr))' : 'repeat(6, minmax(0, 1fr))'
  const start = async () => {
    setBusy(true); setError(null)
    try {
      if (kind === 'cycle') await api.startCycleBot(cycle)
      else await api.startAdvisorBot(adv)
      onStarted()
    } catch (e) { setError((e as Error).message) }
    setBusy(false)
  }
  const lots = Math.max(1, Math.floor(cycle.budget_usdt / cycle.buy_usdt) || 1)
  return (
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        <CardTitle>START A BOT</CardTitle>
        <div style={{ display: 'flex', gap: 4 }}>
          <button onClick={() => setKind('cycle')} style={pill(kind === 'cycle')}>Profit cycle</button>
          <button onClick={() => setKind('advisor')} style={pill(kind === 'advisor')}>Advisor</button>
        </div>
      </div>
      {kind === 'cycle' ? <>
        <div style={{ fontSize: F.sm, color: C.muted, margin: '4px 0 10px', lineHeight: 1.5 }}>
          Buys {fmtUsd(cycle.buy_usdt)} of a coin, and sells that lot only when the price beats what it cost + both fees
          (at real-market rates) + {cycle.threshold_pct}%. Then repeats. Up to {lots} lots wait at once; another one only
          after the price falls {cycle.spacing_pct}% under the cheapest. <span style={{ color: C.orange }}>It never sells a lot
          below that — but it can't make the price come back after a buy.</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 8 }}>
          <div>
            <div style={label}>COIN</div>
            <select value={cycle.symbol} onChange={(e) => setCycle({ ...cycle, symbol: e.target.value })} style={input}>
              {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <Num name="BUDGET (USDT)" value={cycle.budget_usdt} set={(v) => setCycle({ ...cycle, budget_usdt: v })} />
          <Num name="EACH BUY (USDT)" value={cycle.buy_usdt} set={(v) => setCycle({ ...cycle, buy_usdt: v })} />
          <Num name="THRESHOLD %" value={cycle.threshold_pct} set={(v) => setCycle({ ...cycle, threshold_pct: v })} />
          <Num name="SPACING %" value={cycle.spacing_pct} set={(v) => setCycle({ ...cycle, spacing_pct: v })} />
          <Num name="PAUSE BUYS AT DRAWDOWN %" value={cycle.max_drawdown_pct} set={(v) => setCycle({ ...cycle, max_drawdown_pct: v })} />
        </div>
      </> : <>
        <div style={{ fontSize: F.sm, color: C.muted, margin: '4px 0 10px', lineHeight: 1.5 }}>
          Every few minutes it reads the research forecast for the next hour, the trade planner's ATR bracket and what our
          own practice trades showed, and proposes a bracketed buy with its reasons. <b>Ask me</b>: nothing happens until you
          approve. <b>Auto</b>: it approves itself within its limits — {mainnet ? <span style={{ color: C.orange }}>not
          allowed with real money</span> : 'testnet only'}.
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
          {SYMBOLS.map((s) => {
            const on = adv.symbols.includes(s)
            return <button key={s} style={pill(on)} onClick={() => setAdv({ ...adv, symbols: on ? adv.symbols.filter((x) => x !== s) : [...adv.symbols, s] })}>{baseAsset(s)}</button>
          })}
          <span style={{ width: 12 }} />
          <button style={pill(adv.mode === 'ASK')} onClick={() => setAdv({ ...adv, mode: 'ASK' })}>Ask me</button>
          <button style={pill(adv.mode === 'AUTO')} disabled={mainnet} onClick={() => setAdv({ ...adv, mode: 'AUTO' })}>Auto (testnet)</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 8 }}>
          <Num name="BUDGET (USDT)" value={adv.budget_usdt} set={(v) => setAdv({ ...adv, budget_usdt: v })} />
          <Num name="EACH TRADE (USDT)" value={adv.trade_usdt} set={(v) => setAdv({ ...adv, trade_usdt: v })} />
          <Num name="MIN P(UP) NEXT HOUR" value={adv.min_probability} step="0.01" set={(v) => setAdv({ ...adv, min_probability: v })} />
          <Num name="STOP (× ATR)" value={adv.k_atr} set={(v) => setAdv({ ...adv, k_atr: v })} />
          <Num name="TARGET (R)" value={adv.rr} set={(v) => setAdv({ ...adv, rr: v })} />
          <Num name="MAX HOLD (H)" value={adv.max_hold_hours} set={(v) => setAdv({ ...adv, max_hold_hours: v })} />
        </div>
      </>}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
        <button disabled={busy} onClick={start} style={button(C.green, busy)}>{busy ? 'Starting…' : 'Start (budget comes from the fund)'}</button>
        {error && <span style={{ fontSize: F.sm, color: C.orange }}>{error}</span>}
      </div>
    </Card>
  )
}

// ── one bot ─────────────────────────────────────────────────────────
function LotsTable({ lots, advisor }: { lots: Lot[]; advisor: boolean }) {
  if (!lots.length) return null
  return (
    <div style={{ overflowX: 'auto', marginTop: 8 }}>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead><tr>
          <th style={th}>LOT</th><th style={th}>COIN</th><th style={th}>BOUGHT</th><th style={th}>@</th><th style={th}>COST</th>
          <th style={th}>{advisor ? 'TARGET / STOP' : 'SELLS AT'}</th><th style={th}>STATUS</th><th style={th}>RESULT (REAL FEES)</th>
        </tr></thead>
        <tbody>
          {lots.slice().reverse().slice(0, 12).map((l) => (
            <tr key={l.id}>
              <td style={td}>{l.id}</td>
              <td style={td}>{baseAsset(l.symbol)}</td>
              <td style={td}>{fmtQty(l.bought_qty)} · {ago(l.opened_at)} ago</td>
              <td style={td}>{fmtPrice(l.buy_price)}</td>
              <td style={td}>{fmtUsd(l.status === 'OPEN' ? l.cost_usdt : l.sold_cost_usdt, 2)}</td>
              <td style={td}>{fmtPrice(l.target_price)}{l.stop_price ? ` / ${fmtPrice(l.stop_price)}` : ''}</td>
              <td style={{ ...td, color: l.status === 'OPEN' ? C.blue : C.muted }}>{l.status === 'OPEN' ? 'waiting' : l.exit_reason}</td>
              <td style={{ ...td, color: (l.profit_market_usdt ?? 0) >= 0 ? C.green : C.red }}>
                {l.profit_market_usdt === null ? '—' : `${fmtPnl(l.profit_market_usdt, 4)} after ${dur(l.opened_at, l.closed_at)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ProposalCard({ p, onDecide }: { p: AdvisorProposal; onDecide: (approve: boolean) => void }) {
  return (
    <div style={{ border: `1px solid ${C.blue}`, borderRadius: 4, padding: 10, marginTop: 8, background: tint(C.blue, 5) }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 }}>
        <div style={{ fontSize: F.md, fontWeight: 700, color: C.text }}>
          Buy {fmtUsd(p.notional_usdt)} of {baseAsset(p.symbol)} at ~{fmtPrice(p.entry_price)}
          <span style={{ fontWeight: 400, color: C.muted, fontSize: F.sm }}> · stop {fmtPrice(p.stop_price)} · target {fmtPrice(p.target_price)} · max {p.max_hold_hours} h</span>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => onDecide(true)} style={button(C.green)}>Approve</button>
          <button onClick={() => onDecide(false)} style={button(C.muted)}>Reject</button>
        </div>
      </div>
      <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: F.sm, color: C.muted, lineHeight: 1.5 }}>
        {p.reasons.map((r) => <li key={r}>{r}</li>)}
      </ul>
      <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4 }}>Expires in {until(p.expires_at)} · refused at approval if the price moved more than 1%</div>
    </div>
  )
}

function BotCard({ bot, onChange }: { bot: BotState; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const narrow = useMediaQuery(MQ.narrow)
  const act = async (fn: () => Promise<unknown>, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(true); setError(null)
    try { await fn(); onChange() } catch (e) { setError((e as Error).message) }
    setBusy(false)
  }
  const open = bot.lots.filter((l) => l.status === 'OPEN')
  // a stopped bot can still hold coins: open lots, or dust under one lot step after its sales
  const heldCoins = Object.values(bot.pocket?.coins ?? {}).some((q) => q > 0)
  const waiting = bot.proposals.filter((p) => p.status === 'PROPOSED')
  const color = STATUS_COLOR[bot.status] ?? C.muted
  const s = bot.stats
  return (
    <Card style={{ border: `1px solid ${bot.status === 'RUNNING' ? tint(C.green, 50) : C.border}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        <div>
          <span style={{ fontSize: F.md, fontWeight: 700, color: C.text }}>{bot.label}</span>
          <span style={{ fontSize: F.xs, color: C.dim }}> · {bot.id}{bot.lab ? ' · lab' : ''} · by {bot.created_by}</span>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: F.xs, padding: '2px 8px', borderRadius: 3, fontWeight: 700, background: tint(color, 14), color }}>{bot.status}</span>
          {bot.status === 'RUNNING' && <button disabled={busy} onClick={() => act(() => api.controlBot(bot.id, 'pause'))} style={button(C.orange, busy)}>Pause</button>}
          {bot.status === 'PAUSED' && <button disabled={busy} onClick={() => act(() => api.controlBot(bot.id, 'resume'))} style={button(C.green, busy)}>Resume</button>}
          {(bot.status === 'RUNNING' || bot.status === 'PAUSED') && <button disabled={busy} onClick={() => act(() => api.controlBot(bot.id, 'stop'),
            'Stop this bot? No new trades; its cash goes back to the fund. Open lots stay until you sell or release them.')} style={button(C.red, busy)}>Stop</button>}
          {open.length > 0 && <button disabled={busy} onClick={() => act(() => api.sellBotLots(bot.id),
            `Sell all ${open.length} open lot(s) at the market now — even below what they cost?`)} style={button(C.muted, busy)}>Sell open lots now</button>}
          {bot.status === 'STOPPED' && heldCoins && <button disabled={busy} onClick={() => act(() => api.releaseBotCoins(bot.id, 'to_manual'),
            'Move this bot\'s coins to manual trading (no sale; they leave the fund at today\'s price)?')} style={button(C.muted, busy)}>Coins to manual</button>}
        </div>
      </div>
      {bot.status_reason && bot.status !== 'RUNNING' && <div style={{ fontSize: F.xs, color: C.muted, marginTop: 4 }}>{bot.status_reason}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: narrow ? 'repeat(2, minmax(0, 1fr))' : 'repeat(6, minmax(0, 1fr))', gap: 8, marginTop: 8 }}>
        <Stat name="BUDGET" value={fmtUsd(bot.pocket?.budget_usdt ?? 0)} />
        <Stat name="CASH" value={fmtUsd(bot.pocket?.cash_usdt ?? 0)} />
        <Stat name="VALUE NOW" value={bot.value_usdt === null ? '—' : fmtUsd(bot.value_usdt)} />
        <Stat name="P&L (REAL FEES)" value={fmtPnl(bot.pnl_market_usdt, 4)} color={(bot.pnl_market_usdt ?? 0) >= 0 ? C.green : C.red}
          hint={`As charged on the test exchange: ${fmtPnl(bot.pnl_usdt, 4)}; closed: ${fmtPnl(bot.pocket?.realized_market_usdt, 4)}`} />
        <Stat name={bot.kind === 'CYCLE' ? 'CYCLES DONE' : 'WON / STOPPED / TIMED OUT'}
          value={bot.kind === 'CYCLE' ? s.lots_closed : `${s.wins} / ${s.losses} / ${s.timeouts}`} />
        <Stat name="OPEN" value={`${open.length} lot(s)`} />
      </div>
      {bot.waiting_reason && <div style={{ fontSize: F.sm, color: C.muted, marginTop: 8 }}>Now: {bot.waiting_reason}</div>}
      {waiting.map((p) => <ProposalCard key={p.id} p={p} onDecide={(approve) => act(() => api.decideProposal(p.id, approve))} />)}
      {bot.kind === 'ADVISOR' && bot.last_scan.length > 0 && (
        <div style={{ fontSize: F.xs, color: C.dim, marginTop: 8, lineHeight: 1.6 }}>
          Last look: {bot.last_scan.map((r) => <span key={r.symbol}><b style={{ color: r.proposed ? C.green : C.muted }}>{baseAsset(r.symbol)}</b> {r.decision}; </span>)}
        </div>
      )}
      <LotsTable lots={bot.lots} advisor={bot.kind === 'ADVISOR'} />
      {bot.kind === 'ADVISOR' && bot.proposals.filter((p) => p.status !== 'PROPOSED').length > 0 && (
        <div style={{ fontSize: F.xs, color: C.dim, marginTop: 6 }}>
          Earlier proposals: {bot.proposals.filter((p) => p.status !== 'PROPOSED').slice(0, 6).map((p) =>
            `${baseAsset(p.symbol)} ${p.status.toLowerCase()}${p.result ? ` (${p.result})` : ''}`).join(' · ')}
        </div>
      )}
      {error && <div style={{ fontSize: F.sm, color: C.orange, marginTop: 6 }}>{error}</div>}
    </Card>
  )
}

// ── the practice lab ────────────────────────────────────────────────
function LabSection({ lab, onStarted }: { lab: LabReport | null; onStarted: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!lab) return <Card><CardTitle>PRACTICE LAB</CardTitle><div style={{ fontSize: F.sm, color: C.dim }}>Loading…</div></Card>
  const start = async (name: string, budget: number) => {
    if (!window.confirm(`Start this lab experiment? It moves ${fmtUsd(budget)} of test money from the wallet into the automation fund and starts its bots.`)) return
    setBusy(name); setError(null)
    try { await api.startLabPreset(name); onStarted() } catch (e) { setError((e as Error).message) }
    setBusy(null)
  }
  const ci = (x: { mean: number; low: number; high: number } | null) =>
    x ? `${x.mean >= 0 ? '+' : ''}${x.mean.toFixed(3)}% (95%: ${x.low.toFixed(3)} … ${x.high.toFixed(3)})` : '—'
  return (
    <Card>
      <CardTitle>PRACTICE LAB — WHAT OUR OWN TRADES TEACH US</CardTitle>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 10, lineHeight: 1.5 }}>{lab.note}</div>
      <div style={{ fontSize: F.xs, color: C.dim, marginBottom: 10 }}>
        Samples: {Object.entries(lab.samples).map(([k, n]) => `${n} ${k.toLowerCase().replace('_', ' ')}`).join(' · ') || 'none yet'}
      </div>

      {lab.enabled && <>
        <div style={label}>EXPERIMENTS (TESTNET)</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 8, marginBottom: 12 }}>
          {lab.presets.map((p) => (
            <div key={p.name} style={{ ...tile, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: F.sm, fontWeight: 700, color: C.text }}>{p.title}</div>
              <div style={{ fontSize: F.xs, color: C.muted, lineHeight: 1.45, flex: 1 }}>{p.description}</div>
              <button disabled={busy !== null} onClick={() => start(p.name, p.budget_usdt)} style={button(C.purple, busy === p.name)}>
                {busy === p.name ? 'Starting…' : `Start (${fmtUsd(p.budget_usdt, 0)} test money)`}
              </button>
            </div>
          ))}
        </div>
        {error && <div style={{ fontSize: F.sm, color: C.orange, marginBottom: 8 }}>{error}</div>}
      </>}

      <div style={label}>PROFIT-CYCLE VARIANTS</div>
      {lab.scoreboard.length ? (
        <div style={{ overflowX: 'auto', marginBottom: 10 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr>
              <th style={th}>VARIANT</th><th style={th}>LOTS (CLOSED / OPEN)</th><th style={th}>DAYS</th><th style={th}>PROFIT</th>
              <th style={th}>PER DAY</th><th style={th}>PER DAY ON BUDGET</th><th style={th}>PER LOT</th><th style={th}>MEDIAN HOLD</th><th style={th}>SOLD ≤ 24 H</th>
            </tr></thead>
            <tbody>{lab.scoreboard.map((r) => (
              <tr key={r.variant}>
                <td style={td}>{r.variant}{r.running ? '' : ' (stopped)'}</td>
                <td style={td}>{r.lots} ({r.closed} / {r.open})</td>
                <td style={td}>{r.days.toFixed(2)}</td>
                <td style={{ ...td, color: r.profit_market_usdt >= 0 ? C.green : C.red }}>{fmtPnl(r.profit_market_usdt, 4)}</td>
                <td style={td}>{fmtPnl(r.profit_per_day_usdt, 4)}</td>
                <td style={td}>{r.return_per_day_pct === null ? '—' : `${r.return_per_day_pct.toFixed(3)}%`}</td>
                <td style={td}>{ci(r.return_per_lot_pct)}</td>
                <td style={td}>{r.median_hold_h === null ? '—' : `${r.median_hold_h.toFixed(1)} h`}</td>
                <td style={td}>{r.sold_within_24h === null ? `— (${r.lots_older_than_24h} lots ≥ 24 h old)` : `${pct(r.sold_within_24h, 0)} of ${r.lots_older_than_24h}`}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <div style={{ fontSize: F.sm, color: C.dim, marginBottom: 10 }}>No profit-cycle lots yet.</div>}
      {lab.comparisons.length > 0 && (
        <div style={{ fontSize: F.xs, color: C.muted, marginBottom: 10, lineHeight: 1.6 }}>
          {lab.comparisons.map((c) => <div key={`${c.a}-${c.b}`}>{baseAsset(c.symbol)}: {c.a} vs {c.b} — {c.verdict}</div>)}
        </div>
      )}

      <div style={label}>ADVISOR TRADES</div>
      {lab.advisor.length ? lab.advisor.map((r) => (
        <div key={r.variant} style={{ fontSize: F.sm, color: C.muted, marginBottom: 6, lineHeight: 1.5 }}>
          <b style={{ color: C.text }}>{r.variant}</b>: {r.trades} trades ({r.closed} closed) — {r.wins} hit the target, {r.stops} the stop,
          {' '}{r.timeouts} timed out · win rate {pct(r.win_rate, 0)} · return per trade {ci(r.return_pct)}
          {r.by_probability.length > 0 && <div style={{ fontSize: F.xs, color: C.dim }}>
            By forecast: {r.by_probability.map((b) => `P(up) ${b.bucket}: ${b.n} trades, ${pct(b.win_rate, 0)} won, ${b.avg_return_pct >= 0 ? '+' : ''}${b.avg_return_pct.toFixed(3)}%`).join(' · ')}
          </div>}
        </div>
      )) : <div style={{ fontSize: F.sm, color: C.dim, marginBottom: 10 }}>No advisor trades yet.</div>}

      <div style={label}>TRADING COSTS MEASURED ON OUR OWN ORDERS</div>
      {lab.costs.length ? (
        <div style={{ overflowX: 'auto', marginBottom: 10 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr>
              <th style={th}>EXCHANGE</th><th style={th}>COIN</th><th style={th}>ORDERS (FILLED)</th><th style={th}>FILL RATE</th>
              <th style={th}>SLIPPAGE MEDIAN</th><th style={th}>P90</th><th style={th}>FEE CHARGED</th><th style={th}>REAL-MARKET FEE</th>
            </tr></thead>
            <tbody>{lab.costs.map((r) => (
              <tr key={`${r.venue_id}-${r.symbol}`}>
                <td style={td}>{r.venue_id}</td><td style={td}>{baseAsset(r.symbol)}</td>
                <td style={td}>{r.orders} ({r.filled})</td><td style={td}>{pct(r.fill_ratio, 1)}</td>
                <td style={td}>{fmtBps(r.slippage_bps_median, 2)}</td><td style={td}>{fmtBps(r.slippage_bps_p90, 2)}</td>
                <td style={td}>{fmtBps(r.fee_bps_charged, 1)}</td><td style={td}>{fmtBps(r.market_fee_bps, 1)}</td>
              </tr>
            ))}</tbody>
          </table>
          <div style={{ fontSize: F.xs, color: C.dim, marginTop: 4 }}>Slippage = what we paid vs the mid when the order left (+ = a cost). With 20+ filled orders on a coin, the research backtester and trade planner use this instead of an assumed 2 bps.</div>
        </div>
      ) : <div style={{ fontSize: F.sm, color: C.dim, marginBottom: 10 }}>No bot orders yet.</div>}

      <div style={label}>MODELS TRAINED ON OUR OWN TRADES</div>
      {lab.models.map((m) => (
        <div key={m.kind} style={{ fontSize: F.sm, color: C.muted, marginBottom: 4 }}>
          <b style={{ color: C.text }}>{m.question}</b> — {m.status}
          {m.base_rate !== undefined && ` · base rate ${pct(m.base_rate, 0)}`}
          <div style={{ height: 6, borderRadius: 3, background: C.surface, marginTop: 3, maxWidth: 360 }}>
            <div style={{ width: `${Math.min(100, (m.labelled / m.needed) * 100)}%`, height: '100%', borderRadius: 3, background: m.edge ? C.green : C.blue }} />
          </div>
        </div>
      ))}
    </Card>
  )
}

// ── the page ────────────────────────────────────────────────────────
export function Automation() {
  const [fund, setFund] = useState<FundView | null>(null)
  const [bots, setBots] = useState<BotState[]>([])
  const [lab, setLab] = useState<LabReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [f, b] = await Promise.all([api.getFund(), api.getBots()])
      setFund(f); setBots(b.bots); setError(null)
    } catch (e) { setError((e as Error).message) }
  }, [])
  const refreshLab = useCallback(async () => {
    try { setLab(await api.getLab()) } catch (e) { setError((e as Error).message) }
  }, [])
  const refreshAll = useCallback(() => { refresh(); refreshLab() }, [refresh, refreshLab])

  useEffect(() => {
    refreshAll()
    const off = wsClient.subscribe('bot_update', refresh)
    const fast = setInterval(refresh, 10_000)
    const slow = setInterval(refreshLab, 30_000)
    return () => { off(); clearInterval(fast); clearInterval(slow) }
  }, [refresh, refreshLab, refreshAll])

  const mainnet = fund?.mode === 'mainnet'
  const active = bots.filter((b) => b.status === 'RUNNING' || b.status === 'PAUSED')
  const ended = bots.filter((b) => !(b.status === 'RUNNING' || b.status === 'PAUSED'))
  return (
    <div>
      <div style={{ fontSize: F.lg, color: C.text, marginBottom: 6 }}>Bots &amp; Lab</div>
      <div style={{ fontSize: F.sm, color: C.muted, marginBottom: 12, maxWidth: 1000, lineHeight: 1.5 }}>
        Bots trade from a fund you set aside, through the same order path as the order button — pre-trade risk, the kill
        switch and price-protected IOC orders apply to every one of their orders. The kill switch pauses every bot, and a
        restart leaves them paused. The practice lab writes down every bot trade and learns from them.
        {' '}<span style={{ color: C.orange }}>No bot can guarantee a profit: prices can fall after a buy, and forecasts are probabilities.</span>
      </div>
      {error && <div style={{ fontSize: F.sm, color: C.orange, marginBottom: 10 }}>{error}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <FundCard fund={fund} onChange={refreshAll} />
        <StartBot mainnet={mainnet} onStarted={refreshAll} />
        {active.length === 0 && <Card><div style={{ fontSize: F.sm, color: C.dim }}>No bot is running. Move some money into the fund, then start one — or start a lab experiment below.</div></Card>}
        {active.map((b) => <BotCard key={b.id} bot={b} onChange={refreshAll} />)}
        <LabSection lab={lab} onStarted={refreshAll} />
        {ended.length > 0 && <>
          <div style={{ fontSize: F.sm, color: C.dim }}>Stopped bots</div>
          {ended.slice(0, 10).map((b) => <BotCard key={b.id} bot={b} onChange={refreshAll} />)}
        </>}
      </div>
    </div>
  )
}
