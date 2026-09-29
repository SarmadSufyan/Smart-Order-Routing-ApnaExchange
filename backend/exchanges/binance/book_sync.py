"""Binance uses the generic update-id-chain procedure (see update_id_sync)."""

from backend.exchanges.update_id_sync import DiffResult, UpdateIdChainSynchronizer

BinanceBookSynchronizer = UpdateIdChainSynchronizer

__all__ = ["BinanceBookSynchronizer", "DiffResult"]
