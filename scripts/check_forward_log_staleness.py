"""Dead-man's switch for the paper forward log (G6).

Reads the last recorded session date from ``forward_log.csv`` (first column) and counts the NYSE
sessions after it that have already closed. More than ``--max-missed`` (default 2) means the daily
paper job has stopped recording, and the script exits 1 so the weekly workflow opens an issue.

    git show origin/forward-log:forward_log.csv > /tmp/forward_log.csv
    python scripts/check_forward_log_staleness.py --csv /tmp/forward_log.csv

``--today`` (YYYY-MM-DD) pins the date for tests; by default it is today in New York, and today
counts as missed only after 21:30 UTC, when the daily job should have run.

Exit codes: 0 fresh, 1 stale, 2 unreadable input.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ceap.data.calendar import is_trading_day  # noqa: E402

JOB_TIME_UTC = time(21, 30)  # paper-daily.yml cron


def last_session(csv_path: Path) -> date:
    rows = [r for r in csv.reader(csv_path.read_text(encoding="utf-8").splitlines()) if r]
    if len(rows) < 2:  # header only
        raise ValueError(f"{csv_path} has no recorded sessions")
    return date.fromisoformat(rows[-1][0].strip())


def missed_sessions(last: date, today: date, now_utc: datetime | None = None) -> list[date]:
    """Closed NYSE sessions after ``last`` up to ``today`` (today only once the job time has passed)."""
    out: list[date] = []
    d = last + timedelta(days=1)
    while d <= today:
        if d == today and now_utc is not None and now_utc.time() < JOB_TIME_UTC:
            break
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--max-missed", type=int, default=2)
    ap.add_argument("--today", type=date.fromisoformat, default=None)
    args = ap.parse_args(argv)
    try:
        last = last_session(args.csv)
    except (OSError, ValueError, IndexError) as exc:
        print(f"cannot read the forward log: {exc}", file=sys.stderr)
        return 2
    now = None if args.today else datetime.now(UTC)
    today = args.today or now.date()  # type: ignore[union-attr]
    missed = missed_sessions(last, today, now)
    print(f"last recorded session {last}; NYSE sessions closed since: {len(missed)} {[d.isoformat() for d in missed]}")
    if len(missed) > args.max_missed:
        print(f"STALE: more than {args.max_missed} sessions missed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
