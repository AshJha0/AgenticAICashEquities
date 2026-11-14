"""0.10.0 item Q2 (finding R-16): the deflated Sharpe gate is floored, tested on the production path, consistent
in its gross basis, and held to the 0.95 bar (decision O5) whose false-PROMOTE rate is published."""

from __future__ import annotations

import inspect
import json
import math
import re
from types import SimpleNamespace

import pytest

from ceap.analytics import null_sweep
from ceap.analytics import research_assessment as ra
from ceap.analytics.trial_ledger import TrialLedger


def _in_sample(sharpe_ann: float, n_days: int) -> SimpleNamespace:
    return SimpleNamespace(gross_sharpe=sharpe_ann, n_days=n_days, skew=0.0, kurtosis=3.0)


def test_reviewer_case_tiny_ledger_variance_no_longer_passes():
    # the reviewer's probe: Sharpe 0.3 over ten years, 24 trials, ledger variance 1e-8 gave DSR 0.83 (bar 0.003)
    floored, bar_floor = ra.deflated_sharpe(0.3, 2520, 0.0, 3.0, 24, None)
    tiny, bar_tiny = ra.deflated_sharpe(0.3, 2520, 0.0, 3.0, 24, 1e-8)
    assert (tiny, bar_tiny) == (floored, bar_floor)
    assert tiny == pytest.approx(0.151, abs=0.005) and bar_tiny == pytest.approx(0.626, abs=0.005)
    dsr, _, n = ra.alpha_deflated_sharpe(_in_sample(0.3, 2520), {"n_trials": 24, "sharpe_variance": 1e-8})
    assert n == 24 and dsr < ra.DEFAULT_THRESHOLDS.dsr_min
    # a ledger variance above the floor still raises the bar
    _, bar_wide = ra.deflated_sharpe(0.3, 2520, 0.0, 3.0, 24, 4.0 / 2520)
    assert bar_wide > bar_floor


def test_null_sweep_calls_the_production_gate():
    assert null_sweep.alpha_deflated_sharpe is ra.alpha_deflated_sharpe
    src = inspect.getsource(null_sweep.sweep_seed)
    assert "alpha_deflated_sharpe(" in src and not re.search(r"(?<!alpha_)deflated_sharpe\(", src)
    assert "alpha_deflated_sharpe(" in inspect.getsource(ra.assess_research)


def test_ledger_variance_is_gross_and_net_is_recorded_beside_it(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps({"dataset_hash": "d", "params_hash": "legacy", "params": {}, "sharpe_daily": 0.5})
                    + "\n", encoding="utf-8")
    led = TrialLedger(path)
    led.record("d", {"signal": "a"}, 0.02, 0.01)
    s = led.record("d", {"signal": "b"}, -0.01, -0.03)
    assert s.n_trials == 3 and s.sharpe_basis == "gross"
    assert s.sharpe_variance == pytest.approx(0.00045)  # gross only: the legacy (net, no basis) row is left out
    assert s.sharpe_variance_net == pytest.approx(0.0008)
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["sharpe_basis"] == "gross" and rec["sharpe_daily_net"] == -0.03


def test_backtest_tool_records_the_gross_in_sample_sharpe():
    from ceap.data.historical import HistoricalStore
    from ceap.domain.research import backtest_result_from_dict
    from ceap.mcp.backtest.server import _backtest_compute

    ds = HistoricalStore().get("R01")
    payload, gross, net = _backtest_compute(ds, "momentum_12_1", 50_000_000.0, {"rebalance_days": 21}, False)
    ins = backtest_result_from_dict(payload).period("in_sample")
    assert gross == pytest.approx(ins.gross_sharpe / math.sqrt(252))
    assert net == pytest.approx(ins.sharpe / math.sqrt(252))
    assert gross != net


def test_bar_is_095_and_its_false_promote_rate_is_published():
    assert ra.DEFAULT_THRESHOLDS.dsr_min == 0.95
    doc = json.loads(ra.THRESHOLDS_PATH.read_text(encoding="utf-8"))
    assert doc["gates"]["dsr_min"] == 0.95 and doc["sweep_hash"] == ra.COMMITTED_SWEEP_HASH
    assert doc["n_seeds"] >= 500 and 0.0 <= doc["rate"] <= doc["band95"][1]
    assert {"hurdle", "rate", "upper"} <= set(doc["curve"][0])
