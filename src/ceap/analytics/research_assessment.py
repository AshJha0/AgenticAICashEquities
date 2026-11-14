"""Deterministic research assessment: signal statistics + backtest + risk -> verdict and flags.

The Stage 2 analogue of ``attribution.py``: a scoring model with explicit
thresholds whose output is recorded as evidence, shown to the human approver
and reported. The LLM never decides the verdict.

0.4.0 gate chain (D4):

1. ``NO_ALPHA`` unless the in-sample non-overlapping horizon-h IC t (2.7)
   clears the **PROMOTE hurdle** and the deflated Sharpe probability clears
   ``dsr_min``. 0.10 (D3): the hurdle is fixed *a priori*, not fitted to the
   null sweep: ``t >= 1.645`` (one-sided 5 %) for a single preregistered test,
   ``t >= 3.0`` for anything chosen from a search (Harvey, Liu and Zhu 2016).
   The null sweep (2.10) no longer sets the hurdle; it *measures* the
   false-PROMOTE rate at it, and the power study measures detection. The
   deflated Sharpe uses the ledger's trial count floored at the enumerated
   (or preregistered) variants and, when known, the effective count of
   correlated trials (D8, :mod:`ceap.analytics.trial_accounting`).
2. ``REGIME_BREAK`` when the out-of-sample IC t is at or below
   ``-regime_break_t``: the premium reversed.
3. ``OOS_DECAY`` (0.4 name ``OVERFIT``; nothing is fitted) when the
   out-of-sample IC t is below ``oos_ic_t_min`` or the net out-of-sample
   Sharpe is below ``sharpe_min`` without a cost explanation.
4. ``COST_DRAG`` when the gross out-of-sample Sharpe clears the bar but the
   net does not, or costs take at least ``cost_share_max`` of gross.
5. ``CONCENTRATION`` and ``LIMIT_BREACH`` are independent checks; limits are
   evaluated over the whole out-of-sample history (2.12).
6. 0.10 (D4) ``INCONCLUSIVE`` replaces ``REJECT`` when every flag is
   statistical (``NO_ALPHA`` or ``OOS_DECAY``) and the 95 % interval of the
   full-period net Sharpe (Lo 2002 standard error) covers both 0 and
   ``sharpe_min``: the data cannot tell "no edge" from "a tradable edge".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

from ceap.analytics.numerics import sharpe_standard_error
from ceap.domain.findings import HypothesisRow, HypothesisTable
from ceap.domain.research import (
    BacktestResult,
    PortfolioRiskReport,
    ResearchAssessment,
    ResearchFlag,
    ResearchVerdict,
    SignalStatistics,
)
from ceap.paths import data_path

THRESHOLDS_PATH = data_path("reference", "research_thresholds.json")
TRADING_DAYS = 252
EULER_GAMMA = 0.5772156649015329
_N = NormalDist()
# 0.10 (D3): a-priori hurdles. One-sided 5 % for a single preregistered test; t >= 3 for a mined result
# (Harvey, Liu and Zhu 2016, "... and the Cross-Section of Expected Returns", RFS 29(1)). Evidence class: method.
SINGLE_TEST_HURDLE = 1.645
MINED_HURDLE = 3.0
UNSET_HURDLE = MINED_HURDLE
# kept equal to data/reference/research_thresholds.json by a test; the file records the sweep that measured
# the false-PROMOTE rate at the a-priori hurdle (it no longer sets the hurdle)
COMMITTED_HURDLE = MINED_HURDLE
COMMITTED_SWEEP_HASH = "ef474869e5fa503d593b460c7e8921a7d26f106d483a2c5c6c2708dc1faf57d4"


def _load_sweep_hash() -> str:
    try:
        data = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
        return str(data["sweep_hash"])
    except (OSError, KeyError, ValueError):
        return COMMITTED_SWEEP_HASH


_SWEEP_HASH = _load_sweep_hash()


@dataclass(frozen=True)
class ResearchThresholds:
    ic_t_stat_min: float = MINED_HURDLE  # a-priori PROMOTE hurdle for a result chosen from a search (D3)
    ic_t_single_test: float = SINGLE_TEST_HURDLE  # a-priori hurdle for one preregistered test (D3)
    hurdle_sweep_hash: str = _SWEEP_HASH  # the null sweep that measured the false-PROMOTE rate at the hurdle
    sharpe_min: float = 0.5
    oos_is_sharpe_ratio_min: float = 0.5  # reported and scored; no longer a gate (IS Sharpe is too noisy)
    oos_ic_t_min: float = 1.0
    regime_break_t: float = 2.0
    cost_share_max: float = 0.5
    pnl_concentration_max: float = 0.5
    min_dates: int = 30  # non-overlapping in-sample IC observations
    dsr_min: float = 0.5  # deflated Sharpe probability: observed IS Sharpe beats the expected maximum of the trials
    trial_floor: int = 24  # D8: the enumerated search space; a preregistered study passes its own variant count
    inconclusive_z: float = 1.96  # D4: two-sided 95 % interval of the full-period net Sharpe


DEFAULT_THRESHOLDS = ResearchThresholds()


def trial_count(trial: dict[str, Any], th: ResearchThresholds) -> int:
    """Trials for the deflated Sharpe and the hurdle (D8): ledger or effective count, floored at the
    preregistered variants when the record carries them, otherwise at the enumerated search space."""
    from ceap.analytics.trial_accounting import deflation_trial_count

    raw = int(trial.get("n_trials", 1) or 1)
    eff = trial.get("n_trials_effective")
    prereg = trial.get("preregistered_variants")
    floor = int(prereg) if prereg is not None else th.trial_floor
    return deflation_trial_count(raw, float(eff) if eff is not None else None, floor)


def hurdle_for(n_trials: int, th: ResearchThresholds) -> float:
    """A-priori IC t hurdle (D3): the single-test hurdle only when exactly one trial counts."""
    return th.ic_t_single_test if n_trials <= 1 else th.ic_t_stat_min


def sharpe_interval(sharpe_ann: float, n_days: int, z: float = 1.96) -> tuple[float, float]:
    """Two-sided interval for an annualised Sharpe ratio with the iid standard error of Lo (2002) in its
    daily form (``ceap.analytics.numerics.sharpe_standard_error``; Q1, finding R-15). Evidence class: method."""
    if sharpe_ann is None or not math.isfinite(sharpe_ann) or n_days < 2:
        return float("nan"), float("nan")
    se = sharpe_standard_error(sharpe_ann, n_days, TRADING_DAYS)
    return sharpe_ann - z * se, sharpe_ann + z * se


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def _sigmoid(x: float, scale: float) -> float:
    if not _finite(x):
        return 0.0
    return 1.0 / (1.0 + math.exp(-x / scale))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if not _finite(x):
        return lo
    return max(lo, min(hi, x))


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """``SR0``: expected maximum per-period Sharpe of ``n_trials`` trials with the given variance."""
    if n_trials <= 1 or not _finite(sharpe_variance) or sharpe_variance <= 0:
        return 0.0
    return math.sqrt(sharpe_variance) * (
        (1.0 - EULER_GAMMA) * _N.inv_cdf(1.0 - 1.0 / n_trials)
        + EULER_GAMMA * _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    )


def deflated_sharpe(
    sharpe_ann: float, n_days: int, skew: float, kurtosis: float, n_trials: int, sharpe_variance: float | None
) -> tuple[float, float]:
    """Deflated Sharpe ratio (Bailey and Lopez de Prado 2014): ``(DSR probability, SR0 annualised)``.

    Per-day Sharpe ``SR``; ``SR0`` from the ledger's trial count and cross-trial variance (the null value
    ``1 / T`` when the ledger has fewer than two trials with a variance);
    ``DSR = Phi((SR - SR0) sqrt(T - 1) / sqrt(1 - skew SR + (kurt - 1) / 4 SR^2))``.
    """
    if not _finite(sharpe_ann) or n_days < 3:
        return float("nan"), float("nan")
    sr = sharpe_ann / math.sqrt(TRADING_DAYS)
    var = 1.0 / n_days
    if sharpe_variance is not None and _finite(sharpe_variance) and sharpe_variance > 0:
        var = float(sharpe_variance)

    sr0 = expected_max_sharpe(max(n_trials, 1), var)
    g3 = skew if _finite(skew) else 0.0
    g4 = kurtosis if _finite(kurtosis) else 3.0
    denom = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return float("nan"), sr0 * math.sqrt(TRADING_DAYS)
    z = (sr - sr0) * math.sqrt(n_days - 1) / math.sqrt(denom)
    return _N.cdf(z), sr0 * math.sqrt(TRADING_DAYS)


def _p_one_sided(t: float) -> float | None:
    return 1.0 - _N.cdf(t) if _finite(t) else None


def _empty(missing: str) -> ResearchAssessment:
    return ResearchAssessment(
        ResearchVerdict.REJECT,
        (ResearchFlag.INCOMPLETE_ANALYSIS,),
        {"alpha": 0.0, "robustness": 0.0, "cost": 0.0, "diversification": 0.0, "limits": 0.0},
        (missing,),
    )


def assess_research(
    stats: SignalStatistics | None,
    backtest: BacktestResult | None,
    risk: PortfolioRiskReport | None,
    thresholds: ResearchThresholds | None = None,
) -> ResearchAssessment:
    th = thresholds or DEFAULT_THRESHOLDS
    missing = [n for n, v in (("signal", stats), ("backtest", backtest), ("risk", risk)) if v is None]
    if stats is None or backtest is None or risk is None:
        return _empty(f"analysis incomplete: no {', '.join(missing)} result")
    is_s, oos_s = stats.period("in_sample"), stats.period("out_of_sample")
    is_b, oos_b, full_b = backtest.period("in_sample"), backtest.period("out_of_sample"), backtest.period("full")
    if is_s is None or oos_s is None or is_b is None or oos_b is None or full_b is None:
        return _empty("analysis incomplete: in-sample / out-of-sample periods missing")

    gross_ann = full_b.gross_ann_return
    cost_ann = full_b.cost_drag_bps_annual / 10_000.0
    if _finite(gross_ann) and gross_ann > 0 and _finite(cost_ann):
        cost_share = cost_ann / gross_ann
    else:
        cost_share = float("inf") if _finite(cost_ann) and cost_ann > 0 else 0.0
    ratio = backtest.sharpe_ratio_oos_is
    concentration = backtest.pnl_concentration_top3
    breached = [c for c in risk.checks if c.breached]
    history_breaches = [c for c in risk.history_checks if c.get("breached")]
    trial = backtest.trial or {}
    n_trials = trial_count(trial, th)
    hurdle = hurdle_for(n_trials, th)
    trial_var = trial.get("sharpe_variance")
    # the alpha test deflates the in-sample Sharpe *before* trading costs: costs belong to COST_DRAG, not NO_ALPHA
    dsr, sr0_ann = deflated_sharpe(
        is_b.gross_sharpe, is_b.n_days, is_b.skew, is_b.kurtosis, n_trials,
        float(trial_var) if trial_var is not None else None,
    )

    flags: list[ResearchFlag] = []
    rationale: list[str] = []

    # ---- alpha chain: no alpha -> regime break -> OOS decay -> cost drag
    weak_t = not _finite(is_s.ic_t_stat) or is_s.n_dates < th.min_dates or is_s.ic_t_stat < hurdle
    weak_dsr = not _finite(dsr) or dsr < th.dsr_min
    if weak_t or weak_dsr:
        flags.append(ResearchFlag.NO_ALPHA)
        if weak_t:
            rationale.append(
                f"in-sample {is_s.horizon_days}-day IC t-stat {is_s.ic_t_stat:.2f} over {is_s.n_dates} "
                f"non-overlapping periods is below the a-priori {hurdle:.2f} hurdle ({n_trials} trial(s) counted)"
            )
        if weak_dsr:
            rationale.append(
                f"deflated Sharpe probability {dsr:.2f} is below {th.dsr_min:.2f}: in-sample Sharpe before trading "
                f"costs {is_b.gross_sharpe:.2f} "

                f"against {sr0_ann:.2f} expected from the best of {n_trials} ledger trial(s)"
            )
    else:
        cost_drag = (
            _finite(oos_b.gross_sharpe)
            and oos_b.gross_sharpe >= th.sharpe_min
            and ((not _finite(oos_b.sharpe)) or oos_b.sharpe < th.sharpe_min or cost_share >= th.cost_share_max)
        )
        if _finite(oos_s.ic_t_stat) and oos_s.ic_t_stat <= -th.regime_break_t:
            flags.append(ResearchFlag.REGIME_BREAK)
            rationale.append(
                f"out-of-sample IC t-stat {oos_s.ic_t_stat:.2f} is at or below -{th.regime_break_t:.1f}: the premium reversed"
            )
        elif not _finite(oos_s.ic_t_stat) or oos_s.ic_t_stat < th.oos_ic_t_min:
            flags.append(ResearchFlag.OOS_DECAY)
            rationale.append(f"out-of-sample IC t-stat {oos_s.ic_t_stat:.2f} is below {th.oos_ic_t_min:.1f}")
        elif cost_drag:
            flags.append(ResearchFlag.COST_DRAG)
            rationale.append(
                f"gross out-of-sample Sharpe {oos_b.gross_sharpe:.2f} but net {oos_b.sharpe:.2f}; "
                f"costs consume {cost_share:.0%} of gross return"
            )
        elif not _finite(oos_b.sharpe) or oos_b.sharpe < th.sharpe_min:
            flags.append(ResearchFlag.OOS_DECAY)
            rationale.append(f"out-of-sample net Sharpe {oos_b.sharpe:.2f} is below the {th.sharpe_min:.2f} hurdle")

    # ---- independent checks
    if _finite(concentration) and concentration > th.pnl_concentration_max:
        flags.append(ResearchFlag.CONCENTRATION)
        rationale.append(
            f"top three names account for {concentration:.0%} of positive P&L (limit {th.pnl_concentration_max:.0%})"
        )
    if breached or history_breaches:
        flags.append(ResearchFlag.LIMIT_BREACH)
        names = ", ".join(f"{c.limit_name}@{c.symbol}" for c in breached[:4])
        hist = ", ".join(f"{c['limit_name']} on {c['date']}" for c in history_breaches[:3])
        rationale.append(
            f"{len(breached)} as-of and {len(history_breaches)} out-of-sample-history limit breach(es)"
            + (f": {names}" if names else "")
            + (f"; worst dates: {hist}" if hist else "")
        )

    verdict = ResearchVerdict.PROMOTE if not flags else ResearchVerdict.REJECT
    lo, hi = sharpe_interval(full_b.sharpe, full_b.n_days, th.inconclusive_z)
    statistical_only = bool(flags) and set(flags) <= {ResearchFlag.NO_ALPHA, ResearchFlag.OOS_DECAY}
    if statistical_only and _finite(lo) and lo < 0.0 < th.sharpe_min < hi:
        verdict = ResearchVerdict.INCONCLUSIVE
        rationale.append(
            f"inconclusive: the 95% interval of the full-period net Sharpe [{lo:.2f}, {hi:.2f}] covers both 0 and "
            f"the {th.sharpe_min:.2f} minimum; more data is needed to tell no edge from a tradable one"
        )
    if not flags:
        rationale.append(
            f"in-sample IC t-stat {is_s.ic_t_stat:.2f} clears {hurdle:.2f}, deflated Sharpe {dsr:.2f} over "
            f"{n_trials} trial(s), out-of-sample net Sharpe {oos_b.sharpe:.2f}, costs {cost_share:.0%} of gross "
            "return, limits respected"
        )
    scores = {
        "alpha": round(_sigmoid(is_s.ic_t_stat - hurdle, 2.0), 3),
        "robustness": round(_clamp(ratio) if _finite(is_b.sharpe) and is_b.sharpe > 0 else 0.0, 3),
        "cost": round(_clamp(1.0 - cost_share), 3),
        "diversification": round(_clamp(1.0 - concentration), 3),
        "limits": 0.0 if breached or history_breaches else 1.0,
    }
    metrics = {
        "is_ic_t_stat": is_s.ic_t_stat,
        "is_ic_t_stat_nw": is_s.ic_t_stat_nw,
        "oos_ic_t_stat": oos_s.ic_t_stat,
        "is_sharpe": is_b.sharpe,
        "oos_sharpe": oos_b.sharpe,
        "oos_gross_sharpe": oos_b.gross_sharpe,
        "sharpe_ratio_oos_is": ratio,
        "cost_share": cost_share if _finite(cost_share) else float("nan"),
        "pnl_concentration_top3": concentration,
        "max_adv_participation": risk.max_adv_participation,
        "breached_checks": float(len(breached) + len(history_breaches)),
        "hurdle_t": hurdle,
        "full_sharpe_ci_low": lo,
        "full_sharpe_ci_high": hi,
        "deflated_sharpe": dsr,
        "deflated_sharpe_bar": sr0_ann,
        "n_trials": float(n_trials),
    }
    oos_years = oos_b.n_days / TRADING_DAYS if oos_b.n_days else float("nan")
    oos_sr_t = oos_b.sharpe * math.sqrt(oos_years) if _finite(oos_b.sharpe) and _finite(oos_years) else float("nan")
    hypotheses = HypothesisTable(
        rows=(
            HypothesisRow("in_sample_ic_t", is_s.ic_t_stat, _p_one_sided(is_s.ic_t_stat), is_s.n_dates,
                          "PASS" if not weak_t else "FAIL"),
            HypothesisRow("deflated_sharpe", dsr, None if not _finite(dsr) else 1.0 - dsr, is_b.n_days,
                          "PASS" if not weak_dsr else "FAIL"),
            HypothesisRow("out_of_sample_ic_t", oos_s.ic_t_stat, _p_one_sided(oos_s.ic_t_stat), oos_s.n_dates,
                          "PASS" if _finite(oos_s.ic_t_stat) and oos_s.ic_t_stat >= th.oos_ic_t_min else "FAIL"),
            HypothesisRow("out_of_sample_sharpe_t", oos_sr_t, _p_one_sided(oos_sr_t), oos_b.n_days,
                          "PASS" if _finite(oos_b.sharpe) and oos_b.sharpe >= th.sharpe_min else "FAIL"),
        ),
        trials_counted=n_trials,
        correction=f"a-priori hurdle t>={hurdle:.3g} over {n_trials} trial(s) + deflated Sharpe",
    )
    return ResearchAssessment(verdict, tuple(flags), scores, tuple(rationale), metrics, hypotheses)


def thresholds_summary(th: ResearchThresholds | None = None) -> dict[str, Any]:
    t = th or DEFAULT_THRESHOLDS
    return {k: getattr(t, k) for k in t.__dataclass_fields__}
