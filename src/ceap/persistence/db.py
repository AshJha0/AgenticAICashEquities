"""SQLite persistence behind a single writer thread (decision D7).

* Standard library ``sqlite3`` only; no ORM, no server.
* Every statement runs on one dedicated thread. Async callers use :meth:`Database.arun`, which hands
  the work to that thread and awaits it, so the event loop never blocks on disk I/O and there is never
  more than one writer (SQLite's own rule, enforced here rather than discovered as ``database is locked``).
* WAL journal and ``busy_timeout`` so a second process (``ceap db backup``, ``ceap db verify-chain``) can
  read while the server writes.
* Schema versions live in ``PRAGMA user_version``. Migrations are forward-only SQL files in
  ``migrations/NNNN_name.sql``. A file database is backed up with the online backup API before any
  migration runs; a database newer than this code refuses to open.
* The audit log is a hash chain over canonical bytes: each row's hash covers the previous row's hash
  and the canonical JSON of the row, so an edit or deletion anywhere breaks verification from that row on.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, TypeVar

from ceap.util.canonical import canonical_datetime, canonical_json

T = TypeVar("T")

MEMORY = ":memory:"
GENESIS_HASH = "0" * 64
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class DatabaseError(RuntimeError):
    """Persistence failure the caller should surface (not retry)."""


class DatabaseTooNewError(DatabaseError):
    """The file was migrated by a newer release; migrations are forward-only."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations() -> list[Migration]:
    """Migrations shipped with the package, in version order, with no gaps."""
    folder = resources.files("ceap.persistence").joinpath("migrations")
    found: list[Migration] = []
    for entry in folder.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            found.append(Migration(int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort(key=lambda mig: mig.version)
    for expected, mig in enumerate(found, start=1):
        if mig.version != expected:
            raise DatabaseError(f"migration sequence has a gap at {mig.name} (expected version {expected})")
    return found


LATEST_VERSION = len(load_migrations())


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    rows: int
    first_bad_seq: int | None = None
    reason: str = ""


def audit_hash(prev_hash: str, at: str, actor: str, action: str, subject: str, payload_json: str) -> str:
    body = canonical_json(
        {"at": at, "actor": actor, "action": action, "subject": subject, "payload": payload_json}
    )
    return hashlib.sha256(prev_hash.encode("ascii") + body).hexdigest()


class Database:
    """One SQLite file (or ``:memory:``) owned by one writer thread."""

    def __init__(
        self,
        path: str | Path = MEMORY,
        *,
        busy_timeout_ms: int = 5000,
        migrate: bool = True,
        target_version: int | None = None,
        backup_before_migrate: bool = True,
    ) -> None:
        self.path = str(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ceap-db")
        self._thread_ident: int | None = None
        self._conn: sqlite3.Connection | None = None
        self._closed = False
        self.last_backup: Path | None = None
        # an unclosed Database (tests build many) must not keep its writer thread alive
        weakref.finalize(self, self._executor.shutdown, wait=False)
        self.run(lambda _c: None)  # opens the connection on the writer thread
        if migrate:
            self.migrate(target_version, backup=backup_before_migrate)

    # ----------------------------------------------------------- threading
    @property
    def is_memory(self) -> bool:
        return self.path == MEMORY or self.path.startswith("file::memory:")

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._thread_ident = threading.get_ident()
            if not self.is_memory:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=self.busy_timeout_ms / 1000.0)
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            conn.execute("PRAGMA foreign_keys=ON")
            if not self.is_memory:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            self._conn = conn
        return self._conn

    def _call(self, fn: Callable[[sqlite3.Connection], T], write: bool) -> T:
        conn = self._connection()
        if not write or conn.in_transaction:
            return fn(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            out = fn(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return out

    def run(self, fn: Callable[[sqlite3.Connection], T], *, write: bool = True) -> T:
        """Run ``fn(conn)`` on the writer thread (in one transaction when ``write``) and return its value."""
        if self._closed:
            raise DatabaseError("database is closed")
        if threading.get_ident() == self._thread_ident:
            return self._call(fn, write)
        return self._executor.submit(self._call, fn, write).result()

    async def arun(self, fn: Callable[[sqlite3.Connection], T], *, write: bool = True) -> T:
        """Async form of :meth:`run`: the loop awaits while the writer thread works."""
        if self._closed:
            raise DatabaseError("database is closed")
        return await asyncio.wrap_future(self._executor.submit(self._call, fn, write))

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return self.run(lambda c: [dict(r) for r in c.execute(sql, params).fetchall()], write=False)

    # ---------------------------------------------------------- migrations
    def schema_version(self) -> int:
        return int(self.run(lambda c: c.execute("PRAGMA user_version").fetchone()[0], write=False))

    def migrate(self, target: int | None = None, *, backup: bool = True) -> list[int]:
        """Apply pending forward migrations up to ``target`` (default: latest). Returns versions applied."""
        migrations = load_migrations()
        latest = len(migrations)
        target = latest if target is None else target
        if not 0 <= target <= latest:
            raise DatabaseError(f"unknown schema version {target}; this release knows 0..{latest}")
        current = self.schema_version()
        if current > latest:
            raise DatabaseTooNewError(
                f"{self.path} is at schema v{current} but this release knows only up to v{latest}; "
                "migrations are forward-only - upgrade the platform or restore a backup"
            )
        if current > target:
            raise DatabaseError(f"refusing to downgrade {self.path} from v{current} to v{target}")
        pending = [m for m in migrations if current < m.version <= target]
        if not pending:
            return []
        if backup and current > 0 and not self.is_memory:
            self.last_backup = self.backup(Path(f"{self.path}.v{current}.bak"))

        def apply(conn: sqlite3.Connection) -> None:
            for mig in pending:
                try:
                    conn.executescript(
                        f"BEGIN IMMEDIATE;\n{mig.sql}\nPRAGMA user_version={mig.version};\nCOMMIT;"
                    )
                except sqlite3.Error as exc:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise DatabaseError(f"migration {mig.name} failed: {exc}") from exc

        self.run(apply, write=False)
        return [m.version for m in pending]

    # -------------------------------------------------------------- backup
    def backup(self, dest: str | Path) -> Path:
        """Consistent online copy via the SQLite backup API (safe while the server writes)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

        def copy(conn: sqlite3.Connection) -> None:
            target = sqlite3.connect(str(dest))
            try:
                conn.backup(target)
            finally:
                target.close()

        self.run(copy, write=False)
        return dest

    def size_bytes(self) -> int:
        def size(conn: sqlite3.Connection) -> int:
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            return int(pages) * int(page_size)

        return self.run(size, write=False)

    # ----------------------------------------------------------- audit log
    def append_audit(
        self, actor: str, action: str, subject: str, payload: dict[str, Any] | None = None
    ) -> str:
        """Append one hash-chained audit row and return its hash."""
        from ceap.domain.common import utc_now

        payload_json = canonical_json(payload or {}).decode("utf-8")
        at = canonical_datetime(utc_now())

        def append(conn: sqlite3.Connection) -> str:
            row = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS_HASH
            digest = audit_hash(prev, at, actor, action, subject, payload_json)
            conn.execute(
                "INSERT INTO audit_log(at, actor, action, subject, payload_json, prev_hash, hash) "
                "VALUES (?,?,?,?,?,?,?)",
                (at, actor, action, subject, payload_json, prev, digest),
            )
            return digest

        return self.run(append)

    def verify_chain(self) -> ChainReport:
        rows = self.query("SELECT * FROM audit_log ORDER BY seq")
        prev = GENESIS_HASH
        for row in rows:
            if row["prev_hash"] != prev:
                return ChainReport(False, len(rows), row["seq"], "prev_hash does not match the previous row")
            expected = audit_hash(
                prev, row["at"], row["actor"], row["action"], row["subject"], row["payload_json"]
            )
            if row["hash"] != expected:
                return ChainReport(False, len(rows), row["seq"], "row content does not match its hash")
            prev = row["hash"]
        return ChainReport(True, len(rows))

    # --------------------------------------------------------------- close
    def close(self) -> None:
        """Checkpoint the WAL, close the connection and stop the writer thread (releases file locks)."""
        if self._closed:
            return

        def shut(conn: sqlite3.Connection) -> None:
            if not self.is_memory:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            self._conn = None

        try:
            self.run(shut, write=False)
        finally:
            self._closed = True
            self._executor.shutdown(wait=True)

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
