"""0.9.0 lane L5: approval TTL sweep (G8), loop-lag gauge (G9), calendar bound (G12), Docker (G13),
API-key strength and key ids (H11), no print in library code (H12)."""

from __future__ import annotations

import ast
import asyncio
import time
from datetime import date
from pathlib import Path

import pytest

from ceap.api.auth import (
    check_key_strength,
    hash_api_key,
    key_entropy_bits,
    parse_key_hashes,
    parse_subjects,
    principal_label,
)
from ceap.api.loopmon import SWEEP_REASON, LoopLagMonitor, sweep_approvals_once
from ceap.config import Settings, SettingsError
from ceap.observability.metrics import metrics
from ceap.policy.approvals import ApprovalRequest

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ G9 loop lag
async def test_loop_lag_monitor_sees_a_blocked_loop():
    mon = LoopLagMonitor(interval_s=0.05)
    task = asyncio.create_task(mon.run())
    await asyncio.sleep(0.12)  # let the monitor start sleeping
    before = mon.samples
    time.sleep(0.3)  # block the loop on purpose
    await asyncio.sleep(0.12)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    # the wake-up that spans the block is at least ~300 ms late (250 ms leaves room for timer
    # granularity); no bound on idle lag, which depends on how loaded the test machine is
    assert mon.samples > before
    assert mon.max_ms >= 250.0
    assert "ceap_event_loop_lag_seconds" in metrics.render()


# ------------------------------------------------------------------ G8 approval sweep
def test_sweep_expires_orphans_only_and_audits(platform, monkeypatch):
    live = ApprovalRequest.create("T1", "live", "t", {"i": 1}, "r", "p")
    orphan = ApprovalRequest.create("T1", "orphan", "t", {"i": 2}, "r", "p")
    for r in (live, orphan):
        platform.approval_store.insert(r)
    platform.db.run(lambda c: c.execute("UPDATE approvals SET requested_at='2000-01-01T00:00:00Z'"))
    monkeypatch.setattr(platform.approvals, "pending", lambda: [live])  # a live waiter holds it
    ids = sweep_approvals_once(platform, 1.0)
    assert ids == [orphan.id]
    assert platform.approval_store.status(live.id) == "PENDING"
    row = platform.approval_store.get(orphan.id)
    assert row["status"] == "EXPIRED" and row["comment"] == SWEEP_REASON
    audit = platform.db.query("SELECT action, subject FROM audit_log WHERE subject=?", (orphan.id,))
    assert ("approval.expired", orphan.id) in {(a["action"], a["subject"]) for a in audit}


# ------------------------------------------------------------------ H11 keys
def test_key_entropy_estimate_worked_examples():
    import secrets

    assert key_entropy_bits(secrets.token_urlsafe(32)) >= 256
    assert round(key_entropy_bits("prodkey-123456"), 1) == 85.2
    assert key_entropy_bits("") == 0.0


def test_weak_keys_refused_outside_dev_only():
    import secrets

    weak = Settings(llm_provider="mock", environment="prod", api_keys={"prodkey-123456": "trader"})
    with pytest.raises(SettingsError) as exc:
        check_key_strength(weak)
    assert "prodkey-123456" not in str(exc.value)  # the message never echoes the key
    check_key_strength(
        Settings(llm_provider="mock", environment="prod", api_keys={secrets.token_urlsafe(32): "trader"})
    )
    check_key_strength(
        Settings(llm_provider="mock", environment="dev", api_keys={"dev-trader-key": "trader"})
    )
    with pytest.raises(SettingsError):  # long but one repeated character
        check_key_strength(Settings(llm_provider="mock", environment="prod", api_keys={"a" * 80: "trader"}))


