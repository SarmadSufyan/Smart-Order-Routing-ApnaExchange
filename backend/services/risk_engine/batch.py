"""Risk batch: pre-flight many orders at once, in parallel OS processes.

A *pre-flight* checks a list of orders (a TWAP's slices, a basket) WITHOUT
sending anything: each order meets the position it would face if every
earlier order in the list had filled. Two steps:

    1. plan (serial, O(n)): walk the list once, carrying the projected
       position and exposure forward -> one RiskContext per order
    2. evaluate (independent per order -> parallel): the same pure checks as
       the live gate (checks.py) + an optional Monte Carlo stress test

Why processes and not asyncio: asyncio interleaves waiting (network I/O) on
ONE core; CPU work in Python holds the GIL, so only separate processes run
it on several cores at once. Why not always: shipping an order to another
process costs more than the seven checks themselves (microseconds). The
pool pays off only for heavy per-order work — the stress test — and only
above a batch size measured by ``scripts/benchmark_risk_batch.py``. Below
that, the runner stays in-process.

Stress test (price risk of the resulting position): N random price paths over
the horizon, minute by minute (geometric Brownian motion, zero drift, the
volatility measured from our own live mid prices). Per path we keep the
DEEPEST point the position goes underwater — the loss limit is watched
continuously, so the worst moment matters, not just the end. Reported: the
loss not exceeded on 99% of paths (VaR) and the average of the worst 1%
(expected shortfall). Each order's paths come from its own seed, so the
pool and a single process give bit-identical numbers (tested).
"""

import asyncio
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Literal

from backend.shared.models.risk import RiskLimits

from .checks import CheckOutcome, OrderSpec, RiskContext, evaluate_order

Mode = Literal["auto", "serial", "pool"]


@dataclass(frozen=True, slots=True)
class StressParams:
    sigma_per_min: float  # standard deviation of 1-minute log returns
    horizon_min: int = 60
    paths: int = 5_000
    confidence: float = 0.99


@dataclass(slots=True)
class StressResult:
    position: float  # base units held after the order (signed)
    price: float
    var_usdt: float  # worst loss within the horizon, not exceeded on `confidence` of paths
    es_usdt: float  # average loss over the worst (1 - confidence) of paths
    paths: int
    horizon_min: int
    sigma_per_min: float


@dataclass(frozen=True, slots=True)
class BatchJob:
    index: int
    order: OrderSpec
    mid: float
    ctx: RiskContext
    projected_position: float  # after this order, if it and every earlier one filled
    stress: StressParams | None = None
    seed: int = 0


@dataclass(slots=True)
class BatchItem:
    index: int
    order: OrderSpec
    mid: float
    outcome: CheckOutcome
    projected_position: float
    stress: StressResult | None


@dataclass(slots=True)
class BatchReport:
    items: list[BatchItem]
    executor: str  # "in-process" | "pool (N processes)"
    elapsed_ms: float

    @property
    def all_approved(self) -> bool:
        return all(i.outcome.approved for i in self.items)

    @property
    def first_rejection(self) -> BatchItem | None:
        return next((i for i in self.items if not i.outcome.approved), None)


# ── pure functions (run in worker processes; unit-tested directly) ──────
def stress_position(position: float, price: float, params: StressParams, seed: int) -> StressResult:
    """Monte Carlo worst-point loss of holding ``position`` for the horizon."""
    if position == 0 or price <= 0 or params.sigma_per_min <= 0:
        return StressResult(position, price, 0.0, 0.0, 0, params.horizon_min, params.sigma_per_min)
    rng = random.Random(seed)
    gauss = rng.gauss
    sigma = params.sigma_per_min
    drift = -0.5 * sigma * sigma  # zero expected price change (a martingale)
    steps = params.horizon_min
    long = position > 0
    value = abs(position) * price
    worst: list[float] = []
    for _ in range(params.paths):
        x = lo = hi = 0.0  # log price relative to now; lowest / highest point seen
        for _ in range(steps):
            x += drift + sigma * gauss(0.0, 1.0)
            if x < lo:
                lo = x
            elif x > hi:
                hi = x
        # A long position is hurt by the lowest price, a short by the highest.
        worst.append(value * (1.0 - math.exp(lo)) if long else value * (math.exp(hi) - 1.0))
    worst.sort()
    k = min(int(params.confidence * len(worst)), len(worst) - 1)
    tail = worst[k:]
    return StressResult(position, price, round(worst[k], 4), round(sum(tail) / len(tail), 4),
                        params.paths, params.horizon_min, sigma)


