import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime

import httpx

from backend.gateway.config import VENUE_CONFIGS, settings
from backend.shared.events.event_bus import event_bus
from backend.shared.models.venue import (
    HealthScoreConfig,
    VenueHealth,
    VenueMetrics,
    VenueProfile,
    VenueStatus,
)
from backend.shared.utils.logger import get_logger

logger = get_logger("venue_health_monitor")


class VenueHealthMonitor:
    def __init__(self) -> None:
        self._profiles: dict[str, VenueProfile] = {}
        self._health: dict[str, VenueHealth] = {}
        self._metrics: dict[str, VenueMetrics] = {}
        self._latency_buffer: dict[str, list[float]] = defaultdict(list)
        self._order_results: dict[str, dict] = defaultdict(
            lambda: {"fills": 0, "rejects": 0, "total": 0}
        )
        self._start_time: float = time.monotonic()
        self._successful_checks: dict[str, int] = defaultdict(int)
        self._total_checks: dict[str, int] = defaultdict(int)
        self._score_config = HealthScoreConfig()
        self._running = False
        self._http_client: httpx.AsyncClient | None = None

        self._init_venues()

    def _init_venues(self) -> None:
        venue_urls = settings.get_venue_urls()
        for vc in VENUE_CONFIGS:
            vid = vc["venue_id"]
            url = venue_urls.get(vid, f"http://{vc['host']}:{vc['port']}")
            host, port = url.replace("http://", "").split(":")
            self._profiles[vid] = VenueProfile(
                venue_id=vid,
                name=vc["name"],
                cloud=vc["cloud"],
                host=host,
                port=int(port),
                base_latency_ms=vc["base_latency_ms"],
                personality=vc["personality"],
            )
            self._health[vid] = VenueHealth(
                venue_id=vid,
                name=vc["name"],
                cloud=vc["cloud"],
                status=VenueStatus.DISCONNECTED,
                health_score=0.0,
            )
            self._metrics[vid] = VenueMetrics()

    def get_all_health(self) -> list[VenueHealth]:
        return list(self._health.values())

    def get_venue_health(self, venue_id: str) -> VenueHealth | None:
        return self._health.get(venue_id)

    def get_venue_profile(self, venue_id: str) -> VenueProfile | None:
        return self._profiles.get(venue_id)

    def get_venue_metrics(self, venue_id: str) -> VenueMetrics | None:
        return self._metrics.get(venue_id)

    def get_routable_venues(self) -> list[str]:
        return [
            vid
            for vid, h in self._health.items()
            if h.status in (VenueStatus.HEALTHY, VenueStatus.DEGRADED)
        ]

    def get_excluded_venues(self) -> dict[str, str]:
        return {
            vid: h.status.value
            for vid, h in self._health.items()
            if h.status not in (VenueStatus.HEALTHY, VenueStatus.DEGRADED)
        }

    def record_order_result(self, venue_id: str, filled: bool) -> None:
        stats = self._order_results[venue_id]
        stats["total"] += 1
        if filled:
            stats["fills"] += 1
        else:
            stats["rejects"] += 1

    async def blacklist_venue(self, venue_id: str) -> None:
        if venue_id in self._health:
            self._health[venue_id].status = VenueStatus.BLACKLISTED
            self._health[venue_id].last_checked = datetime.now(UTC)
            await event_bus.publish(
                "venue_health",
                self._health[venue_id].model_dump(mode="json"),
            )
            logger.info("venue_blacklisted", venue_id=venue_id)

    async def unblacklist_venue(self, venue_id: str) -> None:
        if venue_id in self._health:
            self._health[venue_id].status = VenueStatus.HEALTHY
            self._health[venue_id].last_checked = datetime.now(UTC)
            await event_bus.publish(
                "venue_health",
                self._health[venue_id].model_dump(mode="json"),
            )
            logger.info("venue_unblacklisted", venue_id=venue_id)

    def _compute_health_score(self, venue_id: str, latency_ms: float) -> float:
        cfg = self._score_config
        w = cfg.weights
        stats = self._order_results[venue_id]
        total = max(stats["total"], 1)
        fill_rate = stats["fills"] / total
        reject_rate = stats["rejects"] / total

        total_checks = max(self._total_checks[venue_id], 1)
        uptime = self._successful_checks[venue_id] / total_checks

        latency_score = 1.0 - min(latency_ms / cfg.max_latency_ms, 1.0)
        fill_score = min(fill_rate, 1.0)
        reject_score = 1.0 - min(reject_rate, 1.0)
        uptime_score = min(uptime, 1.0)
        freshness_score = 1.0

        score = (
            w.latency * latency_score
            + w.fill_rate * fill_score
            + w.reject_rate * reject_score
            + w.uptime * uptime_score
            + w.freshness * freshness_score
        )
        return round(max(0.0, min(1.0, score)), 4)

    def _score_to_status(self, score: float, current: VenueStatus) -> VenueStatus:
        if current == VenueStatus.BLACKLISTED:
            return VenueStatus.BLACKLISTED
        cfg = self._score_config
        if score >= cfg.healthy_threshold:
            return VenueStatus.HEALTHY
        if score >= cfg.degraded_threshold:
            return VenueStatus.DEGRADED
        return VenueStatus.CRITICAL

    async def _check_venue(self, venue_id: str) -> None:
        profile = self._profiles[venue_id]
        url = f"http://{profile.host}:{profile.port}/health"
        self._total_checks[venue_id] += 1

        try:
            start = time.monotonic()
            resp = await self._http_client.get(url, timeout=5.0)
            latency_ms = (time.monotonic() - start) * 1000
            resp.raise_for_status()

            self._successful_checks[venue_id] += 1
            self._latency_buffer[venue_id].append(latency_ms)
            buf = self._latency_buffer[venue_id][-100:]
            self._latency_buffer[venue_id] = buf

            sorted_lat = sorted(buf)
            p50 = sorted_lat[len(sorted_lat) // 2] if buf else 0
            p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if buf else 0
            p99 = sorted_lat[int(len(sorted_lat) * 0.99)] if buf else 0

            stats = self._order_results[venue_id]
            total = max(stats["total"], 1)

            self._metrics[venue_id] = VenueMetrics(
                latency_p50_ms=round(p50, 2),
                latency_p95_ms=round(p95, 2),
                latency_p99_ms=round(p99, 2),
                fill_rate=round(stats["fills"] / total, 4),
                reject_rate=round(stats["rejects"] / total, 4),
                orders_last_5min=stats["total"],
                fills_last_5min=stats["fills"],
                rejects_last_5min=stats["rejects"],
            )

            score = self._compute_health_score(venue_id, p95)
            health = self._health[venue_id]
            new_status = self._score_to_status(score, health.status)

            health.health_score = score
            health.latency_ms = round(p95, 2)
            health.fill_rate = self._metrics[venue_id].fill_rate
            health.reject_rate = self._metrics[venue_id].reject_rate
            health.uptime = round(
                self._successful_checks[venue_id] / max(self._total_checks[venue_id], 1),
                4,
            )
            health.last_checked = datetime.now(UTC)
            health.error_count = 0

            if new_status != health.status:
                old = health.status
                health.status = new_status
                logger.info(
                    "venue_status_changed",
                    venue_id=venue_id,
                    old_status=old.value,
                    new_status=new_status.value,
                    score=score,
                )

            await event_bus.publish("venue_health", health.model_dump(mode="json"))

        except Exception:
            health = self._health[venue_id]
            if health.status != VenueStatus.BLACKLISTED:
                health.status = VenueStatus.DISCONNECTED
            health.health_score = 0.0
            health.error_count += 1
            health.last_checked = datetime.now(UTC)
            await event_bus.publish("venue_health", health.model_dump(mode="json"))

    async def start(self) -> None:
        self._running = True
        self._http_client = httpx.AsyncClient()
        logger.info("venue_health_monitor_started")

        while self._running:
            tasks = [self._check_venue(vid) for vid in self._profiles]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(settings.venue_health_check_interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._http_client:
            await self._http_client.aclose()
        logger.info("venue_health_monitor_stopped")
