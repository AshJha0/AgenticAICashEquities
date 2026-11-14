"""Q1 (review finding R-15): one Sharpe standard error, Lo (2002) daily form, checked by simulation."""

from __future__ import annotations

import math

import numpy as np

from ceap.analytics import profitability, research_assessment
from ceap.analytics.numerics import sharpe_standard_error
from ceap.analytics.research_assessment import sharpe_interval
from ceap.paper.forward import sharpe_ci


def test_interval_covers_the_true_sharpe_93_to_97_percent_over_2000_draws() -> None:
    rng = np.random.default_rng(2002)
    true_ann, n_days, draws = 2.0, 252, 2000  # high Sharpe, where the 0.9.0 form was 73 % too wide
    mu, sd = true_ann / math.sqrt(252) * 0.01, 0.01
    hits = 0
    for _ in range(draws):
        r = rng.normal(mu, sd, n_days)
        est = float(r.mean() / r.std(ddof=1)) * math.sqrt(252)
        lo, hi = sharpe_interval(est, n_days, 1.96)
        hits += lo <= true_ann <= hi
    assert 0.93 <= hits / draws <= 0.97, hits / draws


def test_one_function_everywhere_and_the_errata_value() -> None:
    assert profitability.sharpe_standard_error is sharpe_standard_error
    assert research_assessment.sharpe_standard_error is sharpe_standard_error
    # Sharpe 1 over one year: Lo (2002) gives 1.0040; the 0.9.0 form gave sqrt(1.5) = 1.2247 (22 % wider)
    assert abs(sharpe_standard_error(1.0, 252) - math.sqrt(1.0 + 0.5 / 252)) < 1e-12
    r = np.random.default_rng(1).normal(0.0005, 0.01, 500)
    ci = sharpe_ci(r)
    assert abs(ci["se"] - sharpe_standard_error(ci["sharpe"], 500)) < 1e-12
