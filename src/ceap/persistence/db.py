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
  From 0.8.0 (migration 0004) each link is an HMAC keyed outside the database
  (:mod:`ceap.persistence.chainkey`), and ``verify_chain(expected_head, expected_rows)`` checks the chain
  against a head recorded elsewhere (:mod:`ceap.persistence.anchor`), which is what catches a truncated
  tail or a chain rebuilt by someone holding the key.
* File databases run with ``synchronous=FULL``: an audit row or a paper ledger row that was committed
  survives a power cut, at the cost of one extra fsync per transaction (writes here are small and rare).
* :meth:`Database.arun` has a timeout, and :meth:`Database.health` reports the writer queue depth and
  a write probe for ``/health``.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
import threading
import time
import weakref
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, TypeVar

from ceap.persistence.chainkey import LEGACY_ALG, ChainKeys, walk_chain
from ceap.util.canonical import canonical_datetime, canonical_json

T = TypeVar("T")

MEMORY = ":memory:"
GENESIS_HASH = "0" * 64
DEFAULT_ARUN_TIMEOUT_S = 30.0
KEYED_CHAINS_VERSION = 4
SCHEMA_SEAL_VERSION = 6
_CHAIN_TABLES = ("audit_log", "paper_ledger", "paper_days")
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class DatabaseError(RuntimeError):
    """Persistence failure the caller should surface (not retry)."""


class DatabaseTooNewError(DatabaseError):
    """The file was migrated by a newer release; migrations are forward-only."""


class DatabaseTimeoutError(DatabaseError):
    """An async call waited longer than its timeout for the writer thread."""


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


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})").fetchall())


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    rows: int
    first_bad_seq: int | None = None
    reason: str = ""
    head: str = GENESIS_HASH
    dev_key: bool = False


def audit_body(at: str, actor: str, action: str, subject: str, payload_json: str) -> bytes:
    return canonical_json({"at": at, "actor": actor, "action": action, "subject": subject, "payload": payload_json})


def audit_hash(
    prev_hash: str, at: str, actor: str, action: str, subject: str, payload_json: str,
    keys: ChainKeys | None = None,
) -> str:
    """Link hash of one audit row: HMAC with ``keys`` (current key), or plain SHA-256 without (legacy)."""
    body = audit_body(at, actor, action, subject, payload_json)
    if keys is None:
        return hashlib.sha256(prev_hash.encode("ascii") + body).hexdigest()
    return keys.sign(prev_hash, body)


