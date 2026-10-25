"""Gate: market data only outside real money (its spot testnet holds no test funds)."""

import unittest

from backend.exchanges.gate import GateAdapter


def gate(mode: str, credentials: dict | None = None) -> GateAdapter:
    return GateAdapter(venue_id="V6", name="Gate", mode=mode, symbols=["BTC/USDT"], credentials=credentials)


class SandboxDataOnlyTest(unittest.TestCase):
    def test_in_the_sandbox_gate_is_data_only_even_with_keys(self) -> None:
        a = gate("testnet", credentials={"apiKey": "K", "secret": "S"})
        self.assertFalse(a.execution_available)
        self.assertTrue(a.has_credentials)       # balances stay readable (they are empty, and reconciled)
        self.assertFalse(a.can_trade)            # the router never sends it a child
        self.assertIn("market data only", a.execution_note)
        self.assertIn("no test funds", a.execution_note)

    def test_on_real_money_gate_could_execute(self) -> None:
        a = gate("mainnet", credentials={"apiKey": "K", "secret": "S"})
        self.assertTrue(a.execution_available)
        self.assertTrue(a.has_credentials)
        self.assertIsNone(a.execution_note) if a.can_trade else self.assertIsNotNone(a.execution_note)


if __name__ == "__main__":
    unittest.main()
