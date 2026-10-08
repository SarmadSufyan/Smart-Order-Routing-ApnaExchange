"""The automation fund: money set aside for bots, kept apart from manual trading.

    wallet ──deposit──> fund (unassigned) ──assign──> bot pocket ──orders──> coins / cash
            <─withdraw─                   <─release──

Two rules, enforced at the ONE order entry (``check_order``):
    * a bot order may only use its own pocket (cash for a buy, its coins for a sell);
    * a manual order (button, algo, copilot) may only use what the fund doesn't hold:
      free on the exchanges minus the fund's cash and the bots' coins.

Testnet fees are not real-market fees (Binance's spot testnet charges nothing; found
2026-10-08), so every pocket also keeps its numbers at the real-market taker fee
(``ARB_TAKER_FEES_BPS``), whichever is higher: practice results that would hold with
real money.

Honest limit: on one exchange account the money is pooled. This is ApnaSOR's own
book and gate, not a separate account — Phase D can give automation its own exchange
sub-account and keys.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.shared.events.event_bus import audit, event_bus
from backend.shared.exceptions import DomainError
from backend.shared.models.automation import (
    FUND_HISTORY_KEPT,
    FundEvent,
    FundState,
    FundView,
    Pocket,
)
from backend.shared.models.market_data import split_symbol
from backend.shared.models.order import Order, OrderSide, OrderType
from backend.shared.utils.logger import get_logger

logger = get_logger("automation_fund")

STORE_KEY = "automation_fund"
QUOTE = "USDT"
CENT = 0.01  # differences below a cent are rounding, not a breach
DEFAULT_MARKET_FEE_BPS = 10.0

SEPARATION_NOTE = (
    "Bots trade only from their own pocket of this fund, and manual orders can't use what the fund holds. "
    "On one exchange account the money is physically pooled: this separation is ApnaSOR's own book and order "
    "gate. For real money, a separate exchange sub-account with its own keys makes it physical too.")


class FundError(DomainError):
    def __init__(self, message: str):
        super().__init__(message, "FUND_REJECTED")


# ── pure accounting ─────────────────────────────────────────────────
@dataclass
class FillSummary:
    """What one order did, from its children's fills."""

    filled: float = 0.0  # base asset that traded (gross)
    base_change: float = 0.0  # base asset that moved in (+) or out (-), net of a fee taken in it
    quote_change: float = 0.0  # USDT in (+) or out (-), net of USDT fees and third-coin fees
    notional: float = 0.0  # filled x price
    fee_usdt: float = 0.0  # fees as charged, valued in USDT
    market_fee_usdt: float = 0.0  # per child: max(charged, real-market rate x notional)
    venues: list[str] = field(default_factory=list)

    @property
    def avg_price(self) -> float:
        return self.notional / self.filled if self.filled > 0 else 0.0


PriceFn = Callable[[str, str], float | None]  # (asset, venue) -> USDT


def summarize_order(order: Order, price_usdt: PriceFn, market_fee_bps: dict[str, float],
                    default_fee_bps: float = DEFAULT_MARKET_FEE_BPS) -> FillSummary:
    """Add up an order's fills.

    A fee in the coin bought means fewer coins (Bybit takes the buy fee in the coin);
    a fee in USDT means less cash; a fee in a third coin (BNB) is charged to the pocket's
    cash at its value, because a pocket holds only USDT and the coins it trades.
    """
    base, quote = split_symbol(order.symbol)
    s = FillSummary()
    for c in order.child_orders:
        if c.filled_quantity <= 0:
            continue
        q, px = c.filled_quantity, c.fill_price
        fee = c.fee or 0.0
        fee_base = fee if c.fee_asset == base else 0.0
        fee_quote = fee if c.fee_asset == quote else 0.0
        fee_other = 0.0
        if fee and c.fee_asset not in (base, quote, None):
            fee_other = fee * (price_usdt(c.fee_asset, c.venue_id) or 0.0)
        charged = fee_quote + fee_base * px + fee_other
        s.filled += q
        s.notional += q * px
        s.fee_usdt += charged
        s.market_fee_usdt += max(charged, q * px * market_fee_bps.get(c.venue_id, default_fee_bps) / 1e4)
        if order.side == OrderSide.BUY:
            s.base_change += q - fee_base
            s.quote_change -= q * px + fee_quote + fee_other
        else:
            s.base_change -= q + fee_base
            s.quote_change += q * px - fee_quote - fee_other
        if c.venue_id not in s.venues:
            s.venues.append(c.venue_id)
    return s


def reserved_assets(state: FundState) -> dict[str, float]:
    """What the fund holds: its unassigned cash, every pocket's cash, every bot's coins."""
    out: dict[str, float] = {QUOTE: state.unassigned_usdt + sum(p.cash_usdt for p in state.pockets.values())}
    for pocket in state.pockets.values():
        for asset, qty in pocket.coins.items():
            if qty > 0:
                out[asset] = out.get(asset, 0.0) + qty
    return out


