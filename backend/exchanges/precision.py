"""Lot-size / tick-size arithmetic.

Exchanges reject quantities that are not an exact multiple of the symbol's
step size ("Filter failure: LOT_SIZE" on Binance). Binary floats cannot
represent most decimal steps exactly (0.1 + 0.2 != 0.3), so the rounding is
done in ``Decimal`` and only converted back to float at the end.
"""

from decimal import ROUND_DOWN, ROUND_UP, Decimal


def floor_to_step(value: float, step: float) -> float:
    """Round ``value`` DOWN to the nearest multiple of ``step``.

    Always rounds down so we never send more than was asked for.

    Args:
        value: Quantity to round, e.g. ``0.0012345``.
        step: Exchange lot step, e.g. ``0.00001``. ``<= 0`` disables rounding.

    Returns:
        The rounded quantity, e.g. ``0.00123``.
    """
    if step <= 0 or value <= 0:
        return max(value, 0.0)
    d_step = Decimal(str(step))
    units = (_denoise(value) / d_step).to_integral_value(rounding=ROUND_DOWN)
    return float(units * d_step)


def ceil_to_step(value: float, step: float) -> float:
    """Round ``value`` UP to the nearest multiple of ``step``."""
    if step <= 0 or value <= 0:
        return max(value, 0.0)
    d_step = Decimal(str(step))
    units = (_denoise(value) / d_step).to_integral_value(rounding=ROUND_UP)
    return float(units * d_step)


def _denoise(value: float) -> Decimal:
    """Strip binary-float noise before rounding to a step.

    Summing on-grid quantities in floats can land just below the grid
    (0.1 + 0.7 == 0.7999999999999999), and flooring that loses a whole step —
    found live: a 0.08 BNB slice went out as 0.0799. No exchange step is
    finer than 1e-10, so 12 decimals keeps every real digit.
    """
    return Decimal(str(round(value, 12)))


def is_close(a: float, b: float, rel: float = 1e-9) -> bool:
    """Float equality with a relative tolerance (quantities after rounding)."""
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))