def test_key_ids_label_principals():
    entry = hash_api_key("k" * 50) + ":approver:id=ops-desk-1:subject=alice"
    (rec,) = parse_key_hashes(entry)
    assert rec.key_id == "ops-desk-1" and rec.subject == "alice" and rec.role == "approver"
    assert principal_label("approver", "whatever", rec.key_id) == "approver:ops-desk-1"
    assert (
        principal_label("trader", "abc").startswith("trader:") and len(principal_label("trader", "abc")) == 15
    )
    assert parse_subjects("approver:ops-desk-1=alice,trader:0a1b2c3d=bob") == {
        "approver:ops-desk-1": "alice",
        "trader:0a1b2c3d": "bob",
    }
    with pytest.raises(SettingsError):
        parse_key_hashes(hash_api_key("k" * 50) + ":approver:id=bad id!")


# ------------------------------------------------------------------ G12 calendar, manual dispatch date
def test_calendar_fails_loudly_after_2030():
    from ceap.data.calendar import is_trading_day

    assert is_trading_day(date(2030, 12, 31))
    with pytest.raises(ValueError, match="outside the committed NYSE calendar"):
        is_trading_day(date(2031, 1, 2))


def test_paper_daily_manual_dispatch_uses_new_york_date():
    text = (ROOT / ".github" / "workflows" / "paper-daily.yml").read_text(encoding="utf-8")
    assert "TZ=America/New_York date +%F" in text
    assert 'D="${D:-$(date -u +%F)}"' not in text


# ------------------------------------------------------------------ G13 Docker
def test_dockerfile_is_multi_stage_without_pip_in_runtime():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    stages = text.split("\nFROM ")
    assert len(stages) == 3 and " AS build" in stages[1] and " AS runtime" in stages[2]
    runtime = stages[2]
    assert "pip install" not in runtime  # nothing is installed in the runtime stage
    assert "pip uninstall" in runtime and "COPY --from=build /opt/ceap" in runtime
    assert (ROOT / "deploy" / "compose" / "docker-compose.yml").is_file()
    assert "reverse_proxy ceap:8000" in (ROOT / "deploy" / "compose" / "Caddyfile").read_text(
        encoding="utf-8"
    )


def test_alert_rules_name_runbook_sections():
    import re

    rules = (ROOT / "deploy" / "prometheus" / "ceap-alerts.yml").read_text(encoding="utf-8")
    anchors = re.findall(r'runbook: "docs/RUNBOOK.md#([a-z-]+)"', rules)
    assert len(anchors) == rules.count("- alert:") == 7
    runbook = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    headings = {
        re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
        for h in re.findall(r"^## (.+)$", runbook, re.M)
    }
    assert set(anchors) <= headings, set(anchors) - headings


# ------------------------------------------------------------------ H12 print
#: console entry points may print; library code logs. ``evaluation.py`` prints only under ``verbose=True``,
#: its documented console progress table for ``ceap eval`` (kept on purpose, 0.9.0 audit).
PRINT_ALLOWED_FILES = {"src/ceap/evaluation.py"}


def _entry_point_lines(tree: ast.AST) -> set[int]:
    """Line numbers inside ``if __name__ == "__main__":`` blocks and ``main()`` functions."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        is_main_guard = isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
        is_main_func = isinstance(node, ast.FunctionDef) and node.name == "main"
        if is_main_guard or is_main_func:
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def test_no_print_in_library_code():
    offenders = []
    for path in (ROOT / "src" / "ceap").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "cli" in path.stem or path.name == "__main__.py" or rel in PRINT_ALLOWED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed = _entry_point_lines(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "print"
                and node.lineno not in allowed
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


# ------------------------------------------------------------------ H12 broad excepts
def test_every_broad_except_states_its_reason():
    """H12 audit: each ``except Exception`` in library code carries ``# noqa: BLE001`` and a reason."""
    offenders = []
    for path in (ROOT / "src" / "ceap").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code, _, comment = line.partition("#")
            if "except Exception" in code and not ("BLE001" in comment and " - " in comment):
                offenders.append(f"{rel}:{i}")
    assert offenders == []
