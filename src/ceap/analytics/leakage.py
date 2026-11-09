"""Dynamic leakage tests (PLAN 2B.3), replacing the v1 static linter.

Two tests, both behavioural, run against any feature or signal function
``f(close, volume) -> (n_days, n_symbols)``:

**Perturbation (look-ahead) test.** For cut dates ``t``, replace every input row after ``t`` with
independent random values and recompute. Every output row at or before ``t`` must be *bit-identical*
to the unperturbed output. A feature that reads any future row (a centred window, a forward fill
from the future, a full-sample normalisation, an off-by-one shift) changes some row ``<= t``.

**Shuffled-label test.** A feature's predictive power for the next-period return should vanish
when the return labels are permuted in time (the dependence on time order is destroyed). A
pipeline that leaks the label into the feature (for example a feature computed from the forward
return itself, or rows aligned one day late) keeps a large rank IC even against shuffled labels,
because the leaked value is carried along with the permutation of rows. The test computes the
mean cross-sectional rank IC of ``feature[t]`` against ``label[t]`` where the rows of the
(feature, label) pair are re-derived from a time-permuted price panel; in a causal pipeline the
IC is centred on zero. It fails when ``|mean IC| t-statistic`` exceeds ``t_max`` (0.10 D7: lowered from
4.0 to 3.0; a causal feature's null t is standard normal, so 3.0 is still a 0.3 % two-sided false alarm).

**End-to-end canary (0.10 D7).** The two tests above check a feature function; they cannot see an
off-by-one in the backtest's own timing. :func:`leakage_canary` feeds ``simulate_backtest`` a feature
equal to the *realised* holding-period return, delayed ``d`` days. With rebalance (and holding) period
``h`` and execution lag ``L`` the feature window overlaps the earned window on ``h - d - L`` days, so the
gross Sharpe must be very large while ``d + L < h`` and must collapse to noise exactly when
``d + L >= h``. A backtest that filled before the lag, or earned the decision day's return, would keep a
large Sharpe one delay too long.

The tests are deterministic (seeded) and cheap on the 30-name research universe.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

FeatureFn = Callable[[np.ndarray, np.ndarray | None], np.ndarray]


@dataclass(frozen=True)
class LeakageResult:
    name: str
    passed: bool
    detail: str
    first_bad_cut: int | None = None
    statistic: float | None = None


def _rows_equal(a: np.ndarray, b: np.ndarray) -> bool:
    """Bit-identical, with NaN equal to NaN in the same position."""
    return a.shape == b.shape and bool(np.array_equal(a, b, equal_nan=True))


def perturbation_test(
    fn: FeatureFn,
    close: np.ndarray,
    volume: np.ndarray | None = None,
    cuts: Sequence[int] | None = None,
    seed: int = 0,
    name: str = "feature",
) -> LeakageResult:
    """Perturb inputs after each cut ``t``; outputs at rows ``<= t`` must not change at all."""
    c = np.asarray(close, dtype=float)
    v = None if volume is None else np.asarray(volume, dtype=float)
    n = c.shape[0]
    base = np.asarray(fn(c.copy(), None if v is None else v.copy()), dtype=float)
    rng = np.random.default_rng(seed)
    cut_list = list(cuts) if cuts is not None else sorted({n // 4, n // 2, (3 * n) // 4, n - 2})
    for t in cut_list:
        if not 0 <= t < n - 1:
            continue
        cp = c.copy()
        # positive prices with a different path, so ratios and logs stay defined
        cp[t + 1 :] = c[t] * np.exp(np.cumsum(rng.normal(0, 0.05, cp[t + 1 :].shape), axis=0))
        vp = None
        if v is not None:
            vp = v.copy()
            vp[t + 1 :] = rng.uniform(0.5, 2.0, vp[t + 1 :].shape) * np.nanmean(v)
        out = np.asarray(fn(cp, vp), dtype=float)
        if not _rows_equal(out[: t + 1], base[: t + 1]):
            bad = int(np.flatnonzero(~np.all((out[: t + 1] == base[: t + 1]) | (np.isnan(out[: t + 1]) & np.isnan(base[: t + 1])), axis=1))[0])
            return LeakageResult(name, False, f"rows <= {t} changed when only rows > {t} were perturbed (first row {bad})", t)
    return LeakageResult(name, True, f"bit-identical before {len(cut_list)} cuts")


def _rank_ic(a: np.ndarray, b: np.ndarray) -> float:
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 5:
        return float("nan")
    ra = np.argsort(np.argsort(a[m])).astype(float)
    rb = np.argsort(np.argsort(b[m])).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    den = float(np.sqrt((ra * ra).sum() * (rb * rb).sum()))
    return float((ra * rb).sum() / den) if den > 0 else float("nan")


def forward_return(close: np.ndarray, horizon: int = 1) -> np.ndarray:
    """``close[t + h] / close[t] - 1``; the last ``h`` rows are NaN."""
    c = np.asarray(close, dtype=float)
    out = np.full(c.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:-horizon] = c[horizon:] / c[:-horizon] - 1.0
    return out


def mean_ic_t(feature: np.ndarray, label: np.ndarray, step: int = 1) -> tuple[float, float]:
    ics = np.array([_rank_ic(feature[t], label[t]) for t in range(0, feature.shape[0], step)])
    ics = ics[np.isfinite(ics)]
    if ics.size < 3:
        return float("nan"), float("nan")
    sd = float(ics.std(ddof=1))
    return float(ics.mean()), float(ics.mean() / sd * np.sqrt(ics.size)) if sd > 0 else float("nan")


def shuffled_label_test(
    fn: FeatureFn,
    close: np.ndarray,
    volume: np.ndarray | None = None,
    horizon: int = 1,
    seed: int = 0,
    t_max: float = 3.0,
    name: str = "feature",
) -> LeakageResult:
    """Rebuild the price panel from time-shuffled daily returns, recompute feature and label, test IC.

    Shuffling the rows of daily returns destroys every time-series dependence in the data while
    keeping the cross-sectional return distribution. A causal feature then has no information about
    the next-period return (IC t-stat near zero). A feature that reads the label period has a large
    IC on *any* price panel, shuffled or not, so it fails. Non-overlapping label periods
    (``step = horizon``) keep the t-statistic honest.
    """
    c = np.asarray(close, dtype=float)
    rng = np.random.default_rng(seed)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.log(c[1:] / c[:-1])
    r = np.where(np.isfinite(r), r, 0.0)[rng.permutation(r.shape[0])]
    cs = np.vstack([np.log(np.where(np.isfinite(c[0]), c[0], 100.0))[None, :], r]).cumsum(axis=0)
    shuffled = np.exp(cs)
    vol = None if volume is None else np.asarray(volume, dtype=float)[np.concatenate([[0], 1 + rng.permutation(c.shape[0] - 1)])]
    feat = np.asarray(fn(shuffled, vol), dtype=float)
    label = forward_return(shuffled, horizon)
    mean_ic, t = mean_ic_t(feat, label, step=horizon)
    passed = not (np.isfinite(t) and abs(t) > t_max)
    return LeakageResult(
        name, passed, f"mean rank IC {mean_ic:.4f}, t {t:.2f} against labels on a time-shuffled panel (limit {t_max})",
        statistic=t,
    )


def check_feature(
    fn: FeatureFn, close: np.ndarray, volume: np.ndarray | None = None, name: str = "feature", horizon: int = 1
) -> tuple[LeakageResult, LeakageResult]:
    return (
        perturbation_test(fn, close, volume, name=name),
        shuffled_label_test(fn, close, volume, horizon=horizon, name=name),
    )


def _canary_fn(horizon: int, delay: int) -> FeatureFn:
    """Feature at row t = realised return over days ``t+1 .. t+horizon``, shifted back by ``delay`` days."""

    def fn(close: np.ndarray, volume: np.ndarray | None = None) -> np.ndarray:
        c = np.asarray(close, dtype=float)
        n = c.shape[0]
        fwd = np.full(c.shape, np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            fwd[: n - horizon] = c[horizon:] / c[: n - horizon] - 1.0
        out = np.full(c.shape, np.nan)
        if delay < n:
            out[delay:] = fwd[: n - delay]
        return out

    return fn


def leakage_canary(
    ds: Any, delays: Sequence[int] = (0, 1, 2, 3, 4, 5, 6), horizon: int = 5, execution_lag_days: int = 1
) -> list[dict[str, float]]:
    """Gross Sharpe of a long-short book on the delayed realised-return feature, one row per delay.

    Each row also carries ``overlap = max(h - d - L, 0)``: the days of the earned holding window the
    feature has seen. A correct pipeline has a very large Sharpe when ``overlap > 0`` and a noise-level one
    when ``overlap == 0``. The canary signal is registered only for the duration of the call.
    """
    from ceap.analytics.backtest import simulate_backtest
    from ceap.analytics.signals import SIGNALS, SignalSpec

    rows: list[dict[str, float]] = []
    for d in delays:
        name = f"_leakage_canary_h{horizon}_d{d}"
        SIGNALS[name] = SignalSpec(name, "leakage canary (realised return)", 0, horizon, horizon, _canary_fn(horizon, d))
        try:
            run = simulate_backtest(
                ds, name, rebalance_days=horizon, execution_lag_days=execution_lag_days, cost_multiplier=0.0,
                report_bounds=False,
            )
        finally:
            del SIGNALS[name]
        full = run.result.period("full")
        rows.append({
            "delay": float(d), "overlap": float(max(horizon - d - execution_lag_days, 0)),
            "gross_sharpe": float(full.gross_sharpe) if full is not None else float("nan"),
        })
    return rows
