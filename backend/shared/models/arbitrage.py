"""Arbitrage detector (5c): cross-venue edges, windows and their statistics."""

from datetime import datetime

from pydantic import BaseModel, Field


class ArbEdge(BaseModel):
    """One direction between two venues: buy at one venue's best ask, sell at
    another venue's best bid. Prices are the real market (mainnet books)."""

    symbol: str
    buy_venue: str
    sell_venue: str
    buy_price: float  # best ask on buy_venue
    sell_price: float  # best bid on sell_venue
    gross_bps: float  # (sell - buy) / buy; > 0 means the two books are crossed
    fee_bps: float  # both taker fees, in bps of the buy price
    net_bps: float  # gross minus fees: what is left after paying both exchanges
    size: float  # base units both top-of-book levels show at these prices
    net_usdt: float  # net per unit x size; negative = a loss
    # Filled only in status replies: what OUR accounts could do right now
    # (testnet/demo balances, venue able to execute). None = balance unknown.
    fundable_size: float | None = None
    limited_by: list[str] = []


class ArbWindow(BaseModel):
    """A run of consecutive scans in which one direction stayed crossed."""

    symbol: str
    buy_venue: str
    sell_venue: str
    opened_at: datetime
    closed_at: datetime | None = None  # None = still open
    duration_s: float = 0.0
    scans: int = 1
    peak_gross_bps: float
    peak: ArbEdge  # the scan with the best net edge in this window
    profitable: bool = False  # peak net edge above the threshold (ARB_MIN_NET_BPS)


class ArbSymbolStats(BaseModel):
    """Everything the detector has seen for one symbol since ``since``."""

    symbol: str
    scans: int = 0  # scans with live quotes from at least two venues
    crossed_scans: int = 0  # the best direction was crossed (gross > 0)
    profitable_scans: int = 0  # the best direction was profitable after fees
    sum_best_gross_bps: float = 0.0
    sum_best_net_bps: float = 0.0
    windows: int = 0  # closed windows
    profitable_windows: int = 0
    window_seconds: float = 0.0
    duration_buckets: dict[str, int] = {}
    peak_gross_buckets: dict[str, int] = {}
    max_gross: ArbEdge | None = None
    max_gross_at: datetime | None = None
    max_net: ArbEdge | None = None
    max_net_at: datetime | None = None


class ArbSymbolStatus(BaseModel):
    symbol: str
    venues: list[str] = []  # venues with a live quote right now
    best: ArbEdge | None = None  # the direction with the best net edge right now
    crossed_directions: int = 0  # directions crossed right now
    open_windows: list[ArbWindow] = []
    stats: ArbSymbolStats
    summary: str


class ArbStatus(BaseModel):
    enabled: bool
    running: bool
    scan_interval_ms: int
    min_net_bps: float
    fees_bps: dict[str, float]  # taker fee per venue id
    venue_names: dict[str, str] = {}  # venue id -> exchange name
    stats_since: datetime
    symbols: list[ArbSymbolStatus] = []
    summary: str
    note: str = Field(
        default="Detector only: ApnaSOR never trades these edges. Prices are the real market; "
                "'fundable' uses our sandbox balances.",
    )


class ArbWindowList(BaseModel):
    open: list[ArbWindow] = []
    windows: list[ArbWindow] = []  # closed, newest first
