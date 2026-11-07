"""Build the committed R5 reports from the local price cache (items 5.2, 5.3, 5.5). No network.

Writes ``docs/research/coverage_report.csv`` (monthly coverage), ``docs/research/quality_report.json``
(quality gate and cross-check) and ``docs/research/replication_report.json`` (UMD gate, FF5 + UMD
loadings, per-year, crash months, breakeven, survivor-only bias). In-sample range only; the primary
test statistic of the preregistration is never computed here (item 5.6).

    python scripts/fetch_real_reports.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ceap.analytics.factor_check import crash_table, load_ff, per_year, replication_gate  # noqa: E402
from ceap.analytics.real_costs import breakeven_cost_bps, sensitivity_table  # noqa: E402
from ceap.data import calendar as nyse  # noqa: E402
from ceap.data.real import momentum as mom  # noqa: E402
from ceap.data.real.prereg import require_prereg_lock  # noqa: E402
from ceap.data.real.prices import CACHE, DailySeries, load_stooq, load_yahoo  # noqa: E402
from ceap.data.real.quality import KNOWN_SPLITS, check_series, cross_check  # noqa: E402
from ceap.data.real.universe_pit import PointInTimeUniverse, stooq_symbol, yahoo_symbol  # noqa: E402

DOCS = ROOT / "docs" / "research"
FIRST_FORMATION = date(2004, 12, 1)


def main() -> None:
    lock = require_prereg_lock()
    uni = PointInTimeUniverse.from_reference()
    series: dict[str, DailySeries] = {}
    for f in sorted((CACHE / "yahoo").glob("*.csv")):
        s = load_yahoo(f.stem)
        if s and s.dates:
            series[f.stem] = s
    cal = {d for d in nyse.trading_days(lock.warmup_start, lock.eval_end).tolist()}
    cal = {d if isinstance(d, date) else d.astype("M8[D]").item() for d in cal}

    # quality gate
    reports = {k: check_series(s, cal) for k, s in series.items()}
    stooq_hits = []
    agree = common = 0
    for k, s in series.items():
        st = load_stooq(stooq_symbol(k.replace("-", ".")))
        if st:
            cc = cross_check(st, s)
            stooq_hits.append(k)
            agree += cc.agree_days
            common += cc.common_days
    splits = {
        f"{sym} {ex}": (ex.isoformat() not in [d.isoformat() for d in reports[sym].split_failures])
        for sym, ex, _ in KNOWN_SPLITS
        if sym in reports
    }
    quality = {
        "series": len(series),
        "hard_fail_series": sorted(k for k, r in reports.items() if r.hard_fail),
        "series_with_duplicates": sum(1 for r in reports.values() if r.duplicate_dates),
        "series_unordered": sum(1 for r in reports.values() if r.unordered),
        "series_with_stale_runs": sum(1 for r in reports.values() if r.stale_runs),
        "zero_volume_name_days": sum(r.zero_volume_days for r in reports.values()),
        "name_days": sum(r.n_days for r in reports.values()),
        "off_calendar_name_days": sum(len(r.off_calendar) for r in reports.values()),
        "missing_calendar_name_days": sum(r.missing_calendar_days for r in reports.values()),
        "moves_beyond_40pct": sum(len(r.big_moves) for r in reports.values()),
        "known_splits_pass": splits,
        "cross_check": {
            "secondary_vendor": "stooq",
            "symbols_with_stooq_file": len(stooq_hits),
            "common_name_days": common,
            "agreement_within_10bps": (agree / common) if common else None,
            "status": "RUN"
            if common
            else "NOT RUN: no Stooq files in data/real_cache/stooq "
            "(Stooq serves a JavaScript browser check to scripts; not bypassed, D2)",
        },
    }

    # gross book on the PIT universe
    closes = {k: dict(zip(s.dates, s.close, strict=True)) for k, s in series.items()}
    days = mom.master_days(closes)
    arrays = {k: np.array([v.get(d, np.nan) for d in days]) for k, v in closes.items()}
    empty = np.array([])

    def prices(t: str, on: date) -> np.ndarray:
        return arrays.get(yahoo_symbol(uni.vendor_symbol(t, on)), empty)

    months = mom.build_months(days, prices, uni.members, FIRST_FORMATION, lock.eval_end)
    months = [m for m in months if m.execution >= lock.eval_start]
    cov, worst = mom.coverage(months)
    with (DOCS / "coverage_report.csv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("formation,execution,members,usable,coverage,eligible\n")
        for m in months:
            fh.write(
                f"{m.formation},{m.execution},{m.members},{m.usable},{m.usable / m.members:.4f},{m.eligible}\n"
            )

    survivors = uni.members(days[-1])
    surv = mom.build_months(days, prices, lambda _d: survivors, FIRST_FORMATION, lock.eval_end)
    surv = [m for m in surv if m.execution >= lock.eval_start]

    ff = load_ff()
    gross = {m.execution.strftime("%Y-%m"): m.gross for m in months}
    rep = replication_gate(gross, ff)
    g = [m.gross for m in months if np.isfinite(m.gross)]
    t = [m.turnover for m in months if np.isfinite(m.gross)]
    sg = {m.execution.strftime("%Y-%m"): m.gross for m in surv}
    both = sorted(set(sg) & set(gross))
    replication = {
        "window": f"{months[0].execution} to {months[-1].exit}",
        "months": rep.months,
        "umd_correlation": round(rep.correlation, 4),
        "gate": rep.outcome,
        "min_correlation": 0.5,
        "ff5_umd_loadings": {k: round(v, 4) for k, v in rep.loadings.items()},
        "r_squared": round(rep.r_squared, 4),
        "note": "intercept t-statistic and mean-return t-statistic deliberately not computed (item 5.6)",
        "per_year": [(y, round(a, 4), round(b, 4)) for y, a, b in per_year(gross, ff)],
        "crash_months": [(m, round(a, 4), round(b, 4)) for m, a, b in crash_table(gross, ff)],
        "mean_monthly_turnover": round(float(np.mean(t)), 4),
        "breakeven_one_way_cost_bps": round(breakeven_cost_bps(g, t), 1),
        "flat_cost_sensitivity_annualised_mean": [(c, round(v, 4)) for c, v in sensitivity_table(g, t)],
        "coverage": {
            "overall": round(cov, 4),
            "worst_month": round(worst, 4),
            "min_eligible": min(m.eligible for m in months),
            "gate_95pct": cov >= 0.95,
        },
        "survivor_only": {
            "correlation_with_pit_gross": round(
                float(np.corrcoef([sg[k] for k in both], [gross[k] for k in both])[0, 1]), 4
            ),
            "annualised_mean_gross_minus_pit": round(
                12 * float(np.mean([sg[k] - gross[k] for k in both])), 4
            ),
        },
    }
    (DOCS / "quality_report.json").write_text(
        json.dumps(quality, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    (DOCS / "replication_report.json").write_text(
        json.dumps(replication, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                "coverage": replication["coverage"],
                "umd_correlation": rep.correlation,
                "gate": rep.outcome,
                "cross_check": quality["cross_check"]["status"],
                "splits": splits,
            },
            indent=1,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
