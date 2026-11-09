"""External anchoring of chain heads (0.8.0, item A6).

An HMAC chain stops someone who can write the database file but does not hold the key. It cannot stop
someone who holds the key (or a database keyed with the published dev default) from rebuilding the
whole chain, and on its own no chain notices a cut-off tail. An anchor closes both gaps: it records,
outside the database, how many rows each chain had and the hash of the last one. Verifying later
against the anchor proves that every anchored row is still there, unchanged.

``ceap db anchor --out audit_head.json`` writes:

* ``audit_head.json``: schema, creation time, the key id (never the key) and, per chain, ``rows`` and
  ``head``. Small and deterministic, so it is meant to be committed to git (each commit is then a
  dated, append-only record held by the git host as well as locally).
* ``audit_head.json.sha256``: the SHA-256 of that file in ``sha256sum`` format. This is the digest an
  OpenTimestamps proof commits to: ``ots stamp audit_head.json`` (run by a person; free, no account)
  produces ``audit_head.json.ots``, which later proves the file existed at that time. Nothing in this
  module makes a network call.

``ceap db verify-chain --anchor audit_head.json`` re-verifies each chain and checks it against the
anchor (:func:`ceap.persistence.db.check_anchor`). Rows appended after the anchor are fine.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ceap.persistence.db import GENESIS_HASH, Database
from ceap.util.canonical import canonical_datetime

ANCHOR_SCHEMA = "ceap-chain-anchor/1"


def _paper_books(db: Database) -> list[str]:
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    if "paper_ledger" not in tables:
        return []
    rows = db.query("SELECT book FROM paper_ledger UNION SELECT book FROM paper_days ORDER BY book")
    books = [r["book"] for r in rows]
    if db.query("SELECT 1 FROM paper_forward_log LIMIT 1"):
        from ceap.paper.ledger import FORWARD_BOOK

        if FORWARD_BOOK not in books:
            books.append(FORWARD_BOOK)
    return sorted(books)


def chain_heads(db: Database) -> dict[str, dict[str, Any]]:
    """``{chain: {"rows", "head"}}`` for the audit log and every paper book's ledger, days and log."""
    audit = db.query("SELECT hash FROM audit_log ORDER BY seq")
    out: dict[str, dict[str, Any]] = {
        "audit": {"rows": len(audit), "head": audit[-1]["hash"] if audit else GENESIS_HASH}
    }
    books = _paper_books(db)
    if books:
        from ceap.paper.ledger import PaperLedger

        ledger = PaperLedger(db)
        for book in books:
            lengths = ledger.chain_lengths(book)
            for chain, head in ledger.heads(book).items():
                out[f"paper:{book}:{chain}"] = {"rows": lengths[chain], "head": head}
    return out


def anchor_document(db: Database) -> dict[str, Any]:
    from ceap.domain.common import utc_now

    return {
        "schema": ANCHOR_SCHEMA,
        "created_at": canonical_datetime(utc_now()),
        "key_id": db.keys.current_id,
        "dev_key": db.keys.is_dev_default,
        "chains": chain_heads(db),
    }


def write_anchor(db: Database, path: str | Path) -> dict[str, Any]:
    """Write the anchor file and its ``.sha256`` sidecar; returns the document plus the digest."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = anchor_document(db)
    data = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return {**doc, "sha256": digest, "path": str(path)}


def load_anchor(path: str | Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != ANCHOR_SCHEMA:
        raise ValueError(f"{path} is not a {ANCHOR_SCHEMA} anchor")
    return doc


def verify_against_anchor(db: Database, anchor: dict[str, Any]) -> dict[str, Any]:
    """Verify every anchored chain; ``{"ok", "chains": {name: {"ok", "reason"}}}``."""
    results: dict[str, dict[str, Any]] = {}
    paper: dict[str, dict[str, dict[str, Any]]] = {}
    for name, a in sorted(anchor.get("chains", {}).items()):
        if name == "audit":
            rep = db.verify_chain(a.get("head"), a.get("rows"))
            results[name] = {"ok": rep.ok, "reason": rep.reason}
        elif name.startswith("paper:"):
            _, book, chain = name.split(":", 2)
            paper.setdefault(book, {})[chain] = a
        else:
            results[name] = {"ok": False, "reason": "unknown chain in anchor"}
    if paper:
        from ceap.paper.ledger import PaperLedger

        ledger = PaperLedger(db)
        for book, chains in sorted(paper.items()):
            prep = ledger.verify(book, anchor=chains)
            for chain in chains:
                results[f"paper:{book}:{chain}"] = {"ok": prep.ok, "reason": prep.reason}
    return {"ok": all(r["ok"] for r in results.values()), "chains": results}
