"""
scripts/check_binance_testnet.py

One-command sanity check that your Binance *testnet* API keys work.
Reads the keys from the project .env — NEVER hard-code keys in this file.

Usage (from the project root D:\\Sor-Fyp):
    pip install ccxt python-dotenv
    python scripts/check_binance_testnet.py

What it proves:
    1. Public market data reaches the testnet   -> connectivity OK
    2. fetch_balance() succeeds                  -> your keys are valid
    3. You have test funds credited              -> ready to place test orders
"""

import os
import sys

try:
    import ccxt
except ImportError:
    sys.exit("ccxt is not installed. Run:  pip install ccxt python-dotenv")

try:
    from dotenv import load_dotenv

    load_dotenv()  # loads D:\Sor-Fyp\.env when run from the project root
except ImportError:
    print("[warn] python-dotenv not installed; relying on real environment variables.")

API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET")

if not API_KEY or not API_SECRET:
    sys.exit(
        "Missing BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET in .env.\n"
        "Add them (see PLAN.md section 13) and re-run."
    )

exchange = ccxt.binance(
    {
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
    }
)
exchange.set_sandbox_mode(True)  # <- points ccxt at the Binance SPOT testnet

print("Connecting to Binance SPOT TESTNET ...\n")

# 1) Public market data — proves connectivity (no keys needed for this call)
try:
    ticker = exchange.fetch_ticker("BTC/USDT")
    print(f"  [1/2] Market data OK  -  BTC/USDT last = {ticker['last']}")
except Exception as e:  # noqa: BLE001
    sys.exit(f"  [1/2] Could not reach testnet market data: {e}")

# 2) Private call — proves the API key + secret are valid
try:
    balance = exchange.fetch_balance()
except ccxt.AuthenticationError:
    sys.exit(
        "  [2/2] AUTH FAILED - the API key/secret were rejected.\n"
        "        Re-copy them from https://testnet.binance.vision/ (secret is shown once),\n"
        "        make sure there are no stray spaces, and check they are the *testnet* keys."
    )
except Exception as e:  # noqa: BLE001
    sys.exit(f"  [2/2] Balance call failed: {e}")

nonzero = {k: v for k, v in balance["total"].items() if v and v > 0}
print(f"  [2/2] Keys valid  -  fetch_balance() succeeded\n")

print("  Testnet balances (non-zero):")
if nonzero:
    for asset, amt in sorted(nonzero.items()):
        print(f"      {asset:8s} {amt}")
else:
    print("      (none yet — the testnet sometimes takes a minute to credit funds,")
    print("       or use the faucet on the testnet site)")

print("\nOK — Binance testnet is reachable and your keys work. Ready for Phase 1.")
