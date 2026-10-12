"""Real-money ceilings: the risk limits in force never exceed the caps in .env (Phase D).

On testnet there are none. On real money (``EXCHANGE_MODE=mainnet``) the pre-trade engine clamps every limit it
holds to these caps (saved limits from an earlier run, a RAFT cluster's state, the defaults), and a change from
the Risk page that would go past one is refused before it is replicated. Raising a ceiling means editing .env and
restarting the gateway: a person at the machine, not a click or a stolen login.

Pure functions, tested directly (test_risk/test_ceilings.py).
"""

import math

from backend.shared.models.risk import RiskCeilings, RiskLimits

# limit field -> (what it is in plain words, the .env setting that caps it)
CAPPED: dict[str, tuple[str, str]] = {
    "max_order_notional": ("per-order limit", "MAINNET_MAX_ORDER_USDT"),
    "max_algo_notional_usdt": ("per-algo-order limit", "MAINNET_MAX_ALGO_USDT"),
    "max_notional_exposure": ("total exposure limit", "MAINNET_MAX_EXPOSURE_USDT"),
    "max_trading_loss_usdt": ("trading-loss limit", "MAINNET_MAX_TRADING_LOSS_USDT"),
}


def _value(limits: RiskLimits, field: str) -> float:
    value = float(getattr(limits, field))
    # A trading-loss limit of 0 means "off": the loosest value there is.
    return math.inf if field == "max_trading_loss_usdt" and value <= 0 else value


def apply_ceilings(limits: RiskLimits, ceilings: RiskCeilings | None) -> RiskLimits:
    """The limits with every capped field lowered to its ceiling (unchanged without ceilings)."""
    if ceilings is None:
        return limits
    lowered = {f: getattr(ceilings, f) for f in CAPPED if _value(limits, f) > getattr(ceilings, f)}
    return limits.model_copy(update=lowered) if lowered else limits


def ceiling_breaches(limits: RiskLimits, ceilings: RiskCeilings | None) -> list[str]:
    """Each limit above its real-money ceiling, in plain words (empty = all within)."""
    if ceilings is None:
        return []
    out = []
    for field, (what, env) in CAPPED.items():
        cap, value = getattr(ceilings, field), _value(limits, field)
        if value > cap:
            shown = "off" if math.isinf(value) else f"${value:,.2f}"
            out.append(f"the {what} {shown} is above the real-money ceiling ${cap:,.2f} ({env} in .env)")
    return out
