from abc import ABC, abstractmethod

from backend.shared.models.market_data import NBBO
from backend.shared.models.order import Order
from backend.shared.models.routing import RoutingResult


class RoutingStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def route(
        self,
        order: Order,
        nbbo: NBBO,
        routable_venues: list[str],
    ) -> RoutingResult: ...
