"""The lab-server kit's Python parts: database backups, and the settings deploy.py sends to the server."""

import gzip
import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from scripts import backup_databases

from backend.gateway.config import PROJECT_ROOT, Settings

spec = importlib.util.spec_from_file_location("lab_deploy", PROJECT_ROOT / "deploy" / "lab-server" / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class BackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_backup_is_a_checked_gzipped_copy(self) -> None:
        source = self.dir / "apnasor-testnet.db"
        con = sqlite3.connect(source)
        con.execute("create table t (x)")
        con.executemany("insert into t values (?)", [(i,) for i in range(100)])
        con.commit()
        con.close()
        folder = self.dir / "out"
        folder.mkdir()
        name, size = backup_databases.backup_one(source, folder)
        self.assertEqual(name, "apnasor-testnet.db.gz")
        self.assertGreater(size, 0)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["apnasor-testnet.db.gz"])  # no plain copy left
        restored = self.dir / "restored.db"
        with gzip.open(folder / name) as fin, open(restored, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        con = sqlite3.connect(restored)
        self.assertEqual(con.execute("select count(*) from t").fetchone()[0], 100)
        con.close()

    def test_old_backups_go_but_the_newest_three_always_stay(self) -> None:
        now = datetime(2026, 11, 1, tzinfo=UTC)
        for days in (1, 20, 30, 40, 50):
            (self.dir / (now - timedelta(days=days)).strftime(backup_databases.STAMP)).mkdir()
        (self.dir / "notes").mkdir()  # not a backup: never touched
        removed = backup_databases.prune(self.dir, keep_days=14, now=now)
        self.assertEqual(len(removed), 2)  # 40 and 50 days old; 20 and 30 stay as two of the newest three
        self.assertEqual(len(list(self.dir.iterdir())), 4)
        self.assertTrue((self.dir / "notes").exists())


class ServerSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.laptop_env = self.dir / ".env"
        patches = [mock.patch.object(deploy, "ROOT", self.dir), mock.patch.object(deploy, "LOCAL", self.dir / "out"),
                   mock.patch.object(deploy, "LOGINS", self.dir / "out" / "logins.env")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_laptop_env(self, text: str) -> None:
        self.laptop_env.write_text(text, encoding="utf-8")

    def test_testnet_keys_and_server_settings_only_never_a_mainnet_key(self) -> None:
        self.write_laptop_env(
            "BINANCE_TESTNET_API_KEY=bk\nBINANCE_TESTNET_API_SECRET=bs\nBYBIT_TESTNET_API_KEY=yk\n"
            "BYBIT_TESTNET_API_SECRET=ys\nBINANCE_MAINNET_API_KEY=REAL\nBINANCE_MAINNET_API_SECRET=REAL\n"
            "GEMINI_API_KEY=g\nRAFT_SHARED_SECRET=r\nEXCHANGE_MODE=mainnet\nDEBUG=true\n"
            "AUTH_PASSWORD_HASH_ADMIN=scrypt:16384:8:5:aa:bb\n")
        path, summary = deploy.build_env(["binance", "bybit"])
        text = path.read_text(encoding="utf-8")
        for absent in ("REAL", "MAINNET_API", "GEMINI", "RAFT_SHARED_SECRET"):
            self.assertNotIn(absent, text)
        s = Settings(_env_file=str(path))
        self.assertEqual((s.exchange_mode, s.allow_mainnet, s.debug), ("testnet", False, False))
        self.assertEqual(s.enabled_exchange_list, ["binance", "bybit"])
        self.assertTrue(s.lab_auto_resume)
        self.assertFalse(s.arb_enabled)
        self.assertEqual(s.llm_provider, "off")
        self.assertEqual(s.exchange_credentials("bybit"), {"apiKey": "yk", "secret": "ys"})
        self.assertEqual(s.password_hash_for("admin"), "scrypt:16384:8:5:aa:bb")
        self.assertIn("real login passwords for admin", summary[1])

    def test_a_missing_key_stops_it_and_the_server_logins_win_over_the_laptop(self) -> None:
        self.write_laptop_env("BINANCE_TESTNET_API_KEY=bk\nBINANCE_TESTNET_API_SECRET=bs\n"
                              "AUTH_PASSWORD_HASH_ADMIN=laptop\n")
        with self.assertRaises(SystemExit) as stopped:
            deploy.build_env(["binance", "bybit"])
        self.assertIn("BYBIT_TESTNET_API_KEY", str(stopped.exception))
        (self.dir / "out").mkdir()
        (self.dir / "out" / "logins.env").write_text("AUTH_PASSWORD_HASH_ADMIN=server\n", encoding="utf-8")
        path, _ = deploy.build_env(["binance"])
        self.assertEqual(Settings(_env_file=str(path)).password_hash_for("admin"), "server")

    def test_the_server_reads_exactly_what_the_laptop_reads(self) -> None:
        self.write_laptop_env('BINANCE_TESTNET_API_KEY=a$HOME${HOME}#x\nBINANCE_TESTNET_API_SECRET="s p#$1"\n')
        path, _ = deploy.build_env(["binance"])
        laptop = Settings(_env_file=str(self.laptop_env)).exchange_credentials("binance")
        self.assertEqual(Settings(_env_file=str(path)).exchange_credentials("binance"), laptop)
        self.assertIn("'", path.read_text(encoding="utf-8"))  # single quotes: the server expands nothing again


if __name__ == "__main__":
    unittest.main()
