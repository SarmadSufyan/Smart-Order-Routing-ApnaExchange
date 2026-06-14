"""
server/venue_app.py

FastAPI app factory. One running instance = one venue, serving all symbols.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import SYMBOLS, get_settings, get_profile
from server.engine.price_engine import PriceEngine
from server.engine.orderbook_engine import OrderBookEngine
from server.engine.fill_simulator import FillSimulator
from server.engine.latency_model import LatencyModel
from server.profiles.base import VenueProfile
from server.routers import quote, orderbook, execute, health, admin, metrics

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


@dataclass
class SymbolEngines:
    price_engine: PriceEngine
    orderbook: OrderBookEngine
    fill_sim: FillSimulator


@dataclass
class Engines:
    profile: VenueProfile
    latency: LatencyModel
    symbols: dict[str, SymbolEngines] = field(default_factory=dict)


_engines: Engines | None = None


def get_engines() -> Engines:
    assert _engines is not None, "Engines not initialized — app not started?"
    return _engines


def get_symbol_engines(symbol: str) -> SymbolEngines:
    engines = get_engines()
    if symbol not in engines.symbols:
        sym_list = list(engines.symbols.keys())
        raise ValueError(f"Unknown symbol {symbol}. Available: {sym_list}")
    return engines.symbols[symbol]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engines

    settings = get_settings()
    profile = get_profile()
    latency = LatencyModel(profile)

    if settings.venue_degraded:
        latency.set_degraded(True)
        logger.warning("venue.starting_degraded", venue_id=profile.venue_id)

    symbol_engines: dict[str, SymbolEngines] = {}

    for symbol, sym_cfg in SYMBOLS.items():
        pe = PriceEngine(
            profile,
            initial_price=sym_cfg["initial_price"],
            volatility_mult=sym_cfg["volatility_mult"],
        )
        ob = OrderBookEngine(profile)
        fs = FillSimulator(profile, pe, ob)

        if settings.venue_degraded:
            fs.set_degraded(True)

        symbol_engines[symbol] = SymbolEngines(
            price_engine=pe,
            orderbook=ob,
            fill_sim=fs,
        )

    _engines = Engines(
        profile=profile,
        latency=latency,
        symbols=symbol_engines,
    )

    for symbol, se in symbol_engines.items():
        await se.price_engine.start()

    from server.routers.health import reset_startup_time
    reset_startup_time()

    logger.info(
        "venue.started",
        venue_id=profile.venue_id,
        name=profile.name,
        symbols=list(SYMBOLS.keys()),
        port=settings.venue_port,
        degraded=settings.venue_degraded,
    )

    yield

    for se in symbol_engines.values():
        await se.price_engine.stop()
    _engines = None
    logger.info("venue.stopped", venue_id=profile.venue_id)


settings = get_settings()
profile = get_profile()

app = FastAPI(
    title=f"Venue Simulator — {profile.name} ({profile.venue_id})",
    description=(
        f"Multi-symbol venue simulator for DEIRCP FYP POC.\n"
        f"Symbols: {', '.join(SYMBOLS.keys())}"
    ),
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(quote.router)
app.include_router(orderbook.router)
app.include_router(execute.router)
app.include_router(health.router)
app.include_router(admin.router)
app.include_router(metrics.router)


@app.get("/", summary="Venue info")
async def root() -> dict:
    return {
        "venue_id": profile.venue_id,
        "name": profile.name,
        "narrative_cloud": profile.narrative_cloud,
        "symbols": list(SYMBOLS.keys()),
        "docs": "/docs",
    }
