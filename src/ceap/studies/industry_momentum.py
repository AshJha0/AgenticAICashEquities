"""Study E1: preregistered 49-industry momentum (0.9.0 WS-E).

Plain idea: each month, buy the 7 Ken French industries with the best 6-month return and short the
7 worst, hold one month, and test whether the net mean return is above zero in a holdout that is
opened exactly once.

Formal rules live in ``docs/research/e1/prereg_industry_momentum_2026-11.md``; this module refuses
to run if that file's hash differs from ``docs/research/e1/prereg_lock.json``
(:func:`ceap.data.real.prereg.require_prereg_lock`), refuses to return holdout months unless called
with ``open_holdout=True``, and refuses a second holdout opening (``holdout_ledger.json``).

Worked example: with 49 industries, ranks 1-7 get weight +1/7 and ranks 43-49 get -1/7; if the
long leg averages +1.2 % and the short leg +0.4 % in a month, the gross return is 0.8 %; with
turnover 1.0 at 10 bps per side the net return is 0.8 % - 0.10 % = 0.7 %.
"""

from __future__ import annotations

import io
import json
import math
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ceap.data.real.prereg import PreregistrationError, file_sha256, require_prereg_lock
from ceap.paths import data_path

REPO_ROOT = Path(__file__).resolve().parents[3]
STUDY_DIR = REPO_ROOT / "docs" / "research" / "e1"
PREREG = STUDY_DIR / "prereg_industry_momentum_2026-11.md"
LOCK = STUDY_DIR / "prereg_lock.json"
LEDGER = STUDY_DIR / "holdout_ledger.json"
HASHES = STUDY_DIR / "data_hashes.json"
CACHE_DIR = data_path("real_cache", "kf")

IND49 = "49_Industry_Portfolios_CSV.zip"
FF3 = "F-F_Research_Data_Factors_CSV.zip"
FF5 = "F-F_Research_Data_5_Factors_2x3_CSV.zip"
UMD = "F-F_Momentum_Factor_CSV.zip"
KF_FILES = (IND49, FF3, FF5, UMD)

FORMATION = 6
LEG = 7
MIN_BREADTH = 30
SEED = 20261109
IN_SAMPLE = ("1927-01", "1989-12")
HOLDOUT = ("1990-01", "2015-12")
LIVE_START = "2016-01"

ETF_MAP: dict[str, tuple[str, ...]] = {
    "XLB": ("Chems", "Steel", "Paper", "Boxes", "Mines"),
    "XLE": ("Oil",),
    "XLF": ("Banks", "Insur", "Fin"),
    "XLI": ("Mach", "ElcEq", "Aero", "Trans"),
    "XLK": ("Hardw", "Softw", "Chips"),
    "XLP": ("Food", "Soda", "Beer", "Smoke", "Hshld"),
    "XLU": ("Util",),
    "XLV": ("Hlth", "MedEq", "Drugs"),
    "XLY": ("Autos", "Rtail", "Meals", "Clths", "Fun"),
}


# ---------------------------------------------------------------- data ---------------------------


def record_hashes(cache_dir: Path = CACHE_DIR, out: Path = HASHES) -> dict[str, str]:
    """Write the SHA-256 of each cached raw file (bytes only; no returns are parsed)."""
    import hashlib

    hashes = {n: hashlib.sha256((cache_dir / n).read_bytes()).hexdigest() for n in KF_FILES}
    out.write_text(
        json.dumps({"recorded_on": datetime.now(UTC).date().isoformat(), "sha256": hashes}, indent=2) + "\n",
        encoding="utf-8",
    )
    return hashes


def verify_hashes(cache_dir: Path = CACHE_DIR, rec: Path = HASHES) -> dict[str, str]:
    import hashlib

    want = json.loads(rec.read_text(encoding="utf-8"))["sha256"]
    for n, h in want.items():
        got = hashlib.sha256((cache_dir / n).read_bytes()).hexdigest()
        if got != h:
            raise PreregistrationError(f"cached {n} hash {got} differs from recorded {h}")
    return dict(want)


def parse_first_monthly_block(text: str) -> pd.DataFrame:
    """First block of ``YYYYMM,v1,v2,...`` rows after a header line starting with ','.

    Percent becomes decimal; -99.99 and -999 become NaN. The index is a monthly ``PeriodIndex``.
    """
    header: list[str] | None = None
    rows: list[list[float]] = []
    idx: list[str] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if header is None:
            if line.startswith(",") and len(parts) > 1:
                header = parts[1:]
            continue
        if len(parts) == len(header) + 1 and parts[0].isdigit() and len(parts[0]) == 6:
            idx.append(parts[0])
            rows.append([float(p) for p in parts[1:]])
        elif rows:
            break
    if header is None or not rows:
        raise ValueError("no monthly block found")
    df = pd.DataFrame(rows, columns=header, index=pd.PeriodIndex([f"{s[:4]}-{s[4:]}" for s in idx], freq="M"))
    df = df.mask(df <= -99.99)
    return df / 100.0


