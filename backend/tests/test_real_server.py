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
from backend.services.agents.llm import build_llm_client

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
                self.assertEqual(s.llm_provider, "claude")  # decision A5 (2026-10-23)
                client, why = build_llm_client(s)  # ... but dormant: no key yet, so the copilot's AI is off
                self.assertIsNone(client)
                self.assertIn("COPILOT_CLAUDE_API_KEY", why)
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

    def test_the_copilots_claude_key_turns_its_ai_on(self) -> None:
        self.logins()
        path, _ = deploy.build_env("live", ["binance"])
        with mock.patch.dict(os.environ, {deploy.AI_KEY: "sk-ant-test-0000"}):  # typed with deploy.py ai-key
            s = Settings(_env_file=str(path))
        client, why = build_llm_client(s)
        self.assertIsNone(why)
        self.assertEqual(client.provider, "claude")

    def test_the_key_check_says_no_key_without_one(self) -> None:
        env = {**os.environ, deploy.AI_KEY: ""}
        out = subprocess.run([sys.executable, str(KIT / "check_ai_key.py")], cwd=self.dir, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip(), "NO KEY", out.stderr)

    def test_rehearsal_takes_the_laptops_sandbox_keys_only(self) -> None:
        lines = deploy.sandbox_key_lines(["binance", "bybit"])
        self.assertEqual(lines, ["BINANCE_TESTNET_API_KEY=bk", "BINANCE_TESTNET_API_SECRET=bs",
                                 "BYBIT_TESTNET_API_KEY=yk", "BYBIT_TESTNET_API_SECRET=ys"])
        with self.assertRaises(SystemExit):
            deploy.sandbox_key_lines(["okx"])  # no OKX sandbox key on this laptop
        # HTX has no sandbox: a rehearsal runs it data-only and asks for no key
        self.assertEqual(deploy.sandbox_key_lines(["binance", "htx"]),
                         ["BINANCE_TESTNET_API_KEY=bk", "BINANCE_TESTNET_API_SECRET=bs"])
        # go-live's way back to the rehearsal: OKX with real keys but no sandbox key here runs data-only there
        self.assertEqual(deploy.sandbox_key_lines(["binance", "okx"], strict=False),
                         ["BINANCE_TESTNET_API_KEY=bk", "BINANCE_TESTNET_API_SECRET=bs"])


