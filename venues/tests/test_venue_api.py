"""
tests/test_venue_api.py

Integration tests using FastAPI TestClient.
Run: cd venues && python -m pytest tests/ -v
"""

from __future__ import annotations

import os
import asyncio

# Set env before any imports that read config
os.environ["VENUE_ID"] = "V1"
os.environ["VENUE_NAME"] = "AlphaExchange"
os.environ["VENUE_PORT"] = "8001"
os.environ["VENUE_PROFILE"] = "alpha_exchange"
os.environ["VENUE_DEGRADED"] = "false"

import pytest
from fastapi.testclient import TestClient

from server.venue_app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestQuote:
    def test_returns_valid_shape(self, client: TestClient) -> None:
        resp = client.get("/quote?symbol=AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["venue_id"] == "V1"
        assert data["symbol"] == "AAPL"
        assert data["bid_price"] > 0
        assert data["ask_price"] > data["bid_price"]
        assert data["bid_size"] > 0
        assert data["ask_size"] > 0
        assert "timestamp" in data

    def test_spread_is_positive(self, client: TestClient) -> None:
        resp = client.get("/quote?symbol=AAPL")
        data = resp.json()
        spread = data["ask_price"] - data["bid_price"]
        assert spread > 0


class TestOrderBook:
    def test_returns_correct_depth(self, client: TestClient) -> None:
        resp = client.get("/orderbook?symbol=AAPL&depth=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["bids"]) == 5
        assert len(data["asks"]) == 5

    def test_bids_descending(self, client: TestClient) -> None:
        resp = client.get("/orderbook?symbol=AAPL&depth=5")
        data = resp.json()
        prices = [b["price"] for b in data["bids"]]
        assert prices == sorted(prices, reverse=True)


class TestExecuteOrder:
    def test_fill_or_reject(self, client: TestClient) -> None:
        resp = client.post("/execute-order", json={
            "child_order_id": "test-001",
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 100,
            "price": 200.00,
            "order_type": "MARKET",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["exec_type"] in ("FILL", "PARTIAL", "REJECT")
        assert data["venue_id"] == "V1"
        assert data["venue_latency_ms"] > 0

    def test_many_orders_fill_rate(self, client: TestClient) -> None:
        """Over 50 orders, most should fill (V1 has 3% reject rate)."""
        fills = 0
        n = 50
        for i in range(n):
            resp = client.post("/execute-order", json={
                "child_order_id": f"rate-{i:04d}",
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 10,
                "price": 200.00,
                "order_type": "MARKET",
            })
            if resp.json()["exec_type"] in ("FILL", "PARTIAL"):
                fills += 1
        fill_pct = fills / n * 100
        assert fill_pct > 80, f"Fill rate {fill_pct:.0f}% is too low for V1"


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["venue_id"] == "V1"
        assert data["name"] == "AlphaExchange"


class TestAdmin:
    def test_degrade_and_recover(self, client: TestClient) -> None:
        resp = client.post("/admin/degrade")
        assert resp.json() == {"degraded": True}

        resp = client.get("/health")
        assert resp.json()["status"] == "degraded"

        resp = client.post("/admin/recover")
        assert resp.json() == {"degraded": False}

        resp = client.get("/health")
        assert resp.json()["status"] == "ok"


class TestMetrics:
    def test_returns_prometheus_format(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        text = resp.text
        assert 'venue_volume_total{venue="V1"}' in text
        assert 'venue_degraded{venue="V1"}' in text
