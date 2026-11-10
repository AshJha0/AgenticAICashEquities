"""Coverage for the 0.9.0 approval sweep helpers in ``ceap.api.loopmon`` (no real sleeping)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from ceap.api import loopmon
from ceap.domain.common import utc_now


@pytest.mark.parametrize(
    ("raw", "want"), [(None, 60.0), ("", 60.0), ("abc", 60.0), ("2.5", 2.5), ("0", 0.05), ("0.001", 0.05)]
)
def test_sweep_interval_parsing_and_floor(monkeypatch, raw, want):
    # unset, empty or unparsable: 60 s; anything below the 0.05 s floor is raised to it
    if raw is None:
        monkeypatch.delenv(loopmon.SWEEP_ENV, raising=False)
    else:
        monkeypatch.setenv(loopmon.SWEEP_ENV, raw)
    assert loopmon.sweep_interval_s() == want


class _Store:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        self.calls: list[tuple[Any, str, frozenset[str]]] = []

    def expire_stale(self, cutoff: Any, reason: str, keep: frozenset[str]) -> list[str]:
        self.calls.append((cutoff, reason, keep))
        return list(self.ids)


def _platform(store: Any) -> Any:
    audits: list[tuple] = []
    return SimpleNamespace(
        approval_store=store,
        settings=SimpleNamespace(approval_timeout_s=30.0),
        approvals=SimpleNamespace(pending=lambda: [SimpleNamespace(id="live-1")]),
        audit=lambda *a: audits.append(a),
        audits=audits,
    )


def test_sweep_without_a_capable_store_is_a_no_op():
    assert loopmon.sweep_approvals_once(SimpleNamespace(approval_store=None), 1.0) == []
    assert loopmon.sweep_approvals_once(SimpleNamespace(approval_store=object()), 1.0) == []


def test_sweep_expires_orphans_keeps_live_and_audits_each():
    store = _Store(["a1", "a2"])
    p = _platform(store)
    before = utc_now()
    assert loopmon.sweep_approvals_once(p, 10.0) == ["a1", "a2"]
    cutoff, reason, keep = store.calls[0]
    # cutoff = now - (timeout 30 s + interval 10 s)
    assert before - timedelta(seconds=41) < cutoff <= before - timedelta(seconds=39)
    assert reason == loopmon.SWEEP_REASON and keep == frozenset({"live-1"})
    assert [a[2] for a in p.audits] == ["a1", "a2"]
    assert all(a[:2] == ("timeout", "approval.expired") for a in p.audits)


async def test_sweeper_survives_a_failing_sweep(monkeypatch):
    calls: list[int] = []

    def flaky(platform: Any, interval: float) -> list[str]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db down")
        raise asyncio.CancelledError  # stop the loop after the second sweep

    ticks: list[float] = []

    async def no_sleep(s: float) -> None:
        ticks.append(s)

    async def inline(fn: Any, *a: Any) -> Any:
        return fn(*a)

    monkeypatch.setattr(loopmon, "sweep_approvals_once", flaky)
    monkeypatch.setattr(loopmon.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(loopmon.asyncio, "to_thread", inline)
    with pytest.raises(asyncio.CancelledError):
        await loopmon.approval_sweeper(object(), 7.0)
    assert len(calls) == 2 and ticks == [7.0, 7.0]
