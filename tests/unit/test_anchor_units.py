"""``ceap.persistence.anchor``: chain heads for audit and paper books, anchor file I/O and verification.

Paper rows are written through ``PaperLedger.append_events`` / ``append_log`` on an in-memory database,
so the anchor sees real chains for a simulated book and for the forward book.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ceap.paper.ledger import FORWARD_BOOK, PaperLedger
from ceap.persistence import anchor as an
from ceap.persistence.db import GENESIS_HASH, Database


@pytest.fixture
def db() -> Database:
    d = Database(":memory:")
    yield d
    d.close()


def _paper_rows(db: Database) -> None:
    ledger = PaperLedger(db)

    def write(conn) -> None:  # noqa: ANN001 - sqlite3.Connection
        ledger.append_events(conn, "sim1", "2026-01-02", [{"kind": "fill", "symbol": "AAPL", "quantity": 10.0,
                                                            "price": 100.0, "cash_delta": -1000.0}])
        ledger.append_log(conn, FORWARD_BOOK, {"date": "2026-01-02", "ret": 0.01}, "2026-01-02T21:00:00Z")

    db.run(write)


def test_empty_database_anchors_genesis(db: Database) -> None:
    heads = an.chain_heads(db)
    assert heads["audit"] == {"rows": 0, "head": GENESIS_HASH}
    assert all(not k.startswith("paper:") for k in heads)


def test_chain_heads_include_paper_and_forward_books(db: Database) -> None:
    db.append_audit("ops", "test", "subject", {"n": 1})
    _paper_rows(db)
    heads = an.chain_heads(db)
    assert heads["audit"]["rows"] == 1 and heads["audit"]["head"] != GENESIS_HASH
    assert heads["paper:sim1:ledger"]["rows"] == 1
    assert heads[f"paper:{FORWARD_BOOK}:log"]["rows"] == 1


def test_write_load_verify_and_detect_problems(db: Database, tmp_path: Path) -> None:
    db.append_audit("ops", "test", "subject", {"n": 1})
    _paper_rows(db)
    path = tmp_path / "sub" / "anchor.json"
    doc = an.write_anchor(db, path)
    assert doc["schema"] == an.ANCHOR_SCHEMA and doc["path"] == str(path)
    assert "key" not in json.loads(path.read_text(encoding="utf-8"))  # only the key id, never the key
    loaded = an.load_anchor(path)

    ok = an.verify_against_anchor(db, loaded)
    assert ok["ok"] is True and set(ok["chains"]) >= {"audit", "paper:sim1:ledger"}

    db.append_audit("ops", "later", "subject", {"n": 2})  # rows after the anchor are fine
    assert an.verify_against_anchor(db, loaded)["ok"] is True

    loaded["chains"]["mystery"] = {"rows": 1, "head": "x"}
    bad = an.verify_against_anchor(db, loaded)
    assert bad["ok"] is False and bad["chains"]["mystery"]["reason"] == "unknown chain in anchor"

    loaded["chains"].pop("mystery")
    loaded["chains"]["audit"] = {"rows": 1, "head": "0" * 64}
    assert an.verify_against_anchor(db, loaded)["chains"]["audit"]["ok"] is False

    loaded["chains"]["paper:sim1:ledger"] = {"rows": 5, "head": "0" * 64}  # longer than the chain: cut tail
    assert an.verify_against_anchor(db, loaded)["chains"]["paper:sim1:ledger"]["ok"] is False


def test_load_anchor_rejects_other_schema(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
    with pytest.raises(ValueError, match="is not a ceap-chain-anchor/1 anchor"):
        an.load_anchor(p)
