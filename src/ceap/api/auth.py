"""API-key authentication and capability-based authorisation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from ceap.policy.permissions import capabilities_for

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Principal:
    name: str
    roles: frozenset[str]

    @property
    def capabilities(self) -> frozenset[str]:
        return capabilities_for(self.roles)


async def current_principal(request: Request, api_key: str | None = Depends(api_key_header)) -> Principal:
    settings = request.app.state.platform.settings
    if not api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key header")
    role = settings.api_keys.get(api_key)
    if role is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return Principal(name=f"{role}:{api_key[:6]}", roles=frozenset({role}))


def require_capability(capability: str) -> Callable[[Principal], Principal]:
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if capability not in principal.capabilities:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"role {sorted(principal.roles)} lacks capability {capability}"
            )
        return principal

    return dependency
