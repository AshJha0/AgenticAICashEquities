"""R-02 / X1: the forward record never takes a session before its New York close."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import ceap.paper.job as job_mod
from ceap.paper.config import PaperConfig
from ceap.paper.job import PaperJob, PaperJobError
from ceap.persistence.db import Database

pytestmark = pytest.mark.real_paper_clock


@pytest.fixture
def job(tmp_path):
    return PaperJob(Database(tmp_path / "p.db"), PaperConfig(), risk_free=None)


def test_session_refused_eleven_hours_before_its_close(job):
    # The 0.9.0 false start: the 10 November row was written at 03:42 UTC that day.
    with pytest.raises(PaperJobError, match="has not closed yet"):
        job.run_day("2026-11-10", now=datetime(2026, 11, 10, 3, 42, tzinfo=UTC))


def test_session_refused_one_minute_before_close_and_accepted_after(job):
    with pytest.raises(PaperJobError, match="has not closed yet"):
        job.run_day("2026-11-10", now=datetime(2026, 11, 10, 20, 59, tzinfo=UTC))  # 15:59 New York
    out = job.run_day("2026-11-10", now=datetime(2026, 11, 10, 21, 1, tzinfo=UTC))
    assert out and out[-1].date == "2026-11-10"


def test_default_clock_is_the_wall_clock(job, monkeypatch):
    monkeypatch.setattr(job_mod, "_utcnow", lambda: datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    with pytest.raises(PaperJobError, match="has not closed yet"):
        job.run_day("2026-11-09")


def test_naive_clock_is_refused(job):
    with pytest.raises(PaperJobError, match="timezone-aware"):
        job.run_day("2026-11-10", now=datetime(2026, 11, 11))
