"""0.9.0 G10: chaos tests - kill -9 mid-run converges, injected disk I/O error rolls back whole.

The loop-lag test is in ``test_r090_platform_ops.py``. The kill tests start a child Python process,
kill it hard (``Popen.kill``: SIGKILL on POSIX, TerminateProcess on Windows - no ``finally`` runs, no
atexit, exactly like ``kill -9``) and check the survivor state converges to the uninterrupted result.
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ceap.persistence.db import Database

SRC = str(Path(__file__).resolve().parents[2] / "src")


def _child(code: str, tmp_path: Path) -> subprocess.Popen[bytes]:
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(code), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": SRC + os.pathsep + os.environ.get("PYTHONPATH", ""), "CEAP_ENV": "dev"}
    return subprocess.Popen([sys.executable, str(script)], env=env, cwd=str(tmp_path))


def _wait_for(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _count(path: Path, sql: str) -> int:
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2) as conn:
            return int(conn.execute(sql).fetchone()[0])
    except sqlite3.Error:
        return 0


# ------------------------------------------------------------------ run_day
@pytest.mark.slow
def test_kill_9_mid_run_day_converges(tmp_path):
    from ceap.harness.paper_gate import compare_days
    from ceap.paper.config import PaperConfig
    from ceap.paper.job import PaperJob

    sessions = 25
    db_path = tmp_path / "killed.db"
    proc = _child(
        f"""
        from ceap.paper.config import PaperConfig
        from ceap.paper.job import PaperJob
        from ceap.persistence.db import Database
        db = Database(r"{db_path}")
        job = PaperJob(db, PaperConfig(), risk_free=None)
        end = job.panel.day(job.panel.index_of("2026-11-09") + {sessions - 1})
        from datetime import UTC, datetime
        job.run_day(end, now=datetime(2100, 1, 1, tzinfo=UTC))  # synthetic panel runs past today
        """,
        tmp_path,
    )
    try:
        reached = _wait_for(lambda: _count(db_path, "SELECT COUNT(*) FROM paper_days") >= 3, 240)
    finally:
        proc.kill()  # kill -9: no cleanup runs in the child
        proc.wait(30)
    assert reached, "the child never committed 3 sessions"
    done_before = _count(db_path, "SELECT COUNT(*) FROM paper_days")
    assert done_before < sessions, "the child finished before it was killed; the test proved nothing"

    with Database(db_path) as db:  # the survivor: re-run the same day, it finishes the rest
        job = PaperJob(db, PaperConfig(), risk_free=None)
        end = job.panel.day(job.panel.index_of("2026-11-09") + sessions - 1)
        job.run_day(end)
        with Database() as clean:
            PaperJob(clean, PaperConfig(), risk_free=None).run_day(end)
            report = compare_days(db, clean, end)
        from ceap.paper.ledger import PaperLedger

        assert PaperLedger(db).verify("forward").ok
    assert report.compared == sessions and report.ok, report.differences[:5]


# ------------------------------------------------------------------ harness run (G5)
@pytest.mark.slow
def test_kill_9_mid_investigation_resumes_from_the_journal(tmp_path):
    db_path = tmp_path / "run.db"
    id_file = tmp_path / "task_id.txt"
    proc = _child(
        f"""
        import asyncio, os
        from ceap.config import Settings
        from ceap.harness.durable import SqlRunJournal
        from ceap.llm.client import MockLLMClient
        from ceap.platform import Platform, default_request

        async def main():
            p = Platform(settings=Settings(llm_provider="mock", auto_approve=True, db_path=r"{db_path}"),
                         llm=MockLLMClient())
            req = default_request("AAPL", "T01")
            task = p.build_task(req)
            open(r"{id_file}", "w").write(task.id)
            h = await p.harness()
            journal = SqlRunJournal(p.db)
            puts = 0
            inner = journal.put
            def put(*a, **k):
                nonlocal puts
                ok = inner(*a, **k)
                puts += a[2] == "tool"  # 0.10.0 H1: model replies are journalled too; count tools
                if puts == 3:
                    os._exit(137)  # die like kill -9 straight after the third tool step boundary
                return ok
            journal.put = put
            h.journal = journal
            await h.execute(task, p.policy_context(req, task))
        asyncio.run(main())
        """,
        tmp_path,
    )
    code = proc.wait(240)
    assert code == 137, f"child exited {code}"
    assert _count(db_path, "SELECT COUNT(*) FROM run_journal WHERE kind='tool'") == 3

    import asyncio

    from ceap.config import Settings
    from ceap.harness.durable import SqlRunJournal
    from ceap.llm.client import MockLLMClient
    from ceap.platform import Platform, default_request

    async def resume():
        p = Platform(
            settings=Settings(llm_provider="mock", auto_approve=True, db_path=str(db_path)),
            llm=MockLLMClient(),
        )
        try:
            req = default_request("AAPL", "T01")
            task = dataclasses.replace(p.build_task(req), id=id_file.read_text())
            h = await p.harness()
            h.journal = SqlRunJournal(p.db)
            result = await h.execute(task, p.policy_context(req, task))
            replayed = [
                e
                for s in result.trace.get("spans", [])
                for e in s.get("events", [])
                if e.get("name") == "tool.replayed"
            ]
            return result, replayed, _count(db_path, "SELECT COUNT(*) FROM run_journal")
        finally:
            p.close()

    result, replayed, rows = asyncio.run(resume())
    assert result.success, result.error
    assert len(replayed) == 3  # the three journalled calls were read back, not repeated
    assert rows > 3


# ------------------------------------------------------------------ disk I/O error
def test_injected_disk_io_error_rolls_back_whole_and_the_next_write_succeeds(tmp_path):
    with Database(tmp_path / "io.db") as db:
        before = db.query("SELECT COUNT(*) AS n FROM audit_log")[0]["n"]

        def half_write(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO idempotency_keys(principal, key, body_hash, task_id, response_json, created_at) "
                "VALUES ('p','k','h','t','{}','2026-11-09T00:00:00Z')"
            )
            raise sqlite3.OperationalError("disk I/O error")

        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            db.run(half_write)
        assert db.query("SELECT COUNT(*) AS n FROM idempotency_keys")[0]["n"] == 0  # nothing half-written
        db.append_audit("system", "test.after_io_error", "x", {})
        assert db.query("SELECT COUNT(*) AS n FROM audit_log")[0]["n"] == before + 1
        assert db.health()["write_probe_ok"]


async def test_injected_disk_io_error_on_a_task_write_fails_the_request_not_the_process(
    platform, monkeypatch
):
    from ceap.platform import default_request

    real = platform.db.run
    calls = {"n": 0}

    def flaky(fn, *, write=True):
        calls["n"] += 1
        if write and calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return real(fn, write=write)

    monkeypatch.setattr(platform.db, "run", flaky)
    req = default_request("AAPL", "T01")
    try:
        await platform.investigate(req)
    except sqlite3.OperationalError:
        pass  # surfaced to the caller (HTTP 500 in the API); the process stays up
    monkeypatch.setattr(platform.db, "run", real)
    result = await platform.investigate(req)
    assert result.success
