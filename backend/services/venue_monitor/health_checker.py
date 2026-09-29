"""Scores every live exchange connection and decides which venues are routable.

Health score (ALGORITHMS.md §2) = weighted sum of five signals in [0, 1]:

    latency    REST round-trip p95, measured by pinging the venue
    fill_rate  fills / venue-attributable outcomes
    reject     1 - venue-fault rejects / venue-attributable outcomes
    uptime     successful pings / all pings
    freshness  how recently the market-data feed spoke, x share of books synced

Two rules keep it honest against a real exchange:
  * Cold start — a venue with no order history gets neutral fill/reject
    scores, not zero; absence of evidence is not evidence of failure.
  * Only venue faults count — a reject caused by the client (below minimum
    lot, insufficient balance, no API keys) says nothing about the venue.
"""

import asyncio
from collections import defaultdict
from datetime import UTC, datetime

from backend.exchanges.base import ExchangeAdapter
from backend.exchanges.registry import ExchangeRegistry
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

LATENCY_WINDOW = 30  # pings kept for percentile stats
PING_FAILURES_BEFORE_CRITICAL = 3


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[idx]


class VenueHealthMonitor:
    def __init__(
        self,
        registry: ExchangeRegistry,
        check_interval_s: float = 2.0,
        feed_staleness_threshold_ms: int = 10_000,
    ) -> None:
        self._registry = registry
        self._interval_s = check_interval_s
        self._staleness_ms = feed_staleness_threshold_ms
        self._score_config = HealthScoreConfig()
        self._profiles: dict[str, VenueProfile] = {}
        self._health: dict[str, VenueHealth] = {}
        self._metrics: dict[str, VenueMetrics] = {}
        self._latency: dict[str, list[float]] = defaultdict(list)
        self._order_results: dict[str, dict[str, int]] = defaultdict(
            lambda: {"fills": 0, "rejects": 0, "total": 0}
        )
        self._successful_checks: dict[str, int] = defaultdict(int)
        self._total_checks: dict[str, int] = defaultdict(int)
        self._ping_failures: dict[str, int] = defaultdict(int)
        self._running = False
        self._coherence = None  # set_coherence(); per-symbol execution-book check

        for adapter in registry.all():
            vid = adapter.venue_id
            self._profiles[vid] = VenueProfile(
                venue_id=vid,
                name=adapter.name,
                cloud=adapter.display_label,
                host=adapter.api_host,
                port=443,
                base_latency_ms=0.0,
                personality="live_exchange",
            )
            self._health[vid] = VenueHealth(
                venue_id=vid,
                name=adapter.name,
                cloud=adapter.display_label,
                mode=adapter.mode,
                data_mode=adapter.data_mode,
                status=VenueStatus.DISCONNECTED,
                execution_available=adapter.execution_available,
                trading_enabled=adapter.can_trade,
                execution_note=adapter.execution_note,
            )
            self._metrics[vid] = VenueMetrics()

    def set_coherence(self, coherence_monitor) -> None:
        self._coherence = coherence_monitor

    # ── reads ───────────────────────────────────────────────────────
    def get_all_health(self) -> list[VenueHealth]:
        return list(self._health.values())

    def get_venue_health(self, venue_id: str) -> VenueHealth | None:
        return self._health.get(venue_id)

    def get_venue_profile(self, venue_id: str) -> VenueProfile | None:
        return self._profiles.get(venue_id)

    def get_venue_metrics(self, venue_id: str) -> VenueMetrics | None:
        return self._metrics.get(venue_id)

    def get_routable_venues(self) -> list[str]:
        """Venues whose DATA is healthy enough for the NBBO.

        Whether a venue can also receive orders is a separate question —
        see ``RoutingEngine.executable_venues``.
        """
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

    # ── writes ──────────────────────────────────────────────────────
    def record_order_result(self, venue_id: str, filled: bool, venue_fault: bool = True) -> None:
        """Count an execution outcome. Client-side rejects are ignored."""
        stats = self._order_results[venue_id]
        if filled:
            stats["fills"] += 1
            stats["total"] += 1
        elif venue_fault:
            stats["rejects"] += 1
            stats["total"] += 1

    async def blacklist_venue(self, venue_id: str) -> None:
        if venue_id in self._health:
            self._health[venue_id].status = VenueStatus.BLACKLISTED
            self._health[venue_id].last_checked = datetime.now(UTC)
            await event_bus.publish("venue_health", self._health[venue_id].model_dump(mode="json"))
            logger.info("venue_blacklisted", venue_id=venue_id)

    async def unblacklist_venue(self, venue_id: str) -> None:
        if venue_id in self._health:
            # Re-enter as DISCONNECTED; the next check assigns the real status.
            self._health[venue_id].status = VenueStatus.DISCONNECTED
            self._health[venue_id].last_checked = datetime.now(UTC)
            await event_bus.publish("venue_health", self._health[venue_id].model_dump(mode="json"))
            logger.info("venue_unblacklisted", venue_id=venue_id)

    # ── scoring ─────────────────────────────────────────────────────
    def _compute_health_score(self, venue_id: str, latency_ms: float, freshness: float) -> float:
        cfg = self._score_config
        w = cfg.weights
        stats = self._order_results[venue_id]
        if stats["total"] == 0:
            fill_rate, reject_rate = 1.0, 0.0  # cold start: neutral
        else:
            fill_rate = stats["fills"] / stats["total"]
            reject_rate = stats["rejects"] / stats["total"]
        uptime = self._successful_checks[venue_id] / max(self._total_checks[venue_id], 1)
        latency_score = 1.0 - min(latency_ms / cfg.max_latency_ms, 1.0)

        score = (
            w.latency * latency_score
            + w.fill_rate * fill_rate
            + w.reject_rate * (1.0 - reject_rate)
            + w.uptime * uptime
            + w.freshness * freshness
        )
        return round(max(0.0, min(1.0, score)), 4)

    def _score_to_status(self, score: float) -> VenueStatus:
        cfg = self._score_config
        if score >= cfg.healthy_threshold:
            return VenueStatus.HEALTHY
        if score >= cfg.degraded_threshold:
            return VenueStatus.DEGRADED
        return VenueStatus.CRITICAL

    async def _check_venue(self, adapter: ExchangeAdapter) -> None:
        vid = adapter.venue_id
        health = self._health[vid]
        self._total_checks[vid] += 1

        try:
            latency_ms = await adapter.ping()
            self._successful_checks[vid] += 1
            self._ping_failures[vid] = 0
            buf = self._latency[vid]
            buf.append(latency_ms)
            del buf[:-LATENCY_WINDOW]
            health.error_count = 0
        except Exception as exc:  # noqa: BLE001
            self._ping_failures[vid] += 1
            health.error_count += 1
            logger.warning("venue_ping_failed", venue_id=vid, error=str(exc)[:160])

        lat = sorted(self._latency[vid])
        p50, p95, p99 = _percentile(lat, 0.5), _percentile(lat, 0.95), _percentile(lat, 0.99)
        stats = self._order_results[vid]
        total = max(stats["total"], 1)
        self._metrics[vid] = VenueMetrics(
            latency_p50_ms=round(p50, 2),
            latency_p95_ms=round(p95, 2),
            latency_p99_ms=round(p99, 2),
            fill_rate=round(stats["fills"] / total, 4),
            reject_rate=round(stats["rejects"] / total, 4),
            orders_last_5min=stats["total"],
            fills_last_5min=stats["fills"],
            rejects_last_5min=stats["rejects"],
        )

        feed = adapter.feed_status()
        if feed.connected and feed.last_message_age_ms is not None and feed.books_total:
            age_score = 1.0 - min(feed.last_message_age_ms / self._staleness_ms, 1.0)
            freshness = age_score * feed.books_synced / feed.books_total
        else:
            freshness = 0.0

        if not feed.connected or feed.books_synced == 0:
            score, status = 0.0, VenueStatus.DISCONNECTED
        elif self._ping_failures[vid] >= PING_FAILURES_BEFORE_CRITICAL:
            score, status = 0.1, VenueStatus.CRITICAL
        else:
            score = self._compute_health_score(vid, p95, freshness)
            status = self._score_to_status(score)
            if adapter.degraded and status == VenueStatus.HEALTHY:
                status, score = VenueStatus.DEGRADED, min(score, 0.5)

        if health.status == VenueStatus.BLACKLISTED:
            status = VenueStatus.BLACKLISTED  # only an operator lifts a blacklist

        health.health_score = score
        health.latency_ms = round(p95, 2)
        health.latency_p50_ms = round(p50, 2)
        health.latency_p99_ms = round(p99, 2)
        health.fill_rate = self._metrics[vid].fill_rate
        health.reject_rate = self._metrics[vid].reject_rate
        health.orders_total = stats["total"]
        health.uptime = round(self._successful_checks[vid] / max(self._total_checks[vid], 1), 4)
        health.last_checked = datetime.now(UTC)
        health.feed_connected = feed.connected
        health.feed_age_ms = feed.last_message_age_ms
        health.books_synced = feed.books_synced
        health.books_total = feed.books_total
        health.resyncs = feed.resyncs
        health.reconnects = feed.reconnects
        health.degraded_mode = adapter.degraded
        health.trading_enabled = adapter.can_trade
        health.execution_note = adapter.execution_note
        if self._coherence is not None and adapter.execution_available:
            health.execution_coherence = self._coherence.venue_states(vid)

        if status != health.status:
            logger.info(
                "venue_status_changed",
                venue_id=vid,
                old_status=health.status.value,
                new_status=status.value,
                score=score,
            )
            health.status = status

        await event_bus.publish("venue_health", health.model_dump(mode="json"))

    async def start(self) -> None:
        self._running = True
        logger.info("venue_health_monitor_started", venues=list(self._profiles))
        while self._running:
            await asyncio.gather(
                *(self._check_venue(a) for a in self._registry.all()), return_exceptions=True
            )
            await asyncio.sleep(self._interval_s)

    async def stop(self) -> None:
        self._running = False
        logger.info("venue_health_monitor_stopped")
