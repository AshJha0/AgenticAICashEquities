"""RP.2 ledger integrity and RP.4 forward statistics."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from ceap.paper.config import PaperConfig
from ceap.paper.forward import compare, export_csv, min_days_to_distinguish, sharpe_ci, verify_csv
from ceap.paper.job import PaperJob
from ceap.persistence.db import Database


@pytest.fixture(scope="module")
def filled(tmp_path_factory):
    """A forward book run for 30 sessions on the synthetic feed (shared, read-only)."""
    path = tmp_path_factory.mktemp("paper") / "paper.db"
    db = Database(path)
    job = PaperJob(db, PaperConfig(), risk_free=None)
    end = job.panel.day(job.panel.index_of("2026-11-09") + 29)
    job.run_day(end)
    db.close()
    return path


def test_min_days_closed_form_three_hand_cases():
    # s = S / sqrt(252); T = (1.95996 + 0.84162 sqrt(1 + s^2/2))^2 / s^2, rounded up (S=1: 1979.6 -> 1980)
    assert min_days_to_distinguish(1.0) == 1980
    assert min_days_to_distinguish(0.5) == 7913
    assert min_days_to_distinguish(2.0) == 496
    assert min_days_to_distinguish(0.0) == float("inf")


def test_sharpe_ci_covers_the_true_sharpe_at_nominal_rate():
    rng = np.random.default_rng(20261107)
    true_d, t, n = 0.08, 252, 400
    true_ann = true_d * np.sqrt(252)
    hits = 0
    for _ in range(n):
        ci = sharpe_ci(rng.normal(true_d * 0.01, 0.01, t))
        hits += ci["ci_low"] <= true_ann <= ci["ci_high"]
    assert 0.93 <= hits / n <= 0.97  # nominal 95 %, tolerance 2 pp


def test_compare_labels():
    assert "inside" in compare({"sharpe": 1.0, "ci_low": -0.5, "ci_high": 2.5}, 1.2)
    assert "below" in compare({"sharpe": -1.0, "ci_low": -2.0, "ci_high": 0.1}, 1.2)
    assert "not enough" in compare(sharpe_ci([0.01]), 1.0)


def test_chain_verifies_and_reconciles(filled):
    with Database(filled) as db:
        from ceap.paper.ledger import PaperLedger

        led = PaperLedger(db)
        assert led.verify("forward").ok
        rec = led.reconcile("forward")
        assert rec["ok"] and rec["days"] == 30


def test_ledger_rows_are_append_only(filled):
    with Database(filled) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.run(lambda c: c.execute("UPDATE paper_ledger SET quantity = 0 WHERE seq = 1"))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.run(lambda c: c.execute("DELETE FROM paper_forward_log"))


def test_tampering_breaks_the_chain_at_that_row_and_reconciliation(tmp_path, filled):
    import shutil

    copy = tmp_path / "t.db"
    shutil.copy(filled, copy)
    with Database(copy) as db:
        from ceap.paper.ledger import PaperLedger

        seq = db.query("SELECT seq FROM paper_ledger WHERE kind='fill' ORDER BY seq LIMIT 1")[0]["seq"]
        # an attacker with file access drops the trigger first
        db.run(lambda c: c.execute("DROP TRIGGER paper_ledger_no_update"))
        db.run(lambda c: c.execute("UPDATE paper_ledger SET quantity = quantity + 1 WHERE seq=?", (seq,)))
        led = PaperLedger(db)
        rep = led.verify("forward")
        assert not rep.ok and rep.first_bad == f"ledger seq {seq}"
        assert not led.reconcile("forward")["ok"]


def test_csv_export_round_trips_and_detects_edits(tmp_path, filled):
    with Database(filled) as db:
        from ceap.paper.ledger import PaperLedger

        out = export_csv(PaperLedger(db), "forward", tmp_path / "fwd.csv")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# schema_version: 1\n# label: paper, synthetic or free daily data, not trading")
    rep = verify_csv(out)
    assert rep.ok and rep.rows == 30
    lines = text.splitlines()
    cells = lines[6].split(",")
    cells[1] = str(float(cells[1]) + 1.0)  # edit the readable equity column only
    (tmp_path / "bad.csv").write_text("\n".join([*lines[:6], ",".join(cells), *lines[7:]]) + "\n", encoding="utf-8")
    rep = verify_csv(tmp_path / "bad.csv")
    assert not rep.ok and "equity" in rep.reason
    edited = text.replace('""equity"":', '""equity"":1', 1)  # edit inside the hashed row_json
    assert edited != text
    (tmp_path / "bad2.csv").write_text(edited, encoding="utf-8")
    assert not verify_csv(tmp_path / "bad2.csv").ok