def read_zip(name: str, cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO((cache_dir / name).read_bytes())) as zf:
        text = zf.read(zf.namelist()[0]).decode("latin-1")
    return parse_first_monthly_block(text)


# ---------------------------------------------------------------- strategy -----------------------


@dataclass(frozen=True)
class Book:
    gross: pd.Series
    turnover: pd.Series
    dropped: list[str]


def build_book(
    rets: pd.DataFrame, formation: int = FORMATION, leg: int = LEG, min_breadth: int = MIN_BREADTH
) -> Book:
    """Long top ``leg`` / short bottom ``leg`` on the compounded ``formation``-month return.

    The return of holding month m+1 uses signals from months m-formation+1 .. m only.
    """
    r = rets.to_numpy(dtype=float)
    cols = np.arange(r.shape[1])
    gross: dict[Any, float] = {}
    turn: dict[Any, float] = {}
    dropped: list[str] = []
    prev_w: np.ndarray | None = None
    for t in range(formation - 1, len(r) - 1):
        window = r[t - formation + 1 : t + 1]
        nxt = r[t + 1]
        ok = ~np.isnan(window).any(axis=0) & ~np.isnan(nxt)
        label = rets.index[t + 1]
        if ok.sum() < max(min_breadth, 2 * leg):
            dropped.append(str(label))
            prev_w = None
            continue
        sig = np.prod(1.0 + window[:, ok], axis=0) - 1.0
        elig = cols[ok]
        order = np.lexsort((elig, -sig))  # descending signal, ties by column order
        longs, shorts = elig[order[:leg]], elig[order[-leg:]]
        w = np.zeros(r.shape[1])
        w[longs] = 1.0 / leg
        w[shorts] = -1.0 / leg
        if prev_w is None:
            to = float(np.abs(w).sum())
        else:
            to = float(np.abs(w - prev_w).sum())
        gross[label] = float(nxt[longs].mean() - nxt[shorts].mean())
        turn[label] = to
        prev_w = _drift(w, nxt)
    return Book(pd.Series(gross, dtype=float), pd.Series(turn, dtype=float), dropped)


def _drift(w: np.ndarray, ret: np.ndarray) -> np.ndarray:
    """Weights after one month of returns, each leg re-scaled to gross 1."""
    out = np.zeros_like(w)
    g = np.nan_to_num(ret)
    for mask in (w > 0, w < 0):
        if mask.any():
            v = np.abs(w[mask]) * (1.0 + g[mask])
            out[mask] = np.sign(w[mask]) * v / v.sum()
    return out


def net_returns(book: Book, bps_per_side: float) -> pd.Series:
    return book.gross - bps_per_side / 1e4 * book.turnover


def breakeven_bps(book: Book) -> float:
    return float(book.gross.mean() / book.turnover.mean() * 1e4)


# ---------------------------------------------------------------- statistics ---------------------


def andrews_lag(u: np.ndarray) -> int:
    """Andrews (1991) AR(1) plug-in bandwidth for the Bartlett kernel, floored to an integer."""
    u = np.asarray(u, dtype=float) - np.mean(u)
    n = len(u)
    rho = float(np.dot(u[1:], u[:-1]) / np.dot(u[:-1], u[:-1]))
    rho = max(min(rho, 0.97), -0.97)
    alpha = 4 * rho**2 / (1 - rho**2) ** 2
    return int(math.floor(1.1447 * (alpha * n) ** (1 / 3)))


def nw_ols(y: np.ndarray, xmat: np.ndarray) -> dict[str, Any]:
    """OLS with Newey-West (Bartlett) covariance; lag from Andrews on the residuals."""
    y = np.asarray(y, float)
    xmat = np.asarray(xmat, float)
    n = len(y)
    xtx_inv = np.linalg.inv(xmat.T @ xmat)
    beta = xtx_inv @ xmat.T @ y
    e = y - xmat @ beta
    lag = andrews_lag(e)
    xe = xmat * e[:, None]
    smat = xe.T @ xe
    for j in range(1, lag + 1):
        g = xe[j:].T @ xe[:-j]
        smat += (1 - j / (lag + 1)) * (g + g.T)
    cov = xtx_inv @ smat @ xtx_inv
    se = np.sqrt(np.diag(cov))
    return {"beta": beta, "se": se, "t": beta / se, "lag": lag, "n": n}


def nw_mean_t(x: pd.Series) -> dict[str, Any]:
    res = nw_ols(x.to_numpy(), np.ones((len(x), 1)))
    return {"mean": float(res["beta"][0]), "t": float(res["t"][0]), "lag": res["lag"], "n": res["n"]}


