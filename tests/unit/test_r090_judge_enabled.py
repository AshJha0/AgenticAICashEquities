"""enabled_dimensions fails closed on a malformed calibration file (0.9.0 qual2)."""

from __future__ import annotations

import json

from ceap.eval.judge import enabled_dimensions


def test_malformed_calibration_enables_nothing(tmp_path):
    p = tmp_path / "cal.json"
    for doc in (
        {"enabled": ["faithful"]},
        {"enabled": ["faithful"], "dimensions": {}},
        [1, 2],
        {"enabled": ["faithful"], "dimensions": {"faithful": {"kappa": None}}},
    ):
        p.write_text(json.dumps(doc), encoding="utf-8")
        assert enabled_dimensions(p) == []


def test_well_formed_calibration_enables_only_dimensions_above_the_bar(tmp_path):
    p = tmp_path / "cal.json"
    doc = {
        "enabled": ["faithful", "hedged"],
        "dimensions": {"faithful": {"kappa": 0.75}, "hedged": {"kappa": 0.6}},
    }
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert enabled_dimensions(p) == ["faithful"]
