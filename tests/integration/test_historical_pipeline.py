"""Historical generator, research scenarios and the deterministic research pipeline end to end."""

from __future__ import annotations

import numpy as np
import pytest

from ceap.analytics.backtest import run_backtest
from ceap.analytics.portfolio_risk import target_portfolio, with_checks, with_stress
from ceap.analytics.research_assessment import assess_research
from ceap.analytics.signal_statistics import evaluate_signal
from ceap.data.historical import (
    RESEARCH_UNIVERSE,
    HistoricalGenerator,
    HistoricalStore,
    to_day,
)
from ceap.data.research_scenarios import (
    RESEARCH_TEMPLATES,
    all_research_scenarios,
    get_research_scenario,
    research_scenario_for,
)
from ceap.data.scenarios import SYMBOLS
from ceap.domain.research import ResearchScope, research_tool_arguments


def test_scenario_catalogue():
    scenarios = all_research_scenarios()
    assert len(scenarios) == 21 and len({s.id for s in scenarios}) == 21
    assert len({(s.template, s.seed) for s in scenarios}) == 21
    assert {s.template for s in RESEARCH_TEMPLATES} == {
        "momentum_premium", "no_alpha", "reversal_premium", "regime_break", "cost_drag", "concentration", "mixed",
    }
    assert get_research_scenario("R01").template == "momentum_premium"
    assert get_research_scenario("regime_break").id == "R04"
    assert get_research_scenario("RS21").template == "mixed"
    assert research_scenario_for("R02", seed=7).seed == 7
    with pytest.raises(KeyError):
        get_research_scenario("R99")


def test_universe():
    assert len(RESEARCH_UNIVERSE) == 30 and len(set(RESEARCH_UNIVERSE)) == 30
    assert set(SYMBOLS) <= set(RESEARCH_UNIVERSE) and "TSLA" not in RESEARCH_UNIVERSE


def test_generated_dataset_is_deterministic_and_sane(history):
    ds = history.get("R01")
    assert ds.close.shape == (1008, 30) and ds.n_symbols == 30 and ds.symbols == RESEARCH_UNIVERSE
    assert str(ds.dates[-1]) == "2026-09-18" and (np.diff(ds.dates) > np.timedelta64(0, "D")).all()
    assert np.is_busday(ds.dates).all()
    assert (ds.close > 0).all() and (ds.volume >= 100).all() and (ds.adv_20 > 0).all() and (ds.spread_bps > 0).all()
    assert (ds.low <= np.minimum(ds.open, ds.close) + 1e-9).all()
    assert (ds.high >= np.maximum(ds.open, ds.close) - 1e-9).all()
    assert ds.start == str(ds.dates[252]) and ds.in_sample_end == str(ds.dates[755]) and ds.end == str(ds.dates[-1])
    again = HistoricalGenerator().generate(get_research_scenario("R01"))
    assert np.array_equal(again.close, ds.close) and np.array_equal(again.volume, ds.volume)
    other = HistoricalGenerator().generate(research_scenario_for("R01", seed=999))
    assert not np.array_equal(other.close, ds.close)
    assert ds.summary()["trading_days"] == 1008


def test_index_helpers_and_bars(history):
    ds = history.get("R01")
    assert ds.index_after(ds.start) == 252 and ds.index_at_or_before(ds.in_sample_end) == 755
    assert ds.index_range(None, None) == (252, 1007)
    assert ds.index_at_or_before("2026-09-19") == 1007  # Saturday rolls back
    with pytest.raises(ValueError):
        ds.index_after("2030-01-01")
    with pytest.raises(ValueError):
        ds.index_at_or_before("2000-01-01")
    with pytest.raises(ValueError):
        ds.index_range(ds.end, ds.start)
    bars = ds.bars("AAPL", 0, 4)
    assert len(bars) == 5 and set(bars[0]) == {"date", "open", "high", "low", "close", "volume", "adv_20", "spread_bps"}
    with pytest.raises(KeyError):
        ds.col("TSLA")
    assert str(to_day("2026-09-18T14:00:00+01:00")) == "2026-09-18"


def test_store_caches_and_clears():
    store = HistoricalStore()
    a = store.get("R02")
    assert store.get("R02") is a and store.get(get_research_scenario("R02")) is a
    store.clear()
    assert store.get("R02") is not a


def test_embedded_effects_are_measurable(history):
    momentum = history.get("R01")
    stats = evaluate_signal(momentum, "momentum_12_1", momentum.start, momentum.end, momentum.in_sample_end)
    assert stats.periods["in_sample"].ic_t_stat > 2.0
    broken = history.get("R04")
    stats = evaluate_signal(broken, "momentum_12_1", broken.start, broken.end, broken.in_sample_end)
    assert stats.periods["in_sample"].ic_t_stat > 2.0 > stats.periods["out_of_sample"].ic_t_stat
    bt = run_backtest(broken, "momentum_12_1", broken.start, broken.end, broken.in_sample_end)
    assert bt.periods["in_sample"].sharpe > 0.5 and bt.sharpe_ratio_oos_is < 0.5
    liquid, dear = history.get("R03"), history.get("R05")
    assert dear.spread_bps.mean() / liquid.spread_bps.mean() > 10
    concentrated = with_checks(target_portfolio(history.get("R06"), "momentum_12_1"))
    assert concentrated.any_breached and len(concentrated.breached_symbols) >= 2


def test_research_scope_and_tool_arguments():
    task_input = {"dataset": "R01", "signal": "momentum_12_1", "start": "2023-01-02", "end": "2026-09-18",
                  "in_sample_end": "2025-09-17", "stage_orders": True}
    scope = ResearchScope.from_task_input(task_input)
    assert scope.rebalance_days == 21 and scope.long_short and scope.gross_notional == 5e7 and scope.stage_orders
    args = research_tool_arguments(task_input)
    assert set(args) == {"universe_summary", "evaluate_signal", "run_backtest", "portfolio", "stage_orders"}
    assert args["portfolio"]["as_of"] == "2026-09-18" and args["stage_orders"] == args["portfolio"]
    assert args["run_backtest"]["in_sample_end"] == "2025-09-17"
    assert all(not isinstance(v, (list, dict)) for a in args.values() for v in a.values())


@pytest.mark.evaluation
def test_deterministic_pipeline_matches_scenario_ground_truth(history):
    rows = []
    for spec in all_research_scenarios():
        ds = history.get(spec)
        stats = evaluate_signal(ds, spec.signal, ds.start, ds.end, ds.in_sample_end, rebalance_days=spec.rebalance_days)
        bt = run_backtest(
            ds, spec.signal, ds.start, ds.end, ds.in_sample_end, spec.rebalance_days, spec.long_short
        )
        risk = with_stress(with_checks(target_portfolio(ds, spec.signal, ds.end, spec.long_short)))
        a = assess_research(stats, bt, risk)
        rows.append(
            {
                "id": spec.id,
                "hit": a.verdict is spec.expected_verdict,
                "covered": set(spec.expected_flags) <= set(a.flags),
                "got": [f.value for f in a.flags],
            }
        )
    misses = [r for r in rows if not (r["hit"] and r["covered"])]
    assert sum(r["hit"] for r in rows) / len(rows) >= 0.9, misses
    assert sum(r["covered"] for r in rows) / len(rows) >= 0.9, misses
