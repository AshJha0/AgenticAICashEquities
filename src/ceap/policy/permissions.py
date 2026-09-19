"""Role-based capabilities."""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    TRADER = "trader"
    QUANT = "quant"
    ENGINEER = "engineer"
    ADMIN = "admin"


ROLE_CAPABILITIES: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"tools:read", "investigate:read"}),
    Role.TRADER: frozenset({"tools:read", "investigate", "investigate:read", "risk:medium"}),
    Role.QUANT: frozenset({"tools:read", "investigate", "investigate:read", "risk:medium", "data:export"}),
    Role.ENGINEER: frozenset({"tools:read", "investigate", "investigate:read", "engineering:deep"}),
    Role.ADMIN: frozenset(
        {
            "tools:read",
            "tools:write",
            "investigate",
            "investigate:read",
            "risk:medium",
            "risk:high",
            "data:export",
            "engineering:deep",
            "approvals:decide",
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
