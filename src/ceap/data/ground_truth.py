# ruff: noqa: N803, N806  (mathematical notation: R, P, T, A, B as in the formulas)
"""Ground truth from generator parameters, never from a run (2.9, 2.11).

For a ``realistic_v1`` scenario the expected statistics follow in closed form
from the generator's parameters:

* **Information coefficient.** The signal is a window sum of past returns
  ``S = sign * sum_{a in A} r_a`` and the target is the holding-period return
  ``F = sum_{b in B} r_b`` after the execution lag. With daily cross-sectional
  noise variance ``sigma^2`` and the latent components' autocovariances
  (drift: ``s^2 phi^|k|``; transient: ``Var(dT) = share sigma^2`` and
  ``Cov(dT_t, dT_t+k) = -share sigma^2 (1 - rho) rho^(k-1) / 2``), the Pearson
  correlation is ``Cov(S, F) / sqrt(Var S Var F)``; the rank IC is
  ``(6 / pi) asin(rho / 2)`` (Gaussian).
* **t-statistic** of the non-overlapping horizon IC over ``N`` periods with
  ``n`` names: ``E[t] = IC sqrt(n - 1) sqrt(N)``.
* **Gross Sharpe** of the equal-weight tercile long-short book (``k`` names a
  side): ``1.09 rho sqrt(2k)`` per holding period (``1.09 = phi(z_2/3) / (1/3) / 2``
  for a standard normal), annualised by ``sqrt(252 / h)``.
* **Costs.** Turnover per rebalance ``2 (1 - p_stay)`` where ``p_stay`` is the
  bivariate-normal probability of staying in the tercile given the signal's
  autocorrelation at the rebalance lag; cost per unit traded is the expected
  half spread plus the square-root impact at the expected participation;
  borrow is half the book at the tier-average fee.
* **Deflated Sharpe.** Expected maximum Sharpe of ``N`` null trials over ``T``
  in-sample days, ``SR0 = sqrt(1/T) * ((1-g) Z(1-1/N) + g Z(1-1/(N e)))``.

The verdict follows the assessment's gate chain with these expectations in
place of observed statistics. A scenario whose expected t is within one unit
of the hurdle, or whose expected out-of-sample net Sharpe is within one
standard error of the Sharpe gate, is marked ``near_threshold``: its realised
verdict is a coin toss weighted by the distance, and the evaluation reports it
separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

import numpy as np

from ceap.domain.research import ResearchFlag, ResearchVerdict, search_space

TRADING_DAYS = 252
EULER_GAMMA = 0.5772156649015329
_N = NormalDist()
TERCILE_SPREAD = 1.0910  # E[Z | Z > z_2/3] for a standard normal = phi(0.4307)/(1/3)
SIGNAL_WINDOWS: dict[str, tuple[int, int, int]] = {
    # name: (first offset, last offset, sign) of the return days a signal sums, relative to decision t
    "momentum_12_1": (-251, -21, 1),
    "reversal_5": (-4, 0, -1),
}


@dataclass(frozen=True)
class GroundTruth:
    verdict: ResearchVerdict
    flags: tuple[ResearchFlag, ...]
    near_threshold: bool
    metrics: dict[str, float] = field(default_factory=dict)
    rationale: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "flags": [f.value for f in self.flags],
            "near_threshold": self.near_threshold,
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "rationale": list(self.rationale),
        }


def expected_max_sharpe(n_trials: int, n_obs: int) -> float:
    """Expected maximum per-period Sharpe of ``n_trials`` independent null trials (Bailey and Lopez de Prado)."""
    if n_trials <= 1 or n_obs <= 1:
        return 0.0
    sd = math.sqrt(1.0 / n_obs)
    return sd * (
        (1.0 - EULER_GAMMA) * _N.inv_cdf(1.0 - 1.0 / n_trials)
        + EULER_GAMMA * _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    )


def _kernel_sum(A: np.ndarray, B: np.ndarray, fn: Any) -> float:
    lag = np.abs(A[:, None] - B[None, :])
    return float(fn(lag).sum())


def _components(spec: Any, sigma2: float) -> list[Any]:
    """Autocovariance functions (lag -> covariance) of the latent premium components."""
    comps: list[Any] = []
    if spec.mechanism == "drift" and spec.premium_bps > 0:
        s2 = (spec.premium_bps / 10_000.0 / TRADING_DAYS) ** 2
        phi = 0.5 ** (1.0 / spec.half_life_days)
        comps.append(lambda k, s2=s2, phi=phi: s2 * phi ** k)
    if spec.mechanism == "transient" and spec.transient_share > 0:
        share, rho = spec.transient_share, spec.transient_rho

        def tr(k: np.ndarray, share: float = share, rho: float = rho) -> np.ndarray:
            k = np.asarray(k, dtype=float)
            off = -share * sigma2 * (1.0 - rho) * rho ** np.maximum(k - 1.0, 0.0) / 2.0
            return np.where(k == 0, share * sigma2, off)

        comps.append(tr)
    return comps


def _cov(A: np.ndarray, B: np.ndarray, comps: list[Any], sigma2_noise: float) -> float:
    overlap = len(np.intersect1d(A, B))
    return sigma2_noise * overlap + sum(_kernel_sum(A, B, c) for c in comps)


def signal_correlations(
    spec: Any, signal: str, horizon: int, lag: int, rebalance: int, sigma2: float, premium_scale: float = 1.0
) -> tuple[float, float]:
    """``(rho_SF, rho_SS)``: Pearson correlation of the signal with the holding-period return, and of the
    signal with itself ``rebalance`` days later. ``premium_scale`` multiplies the latent variance."""
    if signal not in SIGNAL_WINDOWS:
        return 0.0, 0.0
    lo, hi, sign = SIGNAL_WINDOWS[signal]
    A = np.arange(lo, hi + 1)
    B = np.arange(lag + 1, lag + horizon + 1)
    comps = [lambda k, c=c: premium_scale * c(k) for c in _components(spec, sigma2)]
    noise = sigma2 if spec.mechanism != "transient" else sigma2 * (1.0 - spec.transient_share)
    var_s = _cov(A, A, comps, noise)
    var_f = _cov(B, B, comps, noise)
    rho_sf = sign * _cov(A, B, comps, noise) / math.sqrt(var_s * var_f)
    rho_ss = _cov(A, A + rebalance, comps, noise) / var_s
    return float(rho_sf), float(rho_ss)


def regime_break_correlation(spec: Any, signal: str, horizon: int, lag: int, sigma2: float) -> float:
    """Correlation of the momentum signal with the out-of-sample return when the drift mean-reverts
    (``oos_premium_multiplier < 0``): ``F`` drift = mult * s * mean(mu over the 12-1 window) / sd."""
    lo, hi, sign = SIGNAL_WINDOWS[signal]
    s2 = (spec.premium_bps / 10_000.0 / TRADING_DAYS) ** 2
    phi = 0.5 ** (1.0 / spec.half_life_days)
    A = np.arange(lo, hi + 1)
    W = np.arange(-251, -20)
    acf = lambda k: s2 * phi ** np.abs(k)  # noqa: E731
    var_m = float(acf(W[:, None] - W[None, :]).mean())
    scale = math.sqrt(s2 / var_m)
    cov = 0.0
    for b in range(lag + 1, lag + horizon + 1):
        cov += spec.oos_premium_multiplier * scale * float(acf(A[:, None] - (b + W)[None, :]).mean(axis=1).sum())
    var_s = sigma2 * A.size + _kernel_sum(A, A, acf)
    var_f = horizon * sigma2 + (spec.oos_premium_multiplier**2) * s2 * horizon**2  # drift variance, upper bound
    return float(sign * cov / math.sqrt(var_s * var_f))


def p_stay_tercile(rho: float, n_grid: int = 4001) -> float:
    """P(stay in the top tercile) for a bivariate normal pair with correlation ``rho``."""
    c = _N.inv_cdf(2.0 / 3.0)
    rho = max(min(rho, 0.999999), -0.999999)
    x = np.linspace(c, 9.0, n_grid)
    dens = np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
    cond = np.array([1.0 - _N.cdf((c - rho * xi) / math.sqrt(1.0 - rho * rho)) for xi in x])
    y = dens * cond
    joint = float(((y[1:] + y[:-1]) / 2.0 * np.diff(x)).sum())  # trapezoid rule

    return joint / (1.0 / 3.0)


def _universe(spec: Any) -> dict[str, float]:
    """Expected cross-sectional variance, cost and borrow inputs from the tier profiles (no draws)."""
    from ceap.data.generator_params import (
        MARKET_ANNUAL_VOL,
        RESEARCH_UNIVERSE,
        SPREAD_VOL_ELASTICITY,
        TIER_OF,
        TIER_PROFILE,
    )

    tiers = [TIER_OF[s] for s in RESEARCH_UNIVERSE]
    idio2 = np.array([TIER_PROFILE[t]["idio_vol"] ** 2 / TRADING_DAYS for t in tiers])
    beta_var = (0.6**2) / 12.0
    m2 = MARKET_ANNUAL_VOL**2 / TRADING_DAYS
    sigma2 = float(idio2.mean() + beta_var * m2)
    n = len(tiers)
    q = (np.arange(64) + 0.5) / 64
    half_spread = []
    inv_sqrt_adv = []
    vol_bps = []
    for t in tiers:
        lo, hi = TIER_PROFILE[t]["spread_bps"]
        half_spread.append((lo + hi) / 2 * spec.spread_multiplier * math.exp(0.15**2 / 2) / 2)
        a_lo, a_hi = TIER_PROFILE[t]["adv_notional"]
        adv = (a_lo + (a_hi - a_lo) * q) * spec.adv_multiplier
        inv_sqrt_adv.append(float((1.0 / np.sqrt(adv)).mean()) * math.exp(-0.35**2 / 8))
        vol_bps.append(math.sqrt(TIER_PROFILE[t]["idio_vol"] ** 2 / TRADING_DAYS + m2) * 10_000.0)
    del SPREAD_VOL_ELASTICITY
    borrow = {"A": 50.0, "B": 200.0, "C": 1000.0}
    return {
        "sigma2": sigma2,
        "n_names": float(n),
        "half_spread_bps": float(np.mean(half_spread)),
        "inv_sqrt_adv": float(np.mean(inv_sqrt_adv)),
        "vol_bps": float(np.mean(vol_bps)),
        "borrow_bps": float(np.mean([borrow[t] for t in tiers])),
        "min_adv_concentrated": float(TIER_PROFILE["C"]["adv_notional"][0] * spec.adv_multiplier * spec.concentration_adv_multiplier),
    }


def expected_statistics(spec: Any, lag: int = 1, gross_notional: float = 50_000_000.0) -> dict[str, float]:
    """Closed-form expectations for a scenario's default configuration (see module docstring)."""
    from ceap.analytics.signals import get_signal
    from ceap.data.generator_params import IN_SAMPLE_DAYS, N_DAYS, WARMUP_DAYS

    u = _universe(spec)
    sigma2 = u["sigma2"]
    h = spec.rebalance_days or get_signal(spec.signal).default_rebalance_days
    n_eff = u["n_names"] - 2.0  # two delisted or late-listed names on average
    if spec.concentration_names > 0:
        n_prem = float(spec.concentration_names)
    else:
        n_prem = n_eff
    k = max(1, round(n_eff / 3))
    is_years_decay = 1.0
    if spec.mechanism == "drift" and spec.decay_half_life_years > 0:
        yrs = np.arange(WARMUP_DAYS, WARMUP_DAYS + IN_SAMPLE_DAYS) / TRADING_DAYS
        is_years_decay = float((0.5 ** (2 * yrs / spec.decay_half_life_years)).mean())
        yrs_o = np.arange(WARMUP_DAYS + IN_SAMPLE_DAYS, N_DAYS) / TRADING_DAYS
        oos_decay = float((0.5 ** (2 * yrs_o / spec.decay_half_life_years)).mean())
    else:
        oos_decay = 1.0
    dilution = n_prem / n_eff
    rho_is, rho_ss = signal_correlations(spec, spec.signal, h, lag, h, sigma2, is_years_decay)
    mult = spec.oos_premium_multiplier
    if spec.mechanism == "drift" and mult < 0:
        rho_oos = regime_break_correlation(spec, spec.signal, h, lag, sigma2)
    else:
        scale = oos_decay * (mult**2 if spec.mechanism == "drift" else max(mult, 0.0) ** 2)
        rho_oos, _ = signal_correlations(spec, spec.signal, h, lag, h, sigma2, scale)
    rho_is *= dilution
    rho_oos *= dilution
    rank = lambda r: 6.0 / math.pi * math.asin(r / 2.0)  # noqa: E731
    n_is = (IN_SAMPLE_DAYS - h - lag) // h
    n_oos = (N_DAYS - WARMUP_DAYS - IN_SAMPLE_DAYS - h - lag) // h
    t_is = rank(rho_is) * math.sqrt(n_eff - 1.0) * math.sqrt(n_is)
    t_oos = rank(rho_oos) * math.sqrt(n_eff - 1.0) * math.sqrt(n_oos)
    per_year = TRADING_DAYS / h
    vol_ann = math.sqrt(sigma2 * TRADING_DAYS / (2 * k))
    gross_sr_is = TERCILE_SPREAD * rho_is * math.sqrt(2 * k) * math.sqrt(per_year)
    gross_sr_oos = TERCILE_SPREAD * rho_oos * math.sqrt(2 * k) * math.sqrt(per_year)
    if spec.crash and spec.mechanism == "drift":
        from ceap.data.generator_params import CRASH_PREMIUM_MULTIPLIER, CRASH_REBOUND_DAYS

        oos_days = N_DAYS - WARMUP_DAYS - IN_SAMPLE_DAYS
        gross_sr_oos *= 1.0 - (1.0 - CRASH_PREMIUM_MULTIPLIER) * CRASH_REBOUND_DAYS / oos_days
    turnover = 2.0 * (1.0 - p_stay_tercile(rho_ss))
    participation_coef = 0.5 / k * gross_notional
    impact_bps = 0.6 * u["vol_bps"] * math.sqrt(participation_coef) * u["inv_sqrt_adv"]

    cost_bps_unit = u["half_spread_bps"] + impact_bps
    cost_ann = turnover * per_year * cost_bps_unit / 10_000.0
    borrow_ann = 0.5 * u["borrow_bps"] / 10_000.0
    net_sr_is = gross_sr_is - (cost_ann + borrow_ann) / vol_ann
    net_sr_oos = gross_sr_oos - (cost_ann + borrow_ann) / vol_ann
    oos_years = (N_DAYS - WARMUP_DAYS - IN_SAMPLE_DAYS) / TRADING_DAYS
    se_oos = math.sqrt((1.0 + net_sr_oos**2 / 2.0) / oos_years)
    sr0_ann = expected_max_sharpe(len(search_space()), IN_SAMPLE_DAYS) * math.sqrt(TRADING_DAYS)
    max_participation = participation_coef / u["min_adv_concentrated"] if spec.concentration_names else 0.0
    return {
        "rho_is": rho_is,
        "rho_oos": rho_oos,
        "rank_ic_is": rank(rho_is),
        "rank_ic_oos": rank(rho_oos),
        "t_is": t_is,
        "t_oos": t_oos,
        "gross_sharpe_is": gross_sr_is,
        "gross_sharpe_oos": gross_sr_oos,
        "net_sharpe_is": net_sr_is,
        "net_sharpe_oos": net_sr_oos,
        "se_sharpe_oos": se_oos,
        "turnover_per_rebalance": turnover,
        "cost_ann": cost_ann,
        "borrow_ann": borrow_ann,
        "cost_share": (cost_ann + borrow_ann) / (gross_sr_oos * vol_ann) if gross_sr_oos > 0 else float("inf"),
        "sr0_ann": sr0_ann,
        "max_participation_concentrated": max_participation,
    }


