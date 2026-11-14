"""Replication gate against Ken French UMD (R5 item 5.5; preregistration section 7, gate 3).

Correlation of the pipeline's gross monthly long-short return with UMD must be at least 0.5,
otherwise the outcome is PIPELINE SUSPECT and no verdict is issued. Also: FF5 + UMD loadings and R2
(descriptive; the intercept's t is deliberately not computed, it belongs to item 5.6), per-year
gross returns, crash months and breakeven cost.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ceap.paths import data_path

FF_PATH = data_path("reference", "ff_factors.csv")
MIN_CORRELATION = 0.5
CRASH_MONTHS = ("2009-03", "2009-04", "2009-05", "2020-11")
FACTORS = ("mkt_rf", "smb", "hml", "rmw", "cma", "umd")


def load_ff(path: Path = FF_PATH) -> dict[str, dict[str, float]]:
    with path.open(encoding="utf-8") as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        return {r["month"]: {k: float(v) for k, v in r.items() if k != "month"} for r in rows}


@dataclass(frozen=True)
class ReplicationResult:
    months: int
    correlation: float
    passed: bool
    loadings: dict[str, float]
    intercept_monthly: float
    r_squared: float

    @property
    def outcome(self) -> str:
        return "PASS" if self.passed else "PIPELINE SUSPECT"


def replication_gate(gross: dict[str, float], ff: dict[str, dict[str, float]]) -> ReplicationResult:
    """``gross`` maps holding month 'YYYY-MM' (labelled by execution date) to the gross return."""
    keys = sorted(k for k in gross if k in ff and np.isfinite(gross[k]))
    if len(keys) < 24:
        raise ValueError(f"need at least 24 overlapping months, got {len(keys)}")
    y = np.array([gross[k] for k in keys])
    umd = np.array([ff[k]["umd"] for k in keys])
    corr = float(np.corrcoef(y, umd)[0, 1])
    x = np.column_stack([np.ones(len(keys))] + [np.array([ff[k][f] for k in keys]) for f in FACTORS])
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    r2 = 1.0 - float(resid @ resid) / float(((y - y.mean()) ** 2).sum())
    return ReplicationResult(
        len(keys),
        corr,
        corr >= MIN_CORRELATION,
        dict(zip(FACTORS, map(float, beta[1:]), strict=True)),
        float(beta[0]),
        r2,
    )


def per_year(gross: dict[str, float], ff: dict[str, dict[str, float]]) -> list[tuple[str, float, float]]:
    """(year, compounded pipeline gross, compounded UMD)."""
    years = sorted({k[:4] for k in gross})
    out = []
    for yr in years:
        ks = [k for k in sorted(gross) if k.startswith(yr) and k in ff and np.isfinite(gross[k])]
        out.append(
            (
                yr,
                float(np.prod([1 + gross[k] for k in ks]) - 1),
                float(np.prod([1 + ff[k]["umd"] for k in ks]) - 1),
            )
        )
    return out


def crash_table(gross: dict[str, float], ff: dict[str, dict[str, float]]) -> list[tuple[str, float, float]]:
    return [(m, gross[m], ff[m]["umd"]) for m in CRASH_MONTHS if m in gross and m in ff]
