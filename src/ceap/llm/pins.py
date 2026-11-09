"""Loader for ``model_pins.toml``: the single source of allowed model ids (PLAN 1.9, D14)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
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
class Price:
    """USD per million tokens (list price as pinned; used for the local estimate only)."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class ModelPins:
    sdk_package: str
    sdk_version: str
    allowed: frozenset[str]
    purposes: dict[str, PurposePin]
    prices: dict[str, Price] = field(default_factory=dict)
    #: A15: prompt id -> (version, sha256 of its canonical bytes), checked when a prompt is loaded
    prompts: dict[str, tuple[str, str]] = field(default_factory=dict)

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
    prices = {
        str(m): Price(
            float(p["input"]),
            float(p["output"]),
            float(p.get("cache_read", 0.0)),
            float(p.get("cache_write", 0.0)),
        )
        for m, p in (raw.get("pricing") or {}).items()
    }
    prompts: dict[str, tuple[str, str]] = {}
    for pid, ref in (raw.get("prompts") or {}).items():
        version, _, sha = str(ref).partition(":")
        if not version.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"model_pins.toml [prompts] {pid!r} must be 'version:sha256', got {ref!r}")
        prompts[str(pid)] = (version, sha)
    sdk = raw.get("sdk") or {}
    return ModelPins(
        str(sdk.get("package", "anthropic")),
        str(sdk.get("version", "")),
        allowed,
        purposes,
        prices,
        prompts,
    )


@lru_cache(maxsize=1)
def load_pins() -> ModelPins:
    text = resources.files("ceap.llm").joinpath("model_pins.toml").read_text(encoding="utf-8")
    return parse_pins(tomllib.loads(text))
