"""A small stand-in exchange for the automation tests.

``Exchange.submit`` plays OrderEntry + router + venue: the fund gate first (as the real
OrderEntry does), then an IOC fill at the best price — only within the limit — with a fee
in the coin on buys and in USDT on sells (like Bybit's demo), moving the wallet's balances.
"""

from datetime import UTC, datetime

from backend.exchanges.base import SymbolRules
from backend.services.order_state.entry import FundReservedError
from backend.shared.exceptions import RiskCheckFailedError
from backend.shared.models.market_data import NBBO, VenueQuote
from backend.shared.models.order import ChildOrder, Order, OrderSide, OrderStatus, OrderType
from backend.shared.models.risk import RiskLimits
from backend.shared.models.wallet import AssetTotal, WalletSnapshot

VENUE = "V4"


class Market:
    def __init__(self, mids: dict[str, float], spread: float = 0.0002) -> None:
        self.mid = dict(mids)
        self.spread = spread

    def bid(self, symbol: str) -> float:
        return self.mid[symbol] * (1 - self.spread / 2)

    def ask(self, symbol: str) -> float:
        return self.mid[symbol] * (1 + self.spread / 2)

    async def compute_nbbo(self, symbol: str) -> NBBO | None:
        if symbol not in self.mid:
            return None
        b, a = self.bid(symbol), self.ask(symbol)
        q = VenueQuote(venue_id=VENUE, symbol=symbol, bid_price=b, ask_price=a, bid_size=10, ask_size=10,
                       last_price=b, volume=0)
        return NBBO(symbol=symbol, best_bid=b, best_ask=a, best_bid_venue=VENUE, best_ask_venue=VENUE,
                    best_bid_size=10, best_ask_size=10, spread=a - b, venue_quotes={VENUE: q})


class Router:
    def executable_venues(self, symbol=None):
        return [VENUE]


class Adapter:
    def rules(self, symbol: str) -> SymbolRules:
        base, quote = symbol.split("/")
        return SymbolRules(symbol=symbol, venue_symbol=base + quote, base=base, quote=quote, amount_step=1e-6,
                           min_amount=1e-6, price_tick=0.01, min_notional=5.0)


class Registry:
    def get(self, venue_id: str):
        return Adapter()


class Wallet:
    def __init__(self, market: Market, free: dict[str, float]) -> None:
        self.market = market
        self.free = dict(free)

    def get_snapshot(self) -> WalletSnapshot:
        return WalletSnapshot(as_of=datetime.now(UTC), mode="testnet",
                              assets=[AssetTotal(asset=a, total=q, free=q) for a, q in self.free.items()])

    def available(self, venue_id: str, asset: str) -> float:
        return self.free.get(asset, 0.0)

    def price_usdt(self, asset: str, venue_id: str) -> float | None:
        if asset == "USDT":
            return 1.0
        return self.market.mid.get(f"{asset}/USDT")


class KillSwitch:
    is_active = False


class Risk:
    def __init__(self) -> None:
        self.kill_switch = KillSwitch()
        self.limits = RiskLimits()


class Exchange:
    def __init__(self, market: Market, wallet: Wallet, guard=None, fee_rate: float = 0.001) -> None:
        self.market = market
        self.wallet = wallet
        self.guard = guard
        self.fee_rate = fee_rate
        self.refuse: str | None = None  # refuse every order before any venue (like a risk rejection)
        self.no_fill = False  # the IOC expires unfilled
        self.orders: list[Order] = []

    async def submit(self, order_in, submitted_by, parent_id=None):
        order = Order(symbol=order_in.symbol, side=order_in.side, quantity=order_in.quantity,
                      order_type=order_in.order_type, limit_price=order_in.limit_price,
                      submitted_by=submitted_by, parent_id=parent_id)
        self.orders.append(order)
        if self.guard is not None and (reason := await self.guard(order, parent_id)):
            order.status, order.rejection_reason = OrderStatus.REJECTED, reason
            return order, FundReservedError(reason)
        if self.refuse:
            order.status, order.rejection_reason = OrderStatus.REJECTED, self.refuse
            return order, RiskCheckFailedError(self.refuse)
        base, quote = order.symbol.split("/")
        buy = order.side == OrderSide.BUY
        price = self.market.ask(order.symbol) if buy else self.market.bid(order.symbol)
        child = ChildOrder(parent_order_id=order.id, venue_id=VENUE, quantity=order.quantity, price=price,
                           sent_at=datetime.now(UTC))
        order.child_orders = [child]
        within = (order.order_type == OrderType.MARKET or (buy and price <= order.limit_price)
                  or (not buy and price >= order.limit_price))
        if self.no_fill or not within:
            child.status, child.reject_reason = OrderStatus.REJECTED, "IOC expired: no liquidity at the limit"
            order.status = OrderStatus.REJECTED
            return order, None
        q = order.quantity
        child.filled_quantity, child.fill_price, child.filled_at = q, price, datetime.now(UTC)
        if buy:
            child.fee, child.fee_asset = q * self.fee_rate, base
            self.wallet.free[quote] = self.wallet.free.get(quote, 0.0) - q * price
            self.wallet.free[base] = self.wallet.free.get(base, 0.0) + q - child.fee
        else:
            child.fee, child.fee_asset = q * price * self.fee_rate, quote
            self.wallet.free[base] = self.wallet.free.get(base, 0.0) - q
            self.wallet.free[quote] = self.wallet.free.get(quote, 0.0) + q * price - child.fee
        child.status = OrderStatus.FILLED
        order.status, order.filled_quantity, order.avg_fill_price = OrderStatus.FILLED, q, price
        return order, None


class Research:
    """Forecasts and trade plans the test controls."""

    def __init__(self, market: Market) -> None:
        self.market = market
        self.p_up = 0.6
        self.edge = False
        self.expectancy_r = 0.1
        self.samples = 500

    async def forecast_horizon(self, symbol: str, tf: str) -> dict:
        return {"p_up": self.p_up, "edge": self.edge, "call": "No proven edge — treat this as about 50/50",
                "typical_move_pct": 0.5, "signals": [{"text": "RSI 40: neutral"}]}

    async def trade_plan(self, symbol, tf, equity, risk_pct, k_atr, rr, max_bars) -> dict:
        entry = self.market.ask(symbol)
        atr = entry * 0.01
        return {"plan": {"entry": entry, "stop": entry - k_atr * atr, "target": entry + rr * k_atr * atr},
                "history": {"samples": self.samples, "win_rate": 0.4, "break_even_win_rate": 0.36,
                            "expectancy_r": self.expectancy_r},
                "cost_bps_round_trip": 24.0}

    async def chart(self, symbol, tf, bars) -> dict:
        return {"close": [self.market.mid[symbol]] * 60, "rsi": [50.0] * 60}

    async def orderbook(self, symbol) -> dict:
        return {"available": False}
