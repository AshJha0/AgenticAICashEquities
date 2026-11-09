"""Task replay bundles (R4 item 4.7).

A bundle is one JSON file holding everything a task consumed: the request, every MCP tool output, every
model exchange (redacted), every approval decision, the prompt hashes and the clock seed. ``ceap replay
<bundle>`` re-runs the harness against those recorded inputs, with no data store, no model and no human,
under an injected clock and id source, and checks that the result is byte-for-byte the stored one.

How the stored result is produced. A live run uses the real clock, so its timestamps, ids and durations
can never be reproduced. Recording therefore takes two passes: pass 1 runs the task live and captures its
inputs; pass 2 replays those captured inputs under the injected clock and stores *that* result, together
with the live run's status for comparison. Any later replay must equal pass 2 exactly; a mismatch means
the code or the inputs changed (the report names the first differing path).

What is injected: ``ceap.domain.common.utc_now`` and ``new_id`` (through context variables, so they reach
dataclass default factories and child asyncio tasks) and ``time.perf_counter`` as seen by ``ceap`` modules
(durations). Model I/O is stored redacted (``ceap.util.redact``); the stored result is computed from the
redacted copy, so redaction never breaks the replay.
"""

from __future__ import annotations

import contextlib
import json
import time as _time
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ceap.domain.common import CLOCK, ID_SOURCE, to_jsonable
from ceap.llm.client import LLMClient
from ceap.llm.models import Degradation, LLMRequest, LLMResponse
from ceap.mcp.client import MCPClient, MCPError
from ceap.policy.approvals import ApprovalDecision, ApprovalGateway, ApprovalRequest
from ceap.util.canonical import canonical_json, content_hash, sha256_hex
from ceap.util.redact import redact

BUNDLE_VERSION = 1
DEFAULT_EPOCH = "2026-01-01T00:00:00+00:00"


class ReplayDivergedError(RuntimeError):
    """The replayed run asked for an input the bundle does not hold (code or inputs changed)."""


# ------------------------------------------------------------------ clock
class DeterministicClock:
    """Wall clock, monotonic counter and ids that advance by fixed steps on every read."""

    def __init__(self, epoch: str = DEFAULT_EPOCH, step_ms: int = 1) -> None:
        self.epoch = datetime.fromisoformat(epoch).astimezone(UTC)
        self.step = timedelta(milliseconds=step_ms)
        self._ticks = 0
        self._perf = 0
        self._ids = 0

    def now(self) -> datetime:
        self._ticks += 1
        return self.epoch + self._ticks * self.step

    def perf_counter(self) -> float:
        self._perf += 1
        return self._perf * self.step.total_seconds()

    def next_id(self) -> str:
        self._ids += 1
        return f"{self._ids:08x}"


@contextlib.contextmanager
def injected_clock(clock: DeterministicClock) -> Iterator[DeterministicClock]:
    """Inject ``clock`` for the duration of the block.

    ``time.perf_counter`` is replaced process-wide (not per module: a module imported lazily during the
    run must see the same clock), so run replays from the CLI or a test, never inside a serving process.
    asyncio's own scheduling uses ``time.monotonic`` and is unaffected.
    """
    real_perf = _time.perf_counter
    clock_token = CLOCK.set(clock.now)
    id_token = ID_SOURCE.set(clock.next_id)
    _time.perf_counter = clock.perf_counter
    try:
        yield clock
    finally:
        _time.perf_counter = real_perf
        CLOCK.reset(clock_token)
        ID_SOURCE.reset(id_token)


# -------------------------------------------------------------- recording
def _key(server: str, tool: str, arguments: dict[str, Any]) -> str:
    return content_hash(
        {"server": server, "tool": tool, "arguments": json.loads(json.dumps(arguments, default=str))}
    )


def _redact_doc(doc: Any) -> Any:
    if isinstance(doc, str):
        return redact(doc)
    if isinstance(doc, list):
        return [_redact_doc(v) for v in doc]
    if isinstance(doc, dict):
        return {k: _redact_doc(v) for k, v in doc.items()}
    return doc


