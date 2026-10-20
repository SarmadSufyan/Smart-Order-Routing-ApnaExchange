"""
scripts/check_exchange_keys.py

Checks the API keys in .env for EVERY enabled exchange (Binance, Bybit, OKX,
Gate). Keys are read through the gateway's own Settings (SecretStr) and are
never printed. Read-only: it reads balances and asks each exchange what the
key may do; it never places an order.

Usage (from the project root D:\\Sor-Fyp):
    python scripts/check_exchange_keys.py                 # the mode in .env (testnet / demo)
    python scripts/check_exchange_keys.py --mode mainnet  # your REAL-money keys, before you switch

For each exchange it reports: keys found? authenticated? balances; and what
the key may do (trade spot? withdraw? limited to an IP?). On real money a key
that can withdraw is refused by the gateway, so this says so first.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt.async_support as ccxt  # noqa: E402

from backend.exchanges.binance.adapter import BinanceAdapter  # noqa: E402
from backend.exchanges.bybit.adapter import BybitAdapter  # noqa: E402
from backend.exchanges.fees import fetch_account_taker_bps  # noqa: E402
from backend.exchanges.gate.adapter import GateAdapter  # noqa: E402
from backend.exchanges.key_permissions import fetch_key_permissions, key_warnings, real_money_problem  # noqa: E402
from backend.exchanges.okx.adapter import OkxAdapter  # noqa: E402
from backend.exchanges.registry import EXCHANGE_CATALOG  # noqa: E402
from backend.gateway.config import Settings, settings  # noqa: E402
from backend.services.arbitrage import parse_fee_schedule  # noqa: E402

SHOW = ["USDT", "BTC", "ETH", "SOL", "BNB", "XRP"]
DEMO_TRADING_VENUES = {"bybit"}  # mirrors CcxtVenueAdapter.test_environment == "demo"
ENV_NAME = {"bybit": "demo", "okx": "demo"}  # what each exchange calls its fake-funds environment
# The gateway's own ccxt options per exchange (spot only). Found 2026-10-08: without Binance's
# "fetchMarkets": ["spot"], this check failed whenever Binance's FUTURES testnet was down,
# although the spot testnet ApnaSOR trades on was fine.
CCXT_OPTIONS = {"binance": BinanceAdapter.ccxt_options, "bybit": BybitAdapter.ccxt_options,
                "okx": OkxAdapter.ccxt_options, "gate": GateAdapter.ccxt_options}
PAD = " " * 19


async def permissions_line(client, exchange_id: str, api_key: str, name: str, mode: str,
                           require_ip_lock: bool = True) -> str:
    """What the exchange says the key may do — the gateway's own real-money rule on mainnet."""
    try:
        perms = await fetch_key_permissions(client, exchange_id, api_key)
    except Exception as exc:  # noqa: BLE001
        if mode == "mainnet":
            return f"{PAD}KEY PERMISSIONS: NO ANSWER ({type(exc).__name__}) - the gateway won't trade here until it gets one"
        return f"{PAD}key permissions: not available on this test environment ({type(exc).__name__})"
    if mode != "mainnet":
        return f"{PAD}key permissions: {perms.summary if perms else 'no endpoint'} (enforced on real money only)"
    problem = real_money_problem(perms, name, require_ip_lock)
    if problem:
        return f"{PAD}REFUSED FOR REAL MONEY - {problem}"
    advice = "".join(f"\n{PAD}  note: {w}" for w in key_warnings(perms))
    return f"{PAD}OK for real money - trade-only key ({perms.summary if perms else ''}){advice}"


async def fee_line(client, symbols: list[str], name: str, public_bps: float | None) -> str:
    """The account's own taker fee (read-only), next to the public rate the gateway would otherwise use."""
    try:
        bps = await fetch_account_taker_bps(client, symbols)
    except Exception as exc:  # noqa: BLE001
        return f"{PAD}account fee: no answer ({type(exc).__name__}) - the public rate is used until there is one"
    if bps is None:
        return f"{PAD}account fee: not reported - the public rate is used"
    public = f", public rate {public_bps / 100:.3f}%" if public_bps is not None else ""
    return f"{PAD}account fee: your {name} taker fee is {bps / 100:.3f}%{public} (routing and bots use yours)"


async def check(exchange_id: str, cfg: Settings) -> str:
    spec = EXCHANGE_CATALOG.get(exchange_id)
    label = f"{spec.name if spec else exchange_id} ({spec.venue_id if spec else '?'})"
    if spec is None or spec.factory is None:
        return f"  {label:16s} skipped - {spec.note if spec else 'unknown exchange'}"
    creds = cfg.exchange_credentials(exchange_id)
    if exchange_id == "coinbase":
        return f"  {label:16s} data-only venue - no keys needed"
    if not creds:
        prefix = f"{exchange_id.upper()}_{cfg.exchange_mode.upper()}_API"
        extra = f" / {prefix}_PASSPHRASE" if exchange_id == "okx" else ""
        return f"  {label:16s} NO KEYS in .env  -> add {prefix}_KEY / {prefix}_SECRET{extra}"
    options = dict(CCXT_OPTIONS.get(exchange_id, {"defaultType": "spot"}))
    client = getattr(ccxt, exchange_id)({**creds, "enableRateLimit": True, "options": options})
    if cfg.exchange_mode == "testnet":
        if exchange_id in DEMO_TRADING_VENUES:
            client.enable_demo_trading(True)  # Bybit: Demo Trading, not Testnet
        else:
            client.set_sandbox_mode(True)  # Binance/Gate testnet, OKX demo header
    try:
        bal = await client.fetch_balance()
        total = bal.get("total", {})
        shown = ", ".join(f"{a} {total[a]:g}" for a in SHOW if total.get(a))
        env = cfg.exchange_mode if cfg.exchange_mode == "mainnet" else ENV_NAME.get(exchange_id, "testnet")
        line = f"  {label:16s} OK - authenticated on {env}. Balances: {shown or 'none of ' + '/'.join(SHOW)}"
        name = spec.name if spec else exchange_id
        line += "\n" + await permissions_line(client, exchange_id, creds["apiKey"], name, cfg.exchange_mode,
                                              cfg.mainnet_require_ip_lock)
        if cfg.exchange_mode == "mainnet":
            public = parse_fee_schedule(cfg.arb_taker_fees_bps).get(spec.venue_id)
            line += "\n" + await fee_line(client, cfg.symbol_list, name, public)
        return line
    except ccxt.AuthenticationError as exc:
        return f"  {label:16s} AUTH FAILED - {str(exc)[:140]}"
    except Exception as exc:  # noqa: BLE001
        return f"  {label:16s} ERROR - {type(exc).__name__}: {str(exc)[:140]}"
    finally:
        await client.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Check every exchange's API keys (read-only).")
    parser.add_argument("--mode", choices=["testnet", "mainnet"], default=settings.exchange_mode,
                        help="which keys to check (default: EXCHANGE_MODE in .env)")
    args = parser.parse_args()
    # The keys of the chosen mode; .env itself is untouched (a mainnet check doesn't switch anything).
    cfg = settings.model_copy(update={"exchange_mode": args.mode})
    print(f"Mode: {cfg.exchange_mode}{'  (REAL-money keys, read-only check)' if args.mode == 'mainnet' else ''}"
          f"   Enabled: {', '.join(cfg.enabled_exchange_list)}\n")
    for line in await asyncio.gather(*(check(e, cfg) for e in cfg.enabled_exchange_list)):
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
