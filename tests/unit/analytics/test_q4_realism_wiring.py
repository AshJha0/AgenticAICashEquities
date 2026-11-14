"""0.10.0 item Q4 (findings R-17, R-20): the research realism toolkit has callers in the verdict path.

PBO and the effective trial count on the 24-trial matrix, the factor-neutral IC gate condition and the
Ledoit-Wolf predicted volatility each become a result field; the market-beta adversarial family is run
through the full verdict and its pass rate is published."""

from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_vectorised_pbo_matches_the_per_split_definition():
    from ceap.analytics.overfitting import pbo_cscv

    rng = np.random.default_rng(5)
    r = rng.normal(0.0, 0.01, (400, 6))
    r[:, 2] += 0.0015
    r[rng.random(r.shape) < 0.03] = np.nan
    blocks = np.split(r, 8)

    def sharpe(x: np.ndarray) -> np.ndarray:
        mu, sd = np.nanmean(x, axis=0), np.nanstd(x, axis=0, ddof=1)
        return np.where(sd > 0, mu / sd, np.nan)

    lam = []
    for chosen in combinations(range(8), 4):
        rest = [k for k in range(8) if k not in chosen]
        sr_is, sr_oos = sharpe(np.vstack([blocks[k] for k in chosen])), sharpe(np.vstack([blocks[k] for k in rest]))
        best = int(np.nanargmax(sr_is))
        w = ((sr_oos < sr_oos[best]).sum() + 1.0) / 7.0
        lam.append(math.log(w / (1.0 - w)))
    assert pbo_cscv(r, 8)["pbo"] == pytest.approx(float((np.array(lam) <= 0).mean()), abs=1e-12)


def test_research_run_records_every_toolkit_field_through_the_harness():
    import asyncio

    from ceap.evaluation import evaluation_platform
    from ceap.platform import default_research_request

    result = asyncio.run(evaluation_platform().research(default_research_request("RS01")))
    assessment = next(e for e in result.evidence if e.description.startswith("Research assessment"))
    metrics = assessment.attributes["metrics"]
    assert 0.0 <= metrics["pbo"] <= 1.0
    assert 1.0 <= metrics["n_trials_effective"] <= 24.0 and 1 <= metrics["n_trials_effective_cluster"] <= 24
    assert metrics["predicted_vol_ann"] > 0.0 and 0.0 <= metrics["covariance_shrinkage"] <= 1.0
    assert math.isfinite(metrics["is_ic_t_factor_neutral"])
    hyp = {row["name"] for row in assessment.attributes["hypotheses"]["rows"]}
    assert "in_sample_ic_t_factor_neutral" in hyp


def test_predicted_volatility_is_the_ledoit_wolf_book_volatility():
    from ceap.analytics.backtest import daily_returns
    from ceap.analytics.portfolio_risk import target_portfolio
    from ceap.analytics.risk_research import ledoit_wolf
    from ceap.data.historical import HistoricalStore

    ds = HistoricalStore().get("R01")
    report = target_portfolio(ds, "momentum_12_1")
    idx = [ds.symbols.index(p.symbol) for p in report.positions]
    w = np.array([p.weight for p in report.positions])
    t = ds.n_days - 1
    window = daily_returns(ds.close[t - 252 : t + 1])[1:, idx]
    cov, delta = ledoit_wolf(window[np.isfinite(window).all(axis=1)])
    assert report.predicted_vol_ann == pytest.approx(math.sqrt(w @ cov @ w * 252), rel=1e-5)
    assert report.covariance_shrinkage == pytest.approx(delta, abs=1e-6)


def test_market_beta_premium_fails_the_factor_neutral_gate_in_the_full_verdict():
    from ceap.analytics.adversarial import family_spec, full_verdict
    from ceap.data.historical import HistoricalGenerator

    out = full_verdict(HistoricalGenerator().generate(family_spec("market_corr", 2400.0, 0)))
    assert out["is_ic_t"] >= 3.0 > out["is_ic_t_factor_neutral"]  # the plain IC passes; the residual does not
    assert out["verdict"] == "REJECT" and "NO_ALPHA" in out["flags"]


def test_full_verdict_pass_rate_of_the_market_beta_family_is_published():
    doc = json.loads((ROOT / "docs" / "research" / "adversarial_full_verdict.json").read_text(encoding="utf-8"))
    rows = {(r["family"], r["premium_bps"]): r for r in doc["rows"]}
    assert doc["seeds_per_point"] >= 16
    for premium in (0.0, 600.0, 1100.0, 2400.0):
        row = rows[("market_corr", premium)]
        assert row["kind"] == "false_positive_rate" and row["promote"] == round(row["promote_rate"] * row["seeds"])
    text = (ROOT / "docs" / "research" / "README.md").read_text(encoding="utf-8")
    top = rows[("market_corr", 2400.0)]
    assert f"{top['promote']} of {top['seeds']}" in text
