"""Background tasks the API runs in ``lifespan`` (0.9.0): event-loop lag gauge (G9), approval sweep (G8).

Event-loop lag. Plain idea: if the loop is free, ``await asyncio.sleep(0.5)`` wakes up 0.5 s later;
if something synchronous holds the loop, it wakes up late, and the lateness is the lag every request
and every approval also suffers. Formally, with ``t0`` the monotonic time before the sleep and
``t1`` after it, ``lag = max(0, t1 - t0 - interval)``, observed into the summary
``ceap_event_loop_lag_ms`` (exported as ``ceap_event_loop_lag_seconds``). Worked example: a tool that
runs ``time.sleep(0.3)`` on the loop while the monitor is sleeping makes that wake-up about 300 ms
late, so one observation of roughly 0.3 s (``tests/unit/test_r090_platform_ops.py``).

Approval sweep. A waiting step expires its own approval after ``CEAP_APPROVAL_TIMEOUT_S``. A row whose
waiter died without a process restart would stay PENDING for ever, so every ``CEAP_APPROVAL_SWEEP_S``
seconds (default 60) the sweep expires PENDING rows older than ``timeout + interval`` that no live
waiter in this process holds; each expiry is audited and counted (``ceap_approvals_swept_total``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timedelta
from typing import Any

from ceap.domain.common import utc_now
from ceap.observability.metrics import metrics

log = logging.getLogger(__name__)

LAG_INTERVAL_S = 0.5
SWEEP_ENV = "CEAP_APPROVAL_SWEEP_S"
SWEEP_REASON = "ttl sweep: no live waiter after the approval timeout"


class LoopLagMonitor:
    """Measures how late the loop wakes up; ``last_ms`` and ``max_ms`` are for /health and tests."""

    def __init__(self, interval_s: float = LAG_INTERVAL_S) -> None:
        self.interval_s = interval_s
        self.last_ms = 0.0
        self.max_ms = 0.0
        self.samples = 0

    async def run(self) -> None:
        while True:
            t0 = time.perf_counter()
            await asyncio.sleep(self.interval_s)
            lag_ms = max(0.0, (time.perf_counter() - t0 - self.interval_s) * 1000.0)
            self.last_ms = lag_ms
            self.max_ms = max(self.max_ms, lag_ms)
            self.samples += 1
            metrics.observe("ceap_event_loop_lag_ms", lag_ms)


def sweep_interval_s() -> float:
    try:
        value = float(os.getenv(SWEEP_ENV) or 60.0)
    except ValueError:
        value = 60.0
    return max(0.05, value)


def sweep_approvals_once(platform: Any, interval_s: float) -> list[str]:
    """One sweep: expire orphaned PENDING approvals (see the module docstring)."""
    store = getattr(platform, "approval_store", None)
    if store is None or not hasattr(store, "expire_stale"):
        return []
    timeout = float(platform.settings.approval_timeout_s)
    live = frozenset(a.id for a in platform.approvals.pending())
    cutoff = utc_now() - timedelta(seconds=timeout + interval_s)
    ids: list[str] = store.expire_stale(cutoff, SWEEP_REASON, keep=live)
    for approval_id in ids:
        platform.audit("timeout", "approval.expired", approval_id, {"reason": SWEEP_REASON})
        metrics.inc("ceap_approvals_swept_total")
    if ids:
        log.warning("approval sweep expired %d orphaned approval(s)", len(ids))
    return ids


async def approval_sweeper(platform: Any, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await asyncio.to_thread(sweep_approvals_once, platform, interval_s)
        except Exception:  # noqa: BLE001 - a failed sweep is logged and retried next interval
            log.exception("approval sweep failed")
