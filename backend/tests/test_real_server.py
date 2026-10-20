"""The real-money server kit's Python parts: the settings it sends, the key writer, and the promotion gate."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.gateway.config import PROJECT_ROOT, Settings

KIT = PROJECT_ROOT / "deploy" / "real-server"
spec = importlib.util.spec_from_file_location("real_deploy", KIT / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

LOGINS = "".join(f"{k}=scrypt:16384:8:5:aa:{k[-5:].lower()}\n" for k in deploy.LOGIN_KEYS)
LAPTOP = ("BINANCE_TESTNET_API_KEY=bk\nBINANCE_TESTNET_API_SECRET=bs\nBYBIT_TESTNET_API_KEY=yk\n"
          "BYBIT_TESTNET_API_SECRET=ys\nBINANCE_MAINNET_API_KEY=REAL\nBINANCE_MAINNET_API_SECRET=REAL\n"
          "GEMINI_API_KEY=g\nRAFT_SHARED_SECRET=r\nMAINNET_MAX_ORDER_USDT=20\nSYMBOLS=BTC/USDT,ETH/USDT\n")


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "out").mkdir()
        (self.dir / ".env").write_text(LAPTOP, encoding="utf-8")
        for p in (mock.patch.object(deploy, "ROOT", self.dir), mock.patch.object(deploy, "LOCAL", self.dir / "out"),
                  mock.patch.object(deploy, "LOGINS", self.dir / "out" / "logins.env")):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def logins(self) -> None:
        (self.dir / "out" / "logins.env").write_text(LOGINS, encoding="utf-8")

    def test_no_password_no_settings(self) -> None:  # the real-money server has no demo login
        with self.assertRaises(SystemExit) as stopped:
            deploy.build_env("rehearsal", ["binance"])
        self.assertIn("deploy.py passwords", str(stopped.exception))

    def test_settings_never_carry_a_key_and_follow_the_decisions(self) -> None:
        self.logins()
        for stage, mode in (("rehearsal", "testnet"), ("live", "mainnet")):
            with self.subTest(stage=stage):
                path, summary = deploy.build_env(stage, ["binance", "bybit"])
                text = path.read_text(encoding="utf-8")
                for absent in ("REAL", "_API_KEY", "_API_SECRET", "GEMINI", "RAFT_SHARED_SECRET"):
                    self.assertNotIn(absent, text)
                with mock.patch.dict(os.environ, {}, clear=False):
                    s = Settings(_env_file=str(path))
                self.assertEqual((s.exchange_mode, s.allow_mainnet), (mode, stage == "live"))
                self.assertEqual(s.llm_provider, "off")  # decision A5
                self.assertFalse(s.mainnet_cycle_bots)  # decision A6
                self.assertTrue(s.mainnet_require_ip_lock)
                self.assertFalse(s.lab_auto_resume)
                self.assertEqual(s.mainnet_max_order_usdt, 20.0)  # the laptop's cap travels
                self.assertEqual(s.symbol_list, ["BTC/USDT", "ETH/USDT"])
                self.assertIsNone(s.exchange_credentials("binance"))  # keys come from keys.env only
                self.assertTrue(s.password_hash_for("admin").startswith("scrypt:"))
                self.assertIn(f"stage {stage}", summary[0])

    def test_keys_reach_the_gateway_through_its_environment(self) -> None:
        self.logins()
        path, _ = deploy.build_env("live", ["binance"])
        keys = {"BINANCE_MAINNET_API_KEY": "k1", "BINANCE_MAINNET_API_SECRET": "s1"}
        with mock.patch.dict(os.environ, keys):  # what systemd's EnvironmentFile does
            s = Settings(_env_file=str(path))
        self.assertEqual(s.exchange_credentials("binance"), {"apiKey": "k1", "secret": "s1"})

    def test_rehearsal_takes_the_laptops_sandbox_keys_only(self) -> None:
        lines = deploy.sandbox_key_lines(["binance", "bybit"])
        self.assertEqual(lines, ["BINANCE_TESTNET_API_KEY=bk", "BINANCE_TESTNET_API_SECRET=bs",
                                 "BYBIT_TESTNET_API_KEY=yk", "BYBIT_TESTNET_API_SECRET=ys"])
        with self.assertRaises(SystemExit):
            deploy.sandbox_key_lines(["okx"])  # no OKX sandbox key on this laptop


class KeyWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.keys = self.dir / "keys.env"

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text: str, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(KIT / "write_keys.py"), str(self.keys), *extra], input=text,
                              capture_output=True, text=True)

    def stored(self) -> dict[str, str]:
        lines = [x for x in self.keys.read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")]
        return {k: v.strip("'") for k, v in (x.split("=", 1) for x in lines)}

    def test_merge_drop_and_never_print_a_whole_key(self) -> None:
        out = self.write("BINANCE_TESTNET_API_KEY=testkey1234\nBINANCE_TESTNET_API_SECRET=testsecret99\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.write("BINANCE_MAINNET_API_KEY=realkeyABCD\nBINANCE_MAINNET_API_SECRET=realsecretWXYZ\n")
        self.assertEqual(len(self.stored()), 4)  # merged, not replaced
        self.assertNotIn("realkey", out.stdout)
        self.assertIn("...ABCD", out.stdout)
        self.write("", "--drop", "testnet")  # go-live: the sandbox keys go
        self.assertEqual(sorted(self.stored()), ["BINANCE_MAINNET_API_KEY", "BINANCE_MAINNET_API_SECRET"])
        if os.name != "nt":
            self.assertEqual(self.keys.stat().st_mode & 0o777, 0o600)

    def test_only_exchange_keys_and_plain_values(self) -> None:
        self.assertNotEqual(self.write("PATH=/tmp\n").returncode, 0)
        self.assertNotEqual(self.write("BINANCE_MAINNET_API_KEY=has space\n").returncode, 0)
        self.assertNotEqual(self.write("BINANCE_MAINNET_API_KEY=it's\n").returncode, 0)
        self.assertFalse(self.keys.exists())  # a refusal writes nothing


class GateTest(unittest.TestCase):
    SHA = "abcdef1234567890abcdef1234567890abcdef12"

    def test_the_lab_must_run_this_commit_for_three_days(self) -> None:
        hour = 3600
        self.assertTrue(deploy.gate_verdict(self.SHA, "20261012-1050-abcdef1234", 0, 73 * hour)[0])
        too_soon = deploy.gate_verdict(self.SHA, "20261012-1050-abcdef1234", 0, 30 * hour)
        self.assertEqual(too_soon, (False, "the lab server has run abcdef1234 for 30 h; it needs 72 h"))
        other = deploy.gate_verdict(self.SHA, "20261012-1050-1111111111", 0, 99 * hour)
        self.assertIn("release it there first", other[1])

    def test_ci_must_be_finished_and_green(self) -> None:
        def run(status: str, conclusion: str | None = None, name: str = "tests") -> dict:
            return {"name": name, "status": status, "conclusion": conclusion}

        self.assertIsNone(deploy.ci_verdict([])[0])
        self.assertTrue(deploy.ci_verdict([run("completed", "success"), run("completed", "skipped", "kit")])[0])
        self.assertFalse(deploy.ci_verdict([run("completed", "success"), run("in_progress")])[0])
        self.assertIn("failed", deploy.ci_verdict([run("completed", "failure")])[1])


if __name__ == "__main__":
    unittest.main()
