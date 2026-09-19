"""Small shared helpers for the domain layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def new_id(prefix: str = "") -> str:
    """Return a short, globally unique identifier with an optional prefix.

    Prefixes make evidence and finding ids readable in reports
    (e.g. ``EXEC-3f2a9c``, ``MARKET-8b11d0``).
    """
    token = uuid.uuid4().hex[:8]
    return f"{prefix}-{token}" if prefix else token


def utc_now() -> datetime:
    """Timezone-aware UTC ``now``."""
    return datetime.now(tz=UTC)


def ensure_utc(ts: datetime) -> datetime:
    """Return ``ts`` as an aware UTC datetime (naive input is assumed UTC)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def bps(numerator: float, denominator: float) -> float:
    """Express ``numerator / denominator`` in basis points (1e4 scale)."""
    if denominator == 0:
        return 0.0
    return numerator / denominator * 10_000.0


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / datetimes / enums into JSON-able values.

    Used at the MCP and API boundary so the domain objects stay plain
    dataclasses while transport layers speak JSON.
    """
    import dataclasses
    from enum import Enum

    if isinstance(obj, Enum):  # before str: str-backed enums must serialise to their value
        return obj.value
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    try:  # numpy scalars
        import numpy as np

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:  # pragma: no cover
        pass
    return str(obj)
