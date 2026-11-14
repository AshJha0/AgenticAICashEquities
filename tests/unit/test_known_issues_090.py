"""One test per 0.9.0 known issue fixed in 0.10.0 (plan item X6)."""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from ceap.domain.tools import ToolRequest
from ceap.harness.durable import idempotency_key
from ceap.paper.pretrade import sigma_band
from ceap.studies.industry_momentum import andrews_lag


def test_andrews_lag_constant_residuals_is_zero() -> None:
    assert andrews_lag(np.full(50, 3.0)) == 0


def test_andrews_lag_unchanged_for_normal_input() -> None:
    rng = np.random.default_rng(0)
    assert andrews_lag(rng.normal(size=500)) >= 0


def test_sigma_band_flat_prices_falls_back_to_cap() -> None:
    limits = type("L", (), {"sigma_window": 20, "price_band_cap": 0.2, "price_band_sigma": 4.0})()
    adj = np.full((30, 1), 10.0)
    assert sigma_band(adj, 25, 0, limits) == pytest.approx(0.2)


@dataclasses.dataclass
class _Arg:
    x: int


def _req(args: dict) -> ToolRequest:
    fields = ToolRequest.model_fields if hasattr(ToolRequest, "model_fields") else {}
    kw = {"tool_id": "t", "arguments": args}
    if "correlation_id" in fields:
        kw["correlation_id"] = "c"
    return ToolRequest(**kw)


def test_idempotency_key_stable_for_sets_and_dataclasses() -> None:
    a = idempotency_key("task", "s1", _req({"s": {"b", "a", "c"}, "d": _Arg(1)}))
    b = idempotency_key("task", "s1", _req({"s": {"c", "a", "b"}, "d": _Arg(1)}))
    assert a == b


def test_idempotency_key_refuses_unstable_objects() -> None:
    with pytest.raises(TypeError):
        idempotency_key("task", "s1", _req({"o": object()}))
