"""``ceap db ...`` and ``ceap replay ...`` driven through ``ceap.cli.main`` in-process.

Covers the persistence CLI (status, migrate, backup, verify-chain with and without an anchor, anchor,
kill-switch, prune), the refusal paths (no file, ``:memory:``) and a record-then-replay round trip,
including a tampered bundle that must exit 1 and name the first differing path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ceap.cli import main


def _run(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict | None, str]:
    code = main(argv)
    cap = capsys.readouterr()
    try:
        doc = json.loads(cap.out)
    except json.JSONDecodeError:
        doc = None
    return code, doc, cap.err


@pytest.fixture
def db_file(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv("CEAP_DB_PATH", raising=False)
    path = str(tmp_path / "ceap.db")
    code, doc, _ = _run(capsys, ["db", "--path", path, "migrate"])
    assert code == 0
    assert doc["to"] >= 1 and doc["from"] == 0
    return path


def test_status_on_missing_file_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "absent.db"
    code, _, err = _run(capsys, ["db", "--path", str(path), "status"])
    assert code == 1
    assert "no database at" in err
    assert not path.exists()  # status must not create the file


def test_memory_path_from_environment_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CEAP_DB_PATH", ":memory:")
    with pytest.raises(SystemExit, match="needs a database file"):
        main(["db", "status"])


def test_path_falls_back_to_environment(
    db_file: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CEAP_DB_PATH", db_file)
    code, doc, _ = _run(capsys, ["db", "status"])
    assert code == 0
    assert doc["path"] == db_file
    assert doc["schema_version"] == doc["latest"]
    assert doc["chain_ok"] is True and doc["audit_rows"] == 0


def test_migrate_again_applies_nothing(db_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "migrate"])
    assert code == 0
    assert doc["applied"] == [] and doc["from"] == doc["to"]


def test_kill_switch_set_status_clear_are_audited(db_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "kill-switch", "set", "--reason", "drill", "--actor", "ops"])
    assert code == 0
    assert doc["active"] is True
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "kill-switch", "status"])
    assert code == 0 and doc["active"] is True and doc["history"]
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "kill-switch", "clear", "--actor", "ops"])
    assert code == 0 and doc["active"] is False
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "status"])
    assert doc["audit_rows"] >= 2 and doc["chain_ok"] is True


def test_backup_copy_verifies(db_file: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _run(capsys, ["db", "--path", db_file, "kill-switch", "set", "--reason", "x"])
    dest = tmp_path / "copy.db"
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "backup", str(dest)])
    assert code == 0
    assert doc["chain_ok"] is True
    assert Path(doc["backup"]).exists()


def test_prune_reports_counts(db_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "prune"])
    assert code == 0
    assert "pruned" in doc and doc["size_bytes"] > 0


def test_anchor_then_verify_then_tamper(db_file: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for reason in ("one", "two"):
        _run(capsys, ["db", "--path", db_file, "kill-switch", "set", "--reason", reason])
    anchor = tmp_path / "anchors" / "head.json"
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "anchor", "--out", str(anchor)])
    assert code == 0
    assert doc["chains"]["audit"]["rows"] >= 2
    sidecar = anchor.with_name("head.json.sha256").read_text(encoding="utf-8")
    assert sidecar.startswith(doc["sha256"]) and sidecar.rstrip().endswith("head.json")

    code, doc, _ = _run(capsys, ["db", "--path", db_file, "verify-chain", "--anchor", str(anchor)])
    assert code == 0 and doc["ok"] is True and doc["chains"]["audit"]["ok"] is True

    code, doc, _ = _run(capsys, ["db", "--path", db_file, "verify-chain"])
    assert code == 0 and doc["ok"] is True and doc["first_bad_seq"] is None

    # rewrite one audit row behind the chain's back
    con = sqlite3.connect(db_file)
    (seq,) = con.execute("SELECT seq FROM audit_log ORDER BY seq LIMIT 1").fetchone()
    con.execute("UPDATE audit_log SET actor=? WHERE seq=?", ("mallory", seq))
    con.commit()
    con.close()

    code, doc, _ = _run(capsys, ["db", "--path", db_file, "verify-chain"])
    assert code == 1 and doc["ok"] is False and doc["first_bad_seq"] == seq
    code, doc, _ = _run(capsys, ["db", "--path", db_file, "verify-chain", "--anchor", str(anchor)])
    assert code == 1 and doc["ok"] is False


@pytest.mark.slow  # records a live T01 run then replays it twice (~25 s)
def test_record_then_replay_round_trip_and_tamper(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = tmp_path / "b" / "t01.json"
    code, doc, _ = _run(capsys, ["replay", str(bundle), "--record", "--dataset", "t01"])
    assert code == 0
    assert doc["tool_calls"] > 0 and doc["model_calls"] > 0
    assert doc["live"]["state"]

    code, doc, _ = _run(capsys, ["replay", str(bundle)])
    assert code == 0
    assert doc["equal"] is True and doc["stored_hash"] == doc["replayed_hash"]
    assert doc["first_difference"] is None

    stored = json.loads(bundle.read_text(encoding="utf-8"))
    stored["result"]["state"] = "tampered"
    bundle.write_text(json.dumps(stored), encoding="utf-8")
    code, doc, _ = _run(capsys, ["replay", str(bundle)])
    assert code == 1
    assert doc["equal"] is False and doc["first_difference"] == "$.state"


@pytest.mark.parametrize(("dataset", "kind"), [("r01", "ResearchRequest"), ("T02", "InvestigationRequest")])
def test_replay_record_picks_request_kind_and_closes_platform(
    dataset: str, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fast twin of the round trip: the recorder is faked, the CLI wiring (request kind, save, exit) is real."""
    import ceap.harness.replay as rp
    import ceap.platform as plat

    seen: dict[str, object] = {}
    real_close = plat.Platform.close

    def close(self: plat.Platform) -> None:
        seen["closed"] = True
        real_close(self)

    async def fake_record(platform: plat.Platform, req: object) -> dict:
        seen["kind"] = type(req).__name__
        seen["dataset"] = req.dataset
        seen["mock"] = platform.settings.llm_provider
        return {"result_hash": "h", "live": {"state": "completed"}, "tool_calls": [1, 2], "llm": [1]}

    monkeypatch.setattr(plat.Platform, "close", close)
    monkeypatch.setattr(rp, "record_bundle", fake_record)
    bundle = tmp_path / "b.json"
    code, doc, _ = _run(capsys, ["replay", str(bundle), "--record", "--dataset", dataset])
    assert code == 0
    assert seen == {"kind": kind, "dataset": dataset.upper(), "mock": "mock", "closed": True}
    assert doc == {"bundle": str(bundle), "result_hash": "h", "live": {"state": "completed"},
                   "tool_calls": 2, "model_calls": 1}
    assert json.loads(bundle.read_text(encoding="utf-8"))["result_hash"] == "h"


@pytest.mark.parametrize("equal", [True, False])
def test_replay_exit_code_follows_equality(
    equal: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import ceap.harness.replay as rp

    async def fake_replay(bundle: dict) -> rp.ReplayReport:
        assert bundle == {"bundle_version": 1}
        return rp.ReplayReport(equal, "s", "s" if equal else "r", None if equal else "$.x")

    monkeypatch.setattr(rp, "replay_bundle", fake_replay)
    path = tmp_path / "b.json"
    path.write_text('{"bundle_version": 1}', encoding="utf-8")
    code, doc, _ = _run(capsys, ["replay", str(path)])
    assert code == (0 if equal else 1)
    assert doc["equal"] is equal and doc["first_difference"] == (None if equal else "$.x")
