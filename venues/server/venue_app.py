"""
server/venue_app.py

FastAPI app factory. One running instance of this = one venue.

Usage:
  VENUE_ID=V1 VENUE_NAME=AlphaExchange VENUE_PORT=8001 VENUE_PROFILE=alpha_exchange \
  uvicorn server.venue_app:app --port 8001
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import get_settings, get_profile
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


# ─── Engine container (global, set once at startup) ──────────────────────────

@dataclass
class Engines:
    profile: VenueProfile
    price_engine: PriceEngine
    orderbook: OrderBookEngine
    fill_sim: FillSimulator
    latency: LatencyModel


_engines: Engines | None = None


def get_engines() -> Engines:
    """Called by routers to access the venue's engine instances."""
    assert _engines is not None, "Engines not initialized — app not started?"
    return _engines


# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engines

    settings = get_settings()
    profile = get_profile()

    price_engine = PriceEngine(profile)
    orderbook = OrderBookEngine(profile)
    latency = LatencyModel(profile)
    fill_sim = FillSimulator(profile, price_engine, orderbook)

    # If VENUE_DEGRADED=true (V3 on its EC2), start in degraded mode
    if settings.venue_degraded:
        fill_sim.set_degraded(True)
        latency.set_degraded(True)
        logger.warning("venue.starting_degraded", venue_id=profile.venue_id)

    _engines = Engines(
        profile=profile,
        price_engine=price_engine,
        orderbook=orderbook,
        fill_sim=fill_sim,
        latency=latency,
    )

    # Start the GBM price engine background task
    await price_engine.start()

    # Reset health uptime counter
    from server.routers.health import reset_startup_time
    reset_startup_time()

    logger.info(
        "venue.started",
        venue_id=profile.venue_id,
        name=profile.name,
        cloud=profile.narrative_cloud,
        region=profile.narrative_region,
        port=settings.venue_port,
        spread_bps=profile.spread_bps,
        latency_range=profile.base_latency_ms_range,
        reject_rate=f"{profile.reject_rate * 100:.1f}%",
        degraded=settings.venue_degraded,
    )

    yield  # App runs

    await price_engine.stop()
    _engines = None
    logger.info("venue.stopped", venue_id=profile.venue_id)


# ─── App factory ─────────────────────────────────────────────────────────────

settings = get_settings()
profile = get_profile()

app = FastAPI(
    title=f"Venue Simulator — {profile.name} ({profile.venue_id})",
    description=(
        f"Simulated trading venue for DEIRCP FYP POC.\n"
        f"Narrative cloud: {profile.narrative_cloud} | Region: {profile.narrative_region}"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routers
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
        "symbol": settings.symbol,
        "docs": "/docs",
    }
