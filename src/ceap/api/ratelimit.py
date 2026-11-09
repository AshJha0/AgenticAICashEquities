"""Per-principal token-bucket rate limit for the endpoints that start model-backed tasks (A10).

Every ``POST /investigations`` and ``POST /research`` takes one token from the caller's bucket. A
bucket holds ``burst`` tokens and refills at ``per_minute / 60`` tokens a second. An empty bucket
answers ``429 Too Many Requests`` with a ``Retry-After`` header, before any task or model call is
made. The per-task LLM caps in ``ceap.llm.router`` bound what one task can spend; this bounds how
many tasks one principal can start.

``CEAP_RATE_LIMIT_PER_MINUTE`` (default 60) and ``CEAP_RATE_LIMIT_BURST`` (default 20) configure it;
``CEAP_RATE_LIMIT_PER_MINUTE=off`` turns it off. The buckets live in process memory, so behind
several replicas the effective limit is per replica.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ceap.config import SettingsError

PER_MINUTE_ENV = "CEAP_RATE_LIMIT_PER_MINUTE"
BURST_ENV = "CEAP_RATE_LIMIT_BURST"
DEFAULT_PER_MINUTE = 60.0
DEFAULT_BURST = 20


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(
        self,
        per_minute: float,
        burst: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_principals: int = 10_000,
    ) -> None:
        if per_minute <= 0 or burst < 1:
            raise ValueError("per_minute must be > 0 and burst >= 1")
        self.rate = per_minute / 60.0
        self.burst = burst
        self._clock = clock
        self._max = max_principals
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def acquire(self, principal: str) -> float:
        """Take one token. Returns 0.0 when allowed, else the seconds until a token is available."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(principal)
            if bucket is None:
                if len(self._buckets) >= self._max:  # drop full (idle) buckets first
                    for key in [k for k, b in self._buckets.items() if b.tokens >= self.burst][: self._max // 10 or 1]:
                        del self._buckets[key]
                bucket = self._buckets[principal] = _Bucket(float(self.burst), now)
            bucket.tokens = min(float(self.burst), bucket.tokens + (now - bucket.updated) * self.rate)
            bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return 0.0
            return (1.0 - bucket.tokens) / self.rate


def from_env() -> RateLimiter | None:
    raw = (os.environ.get(PER_MINUTE_ENV) or "").strip().lower()
    if raw in ("off", "none", "disabled"):
        return None
    try:
        per_minute = float(raw) if raw else DEFAULT_PER_MINUTE
        burst = int(os.environ.get(BURST_ENV) or DEFAULT_BURST)
    except ValueError as exc:
        raise SettingsError(f"{PER_MINUTE_ENV} / {BURST_ENV} must be numbers (or 'off')") from exc
    if per_minute <= 0 or burst < 1:
        raise SettingsError(f"{PER_MINUTE_ENV} must be > 0 and {BURST_ENV} >= 1")
    return RateLimiter(per_minute, burst)


def retry_after_header(seconds: float) -> str:
    return str(max(1, math.ceil(seconds)))