def pocket_value(pocket: Pocket, mark: Callable[[str], float | None]) -> tuple[float | None, float | None]:
    """(value now, unrealized profit) — None while a held coin has no live price."""
    value, unrealized = pocket.cash_usdt, 0.0
    for asset, qty in pocket.coins.items():
        if abs(qty) < 1e-15:
            continue
        m = mark(asset)
        if m is None:
            return None, None
        value += qty * m
        unrealized += qty * m - pocket.cost_usdt.get(asset, 0.0)
    return value, unrealized


# ── the service ─────────────────────────────────────────────────────
class AutomationFund:
    def __init__(self, wallet, aggregator, mode: str, store=None, protection_bps: float = 50.0) -> None:
        self._wallet = wallet
        self._aggregator = aggregator
        self._store = store
        self._protection = protection_bps / 1e4
        self.state = FundState(mode=mode)

    async def load(self) -> None:
        if self._store is None:
            return
        if saved := await self._store.kv_get(STORE_KEY):
            self.state = FundState.model_validate(saved)
            logger.info("automation_fund_restored", allocated=self.state.allocated_usdt,
                        pockets=sorted(self.state.pockets))

    # ── reads ───────────────────────────────────────────────────────
    def pocket(self, bot_id: str) -> Pocket | None:
        return self.state.pockets.get(bot_id)

    def reserved(self) -> dict[str, float]:
        return reserved_assets(self.state)

    def wallet_free(self) -> dict[str, float] | None:
        snap = self._wallet.get_snapshot()
        if snap.as_of is None:
            return None
        return {a.asset: a.free for a in snap.assets}

    def manual_free(self) -> dict[str, float] | None:
        free = self.wallet_free()
        if free is None:
            return None
        reserved = self.reserved()
        return {a: free.get(a, 0.0) - reserved.get(a, 0.0) for a in sorted(set(free) | set(reserved))}

    def mark(self, asset: str) -> float | None:
        return 1.0 if asset == QUOTE else self._wallet.price_usdt(asset, "")

    def view(self) -> FundView:
        reserved = self.reserved()
        free = self.wallet_free() or {}
        coins: dict[str, float] = {}
        for p in self.state.pockets.values():
            for asset, qty in p.coins.items():
                if qty > 0:
                    coins[asset] = coins.get(asset, 0.0) + qty
        coins_value: float | None = 0.0
        for asset, qty in coins.items():
            m = self.mark(asset)
            coins_value = None if m is None or coins_value is None else coins_value + qty * m
        cash = sum(p.cash_usdt for p in self.state.pockets.values())
        value = None if coins_value is None else self.state.unassigned_usdt + cash + coins_value
        realized_gap = sum(p.realized_usdt - p.realized_market_usdt for p in self.state.pockets.values())
        unrealized_gap = sum(sum(p.market_cost_usdt.values()) - sum(p.cost_usdt.values())
                             for p in self.state.pockets.values())
        pnl = None if value is None else value - self.state.allocated_usdt
        warnings = []
        for asset, qty in reserved.items():
            if free and qty > free.get(asset, 0.0) + 1e-9 and (qty - free.get(asset, 0.0)) * (self.mark(asset) or 0) > CENT:
                warnings.append(
                    f"The fund holds {qty:,.8g} {asset} but the exchanges show only {free.get(asset, 0.0):,.8g} free "
                    "(a teammate on the shared testnet keys, a testnet reset, or an order in flight): bots trading "
                    f"{asset} can be refused by the exchange")
        return FundView(
            mode=self.state.mode, allocated_usdt=round(self.state.allocated_usdt, 8),
            unassigned_usdt=round(self.state.unassigned_usdt, 8), bots_cash_usdt=round(cash, 8),
            coins={a: round(q, 12) for a, q in coins.items()},
            coins_value_usdt=None if coins_value is None else round(coins_value, 8),
            value_usdt=None if value is None else round(value, 8),
            pnl_usdt=None if pnl is None else round(pnl, 8),
            pnl_market_usdt=None if pnl is None else round(pnl - realized_gap - unrealized_gap, 8),
            reserved={a: round(q, 12) for a, q in reserved.items()},
            wallet_free={a: round(q, 12) for a, q in free.items()},
            manual_free={a: round(free.get(a, 0.0) - reserved.get(a, 0.0), 12)
                         for a in sorted(set(free) | set(reserved))},
            pockets=self.state.pockets, history=list(reversed(self.state.history[-30:])),
            warnings=warnings, note=SEPARATION_NOTE,
        )

    # ── moving money ────────────────────────────────────────────────
    async def deposit(self, amount: float, actor: str) -> FundState:
        free = self.manual_free()
        if free is None:
            raise FundError("Exchange balances haven't been read yet — try again in a few seconds")
        if amount > free.get(QUOTE, 0.0) + CENT:
            raise FundError(f"Only ${free.get(QUOTE, 0.0):,.2f} USDT is free for this (wallet free minus what the "
                            f"fund already holds); asked for ${amount:,.2f}")
        self.state.allocated_usdt += amount
        self.state.unassigned_usdt += amount
        await self._record(actor, "deposit", amount, None, f"${amount:,.2f} moved from the wallet into the automation fund")
        return self.state

    async def withdraw(self, amount: float, actor: str) -> FundState:
        if amount > self.state.unassigned_usdt + CENT:
            raise FundError(f"Only ${self.state.unassigned_usdt:,.2f} is unassigned in the fund (money a bot holds "
                            "comes back when that bot is stopped)")
        amount = min(amount, self.state.unassigned_usdt)
        self.state.allocated_usdt -= amount
        self.state.unassigned_usdt -= amount
        await self._record(actor, "withdraw", amount, None, f"${amount:,.2f} moved back to the wallet")
        return self.state

    async def assign(self, bot_id: str, amount: float, actor: str) -> Pocket:
        if amount > self.state.unassigned_usdt + CENT:
            raise FundError(
                f"The automation fund has ${self.state.unassigned_usdt:,.2f} unassigned; this bot needs "
                f"${amount:,.2f}. Move money from the wallet into the fund first (Bots & Lab → Automation fund)")
        amount = min(amount, self.state.unassigned_usdt)
        pocket = self.state.pockets.setdefault(bot_id, Pocket())
        pocket.budget_usdt += amount
        pocket.cash_usdt += amount
        self.state.unassigned_usdt -= amount
        await self._record(actor, "assign", amount, bot_id, f"${amount:,.2f} given to {bot_id}")
        return pocket

    async def release(self, bot_id: str, actor: str) -> float:
        """Give a bot's cash back to the fund. Its coins stay in its pocket until sold or released."""
        pocket = self.state.pockets.get(bot_id)
        if pocket is None or pocket.cash_usdt <= 0:
            return 0.0
        amount = pocket.cash_usdt
        pocket.cash_usdt = 0.0
        pocket.budget_usdt -= amount
        self.state.unassigned_usdt += amount
        held = ", ".join(f"{q:,.8g} {a}" for a, q in pocket.coins.items() if q > 0)
        await self._record(actor, "release", amount, bot_id,
                           f"${amount:,.2f} back from {bot_id}" + (f"; it still holds {held}" if held else ""))
        return amount

    async def release_coins(self, bot_id: str, asset: str, how: str, actor: str) -> float:
        """Take a bot's coins off its book without a sale.

        ``to_manual``: they stay in the wallet for manual trading, valued at the live price
        (that value leaves the fund). ``lost``: they are gone (a testnet balance reset).
        Returns the profit (or loss) this books.
        """
        pocket = self.state.pockets.get(bot_id)
        qty = pocket.coins.get(asset, 0.0) if pocket else 0.0
        if pocket is None or qty <= 0:
            return 0.0
        cost = pocket.cost_usdt.get(asset, 0.0)
        market_cost = pocket.market_cost_usdt.get(asset, cost)
        if how == "to_manual":
            mark = self.mark(asset)
            if mark is None:
                raise FundError(f"No live price for {asset} right now — try again in a moment")
            value = qty * mark
            self.state.allocated_usdt -= value
            note = f"{qty:,.8g} {asset} of {bot_id} moved to manual trading at {mark:,.6g} (${value:,.2f})"
        else:
            value = 0.0
            note = f"{qty:,.8g} {asset} of {bot_id} written off as lost (cost ${cost:,.2f})"
        pocket.coins[asset] = 0.0
        pocket.cost_usdt[asset] = 0.0
        pocket.market_cost_usdt[asset] = 0.0
        pocket.realized_usdt += value - cost
        pocket.realized_market_usdt += value - market_cost
        await self._record(actor, "coins_to_manual" if how == "to_manual" else "write_off", value, bot_id, note)
        return value - cost

    # ── what an order did ───────────────────────────────────────────
    def apply_buy(self, bot_id: str, asset: str, fills: FillSummary) -> None:
        pocket = self.state.pockets.setdefault(bot_id, Pocket())
        pocket.cash_usdt += fills.quote_change
        pocket.coins[asset] = pocket.coins.get(asset, 0.0) + fills.base_change
        spent = -fills.quote_change
        pocket.cost_usdt[asset] = pocket.cost_usdt.get(asset, 0.0) + spent
        pocket.market_cost_usdt[asset] = (pocket.market_cost_usdt.get(asset, 0.0) + spent
                                          + (fills.market_fee_usdt - fills.fee_usdt))
        pocket.fees_usdt += fills.fee_usdt
        pocket.market_fees_usdt += fills.market_fee_usdt
        self._persist()

    def apply_sell(self, bot_id: str, asset: str, fills: FillSummary, cost: float, market_cost: float) -> None:
        """``cost`` / ``market_cost``: what the coins sold had cost (from the lots they came from)."""
        pocket = self.state.pockets.setdefault(bot_id, Pocket())
        pocket.cash_usdt += fills.quote_change
        pocket.coins[asset] = pocket.coins.get(asset, 0.0) + fills.base_change
        pocket.cost_usdt[asset] = max(pocket.cost_usdt.get(asset, 0.0) - cost, 0.0)
        pocket.market_cost_usdt[asset] = max(pocket.market_cost_usdt.get(asset, 0.0) - market_cost, 0.0)
        pocket.realized_usdt += fills.quote_change - cost
        pocket.realized_market_usdt += fills.quote_change - (fills.market_fee_usdt - fills.fee_usdt) - market_cost
        pocket.fees_usdt += fills.fee_usdt
        pocket.market_fees_usdt += fills.market_fee_usdt
        if pocket.coins[asset] < 0 and abs(pocket.coins[asset]) < 1e-12:
            pocket.coins[asset] = 0.0
        self._persist()

    # ── the order gate ──────────────────────────────────────────────
    async def check_order(self, order: Order, parent_id: str | None) -> str | None:
        """Why this order may not be sent, or None. Called by OrderEntry before routing."""
        base, quote = split_symbol(order.symbol)
        pocket = self.state.pockets.get(parent_id) if parent_id else None
        is_bot = parent_id is not None and pocket is not None
        if not is_bot and not any(q > 0 for q in self.reserved().values()):
            return None  # nothing set aside: manual trading is exactly as before
        if order.side == OrderSide.BUY:
            price = order.limit_price if order.order_type == OrderType.LIMIT and order.limit_price else None
            if price is None:
                nbbo = await self._aggregator.compute_nbbo(order.symbol)
                if not nbbo or not nbbo.best_ask:
                    return None  # the router rejects it without market data anyway
                price = nbbo.best_ask * (1 + self._protection)
            need = order.quantity * price
            if is_bot:
                if need > pocket.cash_usdt + CENT:
                    return (f"{parent_id} has ${pocket.cash_usdt:,.2f} left in its pocket of the automation fund; "
                            f"this buy needs up to ${need:,.2f}")
                return None
            free = self.manual_free()
            if free is not None and need > free.get(quote, 0.0) + CENT:
                held = self.reserved().get(quote, 0.0)
                return (f"This buy needs up to ${need:,.2f} {quote} but only ${max(free.get(quote, 0.0), 0):,.2f} is free "
                        f"for manual trading: ${held:,.2f} belongs to the automation fund. Use a smaller order or move "
                        "money out of the fund (Bots & Lab)")
            return None
        if is_bot:
            held = pocket.coins.get(base, 0.0)
            if order.quantity > held * (1 + 1e-9) + 1e-12:
                return f"{parent_id} holds {held:,.8g} {base}; it can't sell {order.quantity:,.8g}"
            return None
        free = self.manual_free()
        if free is not None and order.quantity > free.get(base, 0.0) * (1 + 1e-9) + 1e-12:
            held = self.reserved().get(base, 0.0)
            return (f"Only {max(free.get(base, 0.0), 0):,.8g} {base} is free for manual trading: {held:,.8g} {base} "
                    "belongs to bots of the automation fund (sell it from Bots & Lab, or release it to manual)")
        return None

    # ── internals ───────────────────────────────────────────────────
    async def _record(self, actor: str, action: str, amount: float, bot_id: str | None, note: str) -> None:
        self.state.history.append(FundEvent(actor=actor, action=action, amount_usdt=round(amount, 8),
                                            bot_id=bot_id, note=note))
        self.state.history = self.state.history[-FUND_HISTORY_KEPT:]
        self._persist()
        await audit(f"fund_{action}", note, actor=actor, order_id=bot_id,
                    payload={"amount_usdt": amount, "allocated_usdt": self.state.allocated_usdt,
                             "unassigned_usdt": self.state.unassigned_usdt})
        await event_bus.publish("bot_update", {"fund": True, "action": action})
        logger.info("automation_fund_changed", action=action, amount=amount, bot_id=bot_id, actor=actor)

    def _persist(self) -> None:
        self.state.updated_at = datetime.now(UTC)
        if self._store is not None:
            self._store.kv_set(STORE_KEY, self.state.model_dump(mode="json"))
