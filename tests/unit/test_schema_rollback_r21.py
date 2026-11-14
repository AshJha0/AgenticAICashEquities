"""0.10.0 X3 (finding R-21): setting the schema version back by hand must not let forged rows pass."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ceap.persistence.db import LATEST_VERSION, Database, DatabaseError


def _keyed_db(path: Path) -> None:
    with Database(path) as db:
        for i in range(3):
            db.append_audit("s", "x", str(i))
        assert db.verify_chain().ok


def _set_version(path: Path, v: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version={v}")
    conn.commit()
    conn.close()


def test_fresh_database_seals_the_high_water_mark(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    _keyed_db(path)
    with Database(path, migrate=False) as db:
        assert db.schema_version() == LATEST_VERSION
        assert db.schema_seal_error() == ""


def test_rollback_below_keyed_version_is_refused_by_migrate(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    _keyed_db(path)
    _set_version(path, 3)
    with pytest.raises(DatabaseError, match="R-21"):
        Database(path)


def test_rollback_below_keyed_version_fails_verify_without_migrating(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    _keyed_db(path)
    _set_version(path, 3)
    with Database(path, migrate=False) as db:
        report = db.verify_chain()
        assert not report.ok and "rolled back" in (report.reason or "")


def test_rollback_above_keyed_version_fails_on_the_seal(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    _keyed_db(path)
    _set_version(path, 5)
    with Database(path, migrate=False) as db:
        report = db.verify_chain()
        assert not report.ok and "high-water" in (report.reason or "")


def test_edited_seal_fails(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    _keyed_db(path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_seal SET version=99")
    conn.commit()
    conn.close()
    with Database(path, migrate=False) as db:
        assert "does not verify" in db.schema_seal_error()
