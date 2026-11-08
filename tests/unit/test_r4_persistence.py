"""R4 item 4.1: SQLite persistence, forward-only migrations, backup, audit hash chain, retention."""

from __future__ import annotations

import os
import sqlite3
from datetime import timedelta

import pytest

from ceap.domain.common import utc_now
from ceap.persistence.db import LATEST_VERSION, Database, DatabaseError, DatabaseTooNewError, load_migrations
from ceap.persistence.repositories import (
    IdempotencyRepository,
    LlmCallRepository,
    RetentionPolicy,
    StagedOrderRepository,
    TaskRepository,
    TrialLedgerRepository,
    apply_retention,
)
from ceap.util.canonical import canonical_datetime


def _tables(db: Database) -> set[str]:
    return {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrations_are_contiguous_and_latest_is_two():
    assert [m.version for m in load_migrations()] == list(range(1, LATEST_VERSION + 1))
    assert LATEST_VERSION >= 2  # RP adds 0003_paper


def test_upgrade_v1_to_v2_passes_and_backs_up_first(tmp_path):
    path = tmp_path / "ceap.db"
    with Database(path, target_version=1) as v1:
        assert v1.schema_version() == 1
        assert "llm_calls" not in _tables(v1)
        TaskRepository(v1).upsert("TASK-1", "research", "quant:abc", "q", {"dataset": "R01"}, "COMPLETED")
        v1.append_audit("system", "task.created", "TASK-1")

    with Database(path) as v2:  # opening with the new code migrates forward
        assert v2.schema_version() == LATEST_VERSION
        assert {"llm_calls", "trial_ledger", "idempotency_keys"} <= _tables(v2)
        assert TaskRepository(v2).get("TASK-1")["status"] == "COMPLETED"  # data survived
        assert v2.verify_chain().ok
        backup = v2.last_backup
    assert backup is not None and backup.name == "ceap.db.v1.bak"
    with Database(backup, migrate=False) as old:  # the pre-migration copy is still v1
        assert old.schema_version() == 1
        assert old.query("SELECT id FROM tasks")[0]["id"] == "TASK-1"


def test_newer_database_refuses_to_open_and_no_downgrade(tmp_path):
    path = tmp_path / "new.db"
    with Database(path) as db:
        db.run(lambda c: c.execute("PRAGMA user_version=99"), write=False)
    with pytest.raises(DatabaseTooNewError):
        Database(path)
    with Database(tmp_path / "x.db") as db, pytest.raises(DatabaseError):
        db.migrate(1)


def test_backup_restores_and_verifies_then_tamper_breaks_chain(tmp_path):
    with Database(tmp_path / "live.db") as db:
        for i in range(5):
            db.append_audit("approver:1", "approval.decided", f"APR-{i}", {"approved": i % 2 == 0})
        copy_path = db.backup(tmp_path / "backup" / "copy.db")
    with Database(copy_path) as restored:
        report = restored.verify_chain()
        assert report.ok and report.rows == 5
        restored.run(lambda c: c.execute("UPDATE audit_log SET actor='mallory' WHERE seq=3"))
        broken = restored.verify_chain()
        assert not broken.ok and broken.first_bad_seq == 3


def test_deleting_a_middle_audit_row_is_detected(tmp_path):
    with Database(tmp_path / "a.db") as db:
        for i in range(4):
            db.append_audit("s", "x", str(i))
        db.run(lambda c: c.execute("DELETE FROM audit_log WHERE seq=2"))
        report = db.verify_chain()
        assert not report.ok and report.first_bad_seq == 3


def test_windows_file_lock_on_wal_is_released_on_close(tmp_path):
    """A second reader works while the writer holds the WAL; after close every file can be deleted."""
    path = tmp_path / "locks.db"
    writer = Database(path)
    TaskRepository(writer).upsert("T1", "investigation", "o", "d", {}, "RUNNING")
    assert writer.query("PRAGMA journal_mode")[0]["journal_mode"] == "wal"
    assert os.path.exists(f"{path}-wal")
    reader = Database(path, migrate=False)  # e.g. `ceap db verify-chain` while the server runs
    assert reader.query("SELECT status FROM tasks")[0]["status"] == "RUNNING"
    reader.close()
    writer.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(f"{path}{suffix}"):
            os.remove(f"{path}{suffix}")  # raises PermissionError on Windows if a handle leaked
    assert not os.path.exists(path)


def test_single_writer_thread_serialises_concurrent_writes(tmp_path):
    import threading

    with Database(tmp_path / "w.db") as db:
        threads = [
            threading.Thread(target=lambda n=n: [db.append_audit("t", "w", f"{n}-{i}") for i in range(20)])
            for n in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        report = db.verify_chain()
        assert report.ok and report.rows == 100


async def test_arun_does_not_block_the_loop():
    db = Database()
    try:
        assert await db.arun(lambda c: c.execute("SELECT 41 + 1").fetchone()[0], write=False) == 42
    finally:
        db.close()
    with pytest.raises(DatabaseError):
        db.run(lambda c: None)


def test_idempotency_keys_are_scoped_per_principal_and_expire():
    db = Database()
    repo = IdempotencyRepository(db)
    h = repo.body_hash({"question": "x"})
    repo.put("trader:1", "k1", h, "TASK-1", {"task_id": "TASK-1"})
    assert repo.get("trader:1", "k1")["task_id"] == "TASK-1"
    assert repo.get("trader:2", "k1") is None
    old = canonical_datetime(utc_now() - timedelta(hours=25))
    db.run(lambda c: c.execute("UPDATE idempotency_keys SET created_at=?", (old,)))
    assert repo.get("trader:1", "k1") is None  # TTL 24 h


def test_staged_orders_trial_ledger_and_llm_calls_persist():
    db = Database()
    staged = StagedOrderRepository(db)
    assert staged.save("STG-1", {"orders": [1, 2]}) is True
    assert staged.save("STG-1", {"orders": [9]}) is False  # idempotent staging
    assert staged.get("STG-1")["orders"] == [1, 2]
    ledger = TrialLedgerRepository(db)
    assert ledger.record("dh", "p1", 0.5, {}) and not ledger.record("dh", "p1", 0.5, {})
    assert ledger.record("dh", "p2", None, {}) and ledger.count("dh") == 2
    LlmCallRepository(db).record("T1", "narrative", "mock", "abc", {"q": 1}, {"a": 2})
    assert db.query("SELECT prompt_hash FROM llm_calls")[0]["prompt_hash"] == "abc"


def test_retention_prunes_by_age_and_never_touches_the_audit_log():
    db = Database()
    tasks = TaskRepository(db)
    from ceap.persistence.repositories import ResultRepository

    results = ResultRepository(db)
    for i in range(3):
        tasks.upsert(f"T{i}", "investigation", "o", "d", {}, "PENDING")
        results.save(f"T{i}", "COMPLETED", {"i": i}, trace={"spans": []})
    db.append_audit("s", "x", "y")
    LlmCallRepository(db).record("T0", "narrative", "m", "h", {"q": 1}, {"a": 1})
    old = canonical_datetime(utc_now() - timedelta(days=100))
    db.run(lambda c: c.execute("UPDATE results SET finished_at=? WHERE task_id='T0'", (old,)))
    db.run(lambda c: c.execute("UPDATE llm_calls SET at=?", (old,)))
    out = apply_retention(db, RetentionPolicy(results_days=90, traces_days=30, llm_io_days=7))
    assert out["results"] == 1 and results.get("T0") is None and results.get("T1") is not None
    assert db.query("SELECT request_json FROM llm_calls")[0]["request_json"] is None
    assert db.verify_chain().rows == 1


def test_size_bound_prunes_oldest_results(tmp_path):
    with Database(tmp_path / "big.db") as db:
        tasks = TaskRepository(db)
        from ceap.persistence.repositories import ResultRepository

        results = ResultRepository(db)
        for i in range(60):
            tasks.upsert(f"T{i:03d}", "investigation", "o", "d", {}, "PENDING")
            results.save(f"T{i:03d}", "COMPLETED", {"blob": "x" * 20_000})
        before = db.size_bytes()
        out = apply_retention(db, RetentionPolicy(max_bytes=before // 2))
        assert out["size_pruned"] > 0 and db.size_bytes() <= before // 2
        assert results.get("T059") is not None  # newest kept


def test_interrupt_in_flight_marks_pending_and_running():
    db = Database()
    tasks = TaskRepository(db)
    tasks.upsert("A", "research", "o", "d", {}, "RUNNING")
    tasks.upsert("B", "research", "o", "d", {}, "PENDING")
    tasks.upsert("C", "research", "o", "d", {}, "COMPLETED")
    assert sorted(tasks.interrupt_in_flight()) == ["A", "B"]
    assert {r["id"]: r["status"] for r in tasks.find()} == {"A": "INTERRUPTED", "B": "INTERRUPTED", "C": "COMPLETED"}


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    import ceap.persistence.db as dbmod

    real = dbmod.load_migrations

    def broken() -> list[dbmod.Migration]:
        migs = real()
        return [migs[0], dbmod.Migration(2, "0002_broken.sql", "CREATE TABLE ok_t(x); CREATE TABLE tasks(x);")]

    monkeypatch.setattr(dbmod, "load_migrations", broken)
    with Database(tmp_path / "r.db", target_version=1) as db:
        with pytest.raises(DatabaseError):
            db.migrate()
        assert db.schema_version() == 1 and "ok_t" not in _tables(db)
    assert isinstance(sqlite3.sqlite_version, str)
