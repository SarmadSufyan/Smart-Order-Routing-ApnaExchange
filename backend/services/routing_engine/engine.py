"""Order routing pipeline: validate -> price -> risk -> route -> execute.

    PENDING -> VALIDATED -> APPROVED -> ROUTING -> WORKING -> (PARTIALLY_)FILLED
                  \\-> REJECTED at any gate (unsupported symbol, no data, risk, no liquidity)

Child orders go to every winning venue concurrently (``asyncio.gather``) as
IOC LIMIT orders, so the SOR never leaves resting orders on a venue. A
MARKET parent's children are price-protected: capped at the worst price the
router planned to pay, widened by ``price_protection_bps``. Decisions are
made on mainnet books while fills may happen on a testnet book; if the two
drift apart the child expires instead of filling at an arbitrary price.

Each child's UUID doubles as the exchange ``clientOrderId`` so a timed-out
request can be reconciled rather than resent.

A venue that REFUSES a child (bad key, no permission, balance, exchange
error — nothing executed) doesn't sink the order: the refused quantity is
planned again on fresh books without that venue, at most ``MAX_REROUTES``
times, each round price-protected and stopped by the kill switch or a
cancel. A child that expired at its price limit is not re-routed (that would
chase the price). A refusal of our key's right to trade also pauses routing
to that venue for ``trading_block_s`` — found live: an OKX key that could
read balances but not trade failed every order whose best price was on OKX.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from backend.exchanges.base import ExecutionResult
from backend.exchanges.precision import ceil_to_step, floor_to_step, is_close
from backend.exchanges.registry import ExchangeRegistry
from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import DomainError, KillSwitchActiveError, RiskCheckFailedError
from backend.shared.models.execution_report import ExecType
from backend.shared.models.order import ChildOrder, Order, OrderSide, OrderStatus, OrderType
from backend.shared.models.risk import RiskCheckResult
from backend.shared.models.routing import RoutingResult, RoutingStatus
from backend.shared.utils.logger import get_logger

from .strategies.best_price import BestPriceStrategy, fmt_price, fmt_qty

logger = get_logger("routing_engine")

MAX_REROUTES = 2  # extra rounds after the first; each round drops the venues that refused


def net_base_change(order: Order, ex: ExecutionResult) -> float:
    """How much base asset actually moved, net of fees charged IN the base asset.

    Some venues take the buy fee in the coin bought (found live on Bybit:
    buying 731.7 XRP delivered 730.9683 after a 0.7317 XRP fee), so a
    position tracked at the gross fill drifts away from the real wallet.
    """
    base = order.symbol.split("/")[0]
    fee_in_base = ex.fee if ex.fee_asset == base else 0.0
    # BUY: you receive filled - fee. SELL: filled leaves, plus any base fee.
    return ex.filled_qty - fee_in_base if order.side == OrderSide.BUY else ex.filled_qty + fee_in_base


def protected_limit_price(
    side: OrderSide, planned_price: float, bps: float, tick: float = 0.0
) -> float:
    """Worst acceptable price for a MARKET child: planned price widened by ``bps``.

    Rounded to the venue tick TOWARD the planned price (down for a buy, up for
    a sell) so the band is never exceeded and the stored limit is exactly what
    the venue receives.
    """
    band = planned_price * bps / 10_000
    if side == OrderSide.BUY:
        return floor_to_step(planned_price + band, tick) if tick else planned_price + band
    return ceil_to_step(planned_price - band, tick) if tick else planned_price - band


class UnsupportedSymbolError(DomainError):
    def __init__(self, symbol: str, supported: list[str]):
        super().__init__(
            f"Unsupported symbol {symbol}. Tradable: {', '.join(supported)}", "UNSUPPORTED_SYMBOL"
        )


class RoutingEngine:
    def __init__(
        self,
        market_data_aggregator,
        market_data_collector,
        risk_engine,
        venue_monitor,
        order_manager,
        registry: ExchangeRegistry,
        depth_levels: int = 50,
        price_protection_bps: float = 50.0,
        coherence=None,
        wallet=None,
        trading_block_s: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._aggregator = market_data_aggregator
        self._collector = market_data_collector
        self._risk = risk_engine
        self._venue_monitor = venue_monitor
        self._orders = order_manager
        self._registry = registry
        self._depth_levels = depth_levels
        self._protection_bps = price_protection_bps
        self._child_band_bps: dict[str, float] = {}  # band actually used per child
        self._coherence = coherence
        self._wallet = wallet
        self._block_s = trading_block_s
        self._clock = clock
        self._trade_blocks: dict[str, tuple[str, float]] = {}  # venue -> (reason, resume at clock time)
        self._strategy = BestPriceStrategy()
        self._orders_routed_today = 0
        self._timed_orders = 0  # routed by this process (restored ones have no timing)
        self._total_routing_time_ms = 0.0
        self._venue_allocation: dict[str, int] = {}

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def restore_stats(self, orders: list[Order]) -> None:
        """Recount today's routed orders from the history restored after a restart."""
        today = datetime.now(UTC).date()
        for o in orders:
            if o.child_orders and o.created_at.date() == today:
                self._orders_routed_today += 1
                for child in o.child_orders:
                    self._venue_allocation[child.venue_id] = self._venue_allocation.get(child.venue_id, 0) + 1

    @property
    def stats(self) -> dict:
        total = max(self._orders_routed_today, 1)
        return {
            "orders_routed_today": self._orders_routed_today,
            "avg_routing_time_ms": round(self._total_routing_time_ms / max(self._timed_orders, 1), 2),
            "venue_allocation": {
                vid: round(count / total, 2) for vid, count in self._venue_allocation.items()
            },
        }

    def executable_venues(self, symbol: str | None = None) -> list[str]:
        """Venues that are healthy AND able to execute orders.

        With a ``symbol``, also require that the venue's execution book for
        that symbol tracks the real market (coherence monitor).
        """
        venues = [
            vid
            for vid in self._venue_monitor.get_routable_venues()
            if (adapter := self._registry.get(vid)) is not None and adapter.can_trade
            and self._trading_block(vid) is None
        ]
        if symbol and self._coherence is not None:
            venues = [v for v in venues if self._coherence.blocking_reason(v, symbol) is None]
        return venues

    def excluded_venues(self, symbol: str | None = None) -> dict[str, str]:
        """Every non-executable venue with the reason, for status and proofs."""
        excluded = dict(self._venue_monitor.get_excluded_venues())
        for vid in self._venue_monitor.get_routable_venues():
            adapter = self._registry.get(vid)
            if adapter is None:
                continue
            if not adapter.can_trade:
                excluded[vid] = adapter.execution_note or "execution not available"
            elif block := self._trading_block(vid):
                excluded[vid] = block
            elif symbol and self._coherence is not None:
                reason = self._coherence.blocking_reason(vid, symbol)
                if reason:
                    excluded[vid] = reason
        return excluded

    async def venue_capacity(self, order: Order, venues: list[str], nbbo) -> dict[str, float]:
        """Most base asset each venue can trade for this order, from real balances.

        Inventory is fragmented across venues: a venue can only SELL the coins
        it holds and only BUY with the quote currency it holds. Found live —
        a sell routed entirely to the best bid failed because most of the coins
        sat on another venue. Venues whose balance is unknown are not capped.
        """
        if self._wallet is None:
            return {}
        await self._wallet.ensure_fresh()
        base, quote = order.symbol.split("/")
        capacity: dict[str, float] = {}
        for vid in venues:
            if order.side == OrderSide.SELL:
                free = self._wallet.available(vid, base)
                if free is not None:
                    capacity[vid] = free
            else:
                free_quote = self._wallet.available(vid, quote)
                q = nbbo.venue_quotes.get(vid) if nbbo else None
                if free_quote is not None and q and q.ask_price > 0:
                    # conservative: assume we pay up to the protection band above the ask
                    capacity[vid] = free_quote / (q.ask_price * (1 + self._protection_bps / 10_000))
        return capacity

    async def _reject(self, order: Order, reason: str) -> None:
        await self._orders.transition(order.id, OrderStatus.REJECTED, rejection_reason=reason)

    async def route_order(self, order: Order) -> RoutingResult:
        # In flight until every child has a final answer from its venue.
        self._orders.begin_execution(order.id)
        try:
            return await self._route(order)
        finally:
            self._orders.end_execution(order.id)

    async def _route(self, order: Order) -> RoutingResult:
        start = time.monotonic()
        await self._orders.transition(order.id, OrderStatus.VALIDATED)

        # ── Gate 1: symbol ──────────────────────────────────────────
        if order.symbol not in self._collector.symbols:
            await self._reject(order, "Unsupported symbol")
            raise UnsupportedSymbolError(order.symbol, self._collector.symbols)

        nbbo = await self._aggregator.compute_nbbo(order.symbol)
        mid_price = (nbbo.best_bid + nbbo.best_ask) / 2 if nbbo else 0.0
        routable = self.executable_venues(order.symbol)

        # ── Lot-size normalisation (coarsest step among routable venues)
        steps: dict[str, float] = {}
        minimums: dict[str, tuple[float, float]] = {}  # venue -> (min lot, min order value)
        for vid in routable:
            adapter = self._registry.get(vid)
            rules = adapter.rules(order.symbol) if adapter else None
            if rules:
                steps[vid] = rules.amount_step
                minimums[vid] = (rules.min_amount, rules.min_notional)
        original_qty = order.quantity
        if steps:
            normalized = floor_to_step(order.quantity, max(steps.values()))
            if normalized <= 0:
                await self._reject(order, f"Quantity {original_qty} is below the minimum lot size")
                raise RiskCheckFailedError(f"quantity {original_qty} is below the minimum lot size")
            order.quantity = normalized

        # ── Gate 2: pre-trade risk ──────────────────────────────────
        risk_check = await self._risk.check_order(order, mid_price=mid_price)
        if risk_check.result == RiskCheckResult.REJECTED_KILL_SWITCH:
            await self._reject(order, "Kill switch active")
            raise KillSwitchActiveError()
        if risk_check.result != RiskCheckResult.APPROVED:
            reason = risk_check.message or risk_check.result.value
            await self._reject(order, reason)
            raise RiskCheckFailedError(reason)
        await self._orders.transition(order.id, OrderStatus.APPROVED)

        # ── Gate 3: live market data ────────────────────────────────
        if not nbbo:
            await self._reject(order, "No live market data for this symbol")
            return RoutingResult(
                status=RoutingStatus.REJECTED,
                rejection_reason="No live market data for this symbol",
                routing_time_ms=(time.monotonic() - start) * 1000,
            )

        # ── Gate 4: can the venues fund it? (inventory is fragmented) ─
        capacity = await self.venue_capacity(order, routable, nbbo)
        if routable and len(capacity) == len(routable):  # only when every balance is known
            total = sum(capacity.values())
            if total < order.quantity and not is_close(total, order.quantity):
                base, quote = order.symbol.split("/")
                funding = quote if order.side == OrderSide.BUY else base
                breakdown = ", ".join(f"{vid} {fmt_qty(q)}" for vid, q in capacity.items())
                reason = (f"Insufficient balance: the venues can fund {fmt_qty(total)} of "
                          f"{fmt_qty(order.quantity)} {base} from their {funding} ({breakdown})")
                await self._reject(order, reason)
                raise RiskCheckFailedError(reason)

        # ── Decide ──────────────────────────────────────────────────
        await self._orders.transition(order.id, OrderStatus.ROUTING)
        depth = {
            vid: d
            for vid in routable
            if (d := self._collector.get_depth(vid, order.symbol, self._depth_levels)) is not None
        }
        names = {a.venue_id: a.name for a in self._registry.all()}
        result = await self._strategy.route(
            order, nbbo, routable, depth=depth, lot_steps=steps, venue_names=names,
            exclusion_reasons=self.excluded_venues(order.symbol), capacity=capacity, min_sizes=minimums,
        )
        result.routing_time_ms = round((time.monotonic() - start) * 1000, 2)

        if result.decision is not None:
            if not is_close(order.quantity, original_qty):
                result.decision.quantity_rounded_from = original_qty
                result.decision.notes.insert(
                    0, f"Quantity rounded down to the venue lot size: {fmt_qty(original_qty)} -> {fmt_qty(order.quantity)}"
                )
            # Persist the decision so the UI can show exactly why it routed this way.
            self._orders.get_order(order.id).routing_decision = result.decision.model_dump()
        await event_bus.publish("routing_decision", self._decision_event(order, result))

        if result.status == RoutingStatus.REJECTED:
            await self._reject(order, result.rejection_reason or "Routing rejected")
            return result

        for child in result.child_orders:
            await self._orders.add_child_order(order.id, child)
        await self._orders.transition(order.id, OrderStatus.WORKING)

        self._orders_routed_today += 1
        self._timed_orders += 1
        self._total_routing_time_ms += result.routing_time_ms
        for child in result.child_orders:
            self._venue_allocation[child.venue_id] = self._venue_allocation.get(child.venue_id, 0) + 1

        # ── Execute all children concurrently, then apply results in order.
        # Whatever a venue refused is planned again without it (bounded rounds).
        children: list[ChildOrder] = result.child_orders
        refused: set[str] = set()
        for round_no in range(MAX_REROUTES + 1):
            refused_qty = await self._execute_round(order, children, refused)
            if refused_qty <= 0 or round_no == MAX_REROUTES:
                break
            children = await self._reroute(order, refused_qty, refused)
            if not children:
                break

        updated = self._orders.get_order(order.id)
        if updated.filled_quantity <= 0 and updated.status == OrderStatus.WORKING:
            reasons = "; ".join(
                f"{c.venue_id}: {c.reject_reason}" for c in updated.child_orders if c.reject_reason
            )
            await self._reject(order, reasons or "All venue executions rejected")
        return result

    async def _execute_round(self, order: Order, children: list[ChildOrder], refused: set[str]) -> float:
        """Send children concurrently, apply results in order; return the quantity venues refused."""
        executions = await asyncio.gather(*(self._send_child(order, child) for child in children))
        refused_qty = 0.0
        for child, ex in zip(children, executions, strict=True):
            await self._apply_execution(order, child, ex)
            if ex.blocks_trading:
                await self._block_trading(child.venue_id, ex.reason or "the venue refused to trade")
            if ex.retry_elsewhere and ex.filled_qty <= 0:
                refused.add(child.venue_id)
                refused_qty += child.quantity
        return refused_qty

    async def _reroute(self, order: Order, quantity: float, refused: set[str]) -> list[ChildOrder]:
        """Plan ``quantity`` again on fresh books, without the venues that refused it.

        Returns the new children (already attached to the order), or [] when
        there is nowhere left to go — the reason goes into the routing proof.
        Risk approved the whole parent, and a re-route never exceeds what is
        still unfilled, so no new risk check is needed; funding is re-checked.
        """
        base = order.symbol.split("/")[0]
        who = ", ".join(sorted(refused))
        current = self._orders.get_order(order.id)

        def give_up(why: str) -> list[ChildOrder]:
            self._add_note(order, f"{who} refused {fmt_qty(quantity)} {base}; not re-routed: {why}")
            return []

        if self._risk.kill_switch.is_active:
            return give_up("kill switch engaged")
        if current.status == OrderStatus.CANCELLED:
            return give_up("order cancelled")
        venues = [v for v in self.executable_venues(order.symbol) if v not in refused]
        if not venues:
            return give_up("no other venue can execute it")
        steps: dict[str, float] = {}
        minimums: dict[str, tuple[float, float]] = {}
        for vid in venues:
            adapter = self._registry.get(vid)
            if adapter and (rules := adapter.rules(order.symbol)):
                steps[vid] = rules.amount_step
                minimums[vid] = (rules.min_amount, rules.min_notional)
        qty = min(quantity, current.quantity - current.filled_quantity)
        if steps:
            qty = floor_to_step(qty, max(steps.values()))
        if qty <= 0:
            return give_up("below the lot size")
        nbbo = await self._aggregator.compute_nbbo(order.symbol)
        if not nbbo:
            return give_up("no live market data")

        plan = order.model_copy(update={"quantity": qty, "child_orders": []})
        depth = {
            vid: d
            for vid in venues
            if (d := self._collector.get_depth(vid, order.symbol, self._depth_levels)) is not None
        }
        result = await self._strategy.route(
            plan, nbbo, venues, depth=depth, lot_steps=steps,
            venue_names={a.venue_id: a.name for a in self._registry.all()},
            exclusion_reasons={**self.excluded_venues(order.symbol), **{v: "refused this order" for v in refused}},
            capacity=await self.venue_capacity(plan, venues, nbbo), min_sizes=minimums,
        )
        if result.status == RoutingStatus.REJECTED or not result.child_orders:
            return give_up(result.rejection_reason or "no liquidity on the other venues")

        for child in result.child_orders:
            await self._orders.add_child_order(order.id, child)
            self._venue_allocation[child.venue_id] = self._venue_allocation.get(child.venue_id, 0) + 1
        event = self._decision_event(plan, result)
        event["summary"] = f"Re-route after {who} refused: {event['summary']}"
        self._add_note(order, event["summary"])
        await event_bus.publish("routing_decision", event)
        return result.child_orders

    def _add_note(self, order: Order, note: str) -> None:
        """Append to the stored routing proof, so the UI shows what happened after the plan."""
        decision = self._orders.get_order(order.id).routing_decision
        if decision is not None:
            decision.setdefault("notes", []).append(note)

    def _trading_block(self, venue_id: str) -> str | None:
        """Why routing to this venue is paused after it refused our key, or None."""
        block = self._trade_blocks.get(venue_id)
        if block is None:
            return None
        reason, resume_at = block
        left = resume_at - self._clock()
        if left <= 0:
            del self._trade_blocks[venue_id]  # cooldown over: the next order there is the probe
            return None
        return f"trading paused for {max(1, round(left / 60))} more min after: {reason}"

    async def _block_trading(self, venue_id: str, reason: str) -> None:
        first = self._trading_block(venue_id) is None
        self._trade_blocks[venue_id] = (reason, self._clock() + self._block_s)
        if first:
            resume = datetime.now(UTC) + timedelta(seconds=self._block_s)
            logger.warning("venue_trading_blocked", venue_id=venue_id, reason=reason, seconds=self._block_s)
            await audit(
                "venue_trading_blocked",
                f"{venue_id}: no new orders for {self._block_s / 60:g} min, then one retry — {reason}",
                actor="router", venue_id=venue_id, payload={"reason": reason, "resume_at": resume.isoformat()},
            )

    def _decision_event(self, order: Order, result: RoutingResult) -> dict:
        base = order.symbol.split("/")[0]
        d = result.decision
        if result.status == RoutingStatus.REJECTED:
            summary = f"Routing rejected: {result.rejection_reason}"
        else:
            legs = ", ".join(
                f"{c.venue_id} {fmt_qty(c.quantity)} @ {fmt_price(c.price)}" for c in result.child_orders
            )
            total = sum(c.quantity for c in result.child_orders)
            summary = f"Routed {fmt_qty(total)} {base} -> {legs}"
            if d is not None:
                summary += (f" · expected VWAP {fmt_price(d.blended_avg_price)}"
                            f" ({d.expected_slippage_bps:.1f} bps vs best)")
        return {
            "order_id": str(order.id),
            "symbol": order.symbol,
            "side": order.side.value,
            "status": result.status.value,
            "summary": summary,
            "routing_time_ms": result.routing_time_ms,
            "children": [
                {"child_order_id": str(c.id), "venue_id": c.venue_id, "quantity": c.quantity, "price": c.price}
                for c in result.child_orders
            ],
            "expected_vwap": d.blended_avg_price if d else None,
            "expected_slippage_bps": d.expected_slippage_bps if d else None,
            "excluded": self.excluded_venues(order.symbol),
            "notes": d.notes if d else [],
        }

    async def _send_child(self, order: Order, child: ChildOrder) -> ExecutionResult:
        adapter = self._registry.get(child.venue_id)
        child.sent_at = datetime.now(UTC)
        # Last gate before money moves: the kill switch (or an operator cancel)
        # may have been engaged after this order passed pre-trade risk.
        halted = (
            "kill switch engaged before this child was sent" if self._risk.kill_switch.is_active
            else "order cancelled before this child was sent"
            if self._orders.get_order(order.id).status == OrderStatus.CANCELLED
            else None
        )
        if halted:
            return ExecutionResult(exec_type=ExecType.REJECT, requested_qty=child.quantity, reason=halted)
        if adapter is None:
            return ExecutionResult(
                exec_type=ExecType.REJECT, requested_qty=child.quantity,
                reason=f"venue {child.venue_id} is not connected", venue_fault=True, retry_elsewhere=True,
            )
        if order.order_type == OrderType.MARKET:
            planned = child.limit_price or child.price
            rules = adapter.rules(order.symbol)
            band = self._protection_bps
            if rules and rules.max_limit_deviation_bps:
                band = min(band, rules.max_limit_deviation_bps)  # venue's own price-limit rule
            child.limit_price = protected_limit_price(
                order.side, planned, band, rules.price_tick if rules else 0.0
            )
            self._child_band_bps[str(child.id)] = band
        # Every child is an IOC LIMIT: user's limit, or the protected market price.
        return await adapter.place_order(
            symbol=order.symbol,
            side=order.side,
            quantity=child.quantity,
            order_type=OrderType.LIMIT,
            client_order_id=str(child.id),
            limit_price=child.limit_price,
            reference_price=child.price,
        )

    async def _apply_execution(self, order: Order, child: ChildOrder, ex: ExecutionResult) -> None:
        child.exchange_order_id = ex.exchange_order_id
        child.client_order_id = ex.client_order_id
        child.fee, child.fee_asset = ex.fee, ex.fee_asset
        child.quantity = ex.requested_qty  # the venue may have floored it to its own lot size
        filled = ex.exec_type in (ExecType.FILL, ExecType.PARTIAL) and ex.filled_qty > 0

        # An IOC that didn't (fully) fill on a MARKET parent means the venue's
        # book had moved beyond our protection band — say so plainly.
        band = self._child_band_bps.pop(str(child.id), self._protection_bps)
        if order.order_type == OrderType.MARKET and ex.exchange_status == "expired" and child.limit_price:
            unfilled = ex.requested_qty - ex.filled_qty
            ex.reason = (
                f"price protection: {fmt_qty(unfilled)} had no liquidity within "
                f"{band:g} bps of the planned {fmt_price(child.price)} "
                f"(limit {fmt_price(child.limit_price)})"
            )

        if filled:
            await self._orders.update_fill(order.id, child.id, ex.filled_qty, ex.avg_price)
            self._risk.position_tracker.update_position(
                order.symbol, order.side, net_base_change(order, ex), ex.avg_price
            )
        else:
            child.status = OrderStatus.REJECTED
            child.reject_reason = ex.reason
        self._venue_monitor.record_order_result(child.venue_id, filled=filled, venue_fault=ex.venue_fault)

        logger.info(
            "child_order_result",
            order_id=str(order.id),
            child_order_id=str(child.id),
            venue_id=child.venue_id,
            exec_type=ex.exec_type.value,
            filled=ex.filled_qty,
            avg_price=ex.avg_price,
            reason=ex.reason,
        )
        await event_bus.publish(
            "execution_report",
            {
                "order_id": str(order.id),
                "child_order_id": str(child.id),
                "venue_id": child.venue_id,
                "exec_type": ex.exec_type.value,
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": ex.filled_qty if filled else child.quantity,
                "price": ex.avg_price,
                "venue_latency_ms": ex.latency_ms,
                "rejection_reason": ex.reason,
                "exchange_order_id": ex.exchange_order_id,
                "fee": ex.fee,
                "fee_asset": ex.fee_asset,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
