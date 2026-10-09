"""Automation engine: runs the bots (profit cycle, advisor) on the automation fund.

Every order a bot makes is an ordinary order through ``OrderEntry.submit`` — pre-trade
risk, the kill switch, the fund gate (a bot can only use its own pocket), routing and
price-protected IOC children. The engine never talks to an exchange itself.

Safety, the same as the algo engine: the kill switch pauses every running bot, a restart
leaves them paused, an order already at the exchange is never interrupted (pause / stop
take effect between orders), and three orders in a row refused before reaching any
exchange pause the bot with the reason. Advisor auto-approve and lab bots are testnet only
(decided 2026-10-08) — refused on real money. The one exception to "a restart leaves them
paused" is the unattended lab server (LAB_AUTO_RESUME=true, decided 2026-10-09): lab bots
that were running when the gateway stopped are resumed after a delay — testnet only, never
while the kill switch is engaged, never a bot a person or a safety rule paused.

Each loop (every ``check_interval_s``):
    1. open lots: sell the ones whose exit has come (cycle: target; advisor: target / stop /
       time limit) — a target sale is a LIMIT at the target, so it can't fill below it;
    2. CYCLE: buy another lot if the rules allow (``cycle.buy_decision``);
       ADVISOR: every ``scan_interval_s`` look at each coin (``advisor.evaluate_coin``), make
       proposals, approve them itself in AUTO mode; expire unanswered ones.
"""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.exchanges.precision import ceil_to_step, floor_to_step
from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.automation import (
    PROPOSALS_KEPT,
    AdvisorMode,
    AdvisorParams,
    AdvisorProposal,
    BotKind,
    BotState,
    BotStatus,
    CycleParams,
    Lot,
    LotStatus,
    ProposalStatus,
)
from backend.shared.models.market_data import split_symbol
from backend.shared.models.order import OrderCreate, OrderSide, OrderType
from backend.shared.utils.logger import get_logger

from .advisor import evaluate_coin
from .cycle import allocate_sale, buy_decision, buy_quantity, lots_to_sell, target_price
from .fund import AutomationFund, FundError, pocket_value, summarize_order
from .lab import PRESETS, PracticeLab

logger = get_logger("automation")

MAX_CONSECUTIVE_REJECTIONS = 3
STOP_GRACE_S = 15.0
RETRY_AFTER_UNFILLED_S = 30.0  # an IOC that didn't fill waits this long before the next try
MAX_PRICE_MOVE_TO_APPROVE = 0.01  # a proposal is refused if the ask moved more than 1% since
MIN_SIZE_MARGIN = 1.05  # a buy must clear the exchange minimum by 5%, so the later sale (after a fee) does too
DUST_USDT = 0.01


class BotNotFoundError(DomainError):
    def __init__(self, bot_id: str):
        super().__init__(f"Bot {bot_id} not found", "BOT_NOT_FOUND")


class BotRejectedError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "BOT_REJECTED")


