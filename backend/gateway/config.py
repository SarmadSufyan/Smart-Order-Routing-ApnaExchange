"""Gateway configuration.

Every setting can be overridden from the environment or the project-root
``.env`` file. Exchange API keys are held as ``SecretStr`` so they never show
up in reprs, logs, or validation errors.
"""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # A venue+symbol whose EXECUTION book (testnet) sits further than this
    # from the real-market book is excluded from routing (both sides).
    coherence_threshold_bps: float = 50.0
    coherence_check_interval_s: float = 5.0

    # ─── Venue health / wallet ────────────────────────────────────
    venue_health_check_interval_s: float = 2.0
    wallet_refresh_interval_s: float = 15.0

    # ─── Risk defaults (units = base asset, notionals = USDT) ─────
    default_max_order_size: float = 10_000.0
    default_max_order_notional: float = 10_000.0
    default_max_position: float = 50_000.0
    default_max_notional: float = 1_000_000.0
    default_max_orders_per_second: int = 50

    # ─── Auth ─────────────────────────────────────────────────────
    jwt_secret: str = "apna-exchange-poc-secret-change-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

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

    @property
    def symbol_list(self) -> list[str]:
        """Unified symbols, e.g. ``["BTC/USDT", "ETH/USDT"]``."""
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

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
