"""0.10.0 item Q7 (findings Q-10, Q-12, R-17): stale models fail a test, drift is checked at load, the card
header is current, and the trial ledger is persistent by default."""

from __future__ import annotations

import inspect
import json
import re
import warnings
from pathlib import Path

import numpy as np
import pytest

from ceap.ml.registry import (
    MODELS_DIR,
    DriftWarning,
    ModelRegistry,
    StaleModelWarning,
    feature_reference,
    generator_hash,
)

ROOT = Path(__file__).resolve().parents[3]


def test_every_registered_model_was_fitted_to_the_current_generator():
    files = sorted(MODELS_DIR.glob("*.json"))
    assert files
    stale = {p.name: json.loads(p.read_text(encoding="utf-8"))["card"]["generator_hash"] for p in files}
    stale = {k: v for k, v in stale.items() if v != generator_hash()}
    assert not stale, f"retrain with scripts/train_ml_models.py: {stale} != {generator_hash()}"


def test_model_card_header_names_the_current_generator():
    text = (ROOT / "docs" / "ml" / "model_cards.md").read_text(encoding="utf-8")
    header = re.search(r"current generator hash is `([0-9a-f]{16})`", text)
    assert header and header.group(1) == generator_hash()
    assert set(re.findall(r"synthetic generator ([0-9a-f]{16})", text)) == {generator_hash()}


def _toy(reg: ModelRegistry):
    rng = np.random.default_rng(0)
    return reg.register("toy", "ridge", {"coef": [1.0]}, training_data_hash="abc", metrics={},
                        feature_reference={"x": feature_reference(rng.normal(0, 1, 5000))}, created="2026-11-14")


def test_load_warns_when_the_generator_changed_since_fitting(tmp_path):
    reg = ModelRegistry(tmp_path)
    m = _toy(reg)
    with warnings.catch_warnings():
        warnings.simplefilter("error", StaleModelWarning)
        reg.load("toy")  # current generator: silent
    path = reg.path_for("toy", m.model_hash)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["card"]["generator_hash"] = "0" * 16
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.warns(StaleModelWarning, match="retrain"):
        reg.load("toy")


def test_drift_check_runs_at_load_and_in_the_detector_path(tmp_path):
    reg = ModelRegistry(tmp_path)
    _toy(reg)
    with pytest.warns(DriftWarning):
        m = reg.load("toy", features={"x": np.random.default_rng(1).normal(3, 1, 500)})
    assert m.drift["x"] > 0.25
    from ceap.agents import engineering
    from ceap.ml.anomaly import detector_drift, synthetic_session

    assert "detector_drift(" in inspect.getsource(engineering.change_point_scan)
    drift = detector_drift(synthetic_session(0)[0], 60)
    assert drift and all(np.isfinite(v) for v in drift.values())


def test_trial_ledger_is_persistent_by_default_and_survives_a_restart(tmp_path, monkeypatch):
    from ceap.analytics.trial_ledger import DEFAULT_LEDGER_PATH, TrialLedger

    monkeypatch.delenv("CEAP_TRIAL_LEDGER", raising=False)
    monkeypatch.chdir(tmp_path)
    TrialLedger().record("d", {"signal": "a"}, 0.01)
    TrialLedger().record("d", {"signal": "b"}, 0.02)
    assert (tmp_path / DEFAULT_LEDGER_PATH).exists()
    assert TrialLedger().summary("d").n_trials == 2  # a new process reads the earlier trials
    monkeypatch.setenv("CEAP_TRIAL_LEDGER", ":memory:")
    assert TrialLedger().summary("d").n_trials == 0  # the explicit opt-out keeps nothing
