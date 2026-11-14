"""E4 / R-26: deployment recipes work as written."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceap.api.auth import KeyRecord, check_key_strength, hash_api_key, parse_key_hashes
from ceap.config import Settings, SettingsError
from ceap.policy.permissions import ROLE_CAPABILITIES, Role

ROOT = Path(__file__).resolve().parents[2]
KEY = "Zq3_vL9x-Pw2Rk7Tn4Yb8Hc1Md6Fs0Gj5Ua_Ke2Ox9W"


def _hash_script():
    spec = importlib.util.spec_from_file_location("hash_api_key_script", ROOT / "scripts" / "hash_api_key.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("CEAP_ENV", "prod")
    monkeypatch.setenv("CEAP_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CEAP_AUTO_APPROVE", "false")
    monkeypatch.delenv("CEAP_API_KEYS", raising=False)
    monkeypatch.delenv("CEAP_API_KEY_HASHES", raising=False)
    return monkeypatch


def test_hashed_keys_alone_are_a_valid_serving_configuration(prod_env):
    prod_env.setenv("CEAP_API_KEY_HASHES", f"{hash_api_key(KEY)}:auditor")
    s = Settings()
    assert s.api_keys == {}
    s.validate_for_serving()  # must not raise
    check_key_strength(s)


def test_no_keys_at_all_is_still_refused(prod_env):
    with pytest.raises(SettingsError, match="CEAP_API_KEY_HASHES"):
        Settings().validate_for_serving()


def test_malformed_hash_fails_at_start_up(prod_env):
    prod_env.setenv("CEAP_API_KEY_HASHES", "sha256$00$short:auditor")
    with pytest.raises(SettingsError):
        Settings().validate_for_serving()


def test_hash_only_app_authenticates_the_raw_key(prod_env, tmp_path):
    prod_env.setenv("CEAP_API_KEY_HASHES", f"{hash_api_key(KEY)}:auditor")
    prod_env.setenv("CEAP_DB_PATH", str(tmp_path / "ceap.db"))
    from ceap.api.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/tools").status_code == 401
        assert client.get("/tools", headers={"X-API-Key": "wrong"}).status_code == 401
        ok = client.get("/tools", headers={"X-API-Key": KEY})
        assert ok.status_code == 200 and ok.json()


def test_hash_script_matches_the_library_format():
    mod = _hash_script()
    salt = bytes(range(16))
    line = mod.entry(KEY, "trader", key_id="ci", expires="2099-01-01", salt=salt)
    assert line.startswith(hash_api_key(KEY, salt) + ":trader")
    (rec,) = parse_key_hashes(line)
    assert isinstance(rec, KeyRecord) and rec.role == "trader" and rec.key_id == "ci"
    assert rec.matches(KEY) and not rec.matches(KEY + "x")


def test_hash_script_reads_key_from_environment_only(monkeypatch, capsys):
    mod = _hash_script()
    monkeypatch.delenv("KEY", raising=False)
    assert mod.main(["--role", "auditor"]) == 2
    monkeypatch.setenv("KEY", KEY)
    assert mod.main(["--role", "auditor"]) == 0
    assert parse_key_hashes(capsys.readouterr().out.strip())[0].matches(KEY)


def test_compose_example_ships_only_hashed_keys():
    text = (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "CEAP_API_KEY_HASHES" in text
    assert not re.search(r"^\s*CEAP_API_KEYS\s*:", text, re.M)


def test_ci_starts_the_compose_stack_and_calls_health_and_an_authenticated_route():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^  compose:\n(?:    .*\n|\n)*?.*scripts/compose_smoke\.sh", ci, re.M)
    smoke = (ROOT / "scripts" / "compose_smoke.sh").read_text(encoding="utf-8")
    assert "up -d --build --wait" in smoke and "https://localhost/health" in smoke
    assert 'X-API-Key: $key' in smoke and "401" in smoke


def test_cookbook_docker_recipe_does_not_use_a_weak_raw_key(prod_env):
    text = (ROOT / "COOKBOOK.md").read_text(encoding="utf-8")
    section = text.split("## 26. Run in Docker", 1)[1].split("\n## ", 1)[0]
    for raw in re.findall(r'CEAP_API_KEYS="?([^"\s]+)', section):
        prod_env.setenv("CEAP_API_KEYS", raw)
        check_key_strength(Settings())  # any raw key shown must pass the entropy rule
    assert "CEAP_API_KEY_HASHES" in section


def test_cookbook_approval_decision_uses_a_role_that_can_decide():
    text = (ROOT / "COOKBOOK.md").read_text(encoding="utf-8")
    section = text.split("## 6. Turn on human approvals", 1)[1].split("\n## ", 1)[0]
    keys = re.findall(r"X-API-Key: (dev-[a-z0-9]+)-key", section)
    assert keys
    for name in keys:
        role = Role(re.sub(r"\d+$", "", name.removeprefix("dev-")))
        assert "approvals:decide" in ROLE_CAPABILITIES[role], name

