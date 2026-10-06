"""Logins: real passwords (scrypt hashes from .env), the demo login as a testnet-only fallback, audits."""

import threading
import unittest
from contextlib import ExitStack
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.gateway.config import Settings, settings
from backend.gateway.middleware import auth as auth_module
from backend.gateway.middleware.auth import (
    USERS,
    authenticate_user,
    check_login,
    demo_logins,
    login_setup_problems,
    login_summary,
)
from backend.gateway.middleware.passwords import (
    hash_password,
    hash_problem,
    password_problem,
    verify_password,
)
from backend.gateway.routers import auth as auth_router
from backend.shared.events.event_bus import event_bus

CHEAP = {"n": 2**10, "r": 8, "p": 1}  # fast hashes for tests; the real cost is in test_default_cost
REAL = "correct horse battery"


def env(**values: str) -> Settings:
    return Settings(_env_file=None, **values)


class PasswordHashTest(unittest.TestCase):
    def test_right_and_wrong_password(self) -> None:
        stored = hash_password(REAL, **CHEAP)
        self.assertTrue(verify_password(REAL, stored))
        self.assertFalse(verify_password("correct horse battery!", stored))
        self.assertFalse(verify_password("", stored))

    def test_salted_so_equal_passwords_hash_differently(self) -> None:
        self.assertNotEqual(hash_password(REAL, **CHEAP), hash_password(REAL, **CHEAP))

    def test_default_cost(self) -> None:
        stored = hash_password(REAL)  # the cost the script uses: ~0.1-0.2 s
        self.assertTrue(stored.startswith("scrypt:16384:8:5:"))
        self.assertTrue(verify_password(REAL, stored))
        self.assertNotIn(REAL, stored)

    def test_a_plain_password_or_garbage_in_env_is_not_a_hash(self) -> None:
        good = hash_password(REAL, **CHEAP)
        self.assertIsNone(hash_problem(good))
        for bad in ("admin", REAL, "scrypt:16384:8:5:abc", good.replace("scrypt", "bcrypt"),
                    good.replace(":1024:", ":1000:"),  # not a power of two
                    good.replace(":1024:", f":{2**24}:")):  # would take gigabytes per login
            with self.subTest(bad[:30]):
                self.assertIsNotNone(hash_problem(bad))
                self.assertFalse(verify_password("admin", bad))

    def test_password_rules(self) -> None:
        self.assertIsNotNone(password_problem("admin", "short"))
        self.assertIsNotNone(password_problem("trader1", " TRADER1 ".ljust(12)))  # the demo login, disguised
        self.assertIsNone(password_problem("admin", REAL))


class LoginRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.admin_hash = hash_password(REAL, **CHEAP)

    def test_without_real_passwords_the_demo_login_works_on_testnet(self) -> None:
        s = env()
        self.assertEqual(authenticate_user("admin", "admin", s), USERS["admin"])
        self.assertIsNone(authenticate_user("admin", "wrong", s))
        self.assertIsNone(authenticate_user("nobody", "nobody", s))
        self.assertEqual(demo_logins(s), ["admin", "trader1", "risk_mgr"])
        self.assertEqual(login_setup_problems(s), [])

    def test_a_real_password_replaces_the_demo_login_for_that_user_only(self) -> None:
        s = env(auth_password_hash_admin=self.admin_hash)
        self.assertIsNone(authenticate_user("admin", "admin", s))  # demo login refused
        self.assertEqual(authenticate_user("admin", REAL, s), USERS["admin"])
        self.assertEqual(authenticate_user("trader1", "trader1", s), USERS["trader1"])  # still a demo login
        self.assertEqual(demo_logins(s), ["trader1", "risk_mgr"])
        self.assertEqual(login_summary(s), {"real_passwords": ["admin"], "demo_logins": ["trader1", "risk_mgr"]})

    def test_mainnet_has_no_demo_logins(self) -> None:
        s = env(exchange_mode="mainnet", allow_mainnet=True, auth_password_hash_admin=self.admin_hash)
        self.assertIsNone(authenticate_user("trader1", "trader1", s))
        self.assertEqual(authenticate_user("admin", REAL, s), USERS["admin"])
        self.assertEqual(demo_logins(s), [])
        self.assertEqual(login_summary(s), {"real_passwords": ["admin"], "cannot_log_in": ["trader1", "risk_mgr"]})
        self.assertEqual(login_setup_problems(s), [])

    def test_mainnet_without_any_real_password_stops_the_start(self) -> None:
        problems = login_setup_problems(env(exchange_mode="mainnet", allow_mainnet=True))
        self.assertEqual(len(problems), 1)
        self.assertIn("nobody could log in", problems[0])

    def test_a_plain_password_pasted_into_env_stops_the_start_and_never_logs_in(self) -> None:
        s = env(auth_password_hash_trader1="MySecret123")
        self.assertIn("AUTH_PASSWORD_HASH_TRADER1", login_setup_problems(s)[0])
        self.assertIsNone(authenticate_user("trader1", "MySecret123", s))
        self.assertIsNone(authenticate_user("trader1", "trader1", s))
        self.assertNotIn("MySecret123", repr(s))  # SecretStr: never in logs either


