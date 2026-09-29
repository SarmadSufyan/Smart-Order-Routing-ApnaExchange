from backend.shared.models.order import OrderSide
from backend.shared.models.risk import PositionInfo
from backend.shared.utils.logger import get_logger

logger = get_logger("position_tracker")


class PositionTracker:
    def __init__(self) -> None:
        self._positions: dict[str, PositionInfo] = {}

    def get_position(self, symbol: str) -> PositionInfo:
        if symbol not in self._positions:
            self._positions[symbol] = PositionInfo(symbol=symbol)
        return self._positions[symbol]

    def get_all_positions(self) -> dict[str, PositionInfo]:
        return dict(self._positions)

    def get_total_notional(self) -> float:
        return sum(abs(p.notional) for p in self._positions.values())

    def update_position(
        self, symbol: str, side: OrderSide, quantity: float, price: float
    ) -> None:
        pos = self.get_position(symbol)
        prev = pos.net_position
        signed_qty = quantity if side == OrderSide.BUY else -quantity
        new = prev + signed_qty

        if abs(new) < 1e-12:
            new, pos.avg_entry_price = 0.0, 0.0  # flat
        elif prev == 0 or (prev > 0) == (signed_qty > 0):
            # Opening or adding: volume-weighted average entry.
            pos.avg_entry_price = (abs(prev) * pos.avg_entry_price + quantity * price) / abs(new)
        elif (prev > 0) != (new > 0):
            pos.avg_entry_price = price  # flipped through zero: new position at this price
        # else: reducing without flipping — entry price is unchanged

        pos.net_position = new
        pos.notional = new * price

        logger.info(
            "position_updated",
            symbol=symbol,
            side=side.value,
            quantity=quantity,
            net_position=pos.net_position,
            notional=pos.notional,
        )

    def reset(self) -> None:
        self._positions.clear()
