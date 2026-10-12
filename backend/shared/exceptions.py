class DomainError(Exception):
    def __init__(self, message: str, code: str):
        self.message = message
        self.code = code
        super().__init__(message)


class OrderNotFoundError(DomainError):
    def __init__(self, order_id: str):
        super().__init__(f"Order {order_id} not found", "ORDER_NOT_FOUND")


class VenueUnavailableError(DomainError):
    def __init__(self, venue_id: str):
        super().__init__(f"Venue {venue_id} is unavailable", "VENUE_UNAVAILABLE")


class VenueNotFoundError(DomainError):
    def __init__(self, venue_id: str):
        super().__init__(f"Venue {venue_id} not found", "VENUE_NOT_FOUND")


class InvalidStateTransitionError(DomainError):
    def __init__(self, order_id: str, from_state: str, to_state: str, reason: str | None = None):
        message = f"Invalid transition {from_state} -> {to_state} for order {order_id}"
        if reason:
            message = f"Order {order_id} can't move {from_state} -> {to_state}: {reason}"
        super().__init__(message, "INVALID_STATE_TRANSITION")


class RiskCheckFailedError(DomainError):
    def __init__(self, reason: str):
        super().__init__(f"Risk check failed: {reason}", "RISK_CHECK_FAILED")


class RiskLimitAboveCeilingError(DomainError):
    def __init__(self, breaches: list[str]):
        super().__init__(
            "Refused on real money: " + "; ".join(breaches)
            + ". To go higher, raise the ceiling in .env and restart the gateway.",
            "RISK_LIMIT_ABOVE_CEILING",
        )


class KillSwitchActiveError(DomainError):
    def __init__(self):
        super().__init__(
            "Kill switch is active - all orders rejected", "KILL_SWITCH_ACTIVE"
        )
