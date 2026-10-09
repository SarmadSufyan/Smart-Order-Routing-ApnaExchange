"""Gateway configuration.

Every setting can be overridden from the environment or the project-root
``.env`` file. Exchange API keys are held as ``SecretStr`` so they never show
up in reprs, logs, or validation errors.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# JWT_SECRET values that have been public in this repo (the old default here,
# .env.example, the old deployment doc): anyone can read them, so they count
# as "not set".
PUBLIC_JWT_SECRETS = frozenset({
    "apna-exchange-poc-secret-change-in-prod",
    "change-me-use-a-long-random-string-in-prod",
    "change-me-in-production-use-a-long-random-string",
    "dev-secret-change-in-prod",
})
MIN_JWT_SECRET_LENGTH = 32

# The default CORS rule: a page served from this machine, any port (Vite picks
# another port when 5173 is busy).
LOCAL_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1)(:\d{1,5})?"
LOCAL_ORIGIN_RE = re.compile(LOCAL_ORIGIN_REGEX)


class Settings(BaseSettings):
    app_name: str = "ApnaSOR"
    debug: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ─── Exchanges ────────────────────────────────────────────────
    # testnet = fake funds (dev). mainnet = REAL money, and additionally
    # requires ALLOW_MAINNET=true so a single typo can't fire real orders.
    exchange_mode: Literal["testnet", "mainnet"] = "testnet"
    allow_mainnet: bool = False
    # Where order books come from, independent of where orders execute.
    # mainnet = public real-market feeds (no keys needed) -> cross-venue
    # comparison is real-market vs real-market even while executing on testnet.
    market_data_mode: Literal["testnet", "mainnet"] = "mainnet"
    # Default = every venue where orders really execute. Coinbase (data-only,
    # no real testnet) can be added for reference data: "...,coinbase".
    enabled_exchanges: str = "binance,bybit,okx,gate"
    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"

    binance_testnet_api_key: SecretStr | None = None
    binance_testnet_api_secret: SecretStr | None = None
    binance_mainnet_api_key: SecretStr | None = None
    binance_mainnet_api_secret: SecretStr | None = None
    bybit_testnet_api_key: SecretStr | None = None
    bybit_testnet_api_secret: SecretStr | None = None
    okx_testnet_api_key: SecretStr | None = None  # OKX "Demo Trading" API key
    okx_testnet_api_secret: SecretStr | None = None
    okx_testnet_api_passphrase: SecretStr | None = None
    gate_testnet_api_key: SecretStr | None = None
    gate_testnet_api_secret: SecretStr | None = None

    # ─── Market data ──────────────────────────────────────────────
    orderbook_snapshot_depth: int = 1000
    market_data_publish_interval_ms: int = 250
    feed_staleness_threshold_ms: int = 10_000
    routing_depth_levels: int = 50
    # MARKET parents become IOC LIMIT children capped this far past the worst
    # planned price. Decisions use mainnet books but fills happen on the
    # testnet book, so a drifted book must make a child expire, not overpay.
    child_price_protection_bps: float = 50.0
    # Liquidity-sweep routing: every child is capped this far past the best
    # all-in price (price + taker fee). Wider = more certain fill, worse price.
    sweep_band_bps: float = 20.0
    # A venue that refuses our key's right to trade gets no new orders for
    # this long (then one retry); a restart clears it. Refused quantity is
    # re-routed to the other venues either way.
    trading_block_cooldown_s: float = 600.0
    # A venue+symbol whose EXECUTION book (testnet) sits further than this
    # from the real-market book is excluded from routing (both sides).
    coherence_threshold_bps: float = 50.0
    coherence_check_interval_s: float = 5.0

    # ─── Venue health / wallet ────────────────────────────────────
    venue_health_check_interval_s: float = 2.0
    wallet_refresh_interval_s: float = 15.0

    # ─── Risk defaults (units = base asset, notionals = USDT) ─────
    # Limits changed in the UI are saved and override these after a restart.
    default_max_order_size: float = 10_000.0
    default_max_order_notional: float = 10_000.0
    default_max_position: float = 50_000.0
    default_max_notional: float = 1_000_000.0
    default_max_orders_per_second: int = 50
    default_max_algo_notional: float = 50_000.0  # a whole TWAP (parent) order
    default_max_trading_loss: float = 1_000.0  # 0 = off
    default_max_asset_concentration_pct: float = 60.0
    default_max_venue_concentration_pct: float = 80.0
    risk_monitor_interval_s: float = 2.0

    # ─── Risk batch (Phase 6: pre-flight in parallel processes) ───
    # Worker processes for stress-tested batches; 0 = min(CPU cores, 4).
    # Smaller batches stay in-process: below this size, process overhead
    # costs more than it saves (scripts/benchmark_risk_batch.py measures it).
    risk_pool_workers: int = 0
    risk_pool_min_orders: int = 4
    risk_stress_paths: int = 5_000  # Monte Carlo price paths per order
    risk_stress_horizon_min: int = 60
    volatility_sample_interval_s: float = 5.0  # live mid sampling for the stress test

    # ─── Arbitrage detector (5c: detection only, never trades) ────
    arb_enabled: bool = True
    arb_scan_interval_ms: int = 250  # = the book sampling rate; faster sees nothing new
    # Base-tier spot TAKER fee per exchange in bps (both legs would be IOC =
    # taker). Defaults: each exchange's public base tier as listed in ccxt's
    # market data (2026-10-04) — no VIP level, no BNB discount. Since
    # 2026-10-07 the ROUTER uses the same table: every routing strategy ranks
    # venues by price + this fee (our children are IOC, so always taker).
    arb_taker_fees_bps: str = "binance:10,bybit:10,okx:15,gate:20,coinbase:120"
    arb_min_net_bps: float = 0.0  # "profitable" = net edge after fees above this
    arb_audit_cooldown_s: float = 60.0  # at most one audit per direction per this

    # ─── Lab server (unattended practice lab, testnet only) ───────
    # After a restart (crash, reboot, a new release) resume the LAB bots that were running when the
    # gateway stopped, this long after the start (the exchanges' books need a moment). Off = every
    # bot comes back paused for a person to review. Never on real money, never while the kill switch
    # is engaged, never a bot a person or a safety rule paused. deploy/lab-server/ sets it on.
    lab_auto_resume: bool = False
    lab_auto_resume_delay_s: float = 60.0

    # ─── Audit log / book of record ───────────────────────────────
    # Empty = data/apnasor-<exchange_mode>.db, so testnet and mainnet records
    # never mix. ":memory:" keeps nothing (tests).
    database_path: str = ""
    # Research candle history + the live forecast log. Empty = data/market-history.db (real-market
    # data, the same whichever environment orders go to).
    market_history_db_path: str = ""

    # ─── RAFT cluster (Phase 6) ───────────────────────────────────
    # Three node processes replicate the kill switch, risk limits and venue
    # blacklist; the gateway writes through the leader. Off = everything
    # stays local, exactly as before. start_all.py starts the nodes.
    raft_enabled: bool = False
    raft_cluster_nodes: str = "127.0.0.1:9001,127.0.0.1:9002,127.0.0.1:9003"
    # Every RAFT message (node <-> node, gateway <-> node) is signed with this
    # (HMAC-SHA256). Required when RAFT is on; each machine makes its own.
    raft_shared_secret: SecretStr | None = None
    raft_data_dir: str = ""  # empty = data/raft-<exchange_mode>/ (one SQLite file per node)
    raft_tick_ms: int = 50
    raft_election_ticks: int = 10  # election timeout = random 10-20 ticks (0.5-1 s)
    raft_heartbeat_ticks: int = 2  # leader heartbeat every 100 ms
    raft_poll_interval_s: float = 0.5  # gateway re-reads the committed state
    raft_write_timeout_s: float = 3.0  # a write without a majority fails after this

    # ─── LLM copilot ──────────────────────────────────────────────
    # The model only turns a sentence into order parameters; it never sees
    # exchange keys and never submits anything. "off" disables the copilot.
    llm_provider: Literal["gemini", "off"] = "gemini"
    gemini_api_key: SecretStr | None = None  # free key from Google AI Studio
    gemini_model: str = "gemini-3.1-flash-lite"  # fast + on the free tier (2026-10-02)
    llm_timeout_s: float = 30.0
    copilot_proposal_ttl_s: float = 120.0  # a proposal not confirmed in time expires
    # Confirm refuses if the mid moved further than this since the proposal:
    # the trader agreed to a price, not to whatever the market does next.
    copilot_max_price_move_bps: float = 100.0

    # ─── Auth ─────────────────────────────────────────────────────
    # Signs every login token: whoever knows it can forge any login, so it is
    # never written in the code. Empty (or a placeholder that was ever public
    # in this repo) = the gateway's own random key for this machine, created
    # the first time in data/ (see middleware/auth.py: load_jwt_secret).
    jwt_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    # Real login passwords, as scrypt hashes (scripts/hash_password.py prints
    # the line). A user without one keeps the demo login (password = user
    # name) - except on mainnet, where that user can't log in at all.
    auth_password_hash_admin: SecretStr | None = None
    auth_password_hash_trader1: SecretStr | None = None
    auth_password_hash_risk_mgr: SecretStr | None = None

    # ─── Browser access (CORS) ────────────────────────────────────
    # Which web pages may use the API (and open /ws) from a browser. Empty =
    # the frontend on this machine (localhost / 127.0.0.1, any port); else a
    # comma-separated list of exact addresses, and only those. "*" is refused.
    cors_origins: str = ""

    # extra="ignore": .env also holds keys for other tools (Vite, Postgres,
    # RAFT...). Unknown keys must not crash the gateway at startup.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @model_validator(mode="after")
    def _mainnet_interlock(self) -> "Settings":
        if self.exchange_mode == "mainnet" and not self.allow_mainnet:
            raise ValueError(
                "EXCHANGE_MODE=mainnet trades REAL money and also requires "
                "ALLOW_MAINNET=true. Refusing to start."
            )
        return self

    def raft_secret_problem(self) -> str | None:
        """Why the RAFT secret can't be used, or None. Checked where RAFT starts
        (start_all, the gateway, each node) — not here, so loading the settings
        never fails just because RAFT is half set up."""
        secret = self.raft_shared_secret.get_secret_value() if self.raft_shared_secret else ""
        if len(secret) >= 16:
            return None
        return ("RAFT needs RAFT_SHARED_SECRET in .env (16+ characters) - every RAFT message is signed "
                "with it. Generate one with:  python -c \"import secrets; print(secrets.token_hex(32))\"")

    def jwt_secret_from_env(self) -> str | None:
        """The JWT_SECRET from .env, or None when it is empty or a public placeholder."""
        value = self.jwt_secret.get_secret_value().strip() if self.jwt_secret else ""
        return value if value and value not in PUBLIC_JWT_SECRETS else None

    def jwt_secret_is_placeholder(self) -> bool:
        """True when .env holds a JWT_SECRET that is public (and therefore ignored)."""
        value = self.jwt_secret.get_secret_value().strip() if self.jwt_secret else ""
        return value in PUBLIC_JWT_SECRETS

    def jwt_secret_problem(self) -> str | None:
        """Why the JWT_SECRET in .env can't be used, or None (also when unset:
        then the machine's own key is used). A secret someone set on purpose but
        too short stops the start instead of being quietly replaced."""
        value = self.jwt_secret_from_env()
        if value is None or len(value) >= MIN_JWT_SECRET_LENGTH:
            return None
        return (f"JWT_SECRET in .env is shorter than {MIN_JWT_SECRET_LENGTH} characters - anyone who guesses it "
                "can forge a login. Remove the line (the gateway then makes its own key for this machine) or "
                "use a long one:  python -c \"import secrets; print(secrets.token_hex(32))\"")

    @property
    def resolved_jwt_secret_path(self) -> str:
        """This machine's own login-signing key (testnet and mainnet logins never mix)."""
        return str(PROJECT_ROOT / "data" / f"jwt-secret-{self.exchange_mode}.key")

    def password_hash_for(self, username: str) -> str | None:
        """The user's password hash from .env, or None (= demo login). Known user names only."""
        value: SecretStr | None = getattr(self, f"auth_password_hash_{username}", None)
        stored = value.get_secret_value().strip() if value else ""
        return stored or None

    @property
    def demo_logins_allowed(self) -> bool:
        """Demo logins (password = user name) work on testnet only: real money needs real passwords."""
        return self.exchange_mode != "mainnet"

    @property
    def cors_origin_list(self) -> list[str]:
        """The exact browser origins from CORS_ORIGINS (empty = this machine's frontend)."""
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    def cors_problem(self) -> str | None:
        """Why CORS_ORIGINS can't be used, or None."""
        if "*" in self.cors_origin_list:
            return ("CORS_ORIGINS=* would let ANY website you open use this API from your browser (log in, "
                    "trade). List the frontend's addresses instead, e.g. CORS_ORIGINS=http://localhost:5173 - "
                    "or leave it empty for the frontend on this machine.")
        return None

    def origin_allowed(self, origin: str) -> bool:
        """May a page from this origin use the API? Same rule for REST (CORS) and /ws."""
        if origins := self.cors_origin_list:
            return origin in origins and origin != "*"
        return LOCAL_ORIGIN_RE.fullmatch(origin) is not None

    @property
    def symbol_list(self) -> list[str]:
        """Unified symbols, e.g. ``["BTC/USDT", "ETH/USDT"]``."""
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    @property
    def resolved_database_path(self) -> str:
        if self.database_path == ":memory:":
            return self.database_path
        if self.database_path:
            path = Path(self.database_path)
            return str(path if path.is_absolute() else PROJECT_ROOT / path)
        return str(PROJECT_ROOT / "data" / f"apnasor-{self.exchange_mode}.db")

    @property
    def resolved_market_history_path(self) -> str:
        if self.market_history_db_path:
            path = Path(self.market_history_db_path)
            return str(path if path.is_absolute() or self.market_history_db_path == ":memory:" else PROJECT_ROOT / path)
        return str(PROJECT_ROOT / "data" / "market-history.db")

    @property
    def resolved_raft_data_dir(self) -> str:
        if self.raft_data_dir:
            path = Path(self.raft_data_dir)
            return str(path if path.is_absolute() else PROJECT_ROOT / path)
        return str(PROJECT_ROOT / "data" / f"raft-{self.exchange_mode}")

    @property
    def enabled_exchange_list(self) -> list[str]:
        return [e.strip().lower() for e in self.enabled_exchanges.split(",") if e.strip()]

    def exchange_credentials(self, exchange_id: str) -> dict[str, str] | None:
        """ccxt credentials for the active mode, or None if unset.

        Returns ``{"apiKey", "secret"}`` plus ``"password"`` when the venue
        uses a passphrase (OKX). Mainnet keys are only ever read when
        ``exchange_mode == "mainnet"``, so in dev there is no code path that
        can touch real-money keys.
        """
        prefix = f"{exchange_id}_{self.exchange_mode}"
        key: SecretStr | None = getattr(self, f"{prefix}_api_key", None)
        secret: SecretStr | None = getattr(self, f"{prefix}_api_secret", None)
        if not key or not secret:
            return None
        creds = {"apiKey": key.get_secret_value(), "secret": secret.get_secret_value()}
        passphrase: SecretStr | None = getattr(self, f"{prefix}_api_passphrase", None)
        if passphrase:
            creds["password"] = passphrase.get_secret_value()
        return creds


settings = Settings()
