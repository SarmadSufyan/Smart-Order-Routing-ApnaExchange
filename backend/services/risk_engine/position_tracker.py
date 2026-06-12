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
        signed_qty = quantity if side == OrderSide.BUY else -quantity
        pos.net_position += signed_qty
        pos.notional = pos.net_position * price
        pos.avg_entry_price = price

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