class SecretInputTest(unittest.TestCase):
    """Found 2026-10-24: pasting into Windows' hidden prompt stored Ctrl+V's keystroke and a 239-character jumble."""

    def test_what_can_not_be_a_key(self) -> None:
        self.assertIsNone(deploy.secret_problem("0a1b2c3d-4e5f6a7b-8c9d0e1f-2a3b4"))
        for bad, words in (("", "nothing"), ("\x16", "0x16"), ("ab\x16cdefgh", "0x16"), ("has space", "0x20"),
                           ("it's-a-key", "quote"), ("short", "length"), ("x" * 239, "length")):
            with self.subTest(bad=bad[:12]):
                self.assertIn(words, deploy.secret_problem(bad))

    def test_a_bad_value_is_asked_again_and_never_sent(self) -> None:
        with mock.patch.object(deploy.getpass, "getpass", side_effect=["\x16", "0a1b2c3d4e5f"]), \
             mock.patch.object(deploy.sys.stdin, "isatty", return_value=False), \
             mock.patch("builtins.print"):
            self.assertEqual(deploy.ask_secret("HTX Access Key"), "0a1b2c3d4e5f")
        with mock.patch.object(deploy.getpass, "getpass", return_value="\x16"), \
             mock.patch.object(deploy.sys.stdin, "isatty", return_value=False), \
             mock.patch("builtins.print"), self.assertRaises(SystemExit) as stopped:
            deploy.ask_secret("HTX Secret Key")
        self.assertIn("nothing was sent", str(stopped.exception))

    def test_windows_reads_the_clipboard(self) -> None:
        with mock.patch.object(deploy.os, "name", "nt"), mock.patch.object(deploy.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", return_value=""), mock.patch("builtins.print"), \
             mock.patch.object(deploy, "read_clipboard", return_value="  0a1b2c3d4e5f\r\n".strip()):
            self.assertEqual(deploy.ask_secret("OKX API key"), "0a1b2c3d4e5f")

    def test_labels_use_the_exchanges_own_words(self) -> None:
        self.assertEqual(deploy.key_label("htx", "API_KEY"), "HTX Access Key")
        self.assertEqual(deploy.key_label("okx", "API_PASSPHRASE"), "OKX passphrase (the one you chose)")
        self.assertEqual(deploy.key_label("binance", "API_SECRET"), "Binance secret key")


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

    def test_the_phone_alerts_token_and_chat_are_accepted(self) -> None:
        out = self.write("TELEGRAM_BOT_TOKEN=123456:AAbbCCdd\nTELEGRAM_CHAT_ID=-1001234\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.stored(), {"TELEGRAM_BOT_TOKEN": "123456:AAbbCCdd", "TELEGRAM_CHAT_ID": "-1001234"})
        self.assertNotIn("AAbb", out.stdout)

    def test_the_discord_alerts_are_accepted(self) -> None:
        out = self.write("DISCORD_BOT_TOKEN=MTIz.abc.SecretWXYZ\nDISCORD_CHANNEL_ID=1300000000000000001\n"
                         "DISCORD_OWNER_ID=700000000000000001\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(sorted(self.stored()), ["DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID", "DISCORD_OWNER_ID"])
        self.assertNotIn("Secret", out.stdout)

    def test_the_discord_channel_id_from_an_id_or_a_link(self) -> None:
        self.assertEqual(deploy.channel_id(" 1430123456789012345\n"), "1430123456789012345")
        self.assertEqual(deploy.channel_id("https://discord.com/channels/1220000000000000000/1430123456789012345"),
                         "1430123456789012345")
        self.assertEqual(deploy.channel_id(""), "")  # an empty line (Enter pressed before pasting)
        self.assertEqual(deploy.channel_id("general"), "")

    def test_the_discord_owner_is_the_last_person_who_wrote(self) -> None:
        messages = [{"author": {"id": "9", "username": "ApnaSOR Alerts", "bot": True}},  # newest first
                    {"author": {"id": "7", "username": "sarmad", "global_name": "Sarmad"}},
                    {"author": {"id": "8", "username": "older"}}]
        self.assertEqual(deploy.discord_owner(messages), ("7", "Sarmad"))
        self.assertIsNone(deploy.discord_owner([{"author": {"id": "9", "bot": True}}]))

    def test_the_copilots_claude_key_is_accepted_and_can_be_removed(self) -> None:
        out = self.write("BYBIT_MAINNET_API_KEY=realkey1111\nCOPILOT_CLAUDE_API_KEY=sk-ant-api03-secretXYZW\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("secret", out.stdout)
        self.assertIn("...XYZW", out.stdout)
        out = self.write("", "--remove", "COPILOT_CLAUDE_API_KEY")  # deploy.py ai-key --remove
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(sorted(self.stored()), ["BYBIT_MAINNET_API_KEY"])  # only that one went
        self.assertNotEqual(self.write("", "--remove", "PATH").returncode, 0)

    def test_htx_keys_are_accepted(self) -> None:
        out = self.write("HTX_MAINNET_API_KEY=htxaccess1111\nHTX_MAINNET_API_SECRET=htxsecret2222\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(sorted(self.stored()), ["HTX_MAINNET_API_KEY", "HTX_MAINNET_API_SECRET"])
        self.assertNotIn("htxsecret", out.stdout)

    def test_only_exchange_keys_and_plain_values(self) -> None:
        self.assertNotEqual(self.write("PATH=/tmp\n").returncode, 0)
        self.assertNotEqual(self.write("BINANCE_MAINNET_API_KEY=has space\n").returncode, 0)
        self.assertNotEqual(self.write("BINANCE_MAINNET_API_KEY=it's\n").returncode, 0)
        self.assertNotEqual(self.write("HTX_MAINNET_API_SECRET=\x16\n").returncode, 0)  # Ctrl+V's keystroke (found live)
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
