"""Forward-record statistics, CSV export and the paper report.

* **Sharpe and its 95 % CI.** Daily excess returns ``r_t`` (equity return minus rf), ``SR_d = mean / sd``
  (``ddof=1``), standard error ``sqrt((1 + SR_d^2 / 2) / T)`` (Lo 2002, iid), both annualised by
  ``sqrt(252)``.
* **Minimum days.** Days ``T`` for a two-sided test at alpha 0.05 with power 0.8 to tell a forward
  Sharpe of 0 apart from the in-sample Sharpe ``S``: ``T = (z_0.975 + z_0.8 sqrt(1 + s^2 / 2))^2 / s^2`` with
  ``s = S / sqrt(252)``. Before that many days a forward record cannot say much either way.
* Every output carries the label ``paper, synthetic or free daily data, not trading``.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ceap.analytics.backtest import TRADING_DAYS_PER_YEAR
from ceap.paper.feed import LABEL
from ceap.paper.ledger import FORWARD_BOOK, LOG_SCHEMA_VERSION, PaperLedger, VerifyReport, chain_hash
from ceap.persistence.db import GENESIS_HASH

Z_975 = 1.959963984540054
Z_80 = 0.8416212335729143
CSV_COLUMNS = ("date", "equity", "daily_return", "rf", "excess_return", "costs", "borrow", "financing",
               "turnover", "gross_exposure", "net_exposure", "n_positions", "catch_up")


def sharpe_ci(excess: np.ndarray | list[float], z: float = Z_975) -> dict[str, float]:
    r = np.asarray(excess, dtype=float)
    r = r[np.isfinite(r)]
    t = int(r.size)
    nan = float("nan")
    if t < 2 or float(np.std(r, ddof=1)) == 0.0:
        return {"days": t, "sharpe": nan, "ci_low": nan, "ci_high": nan, "se": nan}
    sr_d = float(np.mean(r) / np.std(r, ddof=1))
    se_d = math.sqrt((1.0 + 0.5 * sr_d**2) / t)
    a = math.sqrt(TRADING_DAYS_PER_YEAR)
    return {"days": t, "sharpe": sr_d * a, "ci_low": (sr_d - z * se_d) * a, "ci_high": (sr_d + z * se_d) * a,
            "se": se_d * a}


def min_days_to_distinguish(in_sample_sharpe: float, z_alpha: float = Z_975, z_power: float = Z_80) -> float:
    s = in_sample_sharpe / math.sqrt(TRADING_DAYS_PER_YEAR)
    if s == 0 or not math.isfinite(s):
        return float("inf")
    # null SE sqrt(1/T) (Sharpe 0), alternative SE sqrt((1 + s^2/2)/T) (Lo 2002)
    return float(math.ceil((z_alpha + z_power * math.sqrt(1.0 + 0.5 * s * s)) ** 2 / (s * s)))


def compare(forward: dict[str, float], in_sample_sharpe: float | None) -> str:
    if in_sample_sharpe is None or not math.isfinite(forward["sharpe"]):
        return "not enough forward data to compare"
    if forward["ci_low"] <= in_sample_sharpe <= forward["ci_high"]:
        return "in-sample Sharpe lies inside the forward 95 % CI (consistent; not evidence of skill)"
    side = "below" if forward["ci_high"] < in_sample_sharpe else "above"
    return f"forward 95 % CI lies entirely {side} the in-sample Sharpe"


def in_sample_sharpe_from_backtest(scenario: str, signal: str, rebalance_days: int, long_short: bool,
                                   gross_notional: float) -> float:
    from ceap.analytics.backtest import simulate_backtest
    from ceap.data.historical import HistoricalStore

    run = simulate_backtest(HistoricalStore().get(scenario), signal, rebalance_days=rebalance_days,
                            long_short=long_short, gross_notional=gross_notional)
    return float(run.result.periods["in_sample"].sharpe)


def build_report(ledger: PaperLedger, book: str, in_sample_sharpe: float | None, rf_source: str,
                 forward_start: str) -> dict[str, Any]:
    rows = ledger.log(book)
    excess = [r["excess_return"] for r in rows[1:]]  # the first session has no prior equity
    fwd = sharpe_ci(excess)
    eq = [r["equity"] for r in rows]
    return {
        "label": LABEL,
        "book": book,
        "record": "forward" if book == FORWARD_BOOK else "simulated (backfill; not part of the forward record)",
        "forward_start": forward_start,
        "first_date": rows[0]["date"] if rows else None,
        "last_date": rows[-1]["date"] if rows else None,
        "days_elapsed": len(rows),
        "equity": eq[-1] if eq else None,
        "total_return": eq[-1] / eq[0] - 1.0 if len(eq) > 1 and eq[0] else 0.0,
        "sharpe": fwd["sharpe"],
        "sharpe_ci95": [fwd["ci_low"], fwd["ci_high"]],
        "in_sample_sharpe": in_sample_sharpe,
        "min_days_to_distinguish": min_days_to_distinguish(in_sample_sharpe) if in_sample_sharpe else None,
        "comparison": compare(fwd, in_sample_sharpe),
        "turnover_total": float(sum(r["turnover"] for r in rows)),
        "costs_total": float(sum(r["costs"] for r in rows)),
        "rf_source": rf_source,
        "chain": ledger.verify(book).ok,
        "reconciliation": ledger.reconcile(book)["ok"],
    }


def format_report(rep: dict[str, Any]) -> str:
    def f(x: Any, nd: int = 2) -> str:
        return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else (
            f"{x:.{nd}f}" if isinstance(x, float) else str(x))

    lines = [
        f"PAPER REPORT ({rep['label']})",
        f"book: {rep['book']} - {rep['record']}",
        f"forward start: {rep['forward_start']}; sessions {f(rep['first_date'])} to {f(rep['last_date'])} "
        f"({rep['days_elapsed']} days elapsed)",
        f"equity: {f(rep['equity'])}; total return {f(rep['total_return'] * 100)} %",
        f"Sharpe (excess of rf, annualised): {f(rep['sharpe'])}, 95 % CI [{f(rep['sharpe_ci95'][0])}, "
        f"{f(rep['sharpe_ci95'][1])}]",
        f"in-sample Sharpe: {f(rep['in_sample_sharpe'])}; minimum days to distinguish: "
        f"{f(rep['min_days_to_distinguish'], 0)}",
        f"comparison: {rep['comparison']}",
        f"rf: {rep['rf_source']}",
        f"hash chain ok: {rep['chain']}; reconciliation ok: {rep['reconciliation']}",
        f"label: {rep['label']}",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------- CSV
def export_csv(ledger: PaperLedger, book: str, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ledger.log(book)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(f"# schema_version: {LOG_SCHEMA_VERSION}\n# label: {LABEL}\n# book: {book}\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow([*CSV_COLUMNS, "row_json", "prev_hash", "hash"])
        for r in rows:
            body = {k: v for k, v in r.items() if k not in ("prev_hash", "hash")}
            w.writerow([*(r.get(c) for c in CSV_COLUMNS),
                        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                        r["prev_hash"], r["hash"]])
    return path


def verify_csv(path: str | Path) -> VerifyReport:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        header = [next(fh) for _ in range(3)]
        if not header[0].startswith(f"# schema_version: {LOG_SCHEMA_VERSION}"):
            return VerifyReport(False, 0, None, "unknown schema_version")
        rows = list(csv.DictReader(fh))
    prev = GENESIS_HASH
    for r in rows:
        body = json.loads(r["row_json"])
        if r["prev_hash"] != prev or chain_hash(prev, body) != r["hash"]:
            return VerifyReport(False, len(rows), r["date"], "CSV row does not match its hash")
        for c in CSV_COLUMNS:  # the readable columns must say what the hashed row_json says
            v = body.get(c)
            if r[c] != ("" if v is None else str(v)):
                return VerifyReport(False, len(rows), r["date"], f"column {c} differs from the hashed row")
        prev = r["hash"]
    return VerifyReport(True, len(rows))
