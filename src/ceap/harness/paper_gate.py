"""Paper-day replay gate and forward-log status page (0.9.0, G11).

Plain idea. A forward record is only worth publishing if every recorded day can be recomputed from
its inputs. So before a day is published, the gate replays the forward book from the raw feed into a
fresh, private database and compares the result with what the ledger recorded. Any difference blocks
publication.

Formal statement. For a recorded database ``R`` and a day ``D``, let ``F`` be a fresh database in which
``PaperJob(F, config).run_day(D)`` has run from the forward start (the feed is deterministic for the
synthetic and committed real feeds). The gate passes when, for every date ``d <= D`` recorded in
``R``, the rows of ``paper_days`` agree:

* ``equity`` within an absolute tolerance of 1e-6 (currency units; the ledger reconciles to 1e-6);
* ``state_json`` (positions, cash, open orders) equal as parsed JSON, floats within 1e-9 relative;
* ``snapshot_json`` equal as parsed JSON after dropping :data:`VOLATILE_KEYS` (wall-clock stamps and
  per-run identifiers, which differ by construction).

It also fails when ``R`` has a date the replay does not, or the reverse. The replay never touches
``R``: it reads it, and writes only to ``F``.

Worked example. ``tests/unit/test_r090_paper_gate.py`` records 5 sessions, gates them (pass, 5 days
compared), then changes one recorded ``equity`` by 1.0 and gates again: one difference, on that date.

The status page (``render_status_page``) is the static HTML the Pages site shows for the forward log:
last recorded day, number of sessions, the gate verdict and the last rows. It writes a file; it never
pushes anything anywhere.
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: snapshot keys that differ between two honest runs (wall clock, per-run identifiers)
VOLATILE_KEYS = frozenset(
    {"recorded_at", "generated_at", "decided_at", "requested_at", "run_id", "execution_id", "wall_clock"}
)
EQUITY_ABS_TOL = 1e-6
FLOAT_REL_TOL = 1e-9
FORWARD_BOOK = "forward"


@dataclass
class GateReport:
    day: str
    compared: int = 0
    differences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.differences

    def to_dict(self) -> dict[str, Any]:
        return {"day": self.day, "ok": self.ok, "compared": self.compared, "differences": self.differences}


def _strip(doc: Any) -> Any:
    if isinstance(doc, dict):
        return {k: _strip(v) for k, v in doc.items() if k not in VOLATILE_KEYS}
    if isinstance(doc, list):
        return [_strip(v) for v in doc]
    return doc


def _diff(a: Any, b: Any, path: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{path}.{key}: only in {'recorded' if key in a else 'replayed'}")
            else:
                _diff(a[key], b[key], f"{path}.{key}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            _diff(x, y, f"{path}[{i}]", out)
    elif isinstance(a, float) or isinstance(b, float):
        try:
            same = math.isclose(float(a), float(b), rel_tol=FLOAT_REL_TOL, abs_tol=1e-12)
        except (TypeError, ValueError):
            same = False
        if not same:
            out.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def _days(db: Any, book: str) -> dict[str, dict[str, Any]]:
    rows = db.query("SELECT date, equity, snapshot_json, state_json FROM paper_days WHERE book=?", (book,))
    return {r["date"]: r for r in rows}


def compare_days(recorded: Any, replayed: Any, day: str, book: str = FORWARD_BOOK) -> GateReport:
    """Diff the ``paper_days`` rows of two databases up to ``day`` (see the module docstring)."""
    rec = {d: r for d, r in _days(recorded, book).items() if d <= day}
    rep = {d: r for d, r in _days(replayed, book).items() if d <= day}
    report = GateReport(day=day)
    for d in sorted(set(rec) | set(rep)):
        if d not in rec or d not in rep:
            report.differences.append(
                {"date": d, "field": "row", "detail": f"only in {'recorded' if d in rec else 'replayed'}"}
            )
            continue
        report.compared += 1
        a, b = rec[d], rep[d]
        # written as "not within" so a NaN equity on either side is a difference, never a pass
        if not abs(float(a["equity"]) - float(b["equity"])) <= EQUITY_ABS_TOL:
            report.differences.append(
                {"date": d, "field": "equity", "detail": f"{a['equity']!r} != {b['equity']!r}"}
            )
        for column in ("state_json", "snapshot_json"):
            found: list[str] = []
            _diff(_strip(json.loads(a[column])), _strip(json.loads(b[column])), column, found)
            report.differences.extend({"date": d, "field": column, "detail": f} for f in found[:20])
    return report


def replay_gate(
    recorded: Any, config: Any, day: str, *, feed: Any = None, risk_free: Any = None
) -> GateReport:
    """Replay the forward book to ``day`` in a fresh in-memory database and compare with ``recorded``."""
    from ceap.paper.job import PaperJob
    from ceap.persistence.db import Database

    with Database() as fresh:
        job = PaperJob(fresh, config, feed, allow_paper_auto=True, risk_free=risk_free, report_dir="")
        job.report_dir = None  # the replay writes no report files
        job.run_day(day)
        return compare_days(recorded, fresh, day)


def render_status_page(
    recorded: Any, gate: GateReport | None, out: str | Path, *, last_rows: int = 10
) -> Path:
    """Write the forward-log status page (static HTML for Pages). Returns the path written."""
    days = sorted(_days(recorded, FORWARD_BOOK).items())
    last = days[-1][0] if days else "none yet"
    verdict = (
        "not run" if gate is None else ("PASS" if gate.ok else f"FAIL ({len(gate.differences)} differences)")
    )
    rows = "".join(
        f"<tr><td>{html.escape(d)}</td><td>{float(r['equity']):,.2f}</td></tr>"
        for d, r in days[-last_rows:][::-1]
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CEAP forward log</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --muted:#5f6368; --line:#d0d4d9; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111315; --fg:#e8eaed; --muted:#9aa0a6; --line:#3c4043; }} }}
body {{ background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; margin:0 auto; max-width:720px; padding:16px; }}
table {{ border-collapse:collapse; width:100%; }} td, th {{ border-bottom:1px solid var(--line); padding:4px 8px; text-align:right; }}
td:first-child, th:first-child {{ text-align:left; }} .muted {{ color:var(--muted); }}
</style></head><body>
<h1>CEAP forward log</h1>
<p class="muted">Paper trading on simulated fills. Not trading, not advice.</p>
<p>Last recorded session: <strong>{html.escape(last)}</strong>. Sessions recorded: <strong>{len(days)}</strong>.</p>
<p>Replay gate{"" if gate is None else " for " + html.escape(gate.day)}: <strong>{html.escape(verdict)}</strong>.</p>
<table><thead><tr><th>Date</th><th>Equity</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>
"""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8", newline="\n")
    return path
