"""Retry with exponential backoff for transient tool failures."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from ceap.domain.tools import TransientToolError

T = TypeVar("T")


RetryableError = TransientToolError  # raise to signal a transient failure the harness may retry


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    jitter: float = 0.1
    retry_on: tuple[type[BaseException], ...] = (RetryableError, TimeoutError, ConnectionError)

    def delay(self, attempt: int) -> float:
        raw = min(
            self.max_delay_seconds, self.base_delay_seconds * (self.backoff_multiplier ** (attempt - 1))
        )
        return raw * (1.0 + random.uniform(-self.jitter, self.jitter))  # noqa: S311 - jitter only


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except policy.retry_on as exc:
            if attempt >= policy.max_attempts:
                raise
            if on_retry:
                on_retry(attempt, exc)
            await asyncio.sleep(policy.delay(attempt))
