"""Committed documents and data the code relies on.

Repository-integrity tier (H5): these tests read files outside ``src`` and ``tests`` (scripts, docs,
committed data), so they live here and resolve the repository root explicitly via ``repo_root``.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from repo_root import ROOT

from ceap.data.real import prereg


def test_power_curve_is_committed_and_monotone():
    doc = json.loads((ROOT / "docs" / "quant_power_curve.json").read_text(encoding="utf-8"))
    rows = doc["rows"]
    assert len(rows) >= 6 and doc["seeds_per_point"] >= 40
    for key in ("1.645", "3"):
        rates = [r["detection_rate"][key] for r in rows]
        assert rates[0] <= 0.15 and rates[-1] >= 0.8
        assert all(b >= a - 0.1 for a, b in zip(rates, rates[1:], strict=False))


def test_power_curve_carries_both_hurdles_in_force_and_the_detectable_premium():
    """Q5 (finding R-19): the curve is at the hurdles the gate uses, and names the detectable premium."""
    from ceap.analytics.research_assessment import MINED_HURDLE, SINGLE_TEST_HURDLE

    doc = json.loads((ROOT / "docs" / "quant_power_curve.json").read_text(encoding="utf-8"))
    assert doc["hurdles"] == [SINGLE_TEST_HURDLE, MINED_HURDLE]
    det = doc["detectable_premium_bps"]
    assert det["1.645"] is not None and det["3"] is not None
    assert det["1.645"]["premium_bps"] <= det["3"]["premium_bps"]
    text = (ROOT / "docs" / "research" / "README.md").read_text(encoding="utf-8")
    for h in ("1.645", "3"):
        assert f"{det[h]['premium_bps']:.0f} bps" in text


def test_attribution_file_present() -> None:
    text = (prereg.REPO_ROOT / "data" / "reference" / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "CC BY-SA" in text and "Kenneth R. French" in text and "fja05680" in text


def test_study_recorded_void_and_holdout_unspent() -> None:
    lock = json.loads(prereg.LOCK_PATH.read_text(encoding="utf-8"))
    assert lock["status"]["outcome"] == "VOID" and lock["status"]["pseudo_holdout"] == "UNSPENT"
    assert lock["status"]["test_statistic_computed"] is False
    doc = (prereg.REPO_ROOT / "docs" / "research" / "result_momentum_2026-11.md").read_text(encoding="utf-8")
    assert lock["sha256"] in doc and "fb9858e" in doc and "UNSPENT" in doc
    with pytest.raises(prereg.PreregistrationError):
        prereg.check_in_sample(date(2024, 1, 2), prereg.require_prereg_lock())
