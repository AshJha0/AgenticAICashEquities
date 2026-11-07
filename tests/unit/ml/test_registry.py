"""2B.5: model registry, cards, reproducibility and drift."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

from ceap.ml.registry import (
    PSI_WARN,
    DriftWarning,
    ModelIntegrityError,
    ModelRegistry,
    feature_reference,
    generator_hash,
    model_hash,
    psi,
    synthetic_label,
)

ROOT = Path(__file__).resolve().parents[3]


def _register(reg: ModelRegistry, coef: list[float]):
    rng = np.random.default_rng(0)
    return reg.register(
        "toy",
        "ridge",
        {"coef": coef},
        training_data_hash="abc",
        metrics={"r2": 0.5},
        feature_reference={"x": feature_reference(rng.normal(0, 1, 5000))},
        created="2026-11-07",
    )


def test_round_trip_by_hash_and_card(tmp_path):
    reg = ModelRegistry(tmp_path)
    m = _register(reg, [0.1, -0.2])
    loaded = reg.load("toy", m.model_hash)
    assert loaded.params == {"coef": [0.1, -0.2]} and loaded.card == m.card
    assert m.card.label == synthetic_label() == f"fitted to synthetic generator {generator_hash()}"
    assert len(generator_hash()) == 16
    assert "fitted to synthetic generator" in m.card.to_markdown()
    assert [c.name for c in reg.list()] == ["toy"]


def test_tampered_parameters_are_refused(tmp_path):
    reg = ModelRegistry(tmp_path)
    m = _register(reg, [0.1, -0.2])
    path = reg.path_for("toy", m.model_hash)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["params"]["coef"][0] = 0.2
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ModelIntegrityError):
        reg.load("toy", m.model_hash)


def test_hash_is_stable_below_tolerance_and_moves_above_it():
    h = model_hash("m", "k", {"c": [0.123456789012]})
    assert model_hash("m", "k", {"c": [0.123456789012 + 1e-13]}) == h  # last-bit BLAS noise
    assert model_hash("m", "k", {"c": [-0.0]}) == model_hash("m", "k", {"c": [0.0]})
    assert model_hash("m", "k", {"c": [0.1235]}) != h


def test_psi_stable_and_drift_warning(tmp_path):
    reg = ModelRegistry(tmp_path)
    m = _register(reg, [0.0])
    rng = np.random.default_rng(1)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        same = reg.check_drift(m, {"x": rng.normal(0, 1, 5000)})
    assert same["x"] < 0.02
    with pytest.warns(DriftWarning, match="toy"):
        moved = reg.check_drift(m, {"x": rng.normal(1.0, 1, 5000)})
    assert moved["x"] > PSI_WARN


def test_psi_hand_value():
    ref = {"edges": [0.0], "expected": [0.5, 0.5]}
    a = 0.8
    expected = (a - 0.5) * np.log(a / 0.5) + (0.2 - 0.5) * np.log(0.2 / 0.5)
    assert psi(ref, np.array([1.0] * 8 + [-1.0] * 2)) == pytest.approx(expected)


def test_shipped_models_load_and_carry_the_label():
    reg = ModelRegistry()
    cards = reg.list()
    assert {c.name for c in cards} >= {"change_detector", "ridge_combination", "calibration_engineering_change_point"}
    for c in cards:
        m = reg.load(c.name, c.model_hash)
        assert m.card.label.endswith(f"synthetic generator {m.card.generator_hash}")
        assert m.card.training_data_hash and m.card.created


def _refit_coef(threads: str) -> list[float]:
    env = {**os.environ, "OMP_NUM_THREADS": threads, "OPENBLAS_NUM_THREADS": threads, "MKL_NUM_THREADS": threads}
    env.pop("ANTHROPIC_API_KEY", None)
    env["PYTHONPATH"] = str(ROOT / "src")
    code = (
        "import json\n"
        "from ceap.analytics.ml_signals import combine_signals\n"
        "from ceap.analytics.trial_ledger import TrialLedger\n"
        "from ceap.data.historical import HistoricalGenerator\n"
        "from ceap.data.research_scenarios import research_scenario_for\n"
        "ds = HistoricalGenerator().generate(research_scenario_for('momentum_premium'))\n"
        "r = combine_signals(ds, ledger=TrialLedger(path=None))\n"
        "print(json.dumps([r.alpha, list(r.coef.values()), ds.dataset_hash]))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.nightly
def test_ridge_reproduces_the_card_across_thread_counts_and_os():
    """Same seed, 1 and 4 BLAS threads, on whatever OS runs nightly: within 1e-8 of the stored card."""
    card = ModelRegistry().load("ridge_combination")
    stored = list(card.params["coef"].values())
    for threads in ("1", "4"):
        alpha, coef, dh = _refit_coef(threads)
        assert dh == card.card.training_data_hash
        assert alpha == card.params["alpha"]
        np.testing.assert_allclose(coef, stored, rtol=1e-6, atol=1e-8)
