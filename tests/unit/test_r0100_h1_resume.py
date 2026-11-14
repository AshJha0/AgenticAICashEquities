"""0.10.0 H1 (R-11, AI-2-05): a durable run resumes through the product, not by editing ids.

Acceptance: kill -9 mid-run, resume through the API with no id edited: zero repeated tool or model
calls, no second approval request, one case written.

The child process runs a signed-off investigation with ``CEAP_DURABLE_RUNS=1`` and dies like ``kill -9``
(``os._exit``: no ``finally``, no atexit) straight after the human sign-off decision is journalled. Every
model call, tool call and approval request, in the child and in the survivor, is appended to a log file,
so the survivor can check the sum against an uninterrupted run.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest

from ceap.api.app import create_app
from ceap.config import Settings
from ceap.platform import Platform, default_request

SRC = str(Path(__file__).resolve().parents[2] / "src")
ADMIN = {"X-API-Key": "dev-admin-key"}

HELPERS = """
from pathlib import Path

from ceap.llm.client import MockLLMClient
from ceap.policy.approvals import ApprovalDecision, ApprovalGateway


def _log(path, line):
    with open(path, "a", encoding="utf-8") as fh:  # closed at once: a hard kill loses nothing
        fh.write(line + "\\n")


class CountingLLM(MockLLMClient):
    def __init__(self, log_dir):
        super().__init__()
        self.log = Path(log_dir) / "model.log"

    async def complete(self, request):
        _log(self.log, request.purpose)
        return await super().complete(request)


class HumanApprover(ApprovalGateway):
    def __init__(self, log_dir):
        self.log = Path(log_dir) / "approval.log"

    async def request(self, approval):
        _log(self.log, approval.step_id)
        return ApprovalDecision(approval.id, True, "approver.b", "reviewed")


def count_tools(platform, log_dir):
    inner = platform.mcp_client.invoke
    path = Path(log_dir) / "tool.log"

    async def invoke(server, tool, arguments):
        _log(path, tool)
        return await inner(server, tool, arguments)

    platform.mcp_client.invoke = invoke
"""


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _helpers(tmp_path: Path):
    (tmp_path / "h1_helpers.py").write_text(HELPERS, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("h1_helpers", tmp_path / "h1_helpers.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signed_off_request():
    req = default_request("AAPL", "T01")
    req.attributes = {"signoff": True}
    return req


@pytest.mark.slow
async def test_kill_9_then_resume_through_the_api_repeats_nothing(tmp_path, store, history, monkeypatch):
    helpers = _helpers(tmp_path)
    db_path, logs, clean_logs = tmp_path / "run.db", tmp_path / "logs", tmp_path / "clean"
    logs.mkdir()
    clean_logs.mkdir()
    id_file = tmp_path / "task_id.txt"
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import asyncio, os
            from ceap.config import Settings
            from ceap.harness.durable import APPROVAL_KIND, SqlRunJournal
            from ceap.platform import Platform, default_request
            from h1_helpers import CountingLLM, HumanApprover, count_tools

            inner_put = SqlRunJournal.put

            def put(self, task_id, key, kind, step_id, payload):
                ok = inner_put(self, task_id, key, kind, step_id, payload)
                if kind == APPROVAL_KIND:
                    os._exit(137)  # like kill -9, right after the sign-off decision is journalled
                return ok

            SqlRunJournal.put = put

            async def main():
                p = Platform(settings=Settings(llm_provider="mock", db_path=r"{db_path}"),
                             llm=CountingLLM(r"{logs}"), approvals=HumanApprover(r"{logs}"))
                count_tools(p, r"{logs}")
                req = default_request("AAPL", "T01")
                req.attributes = {{"signoff": True}}
                task = p.build_task(req)
                open(r"{id_file}", "w").write(task.id)
                await p.investigate(req, task)

            asyncio.run(main())
            """
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([SRC, str(tmp_path), os.environ.get("PYTHONPATH", "")]),
        "CEAP_ENV": "dev",
        "CEAP_DURABLE_RUNS": "1",
    }
    proc = subprocess.Popen([sys.executable, str(script)], env=env, cwd=str(tmp_path))
    assert proc.wait(300) == 137, "the child was not killed at the sign-off boundary"
    assert _lines(logs / "approval.log") == 1
    child_models, child_tools = _lines(logs / "model.log"), _lines(logs / "tool.log")
    assert child_models > 0 and child_tools > 0  # the child did real work before it died
    task_id = id_file.read_text()

    # the uninterrupted run, for the expected number of model and tool calls
    monkeypatch.setenv("CEAP_DURABLE_RUNS", "1")
    clean = Platform(
        settings=Settings(llm_provider="mock", db_path=str(tmp_path / "clean.db")),
        llm=helpers.CountingLLM(clean_logs),
        approvals=helpers.HumanApprover(clean_logs),
        store=store,
        history=history,
    )
    helpers.count_tools(clean, clean_logs)
    try:
        assert (await clean.investigate(_signed_off_request())).success
    finally:
        clean.close()
    monkeypatch.delenv("CEAP_DURABLE_RUNS")  # the resume must not depend on the environment

    # the survivor: a new process on the same database, resumed through the API by task id alone
    p = Platform(
        settings=Settings(llm_provider="mock", db_path=str(db_path)),
        llm=helpers.CountingLLM(logs),
        approvals=helpers.HumanApprover(logs),
        store=store,
        history=history,
    )
    helpers.count_tools(p, logs)
    assert task_id in p.recovered["interrupted"]
    app = create_app(p)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            accepted = await c.post(f"/tasks/{task_id}/resume", headers=ADMIN)
            assert accepted.status_code == 202, accepted.text
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                state = (await c.get(f"/investigations/{task_id}", headers=ADMIN)).json()
                if state["status"] not in ("RUNNING", "PENDING"):
                    break
                await _sleep()
            assert state["status"] in ("COMPLETED", "DEGRADED"), state
            again = await c.post(f"/tasks/{task_id}/resume", headers=ADMIN)
            assert again.status_code == 409  # a finished task is never re-run
        result = p.results[task_id]
        cases = len(p.case_memory)
    finally:
        await lifespan.__aexit__(None, None, None)
        p.close()

    assert result.task_id == task_id and result.report is not None
    assert _lines(logs / "approval.log") == 1, "the approver was asked a second time"
    assert _lines(logs / "model.log") > child_models  # the survivor finished the run (the report)
    assert _lines(logs / "model.log") == _lines(clean_logs / "model.log"), "a model call was repeated"
    assert _lines(logs / "tool.log") == _lines(clean_logs / "tool.log"), "a tool call was repeated"
    assert cases == 1


async def _sleep() -> None:
    import asyncio

    await asyncio.sleep(0.1)
