"""G6: the weekly dead-man's switch on the paper forward log, and the workflows that raise an issue.

Repository-integrity tier (H5): reads scripts/ and .github/ through ``repo_root``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from repo_root import ROOT, load_script

stale = load_script("check_forward_log_staleness")


def _csv(tmp_path, last: str | None):
    p = tmp_path / "forward_log.csv"
    p.write_text("session_date,nav\n" + (f"{last},100\n" if last else ""), encoding="utf-8")
    return p


def test_g6_fresh_log_passes(tmp_path):
    # Fri 2026-11-06 recorded; Mon 09 and Tue 10 closed since: 2 missed is the limit, still fresh
    assert stale.main(["--csv", str(_csv(tmp_path, "2026-11-06")), "--today", "2026-11-10"]) == 0


def test_g6_three_missed_sessions_fail(tmp_path):
    assert stale.main(["--csv", str(_csv(tmp_path, "2026-11-06")), "--today", "2026-11-11"]) == 1


def test_g6_holidays_and_weekends_are_not_sessions():
    # Thanksgiving 2026-11-26 is a holiday; 27th is a (half) session
    missed = stale.missed_sessions(date(2026, 11, 25), date(2026, 11, 30))
    assert missed == [date(2026, 11, 27), date(2026, 11, 30)]


def test_g6_today_counts_only_after_the_job_time():
    before = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
    after = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
    assert stale.missed_sessions(date(2026, 11, 9), date(2026, 11, 10), before) == []
    assert stale.missed_sessions(date(2026, 11, 9), date(2026, 11, 10), after) == [date(2026, 11, 10)]


def test_g6_unreadable_log_is_exit_2(tmp_path):
    assert stale.main(["--csv", str(_csv(tmp_path, None))]) == 2
    assert stale.main(["--csv", str(tmp_path / "missing.csv")]) == 2


def test_g6_workflows_open_an_issue_on_failure():
    daily = (ROOT / ".github/workflows/paper-daily.yml").read_text(encoding="utf-8")
    weekly = (ROOT / ".github/workflows/paper-staleness.yml").read_text(encoding="utf-8")
    for text in (daily, weekly):
        assert "issues: write" in text and "if: failure()" in text and "gh issue create" in text
    assert "check_forward_log_staleness.py" in weekly and "--max-missed 2" in weekly
    assert "PAPER_DAILY_ENABLED" in weekly
