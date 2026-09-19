"""API-key authentication and capability-based authorisation.

Principals are identified by a stable, non-secret label derived from the
key (a truncated SHA-256), never by any part of the key itself, because
the principal name is written into approval requests, traces and the
cancel reason - places other principals can read.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from ceap.policy.permissions import capabilities_for

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_UNAUTHORISED_HEADERS = {"WWW-Authenticate": "ApiKey"}


@dataclass(frozen=True)
class Principal:
    name: str
    roles: frozenset[str]

    @property
    def capabilities(self) -> frozenset[str]:
        return capabilities_for(self.roles)


def principal_label(role: str, api_key: str) -> str:
    return f"{role}:{hashlib.sha256(api_key.encode()).hexdigest()[:8]}"


def _lookup(api_keys: dict[str, str], presented: str) -> str | None:
    """Constant-time comparison against every configured key."""
    match: str | None = None
    for key, role in api_keys.items():
        if hmac.compare_digest(key.encode(), presented.encode()):
            match = role
    return match


async def current_principal(request: Request, api_key: str | None = Depends(api_key_header)) -> Principal:
    settings = request.app.state.platform.settings
    if not api_key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing X-API-Key header", headers=_UNAUTHORISED_HEADERS
        )
    role = _lookup(settings.api_keys, api_key)
    if role is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key", headers=_UNAUTHORISED_HEADERS)
    return Principal(name=principal_label(role, api_key), roles=frozenset({role}))


def require_capability(capability: str) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if capability not in principal.capabilities:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"role {sorted(principal.roles)} lacks capability {capability}"
            )
        return principal

    return dependency
