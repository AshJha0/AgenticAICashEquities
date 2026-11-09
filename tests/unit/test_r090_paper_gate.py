"""0.9.0 G11: paper-day replay gate and the forward-log status page."""

from __future__ import annotations

import pytest

from ceap.harness.paper_gate import compare_days, render_status_page, replay_gate
from ceap.paper.config import PaperConfig
from ceap.paper.job import PaperJob
from ceap.persistence.db import Database

SESSIONS = 5


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    path = tmp_path_factory.mktemp("gate") / "paper.db"
    with Database(path) as db:
        job = PaperJob(db, PaperConfig(), risk_free=None)
        end = job.panel.day(job.panel.index_of("2026-11-09") + SESSIONS - 1)
        job.run_day(end)
    return path, end


def test_gate_passes_on_an_honest_record_and_writes_the_page(recorded, tmp_path):
    path, end = recorded
    with Database(path) as db:
        report = replay_gate(db, PaperConfig(), end)
        assert report.ok and report.compared == SESSIONS, report.differences[:5]
        page = render_status_page(db, report, tmp_path / "site" / "forward-log.html")
    text = page.read_text(encoding="utf-8")
    assert f"Last recorded session: <strong>{end}</strong>" in text and "PASS" in text
    assert f"Sessions recorded: <strong>{SESSIONS}</strong>" in text


def test_gate_finds_a_changed_equity_and_only_that(recorded, tmp_path):
    path, end = recorded
    copy = tmp_path / "tampered.db"
    with Database(path) as src:
        src.run(lambda c: c.execute(f"VACUUM INTO '{copy.as_posix()}'"), write=False)
    with Database(copy) as db:
        day = db.query("SELECT date FROM paper_days WHERE book='forward' ORDER BY date LIMIT 1 OFFSET 2")[0][
            "date"
        ]

        def tamper(c):  # an out-of-band edit: drop the append-only guard, then change one equity
            for (name,) in c.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='paper_days'"
            ):
                c.execute(f"DROP TRIGGER {name}")
            c.execute("UPDATE paper_days SET equity = equity + 1.0 WHERE book='forward' AND date=?", (day,))

        db.run(tamper)
        with Database(path) as honest:
            report = compare_days(db, honest, end)
    assert not report.ok
    assert {(d["date"], d["field"]) for d in report.differences} == {(day, "equity")}