class RecordingMCPClient(MCPClient):
    def __init__(self, inner: MCPClient) -> None:
        self.inner = inner
        self.discovery: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[dict[str, Any]] = []

    def servers(self) -> list[str]:
        return self.inner.servers()

    async def discover_tools(self, server: str) -> list[dict[str, Any]]:
        tools = await self.inner.discover_tools(server)
        self.discovery[server] = json.loads(json.dumps(tools, default=str))
        return tools

    async def invoke(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        try:
            out = await self.inner.invoke(server, tool, arguments)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
            self.calls.append(
                {
                    "key": _key(server, tool, arguments),
                    "tool": f"{server}.{tool}",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        self.calls.append(
            {
                "key": _key(server, tool, arguments),
                "tool": f"{server}.{tool}",
                "output": json.loads(json.dumps(to_jsonable(out), default=str)),
            }
        )
        return out

    async def close(self) -> None:
        await self.inner.close()


class ReplayMCPClient(MCPClient):
    def __init__(self, discovery: dict[str, list[dict[str, Any]]], calls: list[dict[str, Any]]) -> None:
        self.discovery = discovery
        self._calls: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for call in calls:
            self._calls[call["key"]].append(call)

    def servers(self) -> list[str]:
        return list(self.discovery)

    async def discover_tools(self, server: str) -> list[dict[str, Any]]:
        return json.loads(json.dumps(self.discovery[server]))

    async def invoke(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        queue = self._calls.get(_key(server, tool, arguments))
        if not queue:
            raise ReplayDivergedError(
                f"bundle has no recorded output for {server}.{tool} with these arguments"
            )
        call = queue[0] if len(queue) == 1 else queue.popleft()  # a repeated call reuses the last output
        if "error" in call:
            raise MCPError(call["error"])
        return json.loads(json.dumps(call["output"]))


def _request_doc(request: LLMRequest) -> dict[str, Any]:
    return _redact_doc(json.loads(json.dumps(to_jsonable(request), default=str)))


def _response_from(doc: dict[str, Any]) -> LLMResponse:
    names = {f.name for f in fields(LLMResponse)}
    kwargs = {k: v for k, v in doc.items() if k in names}
    kwargs["degradations"] = tuple(Degradation(**d) for d in kwargs.get("degradations") or ())
    return LLMResponse(**kwargs)


class RecordingLLM(LLMClient):
    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.exchanges: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self.inner.name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self.inner.complete(request)
        req = _request_doc(request)
        self.exchanges.append(
            {
                "purpose": request.purpose,
                "prompt_hash": sha256_hex(canonical_json(req)),
                "request": req,
                "response": _redact_doc(json.loads(json.dumps(to_jsonable(response), default=str))),
            }
        )
        return response

    def __getattr__(self, item: str) -> Any:  # usage counters and the like
        return getattr(self.inner, item)


class ReplayLLM(LLMClient):
    """Answers each purpose's calls in recorded order (prompts embed run-specific ids, so order, not hash)."""

    def __init__(self, exchanges: list[dict[str, Any]], name: str = "replay") -> None:
        self._by_purpose: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for ex in exchanges:
            self._by_purpose[ex["purpose"]].append(ex)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        queue = self._by_purpose.get(request.purpose)
        if not queue:
            raise ReplayDivergedError(
                f"bundle has no recorded model response for purpose {request.purpose!r}"
            )
        return _response_from(queue.popleft()["response"])


class RecordingApprovals(ApprovalGateway):
    def __init__(self, inner: ApprovalGateway) -> None:
        self.inner = inner
        self.decisions: list[dict[str, Any]] = []

    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        decision = await self.inner.request(approval)
        self.decisions.append(
            {
                "step_id": approval.step_id,
                "approved": decision.approved,
                "decided_by": decision.decided_by,
                "comment": decision.comment,
            }
        )
        return decision


class ReplayApprovals(ApprovalGateway):
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self._by_step: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for d in decisions:
            self._by_step[d["step_id"]].append(d)

    async def request(self, approval: ApprovalRequest) -> ApprovalDecision:
        queue = self._by_step.get(approval.step_id)
        if not queue:
            raise ReplayDivergedError(f"bundle has no recorded approval decision for {approval.step_id}")
        d = queue.popleft()
        return ApprovalDecision(approval.id, d["approved"], d["decided_by"], d["comment"])


# ----------------------------------------------------------------- bundle
@dataclass
class ReplayReport:
    equal: bool
    stored_hash: str
    replayed_hash: str
    first_difference: str | None = None


def _request_spec(req: Any) -> dict[str, Any]:
    from ceap.platform import ResearchRequest

    kind = "research" if isinstance(req, ResearchRequest) else "investigation"
    doc = asdict(req)
    doc["roles"] = sorted(req.roles)
    doc["priority"] = req.priority.value if hasattr(req.priority, "value") else req.priority
    return {"kind": kind, "fields": json.loads(json.dumps(doc, default=lambda v: v.isoformat()))}


def _request_from(spec: dict[str, Any]) -> Any:
    from datetime import date

    from ceap.domain.tasks import TaskPriority
    from ceap.platform import InvestigationRequest, ResearchRequest

    f = dict(spec["fields"])
    f["roles"] = frozenset(f["roles"])
    f["priority"] = TaskPriority(f["priority"])
    if spec["kind"] == "research":
        for k in ("start", "end", "in_sample_end"):
            f[k] = date.fromisoformat(f[k]) if f.get(k) else None
        return ResearchRequest(**f)
    for k in ("window_start", "window_end", "baseline_start", "baseline_end"):
        f[k] = datetime.fromisoformat(f[k]) if f.get(k) else None
    return InvestigationRequest(**f)


def canonical_result(result: Any) -> bytes:
    """The bytes a replay must reproduce: the full result document, trace included, canonical JSON."""
    return canonical_json(json.loads(json.dumps(result.to_dict(include_trace=True), default=str)))


async def _replay_run(bundle: dict[str, Any]) -> Any:
    from ceap.config import Settings
    from ceap.platform import Platform

    settings = Settings(
        llm_provider="mock", auto_approve=False, db_path=":memory:", **bundle.get("settings", {})
    )
    platform = Platform(
        settings=settings,
        llm=ReplayLLM(bundle["llm"]),
        mcp_client=ReplayMCPClient(bundle["discovery"], bundle["tool_calls"]),
        approvals=ReplayApprovals(bundle["approvals"]),
    )
    try:
        with injected_clock(DeterministicClock(bundle["clock"]["epoch"], bundle["clock"]["step_ms"])):
            req = _request_from(bundle["request"])
            if bundle["request"]["kind"] == "research":
                return await platform.research(req)
            return await platform.investigate(req)
    finally:
        platform.close()


async def record_bundle(
    platform: Any, req: Any, epoch: str = DEFAULT_EPOCH, step_ms: int = 1
) -> dict[str, Any]:
    """Run ``req`` live on ``platform`` while capturing its inputs, then build the bundle (see module doc)."""
    from ceap import __version__
    from ceap.platform import ResearchRequest

    mcp = RecordingMCPClient(platform.mcp_client)
    llm = RecordingLLM(platform.llm)
    approvals = RecordingApprovals(platform.approvals)
    saved = (platform.mcp_client, platform.llm, platform.approvals, platform._registry)
    platform.mcp_client, platform.llm, platform.approvals, platform._registry = mcp, llm, approvals, None
    try:
        live = await (
            platform.research(req) if isinstance(req, ResearchRequest) else platform.investigate(req)
        )
    finally:
        platform.mcp_client, platform.llm, platform.approvals, platform._registry = saved
    bundle: dict[str, Any] = {
        "bundle_version": BUNDLE_VERSION,
        "ceap_version": __version__,
        "request": _request_spec(req),
        "settings": {
            "step_timeout_seconds": platform.settings.step_timeout_seconds,
            "task_timeout_seconds": platform.settings.task_timeout_seconds,
            "max_retries": platform.settings.max_retries,
        },
        "clock": {"epoch": epoch, "step_ms": step_ms},
        "seeds": {"dataset": getattr(req, "dataset", None), "generator": "deterministic per dataset id"},
        "discovery": mcp.discovery,
        "tool_calls": mcp.calls,
        "llm": llm.exchanges,
        "prompt_hashes": [ex["prompt_hash"] for ex in llm.exchanges],
        "approvals": approvals.decisions,
        "live": {"status": live.status.value, "state": live.state.value},
    }
    replayed = await _replay_run(bundle)
    data = canonical_result(replayed)
    bundle["result"] = json.loads(data)
    bundle["result_hash"] = sha256_hex(data)
    return bundle


def _first_difference(a: Any, b: Any, path: str = "$") -> str | None:
    if type(a) is not type(b):
        return path
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}"
            d = _first_difference(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}[len]"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            d = _first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else path


async def replay_bundle(bundle: dict[str, Any]) -> ReplayReport:
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError(
            f"unsupported bundle version {bundle.get('bundle_version')}; expected {BUNDLE_VERSION}"
        )
    data = canonical_result(await _replay_run(bundle))
    stored = canonical_json(bundle["result"])
    equal = data == stored
    return ReplayReport(
        equal=equal,
        stored_hash=sha256_hex(stored),
        replayed_hash=sha256_hex(data),
        first_difference=None if equal else _first_difference(bundle["result"], json.loads(data)),
    )


def save_bundle(bundle: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # insertion order is part of the recorded input (tools iterate their arguments): never sort keys
    path.write_text(json.dumps(bundle, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return path


def load_bundle(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
