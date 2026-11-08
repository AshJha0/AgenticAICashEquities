"""R5 v2 in-sample data checks (gates 1 and 2) with Stooq as the primary vendor. No network.

Reads the hand-downloaded Stooq bulk files in ``data/real_cache/stooq/<sym>.us.txt`` and the yfinance
cache in ``data/real_cache/yahoo/``. In-sample dates only: every loader clips at the in-sample end
(2023-12-31) and refuses anything later. No test statistic is computed here.

Writes ``docs/research/v2/coverage_report_v2.csv`` and ``docs/research/v2/insample_checks_v2.json``.

    python scripts/r5v2_insample_checks.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ceap.data import calendar as nyse  # noqa: E402
from ceap.data.real import momentum as mom  # noqa: E402
from ceap.data.real.prereg import require_prereg_lock  # noqa: E402
from ceap.data.real.prices import DailySeries, load_stooq, load_yahoo  # noqa: E402
from ceap.data.real.quality import KNOWN_SPLITS, check_series, cross_check  # noqa: E402
from ceap.data.real.universe_pit import PointInTimeUniverse, stooq_symbol, yahoo_symbol  # noqa: E402

OUT = ROOT / "docs" / "research" / "v2"
FIRST_FORMATION = date(2004, 12, 1)


def vendor_roots(uni: PointInTimeUniverse, start: date, end: date) -> set[str]:
    roots = set()
    for t in uni.ever_members(start, end):
        roots.add(t)
        roots.update(m.vendor for m in uni._map if m.ticker == t)
    return roots


def coverage_for(series: dict[str, DailySeries], uni: PointInTimeUniverse, eval_start, eval_end, days):  # type: ignore[no-untyped-def]
    closes = {k: dict(zip(s.dates, s.close, strict=True)) for k, s in series.items()}
    arrays = {k: np.array([v.get(d, np.nan) for d in days]) for k, v in closes.items()}
    empty = np.array([])

    def prices(t: str, on: date) -> np.ndarray:
        return arrays.get(uni.vendor_symbol(t, on), empty)

    months = mom.build_months(days, prices, uni.members, FIRST_FORMATION, eval_end)
    months = [m for m in months if m.execution >= eval_start]
    cov, worst = mom.coverage(months)
    return months, cov, worst


def main() -> None:
    lock = require_prereg_lock()
    uni = PointInTimeUniverse.from_reference()
    roots = sorted(vendor_roots(uni, lock.warmup_start, lock.eval_end))
    stooq: dict[str, DailySeries] = {}
    yahoo: dict[str, DailySeries] = {}
    for r in roots:
        s = load_stooq(stooq_symbol(r))
        if s and s.dates:
            stooq[r] = s
        y = load_yahoo(yahoo_symbol(r))
        if y and y.dates:
            yahoo[r] = y
    print(f"roots {len(roots)}; stooq {len(stooq)}; yahoo {len(yahoo)}")

    # Day axis: the NYSE calendar (lags are counted in NYSE trading days, prereg section 2). Many
    # Stooq bulk histories start in 2005, so a vendor-derived axis would start the window late.
    days = [
        d if isinstance(d, date) else d.astype("M8[D]").item()
        for d in nyse.trading_days(lock.warmup_start, lock.eval_end).tolist()
    ]
    early = sum(1 for s in stooq.values() if s.dates[0] > date(2004, 1, 1))
    # Gate 1: coverage, Stooq only.
    months, cov, worst = coverage_for(stooq, uni, lock.eval_start, lock.eval_end, days)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "coverage_report_v2.csv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("formation,execution,members,usable,coverage,eligible\n")
        for m in months:
            fh.write(
                f"{m.formation},{m.execution},{m.members},{m.usable},{m.usable / m.members:.4f},{m.eligible}\n"
            )
    by_year: dict[int, list[int]] = {}
    for m in months:
        a = by_year.setdefault(m.execution.year, [0, 0])
        a[0] += m.usable
        a[1] += m.members
    # Descriptive: union of both vendors (Stooq first, yfinance where Stooq has no file).
    union = dict(yahoo) | dict(stooq)
    _, cov_u, worst_u = coverage_for(union, uni, lock.eval_start, lock.eval_end, days)

    members_missing = sorted(set(roots) - set(stooq) - set(yahoo))
    # Gate 2: cross-check and quality.
    agree = common = stooq_days = 0
    per_sym = []
    for r, s in stooq.items():
        stooq_days += max(len(s.dates) - 1, 0)
        if r in yahoo:
            cc = cross_check(s, yahoo[r])
            agree += cc.agree_days
            common += cc.common_days
            per_sym.append((r, cc.common_days, round(cc.agreement, 4)))
    split_ok = {}
    for sym, ex, _ in KNOWN_SPLITS:
        rep = check_series(stooq[sym]) if sym in stooq else None
        split_ok[f"{sym} {ex}"] = bool(rep) and ex not in rep.split_failures  # type: ignore[union-attr]
    hard = sorted(k for k, s in stooq.items() if check_series(s).duplicate_dates or check_series(s).unordered)
    agreement = agree / common if common else 0.0
    overlap = common / stooq_days if stooq_days else 0.0
    worst_syms = sorted(per_sym, key=lambda x: x[2])[:15]
    out = {
        "window": f"{months[0].execution} to {months[-1].exit}",
        "months": len(months),
        "vendor_roots": len(roots),
        "stooq_series": len(stooq),
        "yahoo_series": len(yahoo),
        "stooq_series_starting_after_2004_01_01": early,
        "roots_in_neither_vendor": len(members_missing),
        "gate1_coverage_stooq": round(cov, 4),
        "gate1_worst_month_stooq": round(worst, 4),
        "gate1_min_eligible": min(m.eligible for m in months),
        "gate1_pass": cov >= 0.95,
        "coverage_by_year_stooq": {y: round(a / b, 4) for y, (a, b) in sorted(by_year.items())},
        "descriptive_coverage_stooq_then_yahoo": round(cov_u, 4),
        "descriptive_worst_month_stooq_then_yahoo": round(worst_u, 4),
        "gate2_common_name_days": common,
        "gate2_agreement_within_10bps": round(agreement, 5),
        "gate2_yahoo_overlap_of_stooq_name_days": round(overlap, 4),
        "gate2_known_splits_pass": split_ok,
        "gate2_pass": agreement >= 0.995 and overlap >= 0.80 and all(split_ok.values()),
        "stooq_series_duplicate_or_unordered": hard,
        "lowest_agreement_symbols": worst_syms,
    }
    (OUT / "insample_checks_v2.json").write_text(
        json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps({k: v for k, v in out.items() if k != "lowest_agreement_symbols"}, indent=1, default=str)
    )


if __name__ == "__main__":
    main()
