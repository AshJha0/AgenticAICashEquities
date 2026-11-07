"""Role-based capabilities."""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    TRADER = "trader"
    QUANT = "quant"
    ENGINEER = "engineer"
    APPROVER = "approver"
    ADMIN = "admin"
    AUDITOR = "auditor"


#: read any principal's tasks, results and traces (R4 item 4.3); without it a reader sees only its own
READ_ANY = "tasks:read-any"
#: scrape ``/metrics`` (R4 item 4.6)
METRICS_READ = "metrics:read"


ROLE_CAPABILITIES: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"tools:read", "investigate:read", "research:read"}),
    Role.TRADER: frozenset({"tools:read", "investigate", "investigate:read", "research:read", "risk:medium"}),
    Role.QUANT: frozenset(
        {
            "tools:read",
            "investigate",
            "investigate:read",
            "research",
            "research:read",
            "risk:medium",
            "data:export",
        }
    ),
    Role.ENGINEER: frozenset(
        {"tools:read", "investigate", "investigate:read", "research:read", "engineering:deep", METRICS_READ}
    ),
    # four-eyes (PLAN 1.3, D1): the only role that decides approvals; it cannot investigate or trade
    Role.APPROVER: frozenset(
        {"tools:read", "investigate:read", "research:read", "approvals:decide", READ_ANY}
    ),
    # read-only oversight (R4 item 4.3): every principal's tasks, results, traces and metrics; no actions
    Role.AUDITOR: frozenset({"tools:read", "investigate:read", "research:read", READ_ANY, METRICS_READ}),
    Role.ADMIN: frozenset(
        {
            "tools:read",
            "tools:write",
            "investigate",
            "investigate:read",
            "research",
            "research:read",
            "trading:execute",
            "risk:medium",
            "risk:high",
            "data:export",
            "engineering:deep",
            "tasks:cancel-any",
            READ_ANY,
            METRICS_READ,
        }
    ),
}


def capabilities_for(roles: set[str] | frozenset[str] | list[str]) -> frozenset[str]:
    caps: set[str] = set()
    for r in roles:
        try:
            caps |= ROLE_CAPABILITIES[Role(r)]
        except ValueError:
            continue
    return frozenset(caps)
