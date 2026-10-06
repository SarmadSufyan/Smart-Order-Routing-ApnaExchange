"""Security settings: the login-signing key (never from the code) and which websites may use the API."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.gateway.config import PUBLIC_JWT_SECRETS, Settings
from backend.gateway.middleware import auth as auth_module
from backend.gateway.middleware.auth import load_jwt_secret, load_or_create_secret

LONG = "x" * 40


class JwtSecretSettingTest(unittest.TestCase):
    def test_unset_and_public_placeholders_count_as_not_set(self) -> None:
        self.assertIsNone(Settings(_env_file=None).jwt_secret_from_env())
        self.assertIsNone(Settings(_env_file=None).jwt_secret_problem())  # not a problem: the machine's key is used
        for public in PUBLIC_JWT_SECRETS:
            with self.subTest(public):
                s = Settings(_env_file=None, jwt_secret=public)
                self.assertIsNone(s.jwt_secret_from_env())
                self.assertTrue(s.jwt_secret_is_placeholder())
                self.assertIsNone(s.jwt_secret_problem())

    def test_a_short_secret_set_on_purpose_stops_the_start(self) -> None:
        self.assertIn("shorter than 32", Settings(_env_file=None, jwt_secret="short").jwt_secret_problem())

    def test_a_long_secret_is_used_and_never_shown(self) -> None:
        s = Settings(_env_file=None, jwt_secret=LONG)
        self.assertEqual(s.jwt_secret_from_env(), LONG)
        self.assertIsNone(s.jwt_secret_problem())
        self.assertNotIn(LONG, repr(s))  # SecretStr


class SigningKeyFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "data" / "jwt-secret-testnet.key"
        patcher = mock.patch.object(auth_module, "_signing_key", None)  # leave the module as found
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_created_once_then_reused(self) -> None:
        key, created = load_or_create_secret(self.path)
        self.assertTrue(created)
        self.assertEqual(len(key), 64)  # 256 random bits, hex
        self.assertEqual(load_or_create_secret(self.path), (key, False))  # a restart keeps everyone logged in
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])  # no .tmp left behind

    def test_an_empty_or_short_file_is_replaced(self) -> None:
        self.path.parent.mkdir(parents=True)
        for content in ("", "abc\n"):
            with self.subTest(content):
                self.path.write_text(content, encoding="utf-8")
                key, created = load_or_create_secret(self.path)
                self.assertTrue(created)
                self.assertEqual(self.path.read_text(encoding="utf-8").strip(), key)

    def test_each_machine_gets_its_own_random_key(self) -> None:
        other = Path(self.dir.name) / "other.key"
        self.assertNotEqual(load_or_create_secret(self.path)[0], load_or_create_secret(other)[0])

    def with_key_path(self):
        return mock.patch.object(Settings, "resolved_jwt_secret_path", property(lambda _: str(self.path)))

    def test_without_a_secret_in_env_the_machine_key_signs(self) -> None:
        with self.with_key_path():
            info = load_jwt_secret(Settings(_env_file=None, jwt_secret="change-me-use-a-long-random-string-in-prod"))
        self.assertTrue(info["created"])
        self.assertTrue(info["ignored_public_placeholder"])
        self.assertEqual(auth_module._signing_key, self.path.read_text(encoding="utf-8").strip())
        self.assertNotIn(auth_module._signing_key, repr(info))  # the start-up log never shows the key

    def test_a_secret_in_env_wins_and_no_file_is_made(self) -> None:
        with self.with_key_path():
            self.assertEqual(load_jwt_secret(Settings(_env_file=None, jwt_secret=LONG)), {"source": ".env"})
        self.assertEqual(auth_module._signing_key, LONG)
        self.assertFalse(self.path.exists())

    def test_a_short_secret_is_refused(self) -> None:
        with self.with_key_path(), self.assertRaises(RuntimeError):
            load_jwt_secret(Settings(_env_file=None, jwt_secret="short"))
        self.assertIsNone(auth_module._signing_key)

    def test_tokens_need_a_loaded_key(self) -> None:
        with self.assertRaises(RuntimeError):
            auth_module.create_refresh_token(auth_module.authenticate_user("admin", "admin"))


class AllowedOriginsTest(unittest.TestCase):
    def test_default_is_this_machines_frontend_on_any_port(self) -> None:
        s = Settings(_env_file=None)
        for origin in ("http://localhost:5173", "http://127.0.0.1:5174", "http://localhost", "https://localhost:8443"):
            with self.subTest(origin):
                self.assertTrue(s.origin_allowed(origin))
        for origin in ("https://evil.example", "http://localhost.evil.example", "http://127.0.0.1.evil.example",
                       "http://evil.example/?localhost", "http://192.168.1.20:5173", "null", "*", ""):
            with self.subTest(origin):
                self.assertFalse(s.origin_allowed(origin))

    def test_a_list_in_env_means_only_those(self) -> None:
        s = Settings(_env_file=None, cors_origins=" http://192.168.1.20:5173/ , http://localhost:5173")
        self.assertEqual(s.cors_origin_list, ["http://192.168.1.20:5173", "http://localhost:5173"])
        self.assertTrue(s.origin_allowed("http://192.168.1.20:5173"))
        self.assertFalse(s.origin_allowed("http://127.0.0.1:5173"))  # not listed
        self.assertIsNone(s.cors_problem())

    def test_any_website_is_refused(self) -> None:
        s = Settings(_env_file=None, cors_origins="http://localhost:5173,*")
        self.assertIn("ANY website", s.cors_problem())
        self.assertFalse(s.origin_allowed("*"))


class GatewayCorsTest(unittest.TestCase):
    """The real gateway app's answers to a browser's "may this page call you?" question."""

    @classmethod
    def setUpClass(cls) -> None:
        from backend.gateway.main import app

        cls.client = TestClient(app)  # not as a context manager: no lifespan, nothing connects

    def preflight(self, origin: str):
        return self.client.options("/api/auth/login", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })

    def test_our_frontend_may_call_the_api(self) -> None:
        r = self.preflight("http://localhost:5173")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["access-control-allow-origin"], "http://localhost:5173")
        self.assertNotIn("access-control-allow-credentials", r.headers)  # bearer tokens, no cookies

    def test_another_website_may_not(self) -> None:
        # Before: "*" answered yes to every site, so any page could log in as admin/admin and trade.
        r = self.preflight("https://evil.example")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("access-control-allow-origin", r.headers)


if __name__ == "__main__":
    unittest.main()
