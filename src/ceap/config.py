"""Runtime configuration (environment-driven, no framework dependency).

Defaults are safe by environment: the development API keys and automatic
approvals apply only when ``CEAP_ENV=dev`` (the default). In any other
environment the key map must be supplied explicitly, approvals are queued for
a human, and ``CEAP_AUTO_APPROVE=true`` makes the server refuse to start.

The LLM layer fails closed: ``CEAP_LLM_PROVIDER`` is ``mock`` or ``anthropic``
(``auto`` was removed in 0.4.0); outside dev it must be set explicitly; model
ids must be pinned in ``llm/model_pins.toml``; ``CEAP_LLM_DISABLED=true`` is the
kill switch that forces the deterministic path everywhere.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from ceap.policy.engine import trusted_servers_from_env
from ceap.policy.permissions import Role

logger = logging.getLogger(__name__)

DEV_API_KEYS = (
    "dev-trader-key:trader,dev-quant-key:quant,dev-admin-key:admin,dev-viewer-key:viewer,"
    "dev-approver-key:approver,dev-approver2-key:approver,dev-auditor-key:auditor"
)
LLM_PROVIDERS = ("mock", "anthropic")


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
    raw = os.getenv("CEAP_LLM_PROVIDER")
    if raw is None or raw.strip() == "":
        if _environment() != "dev":
            raise SettingsError(
                f"CEAP_LLM_PROVIDER must be set explicitly (mock or anthropic) for CEAP_ENV={_environment()!r}"
            )
        return "mock"
    provider = raw.strip().lower()
    if provider == "auto":
        raise SettingsError(
            "CEAP_LLM_PROVIDER=auto was removed in 0.4.0 (it silently fell back to the mock); "
            "set CEAP_LLM_PROVIDER=anthropic or CEAP_LLM_PROVIDER=mock"
        )
    if provider not in LLM_PROVIDERS:
        raise SettingsError(f"CEAP_LLM_PROVIDER must be one of {LLM_PROVIDERS}, got {provider!r}")
    return provider


@dataclass(frozen=True)
class Settings:
    environment: str = field(default_factory=_environment)
    llm_provider: str = field(default_factory=_default_provider)  # mock | anthropic
    llm_model: str = field(default_factory=lambda: _env("CEAP_LLM_MODEL", _pinned_default("narrative")))
    llm_planning_model: str | None = field(
        default_factory=lambda: os.getenv("CEAP_LLM_PLANNING_MODEL") or None
    )
    anthropic_api_key: str | None = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None, repr=False
    )
    default_dataset: str = field(default_factory=lambda: _env("CEAP_DEFAULT_DATASET", "T01"))
    step_timeout_seconds: float = field(
        default_factory=lambda: _env_number("CEAP_STEP_TIMEOUT_SECONDS", 30.0, 1.0)
    )
    task_timeout_seconds: float = field(
        default_factory=lambda: _env_number("CEAP_TASK_TIMEOUT_SECONDS", 300.0, 5.0)
    )
    max_retries: int = field(default_factory=lambda: int(_env_number("CEAP_MAX_RETRIES", 2, 0, integer=True)))
    auto_approve: bool = field(default_factory=_default_auto_approve)
    # raw keys never appear in repr, logs or errors (R4 item 4.6); auth compares salted hashes
    api_keys: dict[str, str] = field(default_factory=_default_api_keys, repr=False)
    #: pre-hashed keys ``sha256$<salt hex>$<digest hex>:<role>[:<expires YYYY-MM-DD>]``, comma-separated
    api_key_hashes: str = field(default_factory=lambda: os.getenv("CEAP_API_KEY_HASHES") or "", repr=False)
    knowledge_dir: str | None = field(default_factory=lambda: os.getenv("CEAP_KNOWLEDGE_DIR") or None)
    max_concurrent_investigations: int = field(
        default_factory=lambda: int(_env_number("CEAP_MAX_CONCURRENT_INVESTIGATIONS", 4, 1, integer=True))
    )
    max_retained_results: int = field(
        default_factory=lambda: int(_env_number("CEAP_MAX_RETAINED_RESULTS", 200, 1, integer=True))
    )
    llm_disabled: bool = field(default_factory=lambda: _env_bool("CEAP_LLM_DISABLED", False))
    llm_timeout_s: float = field(default_factory=lambda: _env_number("CEAP_LLM_TIMEOUT_S", 60.0, 1.0))
    llm_max_concurrency: int = field(
        default_factory=lambda: int(_env_number("CEAP_LLM_MAX_CONCURRENCY", 4, 1, integer=True))
    )
    llm_max_calls_per_task: int = field(
        default_factory=lambda: int(_env_number("CEAP_LLM_MAX_CALLS_PER_TASK", 12, 1, integer=True))
    )
    mcp_trusted_servers: frozenset[str] = field(default_factory=trusted_servers_from_env)
    # ---- persistence and platform limits (R4)
    db_path: str = field(default_factory=lambda: _env("CEAP_DB_PATH", _default_db_path()))
    approval_timeout_s: float = field(default_factory=lambda: _env_number("CEAP_APPROVAL_TIMEOUT_S", 120.0, 1.0))
    max_body_bytes: int = field(
        default_factory=lambda: int(_env_number("CEAP_MAX_BODY_BYTES", 65_536, 1024, integer=True))
    )
    retention_results_days: float = field(
        default_factory=lambda: _env_number("CEAP_RETENTION_RESULTS_DAYS", 90.0, 0.0)
    )
    retention_traces_days: float = field(
        default_factory=lambda: _env_number("CEAP_RETENTION_TRACES_DAYS", 30.0, 0.0)
    )
    retention_llm_io_days: float = field(
        default_factory=lambda: _env_number("CEAP_RETENTION_LLM_IO_DAYS", 7.0, 0.0)
    )
    db_max_bytes: int = field(
        default_factory=lambda: int(_env_number("CEAP_DB_MAX_BYTES", 1 << 30, 1 << 20, integer=True))
    )

    def __post_init__(self) -> None:
        if self.llm_provider not in LLM_PROVIDERS:
            raise SettingsError(f"llm_provider must be one of {LLM_PROVIDERS}, got {self.llm_provider!r}")
        from ceap.llm.pins import UnpinnedModelError, load_pins

        pins = load_pins()
        for name, model in (("CEAP_LLM_MODEL", self.llm_model), ("CEAP_LLM_PLANNING_MODEL", self.llm_planning_model)):
            if model is None:
                continue
            try:
                pins.require(model)
            except UnpinnedModelError as exc:
                raise SettingsError(f"{name}: {exc}") from exc

    @property
    def use_anthropic(self) -> bool:
        return self.llm_provider == "anthropic"

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
        if not self.is_dev and self.auto_approve:
            raise SettingsError(
                f"CEAP_AUTO_APPROVE=true is only allowed with CEAP_ENV=dev (got {self.environment!r}); "
                "approvals outside dev must come from a human approver"
            )
        if self.is_dev:
            logger.warning(
                "CEAP_ENV=dev: serving with dev-* API keys and/or default auto-approve=%s. "
                "This configuration must never be used outside a local/demo environment.",
                self.auto_approve,
            )


def _default_db_path() -> str:
    """In-memory in dev (tests and demos leave no file behind); a file everywhere else."""
    return ":memory:" if _environment() == "dev" else "var/ceap.db"


def _pinned_default(purpose: str) -> str:
    from ceap.llm.pins import load_pins

    return load_pins().for_purpose(purpose).model


def _parse_api_keys(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    valid_roles = {r.value for r in Role}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            # never echo the entry: it is (or contains) the raw key (0.10.0 E7, finding E-08)
            raise SettingsError("a CEAP_API_KEYS entry must be key:role (entry not shown: it holds key material)")
        key, role = (part.strip() for part in pair.split(":", 1))
        if len(key) < 8:
            raise SettingsError("API keys must be at least 8 characters")
        if role not in valid_roles:
            raise SettingsError(f"unknown role {role!r} for API key; valid roles: {sorted(valid_roles)}")
        if key in out:
            raise SettingsError("duplicate API key entry in CEAP_API_KEYS (key not shown)")
        out[key] = role
    return out


def load_settings() -> Settings:
    return Settings()
