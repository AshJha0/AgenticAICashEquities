"""Signal combination: ridge over the three z-scored signals (PLAN 2B.2, decision D12).

D12: ridge only, with purged and embargoed walk-forward cross-validation; every hyperparameter
value tried is a trial in the trial ledger; the deflated Sharpe ratio uses the ledger's trial count
and cross-trial Sharpe variance. No gradient boosting, no meta-labeling: three features on thirty
names do not justify them.

Data layout (one research dataset)
    ``X[t, i, :]``  cross-sectional z-scores of the three library signals at date ``t``
                    (membership mask applied), computed from prices up to ``t`` only;
    ``y[t, i]``     the ``h``-day forward return ``close[t+h] / close[t] - 1``, cross-sectionally
                    demeaned. It reads prices up to ``t + h``.

Label-valid in-sample dates are ``start_index <= t <= in_sample_end_index - h``, so no in-sample
label reads an out-of-sample price (this fixes the IS boundary leakage noted in the review).

Purged and embargoed walk-forward CV
    The label-valid in-sample dates are cut into ``n_folds + 1`` contiguous blocks ``B0 .. Bk``.
    Fold ``j`` (``1 <= j <= k``) tests on ``Bj`` and trains on dates ``t`` strictly before it with
    ``t + h + embargo < start(Bj)``: the purge removes every training label whose window
    ``[t, t + h]`` overlaps the test block, and the embargo adds ``embargo`` more days of gap
    against serial correlation in features. Training never uses data after the test block
    (walk-forward), so the embargo after a test block (Lopez de Prado 2018, 7.4) is not needed.

Scoring
    A score ``s_t`` (prediction or single signal) becomes dollar-neutral weights
    ``w = (rank(s) - mean rank) / sum |.|`` on rebalance dates every ``h`` days and is held constant
    for ``h`` days; the daily portfolio return is ``sum_i w_i r_{t,i}`` with simple daily returns.
    Gross of costs. The CV score of a configuration is the per-day Sharpe of its concatenated
    test-block returns; that is what the ledger records.

Out of sample
    The chosen ridge is refitted on every label-valid in-sample date and scored on the
    out-of-sample period, where its deflated Sharpe ratio (Bailey and Lopez de Prado 2014) is
    reported next to each single signal's, all deflated by the same ledger count.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ceap.analytics.leakage import forward_return
from ceap.analytics.research_assessment import TRADING_DAYS, deflated_sharpe
from ceap.analytics.signals import dataset_signal
from ceap.analytics.trial_ledger import TrialLedger

SIGNAL_NAMES: tuple[str, ...] = ("momentum_12_1", "reversal_5", "low_vol_60")
ALPHA_GRID: tuple[float, ...] = (1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0)
DEFAULT_HORIZON = 21
DEFAULT_FOLDS = 5
DEFAULT_EMBARGO = 5
PROMOTE_DSR = 0.95  # out-of-sample deflated Sharpe probability needed to PROMOTE the combination
MODEL_VERSION = "ridge_combination_v1"


# ------------------------------------------------------------------ panel
@dataclass(frozen=True)
class Panel:
    X: np.ndarray  # noqa: N815 - the design tensor (n_days, n_symbols, n_signals)
    y: np.ndarray  # (n_days, n_symbols), demeaned h-day forward return; NaN where undefined
    daily: np.ndarray  # (n_days, n_symbols) simple daily return r[t] = close[t]/close[t-1] - 1
    horizon: int
    start: int  # first index with every signal defined somewhere
    is_end: int  # locked in-sample end index (inclusive)
    signals: tuple[str, ...]


def zscore_rows(signal: np.ndarray, clip: float = 3.0) -> np.ndarray:
    """Vectorised :func:`ceap.analytics.signals.zscore_matrix` (same rules; equal to float rounding)."""
    x = np.asarray(signal, dtype=float)
    fin = np.isfinite(x)
    cnt = fin.sum(axis=1, keepdims=True)
    xs = np.where(fin, x, 0.0)
    mean = xs.sum(axis=1, keepdims=True) / np.maximum(cnt, 1)
    std = np.sqrt((np.where(fin, x - mean, 0.0) ** 2).sum(axis=1, keepdims=True) / np.maximum(cnt, 1))
    ok = (cnt >= 2) & (std > 0) & np.isfinite(std)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(ok, np.clip((x - mean) / np.where(ok, std, 1.0), -clip, clip), 0.0)
    return np.where(fin, z, np.nan)


def build_panel(ds: Any, horizon: int = DEFAULT_HORIZON, signals: Sequence[str] = SIGNAL_NAMES) -> Panel:
    x = np.stack([zscore_rows(dataset_signal(ds, s)) for s in signals], axis=-1)
    close = np.where(ds.member_mask, ds.close, np.nan)
    fwd = forward_return(close, horizon)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows (the last h days) stay NaN
        y = fwd - np.nanmean(fwd, axis=1, keepdims=True)
    daily = np.full(close.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        daily[1:] = close[1:] / close[:-1] - 1.0
    return Panel(x, y, daily, horizon, int(ds.start_index), int(ds.in_sample_end_index), tuple(signals))


def label_valid_is_dates(p: Panel) -> np.ndarray:
    """In-sample dates whose ``h``-day label ends inside the in-sample period."""
    return np.arange(p.start, p.is_end - p.horizon + 1)


def purged_walk_forward(
    dates: np.ndarray, horizon: int, n_folds: int = DEFAULT_FOLDS, embargo: int = DEFAULT_EMBARGO
) -> list[tuple[np.ndarray, np.ndarray]]:
    """``[(train_dates, test_dates)]`` per fold; see the module docstring for the rule."""
    blocks = np.array_split(np.asarray(dates), n_folds + 1)
    folds = []
    for j in range(1, n_folds + 1):
        test = blocks[j]
        if test.size == 0:
            continue
        before = np.concatenate(blocks[:j])
        train = before[before + horizon + embargo < test[0]]
        if train.size:
            folds.append((train, test))
    return folds


def _rows(p: Panel, dates: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xd = p.X[dates].reshape(-1, p.X.shape[-1])
    yd = p.y[dates].reshape(-1)
    m = np.all(np.isfinite(xd), axis=1) & np.isfinite(yd)
    return xd[m], yd[m]


def fit_ridge(p: Panel, dates: np.ndarray, alpha: float) -> np.ndarray:
    """Ridge coefficients without intercept (labels are demeaned) on the pooled rows of ``dates``."""
    from sklearn.linear_model import Ridge

    xd, yd = _rows(p, dates)
    if yd.size < xd.shape[1] + 1:
        return np.zeros(xd.shape[1])
    model = Ridge(alpha=alpha, fit_intercept=False, solver="cholesky").fit(xd, yd)
    return np.asarray(model.coef_, dtype=float)


def predict(p: Panel, coef: np.ndarray) -> np.ndarray:
    """Score per date and name: ``X[t] . coef``; a name with any NaN signal gets NaN."""
    return np.einsum("tis,s->ti", p.X, coef)


def _weights(score: np.ndarray) -> np.ndarray:
    w = np.zeros(score.shape)
    m = np.isfinite(score)
    if m.sum() < 2:
        return w
    r = np.argsort(np.argsort(score[m])).astype(float)
    r -= r.mean()
    s = np.abs(r).sum()
    if s > 0:
        w[m] = r / s
    return w


def portfolio_returns(p: Panel, score: np.ndarray, dates: np.ndarray, stop: int | None = None) -> np.ndarray:
    """Daily returns of the rank-weighted long-short book formed on ``dates[::h]``, each held ``h`` days.

    Returns are read for days ``t+1 .. t+h`` after each rebalance date ``t`` and truncated at the end
    of ``dates`` plus ``h`` (or at ``stop``; CV folds stop at their block end so no day counts twice).
    """
    if dates.size == 0:
        return np.array([])
    last = min(int(dates[-1]) + p.horizon if stop is None else stop, p.daily.shape[0] - 1)
    reb = dates[:: p.horizon].astype(int)
    days = np.concatenate([np.arange(t + 1, min(t + p.horizon, last) + 1) for t in reb])
    held = np.repeat(reb, [max(0, min(t + p.horizon, last) - t) for t in reb])
    weights = np.vstack([_weights(score[t]) for t in reb])
    w = weights[np.searchsorted(reb, held)]
    r = p.daily[days]
    return np.sum(w * np.where(np.isfinite(r), r, 0.0), axis=1)


def _moments(r: np.ndarray) -> tuple[float, float, float]:
    """Per-day Sharpe, skew and (non-excess) kurtosis."""
    if r.size < 3 or float(r.std(ddof=1)) == 0.0:
        return float("nan"), 0.0, 3.0
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    z = (r - mu) / float(r.std())
    return mu / sd, float((z**3).mean()), float((z**4).mean())


# ------------------------------------------------------------------ result
@dataclass(frozen=True)
class SeriesStats:
    name: str
    sharpe_ann: float
    n_days: int
    skew: float
    kurtosis: float
    dsr: float
    sr0_ann: float

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 6) if isinstance(v, float) and math.isfinite(v) else v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class CombinationResult:
    dataset_hash: str
    alpha: float
    coef: dict[str, float]
    cv_sharpe_daily: dict[str, float]  # ledger params key -> CV per-day Sharpe
    n_trials: int
    sharpe_variance: float
    combination: SeriesStats
    singles: dict[str, SeriesStats]
    promote: bool
    horizon: int
    folds: int
    embargo: int
    model_version: str = MODEL_VERSION
    label: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "label": self.label,
            "dataset_hash": self.dataset_hash,
            "alpha": self.alpha,
            "coef": {k: round(v, 10) for k, v in self.coef.items()},
            "cv_sharpe_daily": {k: round(v, 6) for k, v in self.cv_sharpe_daily.items()},
            "n_trials": self.n_trials,
            "sharpe_variance": self.sharpe_variance if math.isfinite(self.sharpe_variance) else None,
            "out_of_sample": {"combination": self.combination.to_dict(), **{k: v.to_dict() for k, v in self.singles.items()}},
            "promote": self.promote,
            "promote_rule": f"out-of-sample deflated Sharpe probability >= {PROMOTE_DSR}",
            "horizon_days": self.horizon,
            "folds": self.folds,
            "embargo_days": self.embargo,
            "gross_of_costs": True,
        }


def _series_stats(name: str, r: np.ndarray, n_trials: int, var: float | None) -> SeriesStats:
    sr, skew, kurt = _moments(r)
    sr_ann = sr * math.sqrt(TRADING_DAYS) if math.isfinite(sr) else float("nan")
    dsr, sr0 = deflated_sharpe(sr_ann, int(r.size), skew, kurt, n_trials, var)
    return SeriesStats(name, sr_ann, int(r.size), skew, kurt, dsr, sr0)


def combine_signals(
    ds: Any,
    ledger: TrialLedger | None = None,
    horizon: int = DEFAULT_HORIZON,
    n_folds: int = DEFAULT_FOLDS,
    embargo: int = DEFAULT_EMBARGO,
    alphas: Sequence[float] = ALPHA_GRID,
) -> CombinationResult:
    """Select the ridge penalty by purged walk-forward CV (each value a ledger trial), report OOS DSR."""
    from ceap.ml.registry import synthetic_label

    trials = ledger if ledger is not None else TrialLedger()
    p = build_panel(ds, horizon)
    dates = label_valid_is_dates(p)
    folds = purged_walk_forward(dates, horizon, n_folds, embargo)
    if not folds:
        raise ValueError("in-sample period too short for purged walk-forward CV")
    dh = ds.dataset_hash
    common = {"horizon": horizon, "folds": n_folds, "embargo": embargo, "version": MODEL_VERSION}
    cv: dict[str, float] = {}

    def cv_sharpe(score_for_fold: Any) -> float:
        rets = np.concatenate([portfolio_returns(p, score_for_fold(tr), te, stop=int(te[-1])) for tr, te in folds])
        return _moments(rets)[0]

    best_alpha, best = alphas[0], -math.inf
    for a in alphas:
        s = cv_sharpe(lambda tr, a=a: predict(p, fit_ridge(p, tr, a)))
        trials.record(dh, {**common, "model": "ridge", "alpha": float(a)}, s)
        cv[f"ridge/alpha={a:g}"] = s
        if math.isfinite(s) and s > best:
            best_alpha, best = float(a), s
    for i, name in enumerate(p.signals):
        s = cv_sharpe(lambda tr, i=i: p.X[..., i])
        trials.record(dh, {**common, "model": "single", "signal": name}, s)
        cv[f"single/{name}"] = s

    summ = trials.summary(dh)
    var = summ.sharpe_variance if math.isfinite(summ.sharpe_variance) else None
    coef = fit_ridge(p, dates, best_alpha)
    oos_dates = np.arange(p.is_end + 1, p.daily.shape[0] - 1)
    combo = _series_stats("combination", portfolio_returns(p, predict(p, coef), oos_dates), summ.n_trials, var)
    singles = {
        name: _series_stats(name, portfolio_returns(p, p.X[..., i], oos_dates), summ.n_trials, var)
        for i, name in enumerate(p.signals)
    }
    return CombinationResult(
        dataset_hash=dh,
        alpha=best_alpha,
        coef=dict(zip(p.signals, (float(c) for c in coef), strict=True)),
        cv_sharpe_daily=cv,
        n_trials=summ.n_trials,
        sharpe_variance=summ.sharpe_variance,
        combination=combo,
        singles=singles,
        promote=bool(math.isfinite(combo.dsr) and combo.dsr >= PROMOTE_DSR),
        horizon=horizon,
        folds=n_folds,
        embargo=embargo,
        label=synthetic_label(),
    )