def check_anchor(rows: list[str], expected_head: str | None, expected_rows: int | None) -> str:
    """Compare a verified chain (its row hashes in order) with an externally recorded head.

    ``expected_rows`` alone: the chain must have at least that many rows (catches truncation).
    ``expected_head`` alone: that hash must appear in the chain. Both: row ``expected_rows`` (1-based)
    must carry ``expected_head``. Rows appended after the anchor are fine. Returns "" when consistent.
    """
    if expected_rows is not None and len(rows) < expected_rows:
        return f"chain has {len(rows)} rows but the anchor recorded {expected_rows} (rows were removed)"
    if expected_head is None:
        return ""
    if expected_rows is not None:
        at = rows[expected_rows - 1] if expected_rows > 0 else GENESIS_HASH
        if at != expected_head:
            return f"row {expected_rows} does not carry the anchored head (history was rewritten)"
        return ""
    if expected_head != GENESIS_HASH and expected_head not in rows:
        return "the anchored head is not in the chain (history was rewritten)"
    return ""


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
        hmac_key: bytes | str | None = None,
        arun_timeout_s: float = DEFAULT_ARUN_TIMEOUT_S,
    ) -> None:
        self.path = str(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.keys = ChainKeys.from_env(hmac_key)
        self.arun_timeout_s = float(arun_timeout_s)
        self._pending = 0
        self._pending_lock = threading.Lock()
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
                conn.execute("PRAGMA synchronous=FULL")
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
        return self._submit(fn, write).result()

    def _submit(self, fn: Callable[[sqlite3.Connection], T], write: bool) -> Future[T]:
        with self._pending_lock:
            self._pending += 1
        fut = self._executor.submit(self._call, fn, write)

        def done(_f: object) -> None:
            with self._pending_lock:
                self._pending -= 1

        fut.add_done_callback(done)
        return fut

    @property
    def queue_depth(self) -> int:
        """Calls submitted to the writer thread and not yet finished (including the running one)."""
        with self._pending_lock:
            return self._pending

    async def arun(
        self, fn: Callable[[sqlite3.Connection], T], *, write: bool = True, timeout: float | None = None
    ) -> T:
        """Async form of :meth:`run`: the loop awaits while the writer thread works.

        Waits at most ``timeout`` seconds (default ``arun_timeout_s``) and then raises
        :class:`DatabaseTimeoutError`. A call that already started on the writer thread still runs to
        completion (SQLite cannot be interrupted mid-transaction safely); one still queued is cancelled.
        """
        if self._closed:
            raise DatabaseError("database is closed")
        fut = self._submit(fn, write)
        limit = self.arun_timeout_s if timeout is None else float(timeout)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=limit)
        except TimeoutError as exc:
            fut.cancel()
            raise DatabaseTimeoutError(
                f"database call waited more than {limit:.1f}s for the writer thread "
                f"(queue depth {self.queue_depth})"
            ) from exc

    def health(self) -> dict[str, Any]:
        """Writer-queue depth plus a write probe: take and release the write lock (``BEGIN IMMEDIATE``
        then ``ROLLBACK``), so nothing is written but a locked or read-only file shows up."""
        depth = self.queue_depth
        t0 = time.perf_counter()

        def probe(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")

        try:
            self.run(probe, write=False)
            ok, error = True, None
        except (sqlite3.Error, DatabaseError) as exc:
            ok, error = False, str(exc)
        return {
            "writer_queue": depth,
            "write_probe_ok": ok,
            "write_probe_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            "write_probe_error": error,
            "dev_key": self.keys.is_dev_default,
        }

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
        if current < KEYED_CHAINS_VERSION <= target:
            rollback = self.rollback_evidence()
            if rollback:
                raise DatabaseError(
                    f"refusing to migrate {self.path} from v{current}: {rollback}; the schema version was "
                    "set back by hand (finding R-21) - restore a backup"
                )
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
        if any(m.version == KEYED_CHAINS_VERSION for m in pending):
            self._seal_epochs()
        if target >= SCHEMA_SEAL_VERSION:
            self._seal_schema(target)
        return [m.version for m in pending]

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def rollback_evidence(self) -> str:
        """Why this file cannot be below v4 ("" when nothing shows it reached v4 before)."""

        def probe(conn: sqlite3.Connection) -> str:
            for t in ("chain_epoch", "schema_seal"):
                if self._table_exists(conn, t):
                    return f"table {t} already exists"
            for t in _CHAIN_TABLES:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
                if "hash_alg" in cols and conn.execute(
                    f"SELECT 1 FROM {t} WHERE hash_alg != 'sha256' LIMIT 1"  # noqa: S608 - fixed names
                ).fetchone():
                    return f"{t} already holds keyed rows"
            return ""

        return str(self.run(probe, write=False))

    def _seal_schema(self, version: int) -> None:
        def seal(conn: sqlite3.Connection) -> None:
            row = conn.execute("SELECT version, mac FROM schema_seal WHERE id=1").fetchone()
            if row is not None and self.keys.schema_ok(int(row[0]), row[1]) and int(row[0]) >= version:
                return
            conn.execute(
                "INSERT INTO schema_seal(id, version, mac) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET version=excluded.version, mac=excluded.mac",
                (version, self.keys.seal_schema(version)),
            )

        self.run(seal)

    def schema_seal_error(self) -> str:
        """"" when the sealed high-water version is absent or consistent with ``user_version``."""
        current = self.schema_version()
        evidence = self.rollback_evidence() if current < KEYED_CHAINS_VERSION else ""
        if evidence:
            return f"schema v{current} is below v{KEYED_CHAINS_VERSION} but {evidence} (rolled back by hand)"
        rows = self.run(
            lambda c: c.execute("SELECT version, mac FROM schema_seal WHERE id=1").fetchall()
            if self._table_exists(c, "schema_seal") else [],
            write=False,
        )
        if not rows:
            return ""
        version, mac = int(rows[0][0]), rows[0][1]
        if not self.keys.schema_ok(version, mac):
            return "schema_seal does not verify (wrong key, or edited)"
        if version > current:
            return f"schema v{current} is below the sealed high-water v{version} (rolled back by hand)"
        return ""

    def _seal_epochs(self) -> None:
        """Seal the legacy-prefix counts migration 0004 just wrote (only rows it left unsealed)."""

        def seal(conn: sqlite3.Connection) -> None:
            unsealed = conn.execute("SELECT chain, legacy_rows FROM chain_epoch WHERE mac IS NULL").fetchall()
            for chain, legacy in unsealed:
                conn.execute(
                    "UPDATE chain_epoch SET mac=? WHERE chain=?", (self.keys.seal_epoch(chain, legacy), chain)
                )

        self.run(seal)

    def legacy_rows(self, chain: str) -> tuple[int, str]:
        """Length of the unkeyed prefix allowed for ``chain`` and an error ("" when the seal is good)."""
        seal_err = self.schema_seal_error()
        if seal_err:
            return 0, seal_err
        if self.schema_version() < KEYED_CHAINS_VERSION:
            return 1 << 62, ""  # pre-0.8.0 file opened without migrating: every row is legacy
        rows = self.query("SELECT legacy_rows, mac FROM chain_epoch WHERE chain=?", (chain,))
        if not rows:
            return 0, ""
        legacy, mac = int(rows[0]["legacy_rows"]), rows[0]["mac"]
        if not self.keys.epoch_ok(chain, legacy, mac):
            return 0, f"chain_epoch seal for {chain} is missing or does not verify (wrong key, or edited)"
        return legacy, ""

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
            if has_column(conn, "audit_log", "hash_alg"):
                digest = audit_hash(prev, at, actor, action, subject, payload_json, self.keys)
                conn.execute(
                    "INSERT INTO audit_log(at, actor, action, subject, payload_json, prev_hash, hash, hash_alg) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (at, actor, action, subject, payload_json, prev, digest, self.keys.alg),
                )
            else:  # a database held below v4 on purpose (tests of older schemas)
                digest = audit_hash(prev, at, actor, action, subject, payload_json)
                conn.execute(
                    "INSERT INTO audit_log(at, actor, action, subject, payload_json, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (at, actor, action, subject, payload_json, prev, digest),
                )
            return digest

        return self.run(append)

    def verify_chain(self, expected_head: str | None = None, expected_rows: int | None = None) -> ChainReport:
        """Recompute the audit chain; with ``expected_head`` / ``expected_rows`` (from an anchor, see
        :func:`check_anchor`) also prove that no tail was cut off and no history rebuilt."""
        rows = self.query("SELECT * FROM audit_log ORDER BY seq")
        dev = self.keys.is_dev_default
        legacy, err = self.legacy_rows("audit")
        if err:
            return ChainReport(False, len(rows), None, err, dev_key=dev)
        links = [
            (str(r["seq"]), r["prev_hash"], r["hash"], r.get("hash_alg", LEGACY_ALG),
             audit_body(r["at"], r["actor"], r["action"], r["subject"], r["payload_json"]))
            for r in rows
        ]
        bad, reason = walk_chain(links, self.keys, legacy)
        if bad is not None:
            return ChainReport(False, len(rows), rows[bad]["seq"], reason, dev_key=dev)
        head = rows[-1]["hash"] if rows else GENESIS_HASH
        problem = check_anchor([r["hash"] for r in rows], expected_head, expected_rows)
        if problem:
            return ChainReport(False, len(rows), None, problem, head=head, dev_key=dev)
        return ChainReport(True, len(rows), head=head, dev_key=dev)

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
