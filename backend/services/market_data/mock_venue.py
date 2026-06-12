"""Lightweight mock venue server for local backend testing.

Run alongside the gateway to simulate all 5 venues locally.
Each venue generates GBM price paths per symbol and responds to
/health, /quote, and /execute-order requests.

Usage:
    python -m backend.services.market_data.mock_venue
"""

import asyncio
import math
import random
import time
from datetime import UTC, datetime

from fastapi import FastAPI, Query
from pydantic import BaseModel

from backend.shared.models.market_data import DEFAULT_SYMBOLS

VENUE_PROFILES = {
    8001: {"venue_id": "V1", "name": "AlphaExchange", "latency_range": (5, 15), "reject_prob": 0.02, "spread_mult": 1.0},
    8002: {"venue_id": "V2", "name": "BetaLiquidity", "latency_range": (2, 8), "reject_prob": 0.01, "spread_mult": 0.8},
    8003: {"venue_id": "V3", "name": "GammaMarkets", "latency_range": (50, 200), "reject_prob": 0.35, "spread_mult": 2.0},
    8004: {"venue_id": "V4", "name": "DeltaPrime", "latency_range": (1, 5), "reject_prob": 0.01, "spread_mult": 0.9},
    8005: {"venue_id": "V5", "name": "EpsilonPool", "latency_range": (10, 25), "reject_prob": 0.03, "spread_mult": 1.2},
}


class PriceEngine:
    def __init__(self, symbol: str, initial_price: float, volatility: float, drift: float = 0.0001):
        self.symbol = symbol
        self.price = initial_price
        self.volatility = volatility
        self.drift = drift
        self.volume = 0.0

    def step(self, dt: float = 0.1) -> float:
        dw = random.gauss(0, math.sqrt(dt))
        change = self.price * (self.drift * dt + self.volatility * dw)
        self.price = max(0.01, self.price + change)
        self.volume += random.uniform(100, 1000)
        return self.price


class MockVenue:
    def __init__(self, port: int):
        self.port = port
        self.profile = VENUE_PROFILES[port]
        self.engines: dict[str, PriceEngine] = {}
        self._started_at = time.monotonic()

        for sym_cfg in DEFAULT_SYMBOLS:
            noise = random.uniform(-0.02, 0.02)
            self.engines[sym_cfg.symbol] = PriceEngine(
                symbol=sym_cfg.symbol,
                initial_price=sym_cfg.initial_price * (1 + noise),
                volatility=sym_cfg.volatility,
            )

    def get_quote(self, symbol: str) -> dict:
        engine = self.engines.get(symbol)
        if not engine:
            return {}

        engine.step()
        mid = engine.price
        spread_pct = 0.0002 * self.profile["spread_mult"]
        half_spread = mid * spread_pct

        bid = round(mid - half_spread, 4)
        ask = round(mid + half_spread, 4)
        bid_size = round(random.uniform(100, 2000), 0)
        ask_size = round(random.uniform(100, 2000), 0)

        return {
            "venue_id": self.profile["venue_id"],
            "symbol": symbol,
            "bid_price": bid,
            "ask_price": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "last_price": round(mid, 4),
            "volume": round(engine.volume, 0),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def simulate_latency(self):
        lo, hi = self.profile["latency_range"]
        delay_ms = random.uniform(lo, hi)
        await asyncio.sleep(delay_ms / 1000)

    def should_reject(self) -> bool:
        return random.random() < self.profile["reject_prob"]


def create_venue_app(port: int) -> FastAPI:
    venue = MockVenue(port)
    app = FastAPI(title=f"Mock {venue.profile['name']}")

    @app.get("/health")
    async def health():
        await venue.simulate_latency()
        return {
            "venue_id": venue.profile["venue_id"],
            "name": venue.profile["name"],
            "status": "ok",
            "uptime_seconds": round(time.monotonic() - venue._started_at, 1),
        }

    @app.get("/quote")
    async def quote(symbol: str = Query(...)):
        await venue.simulate_latency()
        data = venue.get_quote(symbol.upper())
        if not data:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Unknown symbol {symbol}")
        return data

    @app.post("/execute-order")
    async def execute_order(body: dict):
        await venue.simulate_latency()

        if venue.should_reject():
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=400,
                content={
                    "status": "REJECTED",
                    "reason": "Order rejected by venue matching engine",
                    "order_id": body.get("order_id", ""),
                },
            )

        symbol = body.get("symbol", "AAPL")
        engine = venue.engines.get(symbol)
        price = body.get("price", 0)
        if engine:
            slippage = random.uniform(-0.001, 0.001)
            price = round(engine.price * (1 + slippage), 4)

        return {
            "status": "FILLED",
            "order_id": body.get("order_id", ""),
            "fill_price": price,
            "filled_quantity": body.get("quantity", 0),
            "venue_id": venue.profile["venue_id"],
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return app


async def run_all_venues():
    import uvicorn

    servers = []
    for port in VENUE_PROFILES:
        app = create_venue_app(port)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        servers.append(server)

    print(f"Starting {len(servers)} mock venue simulators...")
    for port, profile in VENUE_PROFILES.items():
        print(f"  {profile['venue_id']} ({profile['name']}): http://127.0.0.1:{port}")

    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(run_all_venues())
