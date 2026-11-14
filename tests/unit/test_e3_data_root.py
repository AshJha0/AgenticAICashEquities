"""E3 (R-27): one data resolver, loud failure on missing required data, installed-copy smoke check."""

from __future__ import annotations

from pathlib import Path

import pytest

import ceap.paths as paths
from ceap.paths import DATA_ENV, PACKAGED, REQUIRED, DataFileMissing, data_root, require

REPO_DATA = Path(__file__).resolve().parents[2] / "data"


def test_data_root_order_env_then_package_then_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_ENV, raising=False)
    monkeypatch.setattr(paths, "PACKAGE_DATA", tmp_path / "absent")
    assert data_root() == paths.CHECKOUT_DATA == REPO_DATA
    packaged = tmp_path / "pkg"
    packaged.mkdir()
    monkeypatch.setattr(paths, "PACKAGE_DATA", packaged)
    assert data_root() == packaged
    monkeypatch.setenv(DATA_ENV, str(tmp_path / "mounted"))
    assert data_root() == (tmp_path / "mounted").resolve()


def test_missing_required_data_is_loud_and_not_swallowed(tmp_path, monkeypatch):
    """The 0.9.0 failure: an installed copy with no data served an empty corpus and unfitted models."""
    from ceap.analytics.calibration import CHANGE_POINT_KEY, calibration_info
    from ceap.ml.anomaly import load_detector
    from ceap.ml.registry import ModelRegistry
    from ceap.rag.retrieval import build_knowledge_base

    monkeypatch.setenv(DATA_ENV, str(tmp_path))
    with pytest.raises(DataFileMissing, match=DATA_ENV):
        require("models")
    assert not issubclass(DataFileMissing, OSError)  # `except OSError` fallbacks cannot hide it
    with pytest.raises(DataFileMissing):
        build_knowledge_base()
    with pytest.raises(DataFileMissing):
        ModelRegistry()
    with pytest.raises(DataFileMissing):
        load_detector()
    with pytest.raises(DataFileMissing):
        calibration_info(CHANGE_POINT_KEY)
    # an explicit, deliberately empty directory is still allowed (warns only)
    assert build_knowledge_base(tmp_path).documents == {}


def test_packaged_globs_cover_every_required_input():
    for pattern in PACKAGED:
        assert list(REPO_DATA.glob(pattern)), f"{pattern} matches no committed file"
    for rel in REQUIRED:
        assert any(p.startswith(rel + "/") for p in PACKAGED), f"{rel} is required but not packaged"
    assert not any("real_cache" in p for p in PACKAGED)


def test_smoke_fails_without_data_and_passes_with_it(tmp_path, monkeypatch, capsys):
    from ceap import smoke

    monkeypatch.setenv(DATA_ENV, str(tmp_path))
    assert smoke.main() == 1
    assert "DataFileMissing" in capsys.readouterr().err
    monkeypatch.delenv(DATA_ENV)
    seen = smoke.run()
    assert seen["knowledge_documents"] >= 10 and seen["detector_model_hash"] and seen["calibration_model_hash"]
    assert seen["state"] == "COMPLETED" and seen["report"]
