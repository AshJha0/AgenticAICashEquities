"""Runtime configuration (environment-driven, no framework dependency).

Defaults are safe by environment: the development API keys and automatic
approvals apply only when ``CEAP_ENV=dev`` (the default). In any other
environment the key map must be supplied explicitly and approvals are
queued for a human unless ``CEAP_AUTO_APPROVE=true`` is set deliberately.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ceap.policy.permissions import Role

DEV_API_KEYS = "dev-trader-key:trader,dev-quant-key:quant,dev-admin-key:admin,dev-viewer-key:viewer"
LLM_PROVIDERS = ("auto", "mock", "anthropic")


class SettingsError(ValueError):
    """Raised for an invalid or unsafe configuration value."""


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be a boolean, got {raw!r}")


def _env_number(name: str, default: float, minimum: float, integer: bool = False) -> float:
    raw = _env(name, str(default))
    try:
        value = int(raw) if integer else float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be {'an integer' if integer else 'a number'}, got {raw!r}") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be >= {minimum}, got {value}")
    return value


def _environment() -> str:
    return _env("CEAP_ENV", "dev").lower()


def _default_api_keys() -> dict[str, str]:
    raw = os.getenv("CEAP_API_KEYS")
    if raw is None or raw.strip() == "":
        return _parse_api_keys(DEV_API_KEYS) if _environment() == "dev" else {}
    return _parse_api_keys(raw)


def _default_auto_approve() -> bool:
    return _env_bool("CEAP_AUTO_APPROVE", _environment() == "dev")


def _default_provider() -> str:
    provider = _env("CEAP_LLM_PROVIDER", "auto").lower()
    if provider not in LLM_PROVIDERS:
        raise SettingsError(f"CEAP_LLM_PROVIDER must be one of {LLM_PROVIDERS}, got {provider!r}")
    return provider


@dataclass(frozen=True)
class Settings:
    environment: str = field(default_factory=_environment)
    llm_provider: str = field(default_factory=_default_provider)  # auto | mock | anthropic
    llm_model: str = field(default_factory=lambda: _env("CEAP_LLM_MODEL", "claude-sonnet-4-5"))
    llm_planning_model: str | None = field(
        default_factory=lambda: os.getenv("CEAP_LLM_PLANNING_MODEL") or None
    )
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    default_dataset: str = field(default_factory=lambda: _env("CEAP_DEFAULT_DATASET", "T01"))
    step_timeout_seconds: float = field(
        default_factory=lambda: _env_number("CEAP_STEP_TIMEOUT_SECONDS", 30.0, 1.0)
    )
    task_timeout_seconds: float = field(
        default_factory=lambda: _env_number("CEAP_TASK_TIMEOUT_SECONDS", 300.0, 5.0)
    )
    max_retries: int = field(default_factory=lambda: int(_env_number("CEAP_MAX_RETRIES", 2, 0, integer=True)))
    auto_approve: bool = field(default_factory=_default_auto_approve)
    api_keys: dict[str, str] = field(default_factory=_default_api_keys)
    knowledge_dir: str | None = field(default_factory=lambda: os.getenv("CEAP_KNOWLEDGE_DIR") or None)
    max_concurrent_investigations: int = field(
        default_factory=lambda: int(_env_number("CEAP_MAX_CONCURRENT_INVESTIGATIONS", 4, 1, integer=True))
    )
    max_retained_results: int = field(
        default_factory=lambda: int(_env_number("CEAP_MAX_RETAINED_RESULTS", 200, 1, integer=True))
    )

    @property
    def use_anthropic(self) -> bool:
        if self.llm_provider == "anthropic":
            return True
        if self.llm_provider == "mock":
            return False
        return bool(self.anthropic_api_key)

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"

    def validate_for_serving(self) -> None:
        """Refuse to serve with an unsafe configuration outside development."""
        if not self.api_keys:
            raise SettingsError(
                f"no API keys configured for CEAP_ENV={self.environment!r}; set CEAP_API_KEYS=key:role,... "
                "(the dev-* keys are only applied when CEAP_ENV=dev)"
            )
        if not self.is_dev and any(key.startswith("dev-") for key in self.api_keys):
            raise SettingsError("development API keys (dev-*) must not be used outside CEAP_ENV=dev")


def _parse_api_keys(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    valid_roles = {r.value for r in Role}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise SettingsError(f"CEAP_API_KEYS entry {pair!r} must be key:role")
        key, role = (part.strip() for part in pair.split(":", 1))
        if len(key) < 8:
            raise SettingsError("API keys must be at least 8 characters")
        if role not in valid_roles:
            raise SettingsError(f"unknown role {role!r} for API key; valid roles: {sorted(valid_roles)}")
        out[key] = role
    return out


def load_settings() -> Settings:
    return Settings()
