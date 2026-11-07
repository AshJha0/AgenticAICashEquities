"""Loader for ``model_pins.toml``: the single source of allowed model ids (PLAN 1.9, D14)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

PURPOSES = ("planning", "narrative", "critique", "judge")


class UnpinnedModelError(ValueError):
    """A model id that is not in ``model_pins.toml`` was requested."""


@dataclass(frozen=True)
class PurposePin:
    purpose: str
    model: str
    fallback_model: str | None
    effort: str
    max_tokens: int


@dataclass(frozen=True)
class ModelPins:
    sdk_package: str
    sdk_version: str
    allowed: frozenset[str]
    purposes: dict[str, PurposePin]

    def require(self, model: str) -> str:
        if model not in self.allowed:
            raise UnpinnedModelError(
                f"model {model!r} is not pinned in llm/model_pins.toml; allowed: {sorted(self.allowed)}"
            )
        return model

    def for_purpose(self, purpose: str) -> PurposePin:
        return self.purposes.get(purpose) or self.purposes["narrative"]


def parse_pins(raw: dict[str, Any]) -> ModelPins:
    allowed = frozenset(raw.get("models", {}))
    purposes: dict[str, PurposePin] = {}
    for name, cfg in (raw.get("purpose") or {}).items():
        pin = PurposePin(
            purpose=name,
            model=str(cfg["model"]),
            fallback_model=cfg.get("fallback_model"),
            effort=str(cfg.get("effort", "medium")),
            max_tokens=int(cfg["max_tokens"]),
        )
        for m in (pin.model, pin.fallback_model):
            if m is not None and m not in allowed:
                raise UnpinnedModelError(f"purpose {name}: model {m!r} is not in [models]")
        purposes[name] = pin
    missing = [p for p in PURPOSES if p not in purposes]
    if missing:
        raise ValueError(f"model_pins.toml lacks purposes {missing}")
    sdk = raw.get("sdk") or {}
    return ModelPins(str(sdk.get("package", "anthropic")), str(sdk.get("version", "")), allowed, purposes)


@lru_cache(maxsize=1)
def load_pins() -> ModelPins:
    text = resources.files("ceap.llm").joinpath("model_pins.toml").read_text(encoding="utf-8")
    return parse_pins(tomllib.loads(text))
