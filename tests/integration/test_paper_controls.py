"""Desk controls end to end: kill switch, calendar, EOD reconciliation, traceability, report artefact."""

from __future__ import annotations

import json

import pytest

import ceap.paper.job as jobmod
from ceap.paper.config import PaperConfig
from ceap.paper.feed import SyntheticFeed
from ceap.paper.job import NotASessionError, PaperJob, PaperReconciliationError
from ceap.persistence.db import Database

START = "2026-11-09"
CFG = PaperConfig(rebalance_days=5)


@pytest.fixture(scope="module")
def feed():
    return SyntheticFeed("R01", START)


def _nth(job: PaperJob, n: int) -> str:
    return job.panel.day(job.panel.index_of(START) + n)


def test_kill_switch_cancels_open_orders_and_stages_nothing(feed, monkeypatch, tmp_path):
    with Database() as db:
        job = PaperJob(db, CFG, feed, report_dir=tmp_path)
        job.run_day(START)
        assert job.ledger.state("forward").orders
        monkeypatch.setenv("CEAP_PAPER_HALT", "operator: suspect data")
        out = job.run_day(_nth(job, 1))
        st = job.ledger.state("forward")
        assert out[0].orders_staged == 0 and not st.orders and not st.positions
        kinds = [(e["kind"], json.loads(e["payload_json"]).get("reason")) for e in job.ledger.events("forward")]
        assert ("halt", "operator: suspect data") in kinds and ("cancel", "kill_switch") in kinds
        assert db.query("SELECT 1 FROM audit_log WHERE action='paper.halt'")
        doc = json.loads((tmp_path / "forward" / f"{_nth(job, 1)}.json").read_text(encoding="utf-8"))
        assert doc["halt"] == "operator: suspect data"


def test_calendar_no_run_on_holidays_and_half_days_flagged(feed):
    with Database() as db:
        job = PaperJob(db, CFG, feed)
        with pytest.raises(NotASessionError, match="not an NYSE session"):
            job.run_day("2026-11-26")  # Thanksgiving
        out = job.run_day("2026-11-27")  # day after Thanksgiving: 13:00 close
        assert out[-1].snapshot["early_close"] is True
        assert "2026-11-26" not in [o.date for o in out]


def test_every_fill_traces_to_its_staging_and_approval(feed, tmp_path):
    with Database() as db:
        job = PaperJob(db, CFG, feed, report_dir=tmp_path)
        job.run_day(_nth(job, 7))
        staged = {r["staging_id"] for r in db.query("SELECT staging_id FROM staged_orders")}
        approved = {r["id"] for r in db.query("SELECT id FROM approvals WHERE status='APPROVED'")}
        fills = [e for e in job.ledger.events("forward") if e["kind"] in ("fill", "order")]
        assert fills
        for e in fills:
            d = json.loads(e["payload_json"])
            assert d["staging_id"] in staged and d["approval_id"] in approved
            assert e["order_id"].startswith(d["staging_id"])
        day = _nth(job, 1)
        doc = json.loads((tmp_path / "forward" / f"{day}.json").read_text(encoding="utf-8"))
        assert doc["status"] == "OK" and doc["label"].startswith("paper")
        assert doc["fills"] and all(f["approval_id"] in approved for f in doc["fills"])
        a = doc["snapshot"]["attribution"]
        assert set(a) == {"signal", "drift", "financing", "borrow", "costs"}
        md = (tmp_path / "forward" / f"{day}.md").read_text(encoding="utf-8")
        assert "| signal |" in md and "Reconciliation: OK" in md


def test_hard_to_borrow_book_never_goes_short(feed):
    cfg = CFG.with_(risk={"htb_tiers": ["A", "B", "C"]})
    with Database() as db:
        job = PaperJob(db, cfg, feed)
        job.run_day(_nth(job, 6))
        assert all(q > 0 for q in job.ledger.state("forward").positions.values())
        blocks = [json.loads(e["payload_json"])["rule"] for e in job.ledger.events("forward") if e["kind"] == "risk_block"]
        assert blocks and set(blocks) == {"hard_to_borrow"}
        assert db.query("SELECT COUNT(*) AS n FROM audit_log WHERE action='paper.risk_block'")[0]["n"] == len(blocks)


def _wrap(mutate):
    real = jobmod.simulate_day

    def fake(*a, **k):
        res = real(*a, **k)
        mutate(res.state)
        return res

    return fake


@pytest.mark.parametrize("kind", ["position", "drift"])
def test_eod_reconciliation_fails_loudly_and_commits_nothing(feed, monkeypatch, tmp_path, kind):
    with Database() as db:
        job = PaperJob(db, CFG, feed, report_dir=tmp_path)
        job.run_day(START)
        before = db.query("SELECT COUNT(*) AS n FROM paper_ledger")[0]["n"]
        if kind == "position":
            monkeypatch.setattr(jobmod, "simulate_day", _wrap(lambda st: st.positions.update(
                {next(iter(st.positions)): st.positions[next(iter(st.positions))] + 1})))
        else:  # the book's target moves with no order to reach it: unexplained target-vs-actual drift
            monkeypatch.setattr(jobmod, "simulate_day", _wrap(lambda st: st.target.update(
                {k: 3 * v for k, v in list(st.target.items())[:1]})))
            monkeypatch.setattr(PaperConfig, "rebalance", property(lambda self: 10_000))
        day = _nth(job, 1)
        with pytest.raises(PaperReconciliationError) as exc:
            job.run_day(day)
        assert {b["type"] for b in exc.value.report["breaks"]} >= {kind}
        assert db.query("SELECT COUNT(*) AS n FROM paper_ledger")[0]["n"] == before
        assert not job.ledger.has_day("forward", day)
        doc = json.loads((tmp_path / "forward" / f"{day}.json").read_text(encoding="utf-8"))
        assert doc["status"] == "FAILED_RECONCILIATION"
