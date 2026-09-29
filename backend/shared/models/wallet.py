"""Unified wallet: real balances across every connected exchange.

Read-only aggregation. Funds stay custodied by each exchange; ApnaSOR only
reads balances with the API keys in ``.env`` (see PLAN.md §6 for why this
is deliberately NOT called "non-custodial").
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class WalletAsset(BaseModel):
    asset: str
    free: float = 0.0
    used: float = 0.0  # locked in open orders
    total: float = 0.0
    price_usdt: float | None = None  # live mid from our own order books
    value_usdt: float | None = None


class VenueWallet(BaseModel):
    venue_id: str
    name: str
    mode: str
    assets: list[WalletAsset] = []
    total_value_usdt: float = 0.0
    other_assets_count: int = 0  # non-zero balances outside the trading universe
    error: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssetTotal(BaseModel):
    asset: str
    total: float = 0.0
    free: float = 0.0
    value_usdt: float | None = None
    by_venue: dict[str, float] = {}


class WalletSnapshot(BaseModel):
    as_of: datetime | None = None
    mode: str = ""
    total_value_usdt: float = 0.0
    tracked_assets: list[str] = []
    venues: list[VenueWallet] = []
    assets: list[AssetTotal] = []
