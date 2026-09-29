"""
scripts/check_exchange_keys.py

Checks the API keys in .env for EVERY enabled exchange (Binance, Bybit, OKX,
Gate) against its testnet/demo environment. Keys are read through the
gateway's own Settings (SecretStr) and are never printed.

Usage (from the project root D:\\Sor-Fyp):
    python scripts/check_exchange_keys.py

For each exchange it reports: keys found? authenticated? test balances.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt.async_support as ccxt  # noqa: E402

from backend.exchanges.registry import EXCHANGE_CATALOG  # noqa: E402
from backend.gateway.config import settings  # noqa: E402

SHOW = ["USDT", "BTC", "ETH", "SOL", "BNB", "XRP"]
DEMO_TRADING_VENUES = {"bybit"}  # mirrors CcxtVenueAdapter.test_environment == "demo"
ENV_NAME = {"bybit": "demo", "okx": "demo"}  # what each exchange calls its fake-funds environment


async def check(exchange_id: str) -> str:
    spec = EXCHANGE_CATALOG.get(exchange_id)
    label = f"{spec.name if spec else exchange_id} ({spec.venue_id if spec else '?'})"
    if spec is None or spec.factory is None:
        return f"  {label:16s} skipped - {spec.note if spec else 'unknown exchange'}"
    creds = settings.exchange_credentials(exchange_id)
    if exchange_id == "coinbase":
        return f"  {label:16s} data-only venue - no keys needed"
    if not creds:
        prefix = f"{exchange_id.upper()}_{settings.exchange_mode.upper()}_API"
        extra = f" / {prefix}_PASSPHRASE" if exchange_id == "okx" else ""
        return f"  {label:16s} NO KEYS in .env  -> add {prefix}_KEY / {prefix}_SECRET{extra}"
    client = getattr(ccxt, exchange_id)({**creds, "enableRateLimit": True, "options": {"defaultType": "spot"}})
    if settings.exchange_mode == "testnet":
        if exchange_id in DEMO_TRADING_VENUES:
            client.enable_demo_trading(True)  # Bybit: Demo Trading, not Testnet
        else:
            client.set_sandbox_mode(True)  # Binance/Gate testnet, OKX demo header
    try:
        bal = await client.fetch_balance()
        total = bal.get("total", {})
        shown = ", ".join(f"{a} {total[a]:g}" for a in SHOW if total.get(a))
        env = settings.exchange_mode if settings.exchange_mode == "mainnet" else ENV_NAME.get(exchange_id, "testnet")
        return f"  {label:16s} OK - authenticated on {env}. Balances: {shown or 'none of ' + '/'.join(SHOW)}"
    except ccxt.AuthenticationError as exc:
        return f"  {label:16s} AUTH FAILED - {str(exc)[:140]}"
    except Exception as exc:  # noqa: BLE001
        return f"  {label:16s} ERROR - {type(exc).__name__}: {str(exc)[:140]}"
    finally:
        await client.close()


async def main() -> None:
    print(f"Mode: {settings.exchange_mode}   Enabled: {', '.join(settings.enabled_exchange_list)}\n")
    for line in await asyncio.gather(*(check(e) for e in settings.enabled_exchange_list)):
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
