"""R2T acceptance numbers (PLAN section 2, 2T.1 to 2T.8) at the stated seed counts.

Nightly tier (``CEAP_NIGHTLY=1``): 200 seeds for false flags and cause recall, 100 seeds for the
broker-gap power. PR tier: the same tests on fewer seeds with the same thresholds where the
binomial noise allows it, looser bounds (stated) where it does not.
"""

from __future__ import annotations

import math
import os

import pytest

from ceap.evaluation import run_tca_cause_recall, run_tca_false_flag

NIGHTLY = os.environ.get("CEAP_NIGHTLY") == "1"
nightly = [pytest.mark.nightly, pytest.mark.skipif(not NIGHTLY, reason="nightly: set CEAP_NIGHTLY=1")]


def _apply(marks):
    def deco(f):
        for m in reversed(marks):
            f = m(f)
        return f

    return deco


# ------------------------------------------------------------------- 2T.2 / 2T.4: T01 false flags
@pytest.mark.slow
def test_t01_false_flag_pr_tier():
    """20 seeds: at most 2 flagged (the 5 % bound has a 92 % binomial pass rate at n = 20)."""
    r = run_tca_false_flag(20)
    assert r["deteriorated_rate"] <= 0.10, r["deteriorated_rate"]
    assert r["any_cause_rate"] <= 0.10, r["any_cause_rate"]
    assert abs(r["mean_market_impact_bps"]) < 5.0  # 2T.5: "impact" near zero, not -45 bps


@_apply(nightly)
def test_t01_false_flag_200_seeds():
    r = run_tca_false_flag(200)
    assert r["deteriorated_rate"] <= 0.05, r["deteriorated_rate"]
    assert r["any_cause_rate"] <= 0.05, r["any_cause_rate"]


# ------------------------------------------------------------- 2T.2: U-shape alone creates no cause
@pytest.mark.slow
def test_u_shape_alone_produces_no_cause():
    """Same seeds with and without the U-shaped profile: the matched baseline finds no cause either way."""
    from dataclasses import replace

    from ceap.data.scenarios import get_scenario
    from ceap.data.synthetic import SyntheticMarketGenerator
    from ceap.evaluation import tca_attribution_for

    flat, ushape = SyntheticMarketGenerator(u_shape=False), SyntheticMarketGenerator(u_shape=True)
    for k in range(6):
        spec = replace(get_scenario("T01"), seed=500 + k, side="SELL" if k % 2 else "BUY")
        a, b = tca_attribution_for(spec, flat), tca_attribution_for(spec, ushape)
        assert a["material"] == [] and b["material"] == [], (k, a["material"], b["material"])


# --------------------------------------------------------------- 2T.4: T05 to T10 find their cause
@pytest.mark.slow
def test_cause_recall_pr_tier():
    """10 seeds per template: every template at or above 80 % (the 90 % bar is the nightly test)."""
    out = run_tca_cause_recall(10)
    assert all(v["coverage"] >= 0.8 for v in out.values()), out


@_apply(nightly)
def test_cause_recall_200_seeds():
    out = run_tca_cause_recall(200)
    assert all(v["coverage"] >= 0.9 for v in out.values()), out


# ----------------------------------------------------------- 2T.1: planted 3 bps broker gap, power
def _broker_gap_detected(seed: int, gap: float) -> bool:
    from ceap.analytics.tca_aggregate import compare_groups, measure_universe
    from ceap.data.tca_universe import UniverseConfig, generate_tca_universe

    u = generate_tca_universe(seed, UniverseConfig(broker_gap_bps=gap))
    rows = {g.key: g for g in compare_groups(measure_universe(u), "broker")}
    worst = max(rows.values(), key=lambda g: g.diff_vs_rest_bps)
    return worst.key == "BRK_C" and worst.flagged


#: stated power of the broker comparison at 3 bps, 20 sessions x 40 parents (measured nightly)
STATED_POWER = 0.80


@pytest.mark.slow
def test_broker_gap_power_pr_tier():
    hits = sum(_broker_gap_detected(900 + k, 3.0) for k in range(6))
    assert hits >= 4, hits  # P(>= 4 of 6 | power 0.8) = 0.90


@_apply(nightly)
def test_broker_gap_power_100_seeds():
    hits = sum(_broker_gap_detected(900 + k, 3.0) for k in range(100))
    assert hits / 100 >= STATED_POWER, hits
    # and without a planted gap the comparison rarely flags anyone (size of the test)
    false = 0
    from ceap.analytics.tca_aggregate import compare_groups, measure_universe
    from ceap.data.tca_universe import generate_tca_universe

    for k in range(100):
        rows = compare_groups(measure_universe(generate_tca_universe(2000 + k)), "broker")
        false += any(g.flagged for g in rows)
    assert false / 100 <= 0.10, false


# --------------------------------------------------------------- 2T.8: coverage under shift (refit)
@_apply(nightly)
def test_pretrade_refit_matches_committed_model_and_coverage():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "train_pretrade.py"
    spec = importlib.util.spec_from_file_location("train_pretrade", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    model, cov = mod.train()
    assert all(0.85 <= v <= 0.95 for v in cov.values()), cov
    from ceap.ml.pretrade_cost import default_model

    assert math.isclose(model.impact_coefficient, default_model().impact_coefficient, rel_tol=1e-9)


# ------------------------------------------------------------------- 2T.7: report by one command
def test_tca_report_one_command(tmp_path, capsys):
    from ceap.cli import main

    out = tmp_path / "best_ex.md"
    assert main(["tca-report", "--sessions", "4", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "DEMONSTRATION" in text and "Brokers: residual versus pre-trade estimate" in text
    assert "does not identify a cause" in text


async def test_outlier_cannot_be_closed_by_the_principal_who_raised_it():
    import asyncio

    from ceap.analytics.tca_aggregate import OutlierQueue, OutlierStatus, measure_universe
    from ceap.data.tca_universe import UniverseConfig, generate_tca_universe
    from ceap.policy.approvals import FourEyesViolationError, QueuedApprovalGateway

    ms = measure_universe(generate_tca_universe(7, UniverseConfig(n_sessions=6)))
    queue = OutlierQueue.build(ms, threshold=2.5)
    if not queue.items:  # make the queue non-empty deterministically for the governance check
        queue = OutlierQueue.build(ms, threshold=0.0)
    oid = next(iter(queue.items))
    assert queue.items[oid].reasons
    gw = QueuedApprovalGateway(timeout_seconds=5)
    task = asyncio.create_task(queue.close(oid, "analyst.a", gw, "checked fills"))
    await asyncio.sleep(0)
    pending = gw.pending()
    assert len(pending) == 1 and pending[0].requested_by == "analyst.a"
    with pytest.raises(FourEyesViolationError):
        gw.decide(pending[0].id, True, "analyst.a")
    assert queue.items[oid].status is OutlierStatus.PENDING_SIGN_OFF
    gw.decide(pending[0].id, True, "supervisor.b")
    decision = await task
    assert decision.decided_by == "supervisor.b"
    assert queue.items[oid].status is OutlierStatus.CLOSED
