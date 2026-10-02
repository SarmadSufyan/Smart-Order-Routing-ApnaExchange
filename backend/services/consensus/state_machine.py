"""What the RAFT cluster agrees on: the risk controls that must never differ
between processes — the kill switch, the risk limits, the venue blacklist.

Every node applies the same committed commands in the same order, so every
node ends up with the same state. ``apply`` is deterministic on purpose: no
clock, no randomness — timestamps travel inside the command. Commands SET a
value (never "toggle" or "add 1"), so a command applied twice — a client
retrying after a lost reply — changes nothing.

Commands:
    {"op": "noop"}                                         (new leader's first entry)
    {"op": "set_kill_switch", "state": KillSwitchState}
    {"op": "set_risk_limits", "limits": RiskLimits, "actor"}
    {"op": "set_blacklist", "venue_id", "on": bool, "actor", "at"}
"""

from typing import Any

OPS = {"noop", "set_kill_switch", "set_risk_limits", "set_blacklist"}


class RiskStateMachine:
    def __init__(self) -> None:
        self.kill_switch: dict[str, Any] | None = None  # None = never set through the cluster
        self.risk_limits: dict[str, Any] | None = None
        self.blacklist: dict[str, dict[str, Any]] = {}
        self.applied_index = 0
        self.applied_term = 0

    def apply(self, index: int, term: int, command: dict[str, Any]) -> dict[str, Any]:
        op = command.get("op")
        error = None
        if op == "set_kill_switch" and isinstance(command.get("state"), dict):
            self.kill_switch = command["state"]
        elif op == "set_risk_limits" and isinstance(command.get("limits"), dict):
            self.risk_limits = command["limits"]
        elif op == "set_blacklist" and isinstance(command.get("venue_id"), str):
            if command.get("on"):
                self.blacklist[command["venue_id"]] = {"by": command.get("actor"), "at": command.get("at")}
            else:
                self.blacklist.pop(command["venue_id"], None)
        elif op != "noop":
            error = f"unknown or malformed command: {op!r}"  # every node ignores it the same way
        self.applied_index, self.applied_term = index, term
        return {"index": index, "term": term, "error": error}

    def snapshot(self) -> dict[str, Any]:
        return {
            "applied_index": self.applied_index,
            "applied_term": self.applied_term,
            "kill_switch": self.kill_switch,
            "risk_limits": self.risk_limits,
            "blacklist": dict(self.blacklist),
        }