def sharpe_ann(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    return float(x.mean() / x.std(ddof=1) * math.sqrt(12))


def stationary_bootstrap_sharpe_ci(
    x: pd.Series, mean_block: float = 6.0, n_boot: int = 10_000, seed: int = SEED
) -> tuple[float, float]:
    """95 % percentile CI of the annualised Sharpe (Politis and Romano 1994)."""
    v = x.to_numpy(dtype=float)
    n = len(v)
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    out = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        new = rng.random(n) < p
        starts = rng.integers(n, size=n)
        for i in range(1, n):
            idx[i] = starts[i] if new[i] else (idx[i - 1] + 1) % n
        out[b] = sharpe_ann(v[idx])
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def market_state(ff3: pd.DataFrame, months: int = 36) -> pd.Series:
    """DOWN (True) when the cumulative total market return over the ``months`` ending at the
    formation month is negative. Indexed by the *holding* month (formation + 1)."""
    mkt = ff3["Mkt-RF"] + ff3["RF"]
    cum = (1 + mkt).rolling(months).apply(np.prod, raw=True) - 1
    down = (cum < 0).where(cum.notna())
    down.index = down.index + 1
    return down


def alpha_regression(gross: pd.Series, factors: pd.DataFrame) -> dict[str, Any]:
    df = pd.concat([gross.rename("y"), factors], axis=1, join="inner").dropna()
    xmat = np.column_stack([np.ones(len(df)), df[factors.columns].to_numpy()])
    res = nw_ols(df["y"].to_numpy(), xmat)
    return {
        "alpha_monthly": float(res["beta"][0]),
        "t_alpha": float(res["t"][0]),
        "lag": res["lag"],
        "n": res["n"],
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
        "betas": dict(zip(factors.columns, map(float, res["beta"][1:]), strict=True)),
    }


def state_test(gross: pd.Series, down: pd.Series) -> dict[str, Any]:
    df = pd.concat([gross.rename("y"), down.rename("d")], axis=1, join="inner").dropna()
    if df["d"].nunique() < 2:  # one state only (e.g. no DOWN month in the live window)
        return {
            "status": "NOT TESTABLE",
            "reason": "only one market state in the window",
            "n": len(df),
            "n_down": int(df["d"].sum()),
            "h2_reject": None,
        }
    xmat = np.column_stack([np.ones(len(df)), df["d"].astype(float).to_numpy()])
    res = nw_ols(df["y"].to_numpy(), xmat)
    up, dn = df.loc[df["d"] == 0, "y"], df.loc[df["d"] == 1, "y"]
    return {
        "mean_up": float(up.mean()),
        "mean_down": float(dn.mean()),
        "n_up": len(up),
        "n_down": len(dn),
        "b_down": float(res["beta"][1]),
        "t_b_down": float(res["t"][1]),
        "lag": res["lag"],
        "h2_reject": bool(res["t"][1] < -1.645),
    }


def window_stats(
    book: Book, start: str, end: str, factors: dict[str, pd.DataFrame], down: pd.Series
) -> dict[str, Any]:
    s, e = pd.Period(start, "M"), pd.Period(end, "M")
    sel = (book.gross.index >= s) & (book.gross.index <= e)
    sub = Book(book.gross[sel], book.turnover[sel], [d for d in book.dropped if start <= d <= end])
    n_months = (e - s).n + 1
    out: dict[str, Any] = {
        "start": start,
        "end": end,
        "months_scored": len(sub.gross),
        "months_dropped": sub.dropped,
        "breadth_void": len(sub.dropped) > 0.05 * n_months,
        "mean_turnover": float(sub.turnover.mean()),
        "breakeven_bps_per_side": breakeven_bps(sub),
    }
    for label, bps in (("gross", 0.0), ("net10", 10.0), ("net20", 20.0)):
        x = net_returns(sub, bps)
        nw = nw_mean_t(x)
        out[label] = {
            "mean_monthly": nw["mean"],
            "nw_t": nw["t"],
            "nw_lag": nw["lag"],
            "sharpe_ann": sharpe_ann(x.to_numpy()),
        }
    out["net10"]["sharpe_ci95"] = stationary_bootstrap_sharpe_ci(net_returns(sub, 10.0))
    out["alpha"] = {k: alpha_regression(sub.gross, f) for k, f in factors.items()}
    out["h2"] = state_test(sub.gross, down)
    return out


# ---------------------------------------------------------------- holdout ledger -----------------


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def record_crash_rerun(ledger: Path, result_path: Path, reason: str) -> None:
    """Complete the *same* opening after a crash that wrote and showed no holdout number.

    Allowed only once, only if the ledger records an opening and no holdout result exists; the
    rerun is appended to the ledger so the history stays visible.
    """
    raw = json.loads(ledger.read_text(encoding="utf-8")) if ledger.exists() else {}
    if not raw.get("opened"):
        raise PreregistrationError("no holdout opening recorded; use open_holdout=True")
    if result_path.exists() or raw.get("crash_rerun"):
        raise PreregistrationError("holdout result already exists or a crash rerun was used")
    raw["crash_rerun"] = {"at_utc": datetime.now(UTC).isoformat(), "git_head": _git_head(), "reason": reason}
    ledger.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def open_holdout_once(ledger: Path = LEDGER, data_hashes: dict[str, str] | None = None) -> None:
    """Record the single holdout opening; raise if one is already recorded."""
    if ledger.exists() and json.loads(ledger.read_text(encoding="utf-8")).get("opened"):
        raise PreregistrationError(f"holdout already opened (see {ledger}); a second look is not allowed")
    ledger.write_text(
        json.dumps(
            {
                "opened": True,
                "opened_at_utc": datetime.now(UTC).isoformat(),
                "git_head_at_opening": _git_head(),
                "data_sha256": data_hashes or {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------- runner -------------------------


def run(
    open_holdout: bool = False, cache_dir: Path = CACHE_DIR, crash_rerun_reason: str | None = None
) -> dict[str, Any]:
    require_prereg_lock(PREREG, LOCK)
    hashes = verify_hashes(cache_dir)
    if crash_rerun_reason:
        record_crash_rerun(LEDGER, STUDY_DIR / "result_holdout.json", crash_rerun_reason)
        open_holdout = True
    elif open_holdout:
        open_holdout_once(data_hashes=hashes)
    ind = read_zip(IND49, cache_dir)
    ff3, ff5, umd = read_zip(FF3, cache_dir), read_zip(FF5, cache_dir), read_zip(UMD, cache_dir)
    last = pd.Period(IN_SAMPLE[1], "M")
    if not open_holdout:  # nothing after the in-sample end is even passed to the strategy
        ind, ff3, ff5, umd = (d[d.index <= last] for d in (ind, ff3, ff5, umd))
    umd = umd.rename(columns={umd.columns[0]: "UMD"})
    f3 = pd.concat([ff3[["Mkt-RF", "SMB", "HML"]], umd], axis=1, join="inner")
    f5 = pd.concat([ff5[["Mkt-RF", "SMB", "HML", "RMW", "CMA"]], umd], axis=1, join="inner")
    book = build_book(ind)
    down = market_state(ff3)
    result: dict[str, Any] = {
        "prereg_sha256": file_sha256(PREREG),
        "data_sha256": hashes,
        "n_industries": ind.shape[1],
        "in_sample": window_stats(book, *IN_SAMPLE, {"ff3_umd": f3, "ff5_umd": f5}, down),
    }
    if open_holdout:
        result["holdout"] = window_stats(book, *HOLDOUT, {"ff5_umd": f5}, down)
        live_end = str(book.gross.index[-1])
        result["live"] = window_stats(book, LIVE_START, live_end, {"ff5_umd": f5}, down)
        result["etf_gate"] = etf_gate(ind)
        t = result["holdout"]["net10"]["nw_t"]
        result["h1_verdict"] = "POSITIVE" if t > 1.645 else "NULL"
    return result


def etf_gate(ind: pd.DataFrame) -> dict[str, Any]:
    """Gate 2: median correlation of SPDR sector ETFs vs mapped industries, 1999-01..2015-12."""
    try:
        import yfinance as yf

        px = yf.download(
            list(ETF_MAP),
            start="1998-12-01",
            end="2016-01-31",
            auto_adjust=True,
            progress=False,
            interval="1mo",
        )["Close"]
    except Exception as exc:  # noqa: BLE001 - any fetch failure means the gate is not run
        return {"status": "NOT RUN", "reason": f"ETF fetch failed: {exc!r}"}
    if px is None or px.empty:
        return {"status": "NOT RUN", "reason": "ETF fetch returned no data"}
    px.index = pd.PeriodIndex(px.index, freq="M")
    etf = px.pct_change()
    s, e = pd.Period("1999-01", "M"), pd.Period("2015-12", "M")
    corr: dict[str, float] = {}
    for tk, inds in ETF_MAP.items():
        mapped = ind[list(inds)].mean(axis=1)
        df = pd.concat([etf[tk], mapped], axis=1, join="inner").dropna()
        df = df[(df.index >= s) & (df.index <= e)]
        corr[tk] = float(df.corr().iloc[0, 1])
    med = float(np.median(list(corr.values())))
    return {
        "status": "PASS" if med >= 0.8 else "FAIL",
        "median_corr": med,
        "corr": corr,
        "months": "1999-01..2015-12",
        "source": "yfinance monthly adjusted closes",
    }