class AutomationEngine:
    def __init__(self, entry, order_manager, aggregator, router, registry, risk_engine, wallet,
                 fund: AutomationFund, lab: PracticeLab, symbols: list[str], research=None, store=None,
                 mode: str = "testnet", market_fee_bps: dict[str, float] | None = None,
                 protection_bps: float = 50.0) -> None:
        self._entry = entry
        self._orders = order_manager
        self._aggregator = aggregator
        self._router = router
        self._registry = registry
        self._risk = risk_engine
        self._wallet = wallet
        self.fund = fund
        self.lab = lab
        self._symbols = list(symbols)
        self._research = research
        self._store = store
        self._mode = mode
        self._fees = dict(market_fee_bps or {})
        self._protection = protection_bps / 1e4
        self._bots: dict[str, BotState] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._busy: set[str] = set()  # an order of this bot is at the exchange right now
        self._buy_retry_at: dict[str, datetime] = {}
        self._auto_resume_task: asyncio.Task | None = None

    # ── lifecycle ───────────────────────────────────────────────────
    async def load(self) -> None:
        """Restore the fund, the lab and every bot. A bot that was running comes back PAUSED."""
        await self.fund.load()
        await self.lab.load()
        if self._store is None:
            return
        for bot in await self._store.load_bots():
            self._bots[bot.id] = bot
            if bot.status == BotStatus.RUNNING:  # the gateway died without its clean stop (crash, power cut)
                await self._set_status(bot, BotStatus.PAUSED, "system",
                                       "Gateway restarted while this bot was running — review and resume it",
                                       restart=True)

    def start(self, auto_resume_after_s: float | None = None) -> None:
        """Start listening; with ``auto_resume_after_s`` (the lab server) also resume the interrupted lab bots then."""
        event_bus.subscribe("kill_switch", self._on_kill_switch)
        if auto_resume_after_s is not None:
            self._auto_resume_task = asyncio.create_task(self._auto_resume_later(auto_resume_after_s),
                                                         name="bots-auto-resume")

    async def stop(self) -> None:
        if self._auto_resume_task is not None:  # first: nothing may resume a bot while we stop them
            self._auto_resume_task.cancel()
            await asyncio.gather(self._auto_resume_task, return_exceptions=True)
        event_bus.unsubscribe("kill_switch", self._on_kill_switch)
        for bot in list(self._bots.values()):
            if bot.status == BotStatus.RUNNING:
                await self._set_status(bot, BotStatus.PAUSED, "system", "Gateway stopped — resume it after the restart",
                                       restart=True)
        deadline = asyncio.get_running_loop().time() + STOP_GRACE_S
        while self._busy and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.2)
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ── queries ─────────────────────────────────────────────────────
    def list(self) -> list[BotState]:
        bots = sorted(self._bots.values(), key=lambda b: (b.status not in (BotStatus.RUNNING, BotStatus.PAUSED),
                                                         -b.created_at.timestamp()))
        return [self.view(b) for b in bots]

    def get(self, bot_id: str) -> BotState:
        bot = self._bots.get(bot_id)
        if bot is None:
            raise BotNotFoundError(bot_id)
        return bot

    def view(self, bot: BotState) -> BotState:
        """The bot with its pocket and what it is worth now."""
        out = bot.model_copy(deep=True)
        pocket = self.fund.pocket(bot.id)
        out.pocket = pocket.model_copy(deep=True) if pocket else None
        if pocket is not None:
            value, unrealized = pocket_value(pocket, self.fund.mark)
            out.value_usdt = None if value is None else round(value, 8)
            out.unrealized_usdt = None if unrealized is None else round(unrealized, 8)
            if unrealized is not None:
                out.pnl_usdt = round(pocket.realized_usdt + unrealized, 8)
                extra = sum(pocket.market_cost_usdt.values()) - sum(pocket.cost_usdt.values())
                out.pnl_market_usdt = round(pocket.realized_market_usdt + unrealized - extra, 8)
        return out

    def bots(self) -> dict[str, BotState]:
        return dict(self._bots)

    # ── market helpers ──────────────────────────────────────────────
    async def _quote(self, symbol: str) -> tuple[float, float, float] | None:
        """(best bid, best ask, mid) over the exchanges we can trade this coin on."""
        nbbo = await self._aggregator.compute_nbbo(symbol)
        if not nbbo:
            return None
        executable = set(self._router.executable_venues(symbol))
        quotes = [q for vid, q in nbbo.venue_quotes.items() if vid in executable and not q.is_stale]
        bids = [q.bid_price for q in quotes if q.bid_price > 0]
        asks = [q.ask_price for q in quotes if q.ask_price > 0]
        if not bids or not asks:
            return None
        bid, ask = max(bids), min(asks)
        return bid, ask, (bid + ask) / 2

    def _rules(self, symbol: str) -> dict:
        venues = self._router.executable_venues(symbol)
        return {v: r for v in venues if (a := self._registry.get(v)) and (r := a.rules(symbol))}

    def _min_order_usdt(self, symbol: str, rules: dict, price: float, side: OrderSide) -> float | None:
        """The smallest order (USDT) an exchange that can fund this side accepts."""
        base, quote = split_symbol(symbol)
        mins = []
        for vid, r in rules.items():
            funding = self._wallet.available(vid, quote if side == OrderSide.BUY else base)
            if funding is not None and funding <= 0:
                continue
            mins.append(max(r.min_notional, r.min_amount * price))
        return min(mins) if mins else None

    def _sell_fee_rate(self, venues: list[str]) -> float:
        """The real-market taker fee where the lot's coins are: it can only be sold there.

        Found live (2026-10-08): taking the highest fee of every exchange put Gate's 20 bps on a
        lot bought on Binance (10 bps), though Gate held no BTC to sell — a target ~0.1% too high.
        """
        return max((self._fees.get(v, 10.0) for v in venues), default=max(self._fees.values(), default=10.0)) / 1e4

    # ── create ──────────────────────────────────────────────────────
    def _check_new(self, lab: bool, symbols: list[str]) -> None:
        if self._risk.kill_switch.is_active:
            raise BotRejectedError("Kill switch is active — no new bots")
        if lab and not self.lab.enabled:
            raise BotRejectedError("Lab bots run on testnet only")
        for s in symbols:
            if s not in self._symbols:
                raise BotRejectedError(f"Unsupported symbol {s}. Tradable: {', '.join(self._symbols)}")

    async def _check_size(self, symbol: str, usdt: float, what: str) -> None:
        q = await self._quote(symbol)
        if q is None:
            raise BotRejectedError(f"No live price for {symbol} on an exchange we can trade on right now")
        rules = self._rules(symbol)
        if not rules:
            raise BotRejectedError(f"No exchange can execute {symbol} right now")
        minimum = self._min_order_usdt(symbol, rules, q[1], OrderSide.BUY)
        if minimum is None:
            raise BotRejectedError(f"No exchange that trades {symbol} holds USDT to buy with")
        if usdt < minimum * MIN_SIZE_MARGIN:
            raise BotRejectedError(f"{what} ${usdt:,.2f} is too small for {symbol}: the smallest order an exchange "
                                   f"that can fund it accepts is ~${minimum:,.2f} (+5% room for the fee, so the sale "
                                   f"still clears it) — use at least ${minimum * MIN_SIZE_MARGIN:,.2f}")
        if usdt > self._risk.limits.max_order_notional:
            raise BotRejectedError(f"{what} ${usdt:,.2f} is above the per-order limit "
                                   f"${self._risk.limits.max_order_notional:,.2f}")

    async def create_cycle(self, params: CycleParams, user: str) -> BotState:
        self._check_new(params.lab, [params.symbol])
        await self._check_size(params.symbol, params.buy_usdt, "Each buy of")
        bot = BotState(id=f"CYCLE-{uuid4().hex[:6].upper()}", kind=BotKind.CYCLE, created_by=user, lab=params.lab,
                       label=params.label or f"{params.symbol} cycle {params.threshold_pct:g}%"
                                             + (" (lab)" if params.lab else ""),
                       cycle=params)
        await self._open(bot, params.budget_usdt, user,
                         f"{bot.id} started by {user}: {params.symbol} profit cycle — ${params.buy_usdt:,.2f} per buy, "
                         f"sell at cost + both fees + {params.threshold_pct:g}%, up to {params.lots_allowed} lots, "
                         f"budget ${params.budget_usdt:,.2f}")
        return self.view(bot)

    async def create_advisor(self, params: AdvisorParams, user: str) -> BotState:
        if params.mode == AdvisorMode.AUTO and self._mode != "testnet":
            raise BotRejectedError("Auto-approve is testnet only (decided 2026-10-08): with real money every trade "
                                   "waits for your approval — use mode ASK")
        if self._research is None:
            raise BotRejectedError("The advisor needs the research service, which isn't running")
        self._check_new(params.lab, params.symbols)
        for symbol in params.symbols:
            await self._check_size(symbol, params.trade_usdt, "Each trade of")
        bot = BotState(id=f"ADVISOR-{uuid4().hex[:6].upper()}", kind=BotKind.ADVISOR, created_by=user, lab=params.lab,
                       label=params.label or (f"advisor {params.mode.value.lower()} P≥{params.min_probability:g}"
                                              + (" (lab)" if params.lab else "")),
                       advisor=params)
        coins = ", ".join(s.split("/")[0] for s in params.symbols)
        await self._open(bot, params.budget_usdt, user,
                         f"{bot.id} started by {user}: advisor on {coins}, {params.mode.value} mode — proposes a "
                         f"${params.trade_usdt:,.2f} buy when next-hour P(up) ≥ {params.min_probability:.0%}, "
                         f"budget ${params.budget_usdt:,.2f}")
        return self.view(bot)

    async def _open(self, bot: BotState, budget: float, user: str, summary: str) -> None:
        try:
            await self.fund.assign(bot.id, budget, user)
        except FundError as exc:
            raise BotRejectedError(exc.message) from exc
        self._bots[bot.id] = bot
        self._persist(bot)
        await audit("bot_started", summary, actor=user, order_id=bot.id,
                    symbol=bot.symbols[0] if len(bot.symbols) == 1 else None,
                    payload=(bot.cycle or bot.advisor).model_dump(mode="json"))
        self._launch(bot)
        await self._publish(bot)

    async def start_preset(self, name: str, user: str) -> list[BotState]:
        """A lab experiment: move its budget from the wallet into the fund, start its bots (testnet only)."""
        if not self.lab.enabled:
            raise BotRejectedError("Lab experiments run on testnet only")
        preset = PRESETS.get(name)
        if preset is None:
            raise BotRejectedError(f"Unknown lab preset {name}. Presets: {', '.join(PRESETS)}")
        total = sum(b["budget_usdt"] for b in preset["bots"])
        if self.fund.state.unassigned_usdt + 1e-9 < total:
            try:
                await self.fund.deposit(total - self.fund.state.unassigned_usdt, user)
            except FundError as exc:
                raise BotRejectedError(exc.message) from exc
        started = []
        for spec in preset["bots"]:
            spec = dict(spec)
            kind = spec.pop("kind")
            if kind == "CYCLE":
                label = f"lab: {spec['symbol'].split('/')[0]} cycle {spec['threshold_pct']:g}%"
                started.append(await self.create_cycle(CycleParams(**spec, lab=True, label=label), user))
            else:
                label = f"lab: {name.replace('_', ' ')}"
                started.append(await self.create_advisor(AdvisorParams(**spec, lab=True, label=label), user))
        await audit("lab_preset_started", f"Lab experiment '{preset['title']}' started by {user}: "
                    + ", ".join(b.id for b in started), actor=user, payload={"preset": name})
        return started

    # ── controls ────────────────────────────────────────────────────
    async def pause(self, bot_id: str, actor: str) -> BotState:
        bot = self.get(bot_id)
        if bot.status != BotStatus.RUNNING:
            raise BotRejectedError(f"{bot_id} is {bot.status.value}, not running")
        await self._set_status(bot, BotStatus.PAUSED, actor, f"Paused by {actor}")
        return self.view(bot)

    async def resume(self, bot_id: str, actor: str, reason: str | None = None) -> BotState:
        bot = self.get(bot_id)
        if bot.status != BotStatus.PAUSED:
            raise BotRejectedError(f"{bot_id} is {bot.status.value}, not paused")
        if self._risk.kill_switch.is_active:
            raise BotRejectedError("Kill switch is active — release it before resuming")
        if bot.lab and not self.lab.enabled:
            raise BotRejectedError("Lab bots run on testnet only")
        bot.stats.consecutive_rejections = 0
        await self._set_status(bot, BotStatus.RUNNING, actor, reason or f"Resumed by {actor}")
        task = self._tasks.get(bot_id)
        if task is None or task.done():
            self._launch(bot)
        return self.view(bot)

    async def auto_resume(self) -> list[str]:
        """The lab server after a restart: resume the lab bots that only the restart paused. Returns their ids.

        Testnet only (lab bots can't run on real money), never while the kill switch is engaged, and never a bot
        that a person, the kill switch or the three-refusals rule paused — those wait for a person.
        """
        waiting = [b for b in self._bots.values()
                   if b.lab and b.status == BotStatus.PAUSED and b.resume_after_restart]
        if not waiting:
            return []
        if not self.lab.enabled:
            logger.warning("bots_auto_resume_refused", reason="lab bots run on testnet only", bots=len(waiting))
            return []
        if self._risk.kill_switch.is_active:
            await audit("bots_auto_resume_skipped",
                        f"Lab bots not resumed after the restart: the kill switch is engaged "
                        f"({', '.join(b.id for b in waiting)} stay paused)", actor="system")
            return []
        resumed = []
        for bot in sorted(waiting, key=lambda b: b.created_at):
            try:
                await self.resume(bot.id, "system", "Resumed automatically after the restart (lab server, "
                                                    "LAB_AUTO_RESUME)")
                resumed.append(bot.id)
            except BotRejectedError as exc:
                logger.warning("bot_auto_resume_refused", bot_id=bot.id, reason=exc.message)
        if resumed:
            await audit("bots_auto_resumed", f"Lab bots resumed automatically after the restart: {', '.join(resumed)}",
                        actor="system", payload={"bots": resumed})
        return resumed

    async def _auto_resume_later(self, delay_s: float) -> None:
        await asyncio.sleep(delay_s)  # the exchanges' books and the wallet need a moment after a start
        try:
            await self.auto_resume()
        except Exception as exc:  # noqa: BLE001 — a failed resume leaves the bots paused, which is safe
            logger.error("bots_auto_resume_failed", error=str(exc)[:200])

    async def stop_bot(self, bot_id: str, actor: str) -> BotState:
        """No new trades. Its cash goes back to the fund; open lots stay (sell them or release the coins)."""
        bot = self.get(bot_id)
        if bot.status == BotStatus.STOPPED:
            raise BotRejectedError(f"{bot_id} is already stopped")
        async with self._lock(bot):
            for p in bot.proposals:
                if p.status == ProposalStatus.PROPOSED:
                    p.status, p.result = ProposalStatus.EXPIRED, "the bot was stopped"
            released = await self.fund.release(bot.id, actor)
            left = len(bot.open_lots)
            await self._set_status(bot, BotStatus.STOPPED, actor,
                                   f"Stopped by {actor}: ${released:,.2f} back to the fund"
                                   + (f"; {left} open lot(s) stay until sold or released" if left else ""))
        return self.view(bot)

    async def sell_now(self, bot_id: str, lot_id: int | None, actor: str) -> BotState:
        """The owner sells open lots at the market now — even below cost. Their decision, audited."""
        bot = self.get(bot_id)
        lots = [lot for lot in bot.open_lots if lot_id is None or lot.id == lot_id]
        if not lots:
            raise BotRejectedError(f"{bot_id} has no open lot" + (f" {lot_id}" if lot_id else ""))
        async with self._lock(bot):
            by_symbol: dict[str, list[Lot]] = {}
            for lot in lots:
                by_symbol.setdefault(lot.symbol, []).append(lot)
            for symbol, group in by_symbol.items():
                await self._sell(bot, symbol, group, None, "sold by owner", actor)
        if bot.status == BotStatus.STOPPED:
            await self.fund.release(bot.id, actor)  # a stopped bot keeps no cash: the sale's goes to the fund
        self._touch(bot)
        await self._publish(bot)
        return self.view(bot)

    async def release_coins(self, bot_id: str, how: str, actor: str) -> BotState:
        """Take a stopped bot's open lots off its book without selling (to manual trading, or lost)."""
        bot = self.get(bot_id)
        if bot.status != BotStatus.STOPPED:
            raise BotRejectedError("Stop the bot first — then its coins can be released")
        async with self._lock(bot):
            now = datetime.now(UTC)
            pocket = self.fund.pocket(bot.id)
            for asset in [a for a, q in (pocket.coins.items() if pocket else []) if q > 0]:  # dust too
                try:
                    await self.fund.release_coins(bot.id, asset, how, actor)
                except FundError as exc:
                    raise BotRejectedError(exc.message) from exc
            for lot in bot.open_lots:
                lot.status, lot.closed_at = LotStatus.WRITTEN_OFF, now
                lot.exit_reason = "moved to manual" if how == "to_manual" else "lost"
                self.lab.record_lot(bot, lot)
            await self.fund.release(bot.id, actor)
        self._touch(bot)
        await self._publish(bot)
        return self.view(bot)

    async def decide(self, proposal_id: str, approve: bool, actor: str) -> BotState:
        bot = next((b for b in self._bots.values() for p in b.proposals if p.id == proposal_id), None)
        if bot is None:
            raise BotNotFoundError(proposal_id)
        proposal = next(p for p in bot.proposals if p.id == proposal_id)
        if proposal.status != ProposalStatus.PROPOSED:
            raise BotRejectedError(f"Proposal {proposal_id} is {proposal.status.value}")
        async with self._lock(bot):
            if not approve:
                proposal.status, proposal.decided_by = ProposalStatus.REJECTED, actor
                proposal.decided_at, proposal.result = datetime.now(UTC), f"rejected by {actor}"
                await audit("advisor_rejected", f"{bot.id}: {proposal.symbol} proposal {proposal.id} rejected by {actor}",
                            actor=actor, order_id=bot.id, symbol=proposal.symbol)
            else:
                await self._approve(bot, proposal, actor)
        self._touch(bot)
        await self._publish(bot)
        return self.view(bot)

    async def _on_kill_switch(self, data: dict) -> None:
        if not data.get("active") or "venue_orders_swept" in data:
            return
        for bot in list(self._bots.values()):
            if bot.status == BotStatus.RUNNING:
                await self._set_status(bot, BotStatus.PAUSED, "kill-switch", f"Kill switch engaged: {data.get('reason')}")

    # ── the loop ────────────────────────────────────────────────────
    def _lock(self, bot: BotState) -> asyncio.Lock:
        return self._locks.setdefault(bot.id, asyncio.Lock())

    def _launch(self, bot: BotState) -> None:
        self._wake[bot.id] = asyncio.Event()
        self._tasks[bot.id] = asyncio.create_task(self._guard(bot.id), name=f"bot-{bot.id}")

    async def _guard(self, bot_id: str) -> None:
        bot = self._bots[bot_id]
        try:
            await self._run(bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — record it, never leave a bot silently stuck
            logger.error("bot_failed", bot_id=bot_id, error=str(exc)[:200])
            await self._set_status(bot, BotStatus.FAILED, "system", f"Internal error: {str(exc)[:200]}")

    async def _sleep(self, bot: BotState, seconds: float) -> None:
        wake = self._wake[bot.id]
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _run(self, bot: BotState) -> None:
        params = bot.cycle or bot.advisor
        while bot.status == BotStatus.RUNNING:
            if self._risk.kill_switch.is_active:
                await self._set_status(bot, BotStatus.PAUSED, "kill-switch", "Kill switch is active")
                return
            before = (bot.waiting_reason, len(bot.lots), bot.stats.sells, bot.stats.buys, len(bot.proposals))
            self._roll_day(bot)
            async with self._lock(bot):
                await self._manage_exits(bot)
                if bot.status == BotStatus.RUNNING:
                    if bot.kind == BotKind.CYCLE:
                        await self._cycle_step(bot)
                    else:
                        await self._advisor_step(bot)
            if (bot.waiting_reason, len(bot.lots), bot.stats.sells, bot.stats.buys, len(bot.proposals)) != before:
                self._touch(bot)
                await self._publish(bot)
            await self._sleep(bot, params.check_interval_s)

    def _roll_day(self, bot: BotState) -> None:
        today = datetime.now(UTC).date().isoformat()
        if bot.stats.trades_day != today:
            bot.stats.trades_day, bot.stats.trades_today = today, 0

    # ── profit cycle ────────────────────────────────────────────────
    async def _cycle_step(self, bot: BotState) -> None:
        p = bot.cycle
        q = await self._quote(p.symbol)
        if q is None:
            bot.waiting_reason = f"no live price for {p.symbol} on an exchange we can trade on"
            return
        bid, ask, mid = q
        now = datetime.now(UTC)
        pocket = self.fund.pocket(bot.id)
        nearest = min((lot.target_price for lot in bot.open_lots), default=None)
        to_target = (f" · nearest sell target {nearest:,.6g} ({(nearest / bid - 1) * 100:+.3f}% from the bid)"
                     if nearest else "")
        retry = self._buy_retry_at.get(bot.id)
        if retry and now < retry:
            bot.waiting_reason = f"the last buy didn't fill — trying again in {(retry - now).total_seconds():.0f} s" + to_target
            return
        decision = buy_decision(p, bot.open_lots, pocket.cash_usdt if pocket else 0.0, ask, bid,
                                bot.stats.last_buy_at, now)
        if not decision.buy:
            bot.waiting_reason = decision.reason + to_target
            return
        rules = self._rules(p.symbol)
        if not rules:
            bot.waiting_reason = f"no exchange can execute {p.symbol} right now"
            return
        step = max(r.amount_step for r in rules.values())
        qty = buy_quantity(p.buy_usdt, ask, self._protection, step)
        minimum = self._min_order_usdt(p.symbol, rules, ask, OrderSide.BUY)
        if qty <= 0 or minimum is None or qty * ask < minimum:
            bot.waiting_reason = (f"a ${p.buy_usdt:,.2f} buy is below what an exchange that can fund it accepts "
                                  f"(~${minimum or 0:,.2f}) at this price")
            return
        await self._buy(bot, p.symbol, qty, mid, p.routing, decision.reason, target=None)

    async def _buy(self, bot: BotState, symbol: str, qty: float, mid: float, routing, why: str,
                   target: tuple[float, float, datetime] | None, proposal: AdvisorProposal | None = None) -> Lot | None:
        """Buy for a bot. ``target`` = (target, stop, expires) for an advisor trade; None = profit cycle."""
        base, _ = split_symbol(symbol)
        features = await self.lab.features(symbol)
        order_in = OrderCreate(symbol=symbol, side=OrderSide.BUY, quantity=qty, order_type=OrderType.MARKET,
                               strategy=routing)
        order, error = await self._submit(bot, order_in, mid)
        fills = summarize_order(order, self._wallet.price_usdt, self._fees)
        now = datetime.now(UTC)
        if fills.filled <= 0 or fills.base_change <= 0:
            reason = (error.message if error else None) or order.rejection_reason or "nothing filled"
            self._buy_retry_at[bot.id] = now + timedelta(seconds=RETRY_AFTER_UNFILLED_S)
            bot.waiting_reason = f"buy not filled: {reason}"
            if proposal is not None:
                proposal.status, proposal.result = ProposalStatus.FAILED, f"buy not filled: {reason}"
            await audit("bot_buy_failed", f"{bot.id}: buy of {qty:,.8g} {base} not filled — {reason}",
                        actor=bot.id, order_id=bot.id, symbol=symbol)
            return None
        self.fund.apply_buy(bot.id, base, fills)
        cost = -fills.quote_change
        market_cost = cost + (fills.market_fee_usdt - fills.fee_usdt)
        if target is None:
            tgt = target_price(market_cost, fills.base_change, self._sell_fee_rate(fills.venues), bot.cycle.threshold_pct)
            stop = expires = None
        else:
            tgt, stop, expires = target
        lot = Lot(id=bot.next_lot_id, symbol=symbol, qty=fills.base_change, bought_qty=fills.filled, cost_usdt=cost,
                  market_cost_usdt=market_cost, buy_price=fills.avg_price, mid_at_buy=mid, target_price=tgt,
                  stop_price=stop, expires_at=expires, buy_order_id=str(order.id), venues=fills.venues,
                  proposal_id=proposal.id if proposal else None,
                  features={**features, **(proposal.evidence if proposal else {}),
                            **({"p_up_1h": proposal.p_up} if proposal else {})})
        bot.next_lot_id += 1
        bot.lots.append(lot)
        bot.stats.buys += 1
        bot.stats.trades_today += 1
        bot.stats.last_buy_at = now
        self._buy_retry_at.pop(bot.id, None)
        self.lab.record_lot(bot, lot)
        sell_side = (f"sells at {tgt:,.6g} or higher ({(tgt / fills.avg_price - 1) * 100:+.3f}%)" if stop is None else
                     f"target {tgt:,.6g}, stop {stop:,.6g}, sold by {expires:%H:%M} UTC at the latest")
        bot.waiting_reason = f"bought lot {lot.id}: {fills.base_change:,.8g} {base} @ {fills.avg_price:,.6g}; {sell_side}"
        await audit("bot_buy", f"{bot.id} lot {lot.id}: bought {fills.base_change:,.8g} {base} @ {fills.avg_price:,.6g} "
                    f"(${cost:,.2f} incl. fees, {', '.join(fills.venues)}) — {why}; {sell_side}",
                    actor=bot.id, order_id=bot.id, symbol=symbol, payload=lot.model_dump(mode="json"))
        return lot

    # ── exits (both kinds) ──────────────────────────────────────────
    async def _manage_exits(self, bot: BotState) -> None:
        open_lots = bot.open_lots
        if not open_lots:
            return
        now = datetime.now(UTC)
        by_symbol: dict[str, list[Lot]] = {}
        for lot in open_lots:
            by_symbol.setdefault(lot.symbol, []).append(lot)
        for symbol, lots in by_symbol.items():
            q = await self._quote(symbol)
            if q is None:
                continue
            bid = q[0]
            rules = self._rules(symbol)
            if not rules:
                continue
            min_qty = (self._min_order_usdt(symbol, rules, bid, OrderSide.SELL) or 0.0) / bid
            if bot.kind == BotKind.CYCLE:
                ready = lots_to_sell(lots, bid, min_qty, now)
                if ready:
                    await self._sell(bot, symbol, ready, max(lot.target_price for lot in ready), "target", bot.id)
                continue
            for lot in lots:  # advisor: each trade has its own bracket
                if lot.retry_after is not None and lot.retry_after > now:
                    continue
                if bid >= lot.target_price:
                    await self._sell(bot, symbol, [lot], lot.target_price, "target", bot.id)
                elif lot.stop_price is not None and bid <= lot.stop_price:
                    await self._sell(bot, symbol, [lot], None, "stop", bot.id)
                elif lot.expires_at is not None and now >= lot.expires_at:
                    await self._sell(bot, symbol, [lot], None, "max hold", bot.id)

    async def _sell(self, bot: BotState, symbol: str, lots: list[Lot], limit: float | None, reason: str,
                    actor: str) -> None:
        """Sell these lots together: at ``limit`` or better (rounded UP to the tick), or at the market."""
        base, _ = split_symbol(symbol)
        rules = self._rules(symbol)
        q = await self._quote(symbol)
        if not rules or q is None:
            return
        step = max(r.amount_step for r in rules.values())
        # Dust: coins this bot holds that belong to no open lot (what rounding to the lot size left
        # after earlier sales). It goes out with this sale, so it never grows past one step.
        pocket = self.fund.pocket(bot.id)
        open_same = [lot for lot in bot.open_lots if lot.symbol == symbol]
        dust = max((pocket.coins.get(base, 0.0) if pocket else 0.0) - sum(lot.qty for lot in open_same), 0.0)
        dust_cost = max((pocket.cost_usdt.get(base, 0.0) if pocket else 0.0) - sum(lot.cost_usdt for lot in open_same), 0.0)
        dust_mcost = max((pocket.market_cost_usdt.get(base, 0.0) if pocket else 0.0)
                         - sum(lot.market_cost_usdt for lot in open_same), 0.0)
        qty = floor_to_step(sum(lot.qty for lot in lots) + dust, step)
        if qty <= 0:
            return
        if limit is not None:
            tick = max(r.price_tick for r in rules.values())
            limit = ceil_to_step(limit, tick) if tick else limit
        order_in = OrderCreate(symbol=symbol, side=OrderSide.SELL, quantity=qty,
                               order_type=OrderType.LIMIT if limit else OrderType.MARKET, limit_price=limit,
                               strategy=(bot.cycle or bot.advisor).routing)
        order, error = await self._submit(bot, order_in, q[2])
        fills = summarize_order(order, self._wallet.price_usdt, self._fees)
        now = datetime.now(UTC)
        if fills.filled <= 0:
            for lot in lots:
                lot.retry_after = now + timedelta(seconds=RETRY_AFTER_UNFILLED_S)
            why = (error.message if error else None) or order.rejection_reason or "no fill at the limit"
            bot.waiting_reason = f"sale ({reason}) of {qty:,.8g} {base} not filled: {why}"
            return
        sold = -fills.base_change  # coins that left, incl. a fee taken in the coin
        proceeds, extra_fee = fills.quote_change, fills.market_fee_usdt - fills.fee_usdt
        cost = market_cost = 0.0
        closed = []
        allocated = allocate_sale(lots, sold)
        dust_sold = max(sold - sum(take for _, take in allocated), 0.0)
        if dust > 0 and dust_sold > 0:
            share = min(dust_sold / dust, 1.0)
            cost += dust_cost * share
            market_cost += dust_mcost * share
        for lot, take in allocated:
            share = take / lot.qty
            c, mc = lot.cost_usdt * share, lot.market_cost_usdt * share
            part = take / sold
            lot.qty -= take
            lot.cost_usdt -= c
            lot.market_cost_usdt -= mc
            lot.sold_qty += take
            lot.sold_cost_usdt += c
            lot.sold_market_cost_usdt += mc
            lot.proceeds_usdt += proceeds * part
            lot.proceeds_market_usdt += (proceeds - extra_fee) * part
            lot.sell_order_ids.append(str(order.id))
            lot.retry_after = None
            cost += c
            market_cost += mc
            # What rounding to the lot size leaves (under one step) can never be sold on its own:
            # the lot is done, and that dust stays in the bot's book as coins it holds.
            if lot.qty < step - 1e-15 or lot.qty * q[0] < DUST_USDT:
                lot.status, lot.closed_at, lot.exit_reason = LotStatus.CLOSED, now, reason
                lot.profit_usdt = lot.proceeds_usdt - lot.sold_cost_usdt
                lot.profit_market_usdt = lot.proceeds_market_usdt - lot.sold_market_cost_usdt
                closed.append(lot)
                bot.stats.lots_closed += 1
                if reason == "target":
                    bot.stats.wins += 1
                elif reason == "stop" or (lot.profit_market_usdt or 0) < 0:
                    bot.stats.losses += 1
                elif reason == "max hold":
                    bot.stats.timeouts += 1
            self.lab.record_lot(bot, lot)
        self.fund.apply_sell(bot.id, base, fills, cost, market_cost)
        bot.stats.sells += 1
        profit = proceeds - cost
        lots_txt = ", ".join(str(lot.id) for lot in lots)
        bot.waiting_reason = (f"sold {sold:,.8g} {base} @ {fills.avg_price:,.6g} ({reason}) — lot(s) {lots_txt}: "
                              f"{profit:+,.4f} USDT ({proceeds - extra_fee - market_cost:+,.4f} at real-market fees)")
        await audit("bot_sell", f"{bot.id} lot(s) {lots_txt}: sold {sold:,.8g} {base} @ {fills.avg_price:,.6g} "
                    f"({reason}, {', '.join(fills.venues)}) — profit {profit:+,.4f} USDT, "
                    f"{proceeds - extra_fee - market_cost:+,.4f} at real-market fees"
                    + (f"; {len(closed)} lot(s) closed" if closed else "; partly filled"),
                    actor=actor, order_id=bot.id, symbol=symbol,
                    payload={"lots": [lot.model_dump(mode="json") for lot in lots], "order_id": str(order.id)})

    # ── advisor ─────────────────────────────────────────────────────
    async def _advisor_step(self, bot: BotState) -> None:
        p = bot.advisor
        now = datetime.now(UTC)
        for proposal in bot.proposals:
            if proposal.status == ProposalStatus.PROPOSED and now >= proposal.expires_at:
                proposal.status, proposal.result = ProposalStatus.EXPIRED, "not answered in time"
                await audit("advisor_expired", f"{bot.id}: {proposal.symbol} proposal {proposal.id} expired unanswered",
                            actor=bot.id, order_id=bot.id, symbol=proposal.symbol)
        last = bot.stats.last_scan_at
        if last is not None and (now - last).total_seconds() < p.scan_interval_s:
            wait = p.scan_interval_s - (now - last).total_seconds()
            bot.waiting_reason = f"next look at the market in {wait:.0f} s" + self._open_summary(bot)
            return
        bot.stats.last_scan_at = now
        scan = []
        for symbol in p.symbols:
            if bot.status != BotStatus.RUNNING:
                break
            row = await self._evaluate(bot, symbol)
            scan.append(row)
        bot.last_scan = scan
        proposed = [r["symbol"] for r in scan if r.get("proposed")]
        bot.waiting_reason = (f"looked at {len(scan)} coin(s): " + (f"proposed {', '.join(proposed)}" if proposed
                                                                     else "nothing worth proposing")
                              + self._open_summary(bot))

    def _open_summary(self, bot: BotState) -> str:
        n = len(bot.open_lots)
        waiting = sum(1 for x in bot.proposals if x.status == ProposalStatus.PROPOSED)
        return (f" · {n} open trade(s)" if n else "") + (f" · {waiting} waiting for approval" if waiting else "")

    async def _evaluate(self, bot: BotState, symbol: str) -> dict:
        p = bot.advisor
        if any(lot.symbol == symbol for lot in bot.open_lots):
            return {"symbol": symbol, "decision": "already holds a trade in this coin"}
        if any(x.symbol == symbol and x.status == ProposalStatus.PROPOSED for x in bot.proposals):
            return {"symbol": symbol, "decision": "a proposal for this coin is waiting for approval"}
        forecast = plan = None
        try:
            forecast = await self._research.forecast_horizon(symbol, "1h")
            plan = await self._research.trade_plan(symbol, "1h", equity=p.budget_usdt, risk_pct=1.0, k_atr=p.k_atr,
                                                   rr=p.rr, max_bars=max(1, math.ceil(p.max_hold_hours)))
        except Exception as exc:  # noqa: BLE001 — research still loading: say so for this coin
            forecast = forecast or {"unavailable": str(exc)[:160]}
        q = await self._quote(symbol)
        ask = q[1] if q else None
        p_up = (forecast or {}).get("p_up")
        verdict = evaluate_coin(p, symbol, forecast, plan, ask,
                                lab=self.lab.similar_advisor(p_up) if p_up is not None else None,
                                costs=self.lab.measured_costs(symbol))
        row = {"symbol": symbol, "p_up": verdict.p_up, "edge": (forecast or {}).get("edge"), "decision": verdict.why,
               "proposed": verdict.propose}
        if not verdict.propose:
            return row
        proposal = AdvisorProposal(
            id=f"P-{uuid4().hex[:8].upper()}", bot_id=bot.id,
            expires_at=datetime.now(UTC) + timedelta(seconds=p.proposal_ttl_s), symbol=symbol,
            notional_usdt=p.trade_usdt, entry_price=verdict.entry, stop_price=verdict.stop, target_price=verdict.target,
            max_hold_hours=p.max_hold_hours, p_up=verdict.p_up, reasons=verdict.reasons, evidence=verdict.evidence)
        bot.proposals.insert(0, proposal)
        bot.proposals = bot.proposals[:PROPOSALS_KEPT]
        await audit("advisor_proposal", f"{bot.id} proposes: buy ${p.trade_usdt:,.2f} of {symbol} at ~{verdict.entry:,.6g} "
                    f"(stop {verdict.stop:,.6g}, target {verdict.target:,.6g}) — {verdict.why}",
                    actor=bot.id, order_id=bot.id, symbol=symbol, payload=proposal.model_dump(mode="json"))
        if p.mode == AdvisorMode.AUTO and self._mode == "testnet":
            await self._approve(bot, proposal, f"{bot.id} (auto)")
        return row

    async def _approve(self, bot: BotState, proposal: AdvisorProposal, actor: str) -> None:
        p = bot.advisor
        now = datetime.now(UTC)
        proposal.decided_by, proposal.decided_at = actor, now

        def refuse(why: str, status: ProposalStatus = ProposalStatus.EXPIRED) -> None:
            proposal.status, proposal.result = status, why

        if self._risk.kill_switch.is_active:
            return refuse("kill switch is active")
        if now >= proposal.expires_at:
            return refuse("it had expired")
        if bot.status != BotStatus.RUNNING:
            return refuse(f"the bot is {bot.status.value.lower()}")
        if len(bot.open_lots) >= p.max_open:
            return refuse(f"{p.max_open} trade(s) already open (the limit)")
        if bot.stats.trades_today >= p.max_trades_per_day:
            return refuse(f"{p.max_trades_per_day} trades today already (the limit)")
        pocket = self.fund.pocket(bot.id)
        if pocket is None or pocket.cash_usdt + 1e-9 < proposal.notional_usdt:
            return refuse(f"the bot has ${pocket.cash_usdt if pocket else 0:,.2f} left, the trade needs "
                          f"${proposal.notional_usdt:,.2f}")
        q = await self._quote(proposal.symbol)
        if q is None:
            return refuse("no live price right now")
        bid, ask, mid = q
        if abs(ask / proposal.entry_price - 1) > MAX_PRICE_MOVE_TO_APPROVE:
            return refuse(f"the price moved {(ask / proposal.entry_price - 1) * 100:+.2f}% since the proposal "
                          f"(more than {MAX_PRICE_MOVE_TO_APPROVE:.0%})")
        if bid <= proposal.stop_price or bid >= proposal.target_price:
            return refuse("the price is already outside the bracket")
        rules = self._rules(proposal.symbol)
        if not rules:
            return refuse("no exchange can execute it right now")
        step = max(r.amount_step for r in rules.values())
        qty = buy_quantity(proposal.notional_usdt, ask, self._protection, step)
        proposal.status = ProposalStatus.APPROVED
        await audit("advisor_approved", f"{bot.id}: {proposal.symbol} proposal {proposal.id} approved by {actor}",
                    actor=actor, order_id=bot.id, symbol=proposal.symbol)
        lot = await self._buy(bot, proposal.symbol, qty, mid, p.routing, f"proposal {proposal.id}",
                              target=(proposal.target_price, proposal.stop_price,
                                      now + timedelta(hours=proposal.max_hold_hours)), proposal=proposal)
        if lot is not None:
            proposal.lot_id, proposal.result = lot.id, f"bought as lot {lot.id}"

    # ── one order ───────────────────────────────────────────────────
    async def _submit(self, bot: BotState, order_in: OrderCreate, mid: float | None):
        self._busy.add(bot.id)
        try:
            order, error = await self._entry.submit(order_in, submitted_by=bot.created_by, parent_id=bot.id)
        finally:
            self._busy.discard(bot.id)
        self.lab.record_execution(bot, order, mid)
        if not order.child_orders:
            bot.stats.consecutive_rejections += 1
            if bot.stats.consecutive_rejections >= MAX_CONSECUTIVE_REJECTIONS and bot.status == BotStatus.RUNNING:
                why = (error.message if error else None) or order.rejection_reason or "rejected"
                await self._set_status(bot, BotStatus.PAUSED, bot.id,
                                       f"{MAX_CONSECUTIVE_REJECTIONS} orders in a row were refused before reaching any "
                                       f"exchange: {why}")
        else:
            bot.stats.consecutive_rejections = 0
        return order, error

    # ── helpers ─────────────────────────────────────────────────────
    def _touch(self, bot: BotState) -> None:
        bot.updated_at = datetime.now(UTC)
        self._persist(bot)

    def _persist(self, bot: BotState) -> None:
        if self._store is not None:
            self._store.upsert_bot(bot)

    async def _publish(self, bot: BotState) -> None:
        await event_bus.publish("bot_update", {"id": bot.id, "status": bot.status.value})

    async def _set_status(self, bot: BotState, status: BotStatus, actor: str, reason: str,
                          restart: bool = False) -> None:
        bot.status, bot.status_reason = status, reason
        bot.resume_after_restart = restart  # any later change (a person, the kill switch, a resume) clears it
        self._touch(bot)
        if (wake := self._wake.get(bot.id)) is not None:
            wake.set()
        event = {BotStatus.RUNNING: "bot_resumed", BotStatus.PAUSED: "bot_paused",
                 BotStatus.STOPPED: "bot_stopped"}.get(status, "bot_failed")
        await audit(event, f"{bot.id} {status.value}: {reason}", actor=actor, order_id=bot.id)
        await self._publish(bot)
        logger.info("bot_status", bot_id=bot.id, status=status.value, reason=reason)
