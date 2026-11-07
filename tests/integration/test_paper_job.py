"""RP.3 / RP.4: the daily job through the real approval gateway and SQLite ledger."""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from ceap.cli import main
from ceap.paper.config import PaperConfig
from ceap.paper.feed import SyntheticFeed
from ceap.paper.job import PaperAutoRefusedError, PaperJob, PaperJobError
from ceap.persistence.db import Database

START = "2026-11-09"
CFG = PaperConfig(rebalance_days=5)


def _counts(db: Database) -> dict[str, int]:
    return {t: db.query(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
            for t in ("paper_ledger", "paper_days", "paper_forward_log", "paper_simulated_log", "approvals",
                      "staged_orders")}


def _nth(job: PaperJob, n: int) -> str:
    return job.panel.day(job.panel.index_of(START) + n)


@pytest.fixture
def feed():
    return SyntheticFeed("R01", START)


def test_run_day_is_idempotent_and_catches_up(feed):
    with Database() as db:
        job = PaperJob(db, CFG, feed)
        out = job.run_day(_nth(job, 7))
        assert [o.status for o in out] == ["done"] * 8
        assert [o.catch_up for o in out] == [True] * 7 + [False]
        before, heads = _counts(db), job.ledger.heads("forward")
        again = job.run_day(_nth(job, 7)) + job.run_day(_nth(job, 3))
        assert [o.status for o in again] == ["noop", "noop"]
        assert _counts(db) == before and job.ledger.heads("forward") == heads
        assert job.ledger.verify("forward").ok and job.ledger.reconcile("forward")["ok"]
        assert db.verify_chain().ok


@pytest.mark.parametrize("where", ["after_events", "before_day"])
def test_crash_mid_day_then_rerun_equals_uninterrupted(feed, where):
    with Database() as clean:
        ref = PaperJob(clean, CFG, feed)
        ref.run_day(_nth(ref, 4))
        ref.run_day(_nth(ref, 6))  # same call pattern: the catch-up flag is part of the hashed log row
        want = ref.ledger.heads("forward")
    with Database() as db:
        job = PaperJob(db, CFG, feed)
        job.run_day(_nth(job, 4))
        crash_day = _nth(job, 5)  # a rebalance day (offset 5), so an approval precedes the crash
        with pytest.raises(RuntimeError, match="injected crash"):
            job.run_day(crash_day, _crash=where)
        assert not job.ledger.has_day("forward", crash_day)
        assert job.ledger.last_day("forward")["date"] == _nth(job, 4)  # nothing of the crashed day survived
        assert not db.query("SELECT 1 FROM staged_orders WHERE payload_json LIKE ?", (f'%"as_of":"{crash_day}"%',))
        assert not db.query("SELECT 1 FROM audit_log WHERE action='paper.day_closed' AND subject=?",
                            (f"forward:{crash_day}",))
        job.run_day(_nth(job, 6))
        assert job.ledger.heads("forward") == want
        actions = [r["action"] for r in db.query("SELECT action FROM audit_log ORDER BY seq")]
        assert "paper.approval_reused" in actions


def test_staging_goes_through_the_gateway_and_is_audited(feed):
    with Database() as db:
        job = PaperJob(db, CFG, feed)
        out = job.run_day(START)
        assert out[0].orders_staged > 0
        apr = db.query("SELECT * FROM approvals")[0]
        assert apr["status"] == "APPROVED" and apr["decided_by"] == "paper-auto"
        assert apr["requested_by"] == "paper-job" and apr["tool_id"] == "paper.stage_orders"
        actions = [r["action"] for r in db.query("SELECT action FROM audit_log ORDER BY seq")]
        for a in ("approval.requested", "paper.auto_decide", "approval.decided", "paper.day_closed"):
            assert a in actions
        staged = json.loads(db.query("SELECT payload_json FROM staged_orders")[0]["payload_json"])
        assert staged["account"] == "PAPER_RESEARCH" and staged["approval_id"] == apr["id"]
        assert db.verify_chain().ok


def test_paper_auto_refused_outside_dev_without_flag(feed, monkeypatch):
    monkeypatch.setenv("CEAP_ENV", "prod")
    with Database() as db:
        with pytest.raises(PaperAutoRefusedError):
            PaperJob(db, CFG, feed).run_day(START)
        assert all(v == 0 for v in _counts(db).values())
        PaperJob(db, CFG, feed, allow_paper_auto=True).run_day(START)
        rows = db.query("SELECT payload_json FROM audit_log WHERE action='paper.auto_decide'")
        assert json.loads(rows[0]["payload_json"]) == {"env": "prod", "explicit_flag": True}


def test_forward_record_is_never_backfilled(feed):
    with Database() as db:
        job = PaperJob(db, CFG, feed)
        with pytest.raises(PaperJobError, match="never backfilled"):
            job.run_day(job.panel.day(job.panel.index_of(START) - 1))
        with pytest.raises(PaperJobError, match="never writes the forward book"):
            job.backfill("2026-10-01", "2026-10-05", book="forward")
        out = job.backfill("2026-10-01", "2026-10-09")
        assert out and all(o.book == "simulated" for o in out)
        assert _counts(db)["paper_forward_log"] == 0 and _counts(db)["paper_simulated_log"] == len(out)
        assert [o.status for o in job.backfill("2026-10-01", "2026-10-09")] == ["noop"] * len(out)
        with pytest.raises(PaperJobError, match="gap"):
            job.backfill("2026-10-20", "2026-10-21")


def test_target_uses_no_future_rows(feed):
    with Database() as db:
        job = PaperJob(db, CFG, feed)
        i = job.panel.index_of(START)
        base = job.target_shares(i, 1e7)
        p = job.panel
        noisy = p.close.copy()
        noisy[i + 1:] *= np.random.default_rng(1).uniform(0.5, 1.5, noisy[i + 1:].shape)
        job.panel = replace(p, close=noisy)
        assert job.target_shares(i, 1e7) == base


def test_cli_run_day_report_export_verify(tmp_path, capsys):
    db = str(tmp_path / "p.db")
    assert main(["paper", "run-day", "--date", "2026-11-13", "--db", db]) == 0
    assert main(["paper", "run-day", "--date", "2026-11-14", "--db", db]) == 3  # a Saturday
    assert main(["paper", "run-day", "--date", "2026-11-06", "--db", db]) == 2  # before the forward start
    assert main(["paper", "verify", "--db", db]) == 0
    capsys.readouterr()
    assert main(["paper", "report", "--db", db]) == 0
    text = capsys.readouterr().out
    assert "paper, synthetic or free daily data, not trading" in text
    assert "minimum days to distinguish" in text and "95 % CI" in text
    out = tmp_path / "fwd.csv"
    assert main(["paper", "export", "--db", db, "--out", str(out)]) == 0
    assert main(["paper", "verify-csv", "--csv", str(out)]) == 0


def test_paper_daily_workflow_is_disabled_by_default():
    from pathlib import Path

    import yaml

    wf = yaml.safe_load((Path(__file__).resolve().parents[2] / ".github/workflows/paper-daily.yml").read_text())
    job = wf["jobs"]["run-day"]
    assert job["if"].replace(" ", "") == "${{vars.PAPER_DAILY_ENABLED=='true'}}"
    assert wf[True]["schedule"][0]["cron"] == "30 21 * * 1-5"  # YAML 1.1 reads the `on` key as True
    run = " ".join(s.get("run", "") for s in job["steps"])
    assert "--allow-paper-auto" in run and "forward-log" in run and "ANTHROPIC_API_KEY" not in run