def evaluate_jobs(jobs: list[BatchJob]) -> list[BatchItem]:
    """Evaluate independent jobs. Top-level so a worker process can import it."""
    out: list[BatchItem] = []
    for job in jobs:
        outcome = evaluate_order(job.ctx, job.order, job.mid)
        stress = (stress_position(job.projected_position, job.mid, job.stress, job.seed)
                  if job.stress is not None else None)
        out.append(BatchItem(job.index, job.order, job.mid, outcome, job.projected_position, stress))
    return out


def plan_jobs(
    orders: list[OrderSpec],
    mids: dict[str, float],
    *,
    limits: RiskLimits,
    kill_switch_active: bool,
    positions: dict[str, float],
    notionals: dict[str, float],
    stress: dict[str, StressParams] | None = None,
    seed: int = 0,
) -> list[BatchJob]:
    """Serial pass: the context each order meets if every earlier one filled.

    ``positions`` = net base units per symbol now; ``notionals`` = |USDT|
    exposure per symbol now (as the live gate counts it). Assuming earlier
    orders fill in full is the conservative choice for position limits.
    """
    pos = dict(positions)
    exposure = dict(notionals)
    jobs: list[BatchJob] = []
    for i, order in enumerate(orders):
        mid = mids.get(order.symbol, 0.0)
        ctx = RiskContext(limits=limits, kill_switch_active=kill_switch_active,
                          net_position=pos.get(order.symbol, 0.0), total_notional=sum(exposure.values()))
        signed = order.quantity if order.side == "BUY" else -order.quantity
        after = pos.get(order.symbol, 0.0) + signed
        pos[order.symbol] = after
        if mid > 0:
            exposure[order.symbol] = abs(after * mid)
        params = stress.get(order.symbol) if stress else None
        jobs.append(BatchJob(i, order, mid, ctx, after, params, seed + i))
    return jobs


def split_round_robin(jobs: list[BatchJob], parts: int) -> list[list[BatchJob]]:
    """Deal jobs out like cards so every worker gets a similar mix."""
    chunks = [jobs[i::parts] for i in range(max(1, min(parts, len(jobs))))]
    return [c for c in chunks if c]


def _warm_up() -> int:
    return os.getpid()


# ── runner ──────────────────────────────────────────────────────────────
class RiskBatchRunner:
    """Runs batch jobs in-process or on a warm pool of worker processes.

    ``auto`` uses the pool only for stress-tested batches of at least
    ``pool_min_orders`` orders (below that, process overhead wins — see the
    benchmark). In-process work with a stress test runs in a thread so the
    gateway's event loop keeps serving market data and orders meanwhile.
    """

    def __init__(self, workers: int = 0, pool_min_orders: int = 4) -> None:
        self.workers = workers or min(os.cpu_count() or 2, 4)
        self.pool_min_orders = pool_min_orders
        self._pool: ProcessPoolExecutor | None = None
        self._warming: asyncio.Task | None = None

    @property
    def pool_ready(self) -> bool:
        return self._pool is not None and self._warming is not None and self._warming.done()

    def start(self) -> None:
        """Spawn the workers in the background (≈1 s on Windows: each re-imports Python)."""
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=self.workers)
            self._warming = asyncio.create_task(self._warm())

    async def _warm(self) -> None:
        loop = asyncio.get_running_loop()
        await asyncio.gather(*(loop.run_in_executor(self._pool, _warm_up) for _ in range(self.workers)))

    async def stop(self) -> None:
        if self._warming is not None and not self._warming.done():
            self._warming.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        self._pool, self._warming = None, None

    def choose(self, jobs: list[BatchJob], mode: Mode = "auto") -> Literal["serial", "pool"]:
        if mode != "auto":
            return mode
        heavy = any(j.stress is not None for j in jobs)
        return "pool" if heavy and len(jobs) >= self.pool_min_orders else "serial"

    async def run(self, jobs: list[BatchJob], mode: Mode = "auto") -> BatchReport:
        start = time.perf_counter()
        if self.choose(jobs, mode) == "pool":
            try:
                items = await self._run_pool(jobs)
                return BatchReport(items, f"pool ({self.workers} processes)", _ms(start))
            except BrokenProcessPool:
                await self.stop()  # a worker died: rebuild next time, finish in-process now
        heavy = any(j.stress is not None for j in jobs)
        items = await asyncio.to_thread(evaluate_jobs, jobs) if heavy else evaluate_jobs(jobs)
        return BatchReport(items, "in-process", _ms(start))

    async def _run_pool(self, jobs: list[BatchJob]) -> list[BatchItem]:
        self.start()
        loop = asyncio.get_running_loop()
        parts = await asyncio.gather(*(
            loop.run_in_executor(self._pool, evaluate_jobs, chunk)
            for chunk in split_round_robin(jobs, self.workers)
        ))
        return sorted((item for part in parts for item in part), key=lambda i: i.index)


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