def derive_ground_truth(spec: Any, thresholds: Any = None, lag: int = 1) -> GroundTruth:
    """Expected verdict and flags from parameters, following the assessment's gate chain."""
    from ceap.analytics.research_assessment import DEFAULT_THRESHOLDS

    th = thresholds or DEFAULT_THRESHOLDS
    e = expected_statistics(spec, lag)
    flags: list[ResearchFlag] = []
    why: list[str] = []
    detectable = e["t_is"] >= th.ic_t_stat_min and e["gross_sharpe_is"] >= e["sr0_ann"]
    if not detectable:
        flags.append(ResearchFlag.NO_ALPHA)
        why.append(
            f"expected in-sample IC t {e['t_is']:.2f} vs hurdle {th.ic_t_stat_min:.2f}; expected in-sample gross "
            f"Sharpe {e['gross_sharpe_is']:.2f} vs deflation bar {e['sr0_ann']:.2f}"
        )
    elif e["t_oos"] <= -th.regime_break_t:
        flags.append(ResearchFlag.REGIME_BREAK)
        why.append(f"expected out-of-sample IC t {e['t_oos']:.2f}: the premium reverses")
    elif e["t_oos"] < th.oos_ic_t_min:
        flags.append(ResearchFlag.OOS_DECAY)
        why.append(f"expected out-of-sample IC t {e['t_oos']:.2f} below {th.oos_ic_t_min:.2f}")
    elif e["gross_sharpe_oos"] >= th.sharpe_min and (
        e["net_sharpe_oos"] < th.sharpe_min or e["cost_share"] >= th.cost_share_max
    ):
        flags.append(ResearchFlag.COST_DRAG)
        why.append(
            f"expected gross Sharpe {e['gross_sharpe_oos']:.2f}, net {e['net_sharpe_oos']:.2f}, costs "
            f"{e['cost_share']:.0%} of gross"
        )
    elif e["net_sharpe_oos"] < th.sharpe_min:
        flags.append(ResearchFlag.OOS_DECAY)
        why.append(f"expected out-of-sample net Sharpe {e['net_sharpe_oos']:.2f} below {th.sharpe_min:.2f}")
    if spec.concentration_names > 0:
        flags.append(ResearchFlag.CONCENTRATION)
        why.append(f"premium planted in {spec.concentration_names} names only")
    if e["max_participation_concentrated"] > 0.10:
        flags.append(ResearchFlag.LIMIT_BREACH)
        why.append(f"expected participation {e['max_participation_concentrated']:.0%} of ADV in the thinnest name")
    near = abs(e["t_is"] - th.ic_t_stat_min) < 1.0 or (
        detectable and abs(e["net_sharpe_oos"] - th.sharpe_min) < e["se_sharpe_oos"]
    )
    verdict = ResearchVerdict.PROMOTE if not flags else ResearchVerdict.REJECT
    return GroundTruth(verdict, tuple(flags), near, e, tuple(why))
