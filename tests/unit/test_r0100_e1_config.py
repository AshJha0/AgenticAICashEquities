"""Runtime configuration fails closed (0.10.0 E1 coverage for the top-level ``ceap.config``).

Environment parsing, the provider rule outside dev, API-key parsing that never echoes key material,
model pins, and the start-up guards in ``validate_for_serving``.
"""

from __future__ import annotations

import logging

import pytest

from ceap import config
from ceap.api.auth import hash_api_key
from ceap.config import Settings, SettingsError

_ENV = (
    "CEAP_ENV",
    "CEAP_LLM_PROVIDER",
    "CEAP_API_KEYS",
    "CEAP_API_KEY_HASHES",
    "CEAP_AUTO_APPROVE",
    "CEAP_DB_PATH",
    "CEAP_LLM_MODEL",
    "CEAP_LLM_PLANNING_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("raw", "want"), [("1", True), (" YES ", True), ("on", True), ("0", False), ("off", False)]
)
def test_env_bool_accepts_the_usual_spellings(monkeypatch, raw, want):
    monkeypatch.setenv("CEAP_X", raw)
    assert config._env_bool("CEAP_X", not want) is want


def test_env_bool_blank_is_default_and_junk_is_refused(monkeypatch):
    monkeypatch.setenv("CEAP_X", "  ")
    assert config._env_bool("CEAP_X", True) is True
    monkeypatch.setenv("CEAP_X", "maybe")
    with pytest.raises(SettingsError, match="CEAP_X must be a boolean, got 'maybe'"):
        config._env_bool("CEAP_X", False)


def test_env_number_parses_and_enforces_type_and_minimum(monkeypatch):
    monkeypatch.setenv("CEAP_N", "2.5")
    assert config._env_number("CEAP_N", 1.0, 1.0) == 2.5
    with pytest.raises(SettingsError, match="must be an integer, got '2.5'"):
        config._env_number("CEAP_N", 1, 0, integer=True)
    monkeypatch.setenv("CEAP_N", "0.5")
    with pytest.raises(SettingsError, match="CEAP_N must be >= 1.0, got 0.5"):
        config._env_number("CEAP_N", 1.0, 1.0)
    monkeypatch.setenv("CEAP_N", "lots")
    with pytest.raises(SettingsError, match="must be a number"):
        config._env_number("CEAP_N", 1.0, 1.0)
    monkeypatch.delenv("CEAP_N")
    assert config._env_number("CEAP_N", 7, 0, integer=True) == 7


def test_dev_defaults_are_mock_dev_keys_auto_approve_and_memory_db():
    s = Settings()
    assert s.is_dev and s.llm_provider == "mock" and not s.use_anthropic
    assert s.auto_approve is True and s.db_path == ":memory:"
    assert set(s.api_keys.values()) >= {"trader", "approver", "auditor"}
    assert "dev-trader-key" not in repr(s)  # raw keys never appear in the repr


def test_outside_dev_the_provider_must_be_explicit_and_keys_are_not_defaulted(monkeypatch):
    monkeypatch.setenv("CEAP_ENV", "prod")
    with pytest.raises(SettingsError, match="CEAP_LLM_PROVIDER must be set explicitly"):
        Settings()
    monkeypatch.setenv("CEAP_LLM_PROVIDER", " Mock ")
    s = Settings()
    assert s.llm_provider == "mock" and s.api_keys == {} and s.auto_approve is False
    assert s.db_path == "var/ceap.db"


@pytest.mark.parametrize(("raw", "match"), [("auto", "removed in 0.4.0"), ("openai", "must be one of")])
def test_removed_and_unknown_providers_are_refused(monkeypatch, raw, match):
    monkeypatch.setenv("CEAP_LLM_PROVIDER", raw)
    with pytest.raises(SettingsError, match=match):
        Settings()


def test_provider_passed_directly_is_checked_too():
    with pytest.raises(SettingsError, match="llm_provider must be one of"):
        Settings(llm_provider="local")


def test_unpinned_models_are_refused_with_the_variable_named():
    with pytest.raises(SettingsError, match="CEAP_LLM_PLANNING_MODEL"):
        Settings(llm_provider="mock", llm_planning_model="not-a-pinned-model")


@pytest.mark.parametrize(
    ("raw", "match", "secret"),
    [
        ("supersecretvalue", "entry not shown", "supersecretvalue"),
        ("short:trader", "at least 8 characters", "short"),
        ("longenoughkey:wizard", "unknown role 'wizard'", "longenoughkey"),
        ("dupdupdupdup:trader,dupdupdupdup:viewer", "duplicate API key entry", "dupdupdupdup"),
    ],
)
def test_api_key_parsing_errors_never_echo_the_key(raw, match, secret):
    with pytest.raises(SettingsError, match=match) as info:
        config._parse_api_keys(raw)
    assert secret not in str(info.value)


def test_api_key_parsing_skips_blanks_and_strips():
    assert config._parse_api_keys(" , aaaaaaaa : trader ,") == {"aaaaaaaa": "trader"}


def _prod(**kw) -> Settings:
    return Settings(environment="prod", llm_provider="mock", auto_approve=False, **kw)


def test_serving_needs_keys_outside_dev():
    with pytest.raises(SettingsError, match="no API keys configured for CEAP_ENV='prod'"):
        _prod(api_keys={}).validate_for_serving()
    _prod(
        api_keys={}, api_key_hashes=hash_api_key("k") + ":trader"
    ).validate_for_serving()  # hashes alone suffice


def test_serving_refuses_malformed_key_hashes_at_start_up():
    with pytest.raises(SettingsError, match="sha256"):
        _prod(api_keys={}, api_key_hashes="md5$aa$" + "0" * 64 + ":trader").validate_for_serving()


def test_serving_refuses_dev_keys_and_auto_approve_outside_dev():
    with pytest.raises(SettingsError, match=r"development API keys \(dev-\*\)"):
        _prod(api_keys={"dev-trader-key": "trader"}).validate_for_serving()
    with pytest.raises(SettingsError, match="CEAP_AUTO_APPROVE=true is only allowed with CEAP_ENV=dev"):
        Settings(
            environment="staging", llm_provider="mock", auto_approve=True, api_keys={"a" * 43: "trader"}
        ).validate_for_serving()


def test_serving_in_dev_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="ceap.config"):
        Settings(llm_provider="mock").validate_for_serving()
    assert "must never be used outside a local/demo environment" in caplog.text


def test_load_settings_reads_the_environment(monkeypatch):
    monkeypatch.setenv("CEAP_AUTO_APPROVE", "false")
    assert config.load_settings().auto_approve is False
