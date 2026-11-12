"""``ceap.cli`` subcommands: argument handling, exit codes and output, without running the agents.

The platform, evaluation suites, null sweep, evaluation report and uvicorn are replaced with small
fakes so each test pins the CLI layer only (parsing, defaults, error mapping, --json writing). The
data commands that are cheap (tca-report, generate-data) run for real on small inputs.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ceap.cli as cli
from ceap.cli import main
from ceap.config import SettingsError
from ceap.platform import RequestNotPermitted


class _Result:
    def __init__(self, narrative: str | None, error: str | None = None) -> None:
        self.report = SimpleNamespace(narrative=narrative) if narrative is not None else None
        self.state = SimpleNamespace(value="completed" if narrative is not None else "failed")
        self.error = error
        self.duration_ms = 12.0
        self.findings = [1, 2]
        self.evidence = [1]
        self.warnings: list[str] = []

    def to_dict(self, include_trace: bool = False) -> dict[str, Any]:
        return {"state": self.state.value, "trace": include_trace, "nan_free": 1.5}


class _FakePlatform:
    """Records the request it was given and returns (or raises) the configured outcome."""

    outcome: Any = None
    seen: list[Any] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def _go(self, req: Any) -> Any:
        _FakePlatform.seen.append(req)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    investigate = _go
    research = _go


@pytest.fixture
def fake_platform(monkeypatch: pytest.MonkeyPatch) -> type[_FakePlatform]:
    _FakePlatform.seen = []
    _FakePlatform.outcome = _Result("all good")
    monkeypatch.setattr(cli, "Platform", _FakePlatform)
    return _FakePlatform


# ------------------------------------------------------------------ investigate
def test_investigate_success_writes_json_and_summary(
    fake_platform: type[_FakePlatform], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "r.json"
    code = main(
        ["investigate", "Analyse aapl execution between 14:00 and 15:00", "--json", str(out), "--trace",
         "--session-date", "2026-03-02"]
    )
    assert code == 0
    req = fake_platform.seen[0]
    assert req.symbol == "AAPL" and req.principal == "cli" and req.roles == frozenset({"trader"})
    assert req.window_start.date() == date(2026, 3, 2)
    assert json.loads(out.read_text(encoding="utf-8")) == {"state": "completed", "trace": True, "nan_free": 1.5}
    text = capsys.readouterr().out
    assert "all good" in text and "2 findings; 1 evidence records" in text and f"wrote {out}" in text


def test_investigate_symbol_flag_overrides_question(fake_platform: type[_FakePlatform]) -> None:
    assert main(["investigate", "Analyse execution between 14:00 and 15:00", "--symbol", "msft"]) == 0
    assert fake_platform.seen[0].symbol == "MSFT"


def test_investigate_without_symbol_or_window_is_a_usage_error(
    fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["investigate", "how did we do?"]) == 2
    assert "Could not determine symbol and window" in capsys.readouterr().err
    assert fake_platform.seen == []


def test_investigate_bad_session_date(fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["investigate", "Analyse AAPL between 14:00 and 15:00", "--session-date", "2026-13-40"]) == 2
    assert "Invalid --session-date" in capsys.readouterr().err


def test_investigate_platform_value_error_exits_2(
    fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_platform.outcome = ValueError("no such dataset")
    assert main(["investigate", "Analyse AAPL between 14:00 and 15:00"]) == 2
    assert "Cannot run investigation: no such dataset" in capsys.readouterr().err


def test_investigate_without_report_exits_1(fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]) -> None:
    fake_platform.outcome = _Result(None, error="timed out")
    assert main(["investigate", "Analyse AAPL between 14:00 and 15:00"]) == 1
    assert "Investigation failed: timed out" in capsys.readouterr().err


# --------------------------------------------------------------------- research
def test_research_defaults_and_json(
    fake_platform: type[_FakePlatform], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "res.json"
    code = main(["research", "is it any good?", "--dataset", "r02", "--long-only", "--json", str(out),
                 "--start", "2020-01-02", "--end", "2021-01-04", "--in-sample-end", "2020-06-30"])
    assert code == 0
    req = fake_platform.seen[0]
    assert req.signal == "momentum_12_1" and req.dataset == "R02"
    assert req.long_short is False and req.roles == frozenset({"quant"})
    assert (req.start, req.end, req.in_sample_end) == (date(2020, 1, 2), date(2021, 1, 4), date(2020, 6, 30))
    assert req.rebalance_days == cli.SIGNALS["momentum_12_1"].default_rebalance_days
    assert out.exists()
    assert "all good" in capsys.readouterr().out


def test_research_explicit_rebalance_is_kept(fake_platform: type[_FakePlatform]) -> None:
    assert main(["research", "q", "--rebalance-days", "7"]) == 0
    assert fake_platform.seen[0].rebalance_days == 7


def test_research_bad_date_exits_2(fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["research", "q", "--start", "yesterday"]) == 2
    assert "Invalid date" in capsys.readouterr().err
    assert fake_platform.seen == []


def test_research_not_permitted_exits_3(fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]) -> None:
    fake_platform.outcome = RequestNotPermitted("viewer may not stage orders")
    assert main(["research", "q", "--role", "viewer", "--stage-orders"]) == 3
    assert "Not permitted: viewer may not stage orders" in capsys.readouterr().err


def test_research_value_error_exits_2(fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]) -> None:
    fake_platform.outcome = ValueError("window too short")
    assert main(["research", "q"]) == 2
    assert "Cannot run research: window too short" in capsys.readouterr().err


def test_research_failure_without_report_exits_1(
    fake_platform: type[_FakePlatform], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_platform.outcome = _Result(None, error="boom")
    assert main(["research", "q"]) == 1
    assert "failed: boom" in capsys.readouterr().err


# --------------------------------------------------------------------- evaluate
def _patch_suites(monkeypatch: pytest.MonkeyPatch, inv_acc: float, res_acc: float) -> list[str]:
    import ceap.evaluation as ev

    calls: list[str] = []

    async def inv(limit: Any = None, verbose: bool = False) -> dict[str, Any]:
        calls.append(f"inv:{limit}")
        return {"primary_accuracy": inv_acc, "rows": ["dropped"]}

    async def res(limit: Any = None, verbose: bool = False) -> dict[str, Any]:
        calls.append(f"res:{limit}")
        return {"clear_verdict_accuracy": res_acc, "rows": ["dropped"]}

    monkeypatch.setattr(ev, "run_evaluation", inv)
    monkeypatch.setattr(ev, "run_research_evaluation", res)
    return calls


def test_evaluate_all_passes_and_drops_rows(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _patch_suites(monkeypatch, 0.95, 0.85)
    assert main(["evaluate", "--suite", "all", "--limit", "2"]) == 0
    assert calls == ["inv:2", "res:2"]
    assert "dropped" not in capsys.readouterr().out


@pytest.mark.parametrize(("suite", "inv_acc", "res_acc"), [("investigation", 0.89, 1.0), ("research", 1.0, 0.79)])
def test_evaluate_below_bar_exits_1(monkeypatch: pytest.MonkeyPatch, suite: str, inv_acc: float, res_acc: float) -> None:
    _patch_suites(monkeypatch, inv_acc, res_acc)
    assert main(["evaluate", "--suite", suite]) == 1


def test_evaluate_null_suite_reports_rate_at_committed_hurdle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import ceap.analytics.null_sweep as ns

    written: list[Any] = []

    def sweep(seeds: int, workers: int = 1) -> dict[str, Any]:
        assert (seeds, workers) == (4, 2)
        return {
            "chosen": 2.5, "n_seeds": seeds, "search_size": 7, "sweep_hash": "abc",
            "per_seed": [{"stat": 0.1}, {"stat": None}, {"stat": 9.0}, {"stat": -1.0}],
            "abs_t_gt_2_rate": 0.25,
        }

    monkeypatch.setattr(ns, "run_null_sweep", sweep)
    monkeypatch.setattr(ns, "write_thresholds", lambda s: written.append(s) or Path("thr.json"))
    assert main(["evaluate", "--suite", "null", "--seeds", "4", "--workers", "2", "--write-thresholds"]) == 0
    out = capsys.readouterr().out
    doc = json.loads(out[: out.rindex("}") + 1])
    assert doc["seeds"] == 4 and doc["sweep_hash"] == "abc"
    assert doc["false_promote_rate_at_committed_hurdle"] == pytest.approx(0.25)  # only 9.0 clears the hurdle
    assert "wrote thr.json" in out and len(written) == 1


# ------------------------------------------------------------ evaluation-report
def _patch_report(monkeypatch: pytest.MonkeyPatch, complete: bool) -> list[Any]:
    import ceap.evaluation_report as er

    appended: list[Any] = []

    async def build(limit: Any = None) -> dict[str, Any]:
        return {"complete": complete, "limit": limit}

    monkeypatch.setattr(er, "build_report", build)
    monkeypatch.setattr(er, "render_markdown", lambda r: f"# report limit={r['limit']}")
    monkeypatch.setattr(er, "history_row", lambda r, v, d: {"version": v})
    monkeypatch.setattr(er, "append_history", appended.append)
    return appended


def test_evaluation_report_quick_refuses_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    appended = _patch_report(monkeypatch, complete=False)
    out = tmp_path / "er.json"
    assert main(["evaluation-report", "--quick", "--json", str(out), "--append-history", "0.9.0"]) == 2
    cap = capsys.readouterr()
    assert "limit=3" in cap.out and "refusing to append" in cap.err
    assert json.loads(out.read_text(encoding="utf-8"))["complete"] is False
    assert appended == []


def test_evaluation_report_full_appends_history(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    appended = _patch_report(monkeypatch, complete=True)
    assert main(["evaluation-report", "--append-history", "0.9.0"]) == 0
    assert appended == [{"version": "0.9.0"}]
    assert "limit=None" in capsys.readouterr().out


# ------------------------------------------------------------ other subcommands
def test_profitability_uses_dataset_defaults(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import ceap.analytics.profitability as prof
    import ceap.data.historical as hist

    ds = SimpleNamespace(scenario=SimpleNamespace(signal="sig_x", rebalance_days=5))
    monkeypatch.setattr(hist, "HistoricalStore", lambda: SimpleNamespace(get=lambda dsid: ds))
    seen: dict[str, Any] = {}

    def table(d: Any, signal: str, **kw: Any) -> dict[str, Any]:
        seen.update(signal=signal, **kw)
        return {"rows": [1.0]}

    monkeypatch.setattr(prof, "profitability_table", table)
    assert main(["profitability", "--long-only"]) == 0
    assert seen == {"signal": "sig_x", "rebalance_days": 5, "long_short": False, "gross_notional": 50_000_000.0}
    assert json.loads(capsys.readouterr().out) == {"rows": [1.0]}


def test_tca_report_markdown_to_file_and_json_to_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import ceap.analytics.tca_aggregate as agg
    import ceap.data.tca_universe as uni

    seen: list[Any] = []
    monkeypatch.setattr(uni, "generate_tca_universe", lambda seed, cfg: seen.append((seed, cfg)) or "U")
    monkeypatch.setattr(agg, "best_execution_report", lambda u: {"universe": u, "month": "2026-01"})
    monkeypatch.setattr(agg, "render_report_markdown", lambda r: f"# Best execution {r['month']}")
    out = tmp_path / "tca.md"
    assert main(["tca-report", "--sessions", "3", "--seed", "7", "--broker-gap-bps", "1.5", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "# Best execution 2026-01"
    assert f"wrote {out}" in capsys.readouterr().out
    seed, cfg = seen[0]
    assert (seed, cfg.n_sessions, cfg.broker_gap_bps, cfg.family) == (7, 3, 1.5, "T01")
    assert main(["tca-report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"universe": "U", "month": "2026-01"}


def test_generate_data_prints_written_files_and_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import ceap.data.export as export

    ds = SimpleNamespace(summary=lambda: {"rows": 3})
    monkeypatch.setattr(cli, "DatasetStore", lambda: SimpleNamespace(get=lambda spec: ds))
    seen: dict[str, Any] = {}

    def fake_export(d: Any, out: Path, fmt: str) -> list[Path]:
        seen.update(d=d, out=out, fmt=fmt)
        return [out / "orders.parquet"]

    monkeypatch.setattr(export, "export_dataset", fake_export)
    assert main(["generate-data", "--scenario", "T02", "--out", str(tmp_path), "--format", "parquet"]) == 0
    assert seen == {"d": ds, "out": tmp_path, "fmt": "parquet"}
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [str(tmp_path / "orders.parquet"), '{"rows": 3}']

def test_serve_passes_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    assert main(["serve", "--port", "9001"]) == 0
    assert seen == {"app": "ceap.api.app:app", "host": "127.0.0.1", "port": 9001, "reload": False}


def test_settings_error_maps_to_exit_2(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def broken(args: Any) -> int:
        raise SettingsError("CEAP_LLM_PROVIDER must be one of ...")

    monkeypatch.setattr(cli, "_cmd_scenarios", broken)
    assert main(["scenarios"]) == 2
    assert "Configuration error: CEAP_LLM_PROVIDER" in capsys.readouterr().err


def test_research_scenarios_templates(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["research-scenarios"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert lines and all("verdict=" in ln and "E[t]=" in ln for ln in lines)