class CheckLoginTest(unittest.IsolatedAsyncioTestCase):
    async def test_runs_on_the_password_thread_not_the_event_loop(self) -> None:
        seen: list[str] = []

        def fake(username: str, password: str) -> None:
            seen.append(threading.current_thread().name)

        with mock.patch.object(auth_module, "authenticate_user", fake):
            await check_login("admin", "x")
        self.assertTrue(seen[0].startswith("password-check"))


class LoginRouteTest(unittest.TestCase):
    """POST /api/auth/login and GET /api/auth/demo-logins, independent of the developer's own .env."""

    def setUp(self) -> None:
        self.admin_hash = hash_password(REAL, **CHEAP)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(auth_module, "_signing_key", "k" * 64))
        stack.enter_context(mock.patch.object(settings, "exchange_mode", "testnet"))
        for name in USERS:
            value = SecretStr(self.admin_hash) if name == "admin" else None
            stack.enter_context(mock.patch.object(settings, f"auth_password_hash_{name}", value))
        self.audits: list[dict] = []

        async def record(data: dict) -> None:
            self.audits.append(data)

        event_bus.subscribe("audit", record)
        self.addCleanup(event_bus.unsubscribe, "audit", record)
        app = FastAPI()
        app.include_router(auth_router.router)
        self.client = TestClient(app)

    def test_real_password_logs_in_and_is_audited(self) -> None:
        r = self.client.post("/api/auth/login", json={"username": "admin", "password": REAL})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["username"], "admin")
        self.assertEqual([(a["event_type"], a["actor"]) for a in self.audits], [("login", "admin")])
        self.assertNotIn(REAL, repr(self.audits))

    def test_failed_logins_are_audited_without_the_password(self) -> None:
        for username, password in (("admin", "admin"), ("nobody\x1b[31m", "pw-typed-by-someone")):
            r = self.client.post("/api/auth/login", json={"username": username, "password": password})
            self.assertEqual(r.status_code, 401)
        self.assertEqual([(a["event_type"], a["actor"]) for a in self.audits],
                         [("login_failed", "unknown"), ("login_failed", "unknown")])
        self.assertIn("no such user", self.audits[1]["summary"])
        self.assertNotIn("\x1b", self.audits[1]["summary"])  # typed text is cleaned before the audit trail
        self.assertNotIn("pw-typed-by-someone", repr(self.audits))

    def test_demo_logins_lists_only_users_without_a_real_password(self) -> None:
        r = self.client.get("/api/auth/demo-logins")
        self.assertEqual(r.json()["demo_logins"], ["trader1", "risk_mgr"])
        r = self.client.post("/api/auth/login", json={"username": "trader1", "password": "trader1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("(demo login)", self.audits[-1]["summary"])


if __name__ == "__main__":
    unittest.main()
