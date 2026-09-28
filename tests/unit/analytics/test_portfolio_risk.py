"""Portfolio risk: target portfolio, limits, stress and betas."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from ceap.analytics.portfolio_risk import (
    PortfolioLimits,
    check_limits,
    market_betas,
    stress,
    target_portfolio,
    with_checks,
    with_stress,
)
from ceap.domain.research import PortfolioPosition


def test_target_portfolio_geometry(history):
    ds = history.get("R01")
    report = target_portfolio(ds, "momentum_12_1", ds.end, long_short=True, gross_notional=5e7)
    assert report.as_of == ds.end and len(report.positions) == 20
    assert report.gross == pytest.approx(1.0) and report.net == pytest.approx(0.0)
    assert report.max_abs_weight == pytest.approx(0.05) and report.hhi == pytest.approx(0.05)
    assert report.effective_names == pytest.approx(20.0)
    assert all(p.adv_participation >= 0 and abs(p.notional) == pytest.approx(2.5e6) for p in report.positions)
    assert all(p.quantity == round(p.notional / p.price) for p in report.positions)
    long_only = target_portfolio(ds, "momentum_12_1", ds.end, long_short=False)
    assert len(long_only.positions) == 10 and long_only.net == pytest.approx(1.0)


def test_target_portfolio_needs_signal_history(history):
    ds = history.get("R01")
    with pytest.raises(ValueError):
        target_portfolio(ds, "momentum_12_1", ds.first_date)
    with pytest.raises(ValueError):
        target_portfolio(ds, "momentum_12_1", ds.end, gross_notional=-1)


def test_limits_respected_on_a_liquid_book_and_breached_when_concentrated(history):
    ok = with_checks(target_portfolio(history.get("R01"), "momentum_12_1"))
    assert not ok.any_breached and {c.limit_name for c in ok.checks} >= {"max_gross", "max_net", "max_hhi"}
    bad = replace(
        ok,
        gross=1.5,
        net=0.6,
        positions=(PortfolioPosition("PLUG", 0.05, 2.5e6, 100, 25.0, 0.4),),
    )
    checks = check_limits(bad)
    names = {(c.limit_name, c.symbol) for c in checks if c.breached}
    assert ("max_gross", "PORTFOLIO") in names and ("max_net", "PORTFOLIO") in names
    assert ("adv_participation", "PLUG") in names
    assert replace(bad, checks=checks).breached_symbols == ("PLUG", "PORTFOLIO")
    relaxed = PortfolioLimits(max_gross=2.0, max_net=1.0, max_adv_participation=0.5)
    assert not any(c.breached for c in check_limits(bad, relaxed))
    assert PortfolioLimits.for_book(False).max_net == pytest.approx(1.05)


def test_stress_signs(history):
    report = replace(target_portfolio(history.get("R01"), "momentum_12_1"), beta=0.2, net=0.1)
    s = stress(report, -500.0)
    assert s.pnl_market == pytest.approx(5e7 * 0.2 * -0.05)
    assert s.pnl_flat == pytest.approx(5e7 * 0.1 * -0.05)
    assert s.worst_single_name == pytest.approx(-5e7 * 0.05 * 0.05)
    assert with_stress(report, -500.0).stress == s


def test_market_betas_recover_a_factor_structure():
    rng = np.random.default_rng(6)
    n, m = 400, 6
    betas = np.linspace(0.5, 1.5, m)
    market = rng.normal(0, 0.01, n)
    ret = betas[None, :] * market[:, None] + rng.normal(0, 0.001, (n, m))
    close = 100 * np.cumprod(1 + ret, axis=0)
    est = market_betas(close, n - 1, lookback=252)
    assert est == pytest.approx(betas / betas.mean(), abs=0.05)
    assert market_betas(close, 5).tolist() == [1.0] * m
