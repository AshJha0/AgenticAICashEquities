"""Cooperative cancellation."""

from __future__ import annotations

import asyncio


class TaskCancelled(RuntimeError):  # noqa: N818 - domain vocabulary, not an error suffix
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason: str | None = None

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TaskCancelled(self.reason or "cancelled")

    async def wait(self) -> None:
        await self._event.wait()
