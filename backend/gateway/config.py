from pydantic import Field
from pydantic_settings import BaseSettings


class VenueConfig(BaseSettings):
    venue_id: str
    name: str
    cloud: str
    host: str
    port: int
    base_latency_ms: float
    personality: str


VENUE_CONFIGS: list[dict] = [
    {
        "venue_id": "V1",
        "name": "AlphaExchange",
        "cloud": "AWS",
        "host": "localhost",
        "port": 8001,
        "base_latency_ms": 10.0,
        "personality": "stable",
    },
    {
        "venue_id": "V2",
        "name": "BetaLiquidity",
        "cloud": "GCP",
        "host": "localhost",
        "port": 8002,
        "base_latency_ms": 5.0,
        "personality": "fast",
    },
    {
        "venue_id": "V3",
        "name": "GammaMarkets",
        "cloud": "Azure",
        "host": "localhost",
        "port": 8003,
        "base_latency_ms": 125.0,
        "personality": "degraded",
    },
    {
        "venue_id": "V4",
        "name": "DeltaPrime",
        "cloud": "AWS",
        "host": "localhost",
        "port": 8004,
        "base_latency_ms": 3.0,
        "personality": "premium",
    },
    {
        "venue_id": "V5",
        "name": "EpsilonPool",
        "cloud": "GCP",
        "host": "localhost",
        "port": 8005,
        "base_latency_ms": 17.0,
        "personality": "dark_pool",
    },
]


class Settings(BaseSettings):
    app_name: str = "Apna Exchange"
    debug: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    venue_v1_url: str = "http://localhost:8001"
    venue_v2_url: str = "http://localhost:8002"
    venue_v3_url: str = "http://localhost:8003"
    venue_v4_url: str = "http://localhost:8004"
    venue_v5_url: str = "http://localhost:8005"

    default_max_order_size: float = 10_000.0
    default_max_position: float = 50_000.0
    default_max_notional: float = 1_000_000.0
    default_max_orders_per_second: int = 50

    market_data_poll_interval_ms: int = 100
    data_staleness_threshold_ms: int = 2000
    venue_health_check_interval_s: float = 1.0
    health_score_recompute_interval_s: float = 5.0

    jwt_secret: str = "apna-exchange-poc-secret-change-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def get_venue_urls(self) -> dict[str, str]:
        return {
            "V1": self.venue_v1_url,
            "V2": self.venue_v2_url,
            "V3": self.venue_v3_url,
            "V4": self.venue_v4_url,
            "V5": self.venue_v5_url,
        }


settings = Settings()
