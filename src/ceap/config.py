"""Runtime configuration (environment-driven, no framework dependency)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str = field(default_factory=lambda: os.getenv("CEAP_ENV", "dev"))
    llm_provider: str = field(
        default_factory=lambda: os.getenv("CEAP_LLM_PROVIDER", "auto")
    )  # auto | mock | anthropic
    llm_model: str = field(default_factory=lambda: os.getenv("CEAP_LLM_MODEL", "claude-sonnet-4-5"))
    llm_planning_model: str | None = field(default_factory=lambda: os.getenv("CEAP_LLM_PLANNING_MODEL"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    default_dataset: str = field(default_factory=lambda: os.getenv("CEAP_DEFAULT_DATASET", "T01"))
    step_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("CEAP_STEP_TIMEOUT_SECONDS", "30"))
    )
    task_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("CEAP_TASK_TIMEOUT_SECONDS", "300"))
    )
    max_retries: int = field(default_factory=lambda: int(os.getenv("CEAP_MAX_RETRIES", "2")))
    auto_approve: bool = field(default_factory=lambda: _env_bool("CEAP_AUTO_APPROVE", True))
    api_keys: dict[str, str] = field(
        default_factory=lambda: _parse_api_keys(
            os.getenv(
                "CEAP_API_KEYS",
                "dev-trader-key:trader,dev-quant-key:quant,dev-admin-key:admin,dev-viewer-key:viewer",
            )
        )
    )
    knowledge_dir: str | None = field(default_factory=lambda: os.getenv("CEAP_KNOWLEDGE_DIR"))

    @property
    def use_anthropic(self) -> bool:
        if self.llm_provider == "anthropic":
            return True
        if self.llm_provider == "mock":
            return False
        return bool(self.anthropic_api_key)


def _parse_api_keys(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" in pair:
            key, role = pair.split(":", 1)
            out[key.strip()] = role.strip()
    return out


def load_settings() -> Settings:
    return Settings()
