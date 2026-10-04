"""Basket pre-flight for the API: live prices + measured volatility in,
a per-order verdict out. Nothing is sent to any exchange.

The work itself is ``PreTradeRiskEngine.preflight`` (the risk batch); this
service only gathers what it needs and explains the result.
"""

from backend.shared.events.event_bus import audit
from backend.shared.exceptions import DomainError
from backend.shared.models.risk import (
    PreflightItemResult,
    PreflightRequest,
    PreflightResponse,
    StressSummary,
)

from .batch import StressParams
from .checks import OrderSpec


class PreflightSymbolError(DomainError):
    def __init__(self, symbol: str, supported: list[str]):
        super().__init__(f"Unsupported symbol {symbol}. Tradable: {', '.join(supported)}", "UNSUPPORTED_SYMBOL")


class PreflightService:
    def __init__(self, risk_engine, aggregator, symbols: list[str], volatility,
                 stress_paths: int = 5_000, stress_horizon_min: int = 60) -> None:
        self._risk = risk_engine
        self._aggregator = aggregator
        self._symbols = symbols
        self._vol = volatility
        self._paths = stress_paths
        self._horizon = stress_horizon_min

    async def run(self, req: PreflightRequest, actor: str) -> PreflightResponse:
        specs = [OrderSpec(o.symbol.upper(), o.side, o.quantity) for o in req.orders]
        for spec in specs:
            if spec.symbol not in self._symbols:
                raise PreflightSymbolError(spec.symbol, self._symbols)

        notes: list[str] = []
        mids: dict[str, float] = {}
        for symbol in dict.fromkeys(s.symbol for s in specs):
            nbbo = await self._aggregator.compute_nbbo(symbol)
            if nbbo and nbbo.best_bid and nbbo.best_ask:
                mids[symbol] = (nbbo.best_bid + nbbo.best_ask) / 2
            else:
                notes.append(f"No live price for {symbol}: its USDT checks were skipped (an order would be "
                             "rejected for missing market data)")

        stress: dict[str, StressParams] | None = None
        if req.stress:
            stress = {}
            for symbol in mids:
                sigma = self._vol.sigma(symbol)
                if sigma is None:
                    notes.append(f"No stress test for {symbol} yet: only {self._vol.samples(symbol)} live price "
                                 "samples (needs about a minute after start-up)")
                else:
                    stress[symbol] = StressParams(sigma, self._horizon, self._paths)

        report = await self._risk.preflight(specs, mids, stress=stress, mode=req.mode)
        results = [
            PreflightItemResult(
                index=i.index, symbol=i.order.symbol, side=i.order.side, quantity=i.order.quantity,
                mid_price=round(i.mid, 8), result=i.outcome.result, checks_passed=i.outcome.passed,
                checks_failed=i.outcome.failed, message=i.outcome.message,
                position_after=round(i.projected_position, 12),
                stress=StressSummary(
                    position_after=round(i.stress.position, 12), var_99_usdt=i.stress.var_usdt,
                    expected_shortfall_usdt=i.stress.es_usdt, horizon_min=i.stress.horizon_min,
                    paths=i.stress.paths, sigma_per_min_pct=round(i.stress.sigma_per_min * 100, 5),
                ) if i.stress is not None else None,
            )
            for i in report.items
        ]
        approved = sum(1 for r in results if r.result.value == "APPROVED")
        if any(r.stress for r in results):
            notes.append(f"Stress test: {self._paths:,} simulated price paths per order over {self._horizon} min, "
                         "volatility measured from our own live mid prices; losses are the deepest point "
                         "within the horizon")
        await audit(
            "risk_preflight",
            f"Pre-flight of {len(results)} order(s) by {actor}: {approved} pass, {len(results) - approved} fail"
            f"{' + stress test' if stress else ''} — {report.executor}, {report.elapsed_ms:g} ms (nothing sent)",
            actor=actor,
            payload={"orders": len(results), "approved": approved, "stress": bool(stress),
                     "executor": report.executor, "elapsed_ms": report.elapsed_ms},
        )
        return PreflightResponse(
            all_approved=approved == len(results), approved=approved, rejected=len(results) - approved,
            executor=report.executor, elapsed_ms=report.elapsed_ms, notes=notes, results=results,
        )
