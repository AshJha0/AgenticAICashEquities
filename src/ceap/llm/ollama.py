"""A local open-weight model behind :class:`LLMClient` (0.9.0 B1), plus its cassettes.

Plain idea: the platform's agents talk to one interface, ``LLMClient``. This adapter answers that
interface with a model running on this machine under Ollama (``http://localhost:11434``), so the
model-behaviour metrics can be measured on a real model at zero spend, and CI can replay what the
model said without Ollama installed.

Formal statement:

* :class:`OllamaLLMClient` sends ``POST /api/chat`` with ``stream=false``, ``options.seed`` and
  ``options.temperature`` (sampling is allowed for a local model; the seed makes a run repeatable on
  the same build), and, when the request carries an ``output_model``, ``format`` = that model's JSON
  schema (Ollama's grammar-constrained structured output). The reply is validated against the same
  Pydantic model; a reply that does not validate gets ``parsed=None`` and ``parse_errors``, exactly
  like the Anthropic adapter, so the agents' repair and fallback rungs behave identically.
* Every answer has ``provenance = "local_model"`` (``eval.clients.EVIDENCE_CLASSES``) and
  ``token_source = "ollama"`` (Ollama's own ``prompt_eval_count`` / ``eval_count``).
* :class:`LocalCassette` is the record of one run: a provenance block (``provenance: local_model``,
  model, digest, seed, temperature) and one interaction per call, keyed by
  :func:`local_request_sha256` of the canonical request. :class:`LocalCassetteClient` replays it;
  a request with no recording raises :class:`ceap.llm.recording.CassetteStaleError`.

Worked example: ``OllamaLLMClient("qwen2.5:7b-instruct", seed=1, recorder=cas)`` answers a planning
request; ``cas.interactions[0].response["parsed"]`` holds the plan and ``write_local_cassette`` saves
it. In CI, ``LocalCassetteClient(load_local_cassette(path))`` returns the same ``LLMResponse``.

Live calls are gated: nothing constructs this client unless ``CEAP_LOCAL_MODEL=1`` is set (tests
use the ``local_model`` marker and skip without it). There is no network call outside localhost.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ceap.llm.client import LLMClient
from ceap.llm.models import LLMRequest, LLMResponse
from ceap.llm.recording import CassetteStaleError, canonical_json, mask_nonces
from ceap.llm.schemas import output_schema

OLLAMA_URL = os.environ.get("CEAP_OLLAMA_URL", "http://localhost:11434")
LOCAL_MODEL_ENV = "CEAP_LOCAL_MODEL"
PRIMARY_LOCAL_MODEL = "qwen2.5:7b-instruct"
CHALLENGER_LOCAL_MODEL = "llama3.1:8b-instruct-q4_K_M"
LOCAL_MODELS = (PRIMARY_LOCAL_MODEL, CHALLENGER_LOCAL_MODEL)
TOKEN_SOURCE = "ollama"
JSON_SUFFIX = (
    "\n\nAnswer with ONE JSON object that conforms to the response schema. No prose outside the JSON."
)


def live_enabled() -> bool:
    return os.environ.get(LOCAL_MODEL_ENV, "").strip().lower() in ("1", "true", "yes")


def _text(content: Any) -> str:
    """Flatten Anthropic-style content blocks (text and document blocks) to plain text."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else [content]:
        if not isinstance(block, dict):
            parts.append(str(block))
        elif block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "document":
            src = block.get("source") or {}
            title = block.get("title") or ""
            body = src.get("data") if isinstance(src.get("data"), str) else json.dumps(src.get("content", ""))
            parts.append(f"<document title={title!r}>\n{body}\n</document>")
        else:
            parts.append(json.dumps(block, default=str))
    return "\n\n".join(parts)


def chat_body(request: LLMRequest, model: str, seed: int, temperature: float) -> dict[str, Any]:
    """The exact ``/api/chat`` body for ``request`` (also the cassette match key)."""
    system = request.system_prompt
    fmt: Any = None
    if request.output_model is not None:
        fmt = output_schema(request.output_model)
        system = system + JSON_SUFFIX
    messages = [{"role": "system", "content": system}]
    messages += [
        {"role": m.get("role", "user"), "content": _text(m.get("content", ""))} for m in request.messages
    ]
    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": {"seed": seed, "temperature": temperature, "num_ctx": 8192},
        "keep_alive": "30m",
    }
    if fmt is not None:
        body["format"] = fmt
    return body


def local_request_sha256(body: dict[str, Any], purpose: str) -> str:
    return hashlib.sha256(canonical_json(mask_nonces({"purpose": purpose, "body": body}))).hexdigest()


def _validate(request: LLMRequest, content: str) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    if request.output_model is None:
        return None, ()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, (f"not JSON: {exc}",)
    try:
        return request.output_model.model_validate(raw).model_dump(mode="json"), ()
    except ValidationError as exc:
        return None, tuple(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:12])


# ------------------------------------------------------------------------------------ cassettes


class LocalProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: Literal["local_model"] = "local_model"
    model: str
    digest: str = ""
    seed: int
    temperature: float
    runtime: str = "ollama"
    note: str = ""


class LocalInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    request_sha256: str
    response: dict[str, Any]
    latency_ms: float = 0.0


class LocalCassette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: LocalProvenance
    interactions: list[LocalInteraction] = []


def write_local_cassette(path: Path, cassette: LocalCassette) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cassette.model_dump(mode="json"), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_local_cassette(path: Path) -> LocalCassette:
    return LocalCassette.model_validate_json(path.read_text(encoding="utf-8"))


def _response_dict(r: LLMResponse) -> dict[str, Any]:
    return {
        "content": r.content,
        "parsed": r.parsed,
        "parse_errors": list(r.parse_errors),
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "model": r.model,
        "stop_reason": r.stop_reason,
    }


def _response_of(d: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content=d["content"],
        tool_calls=[],
        input_tokens=int(d["input_tokens"]),
        output_tokens=int(d["output_tokens"]),
        model=d["model"],
        stop_reason=d.get("stop_reason"),
        parsed=d.get("parsed"),
        parse_errors=tuple(d.get("parse_errors") or ()),
        token_source=TOKEN_SOURCE,
    )


# ------------------------------------------------------------------------------------ clients


class OllamaLLMClient(LLMClient):
    """Live local model. ``recorder`` (a :class:`LocalCassette`) captures every call when given."""

    provenance = "local_model"

    def __init__(
        self,
        model: str = PRIMARY_LOCAL_MODEL,
        *,
        seed: int = 0,
        temperature: float = 0.0,
        url: str = OLLAMA_URL,
        timeout_s: float = 1800.0,
        recorder: LocalCassette | None = None,
        transport: Any = None,
        checkpoint: Path | None = None,
    ) -> None:
        if model not in LOCAL_MODELS:
            raise ValueError(f"local model {model!r} is not pinned (allowed: {LOCAL_MODELS})")
        self.model, self.seed, self.temperature = model, seed, temperature
        self.url, self.timeout_s, self.recorder, self.transport = (
            url.rstrip("/"),
            timeout_s,
            recorder,
            transport,
        )
        self.latencies: list[tuple[str, float]] = []
        # identical body + seed gives the same reply: answer repeats (e.g. an eval wrapper re-asking)
        # from memory instead of spending minutes of CPU on them
        self._memo: dict[str, LLMResponse] = {}
        # checkpoint: the cassette is rewritten after every call, and a restarted run answers the
        # requests already recorded there from the file (a long CPU run can then be resumed)
        self.checkpoint = checkpoint
        if checkpoint is not None and recorder is not None and checkpoint.exists():
            old = load_local_cassette(checkpoint)
            same = (old.provenance.model, old.provenance.seed, old.provenance.temperature)
            if same != (model, seed, temperature):
                # the first call would overwrite another run's recording with this one
                raise ValueError(
                    f"checkpoint {checkpoint} records {same}, not {(model, seed, temperature)}; "
                    "move it aside or pass a different path"
                )
            for i in old.interactions:
                recorder.interactions.append(i)
                self._memo[i.request_sha256] = _response_of(i.response)
                self.latencies.append((i.purpose, i.latency_ms))

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport) as http:
            r = await http.post(f"{self.url}/api/chat", json=body)
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = chat_body(request, self.model, self.seed, self.temperature)
        sha = local_request_sha256(body, request.purpose)
        if sha in self._memo:
            return self._memo[sha]
        started = time.perf_counter()
        data = await self._post(body)
        latency_ms = (time.perf_counter() - started) * 1000
        content = str((data.get("message") or {}).get("content", ""))
        parsed, errors = _validate(request, content)
        done = data.get("done_reason") or "stop"
        response = LLMResponse(
            content=content,
            tool_calls=[],
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            model=self.model,
            stop_reason="max_tokens" if done == "length" else "end_turn",
            parsed=parsed,
            parse_errors=errors,
            token_source=TOKEN_SOURCE,
        )
        self.latencies.append((request.purpose, latency_ms))
        if self.recorder is not None:
            self.recorder.interactions.append(
                LocalInteraction(
                    purpose=request.purpose,
                    request_sha256=sha,
                    response=_response_dict(response),
                    latency_ms=round(latency_ms, 1),
                )
            )
        self._memo[sha] = response
        if self.checkpoint is not None and self.recorder is not None:
            write_local_cassette(self.checkpoint, self.recorder)
        return response


class LocalCassetteClient(LLMClient):
    """Replays a :class:`LocalCassette` by request hash (CI path: no Ollama needed)."""

    provenance = "local_model"

    def __init__(self, cassette: LocalCassette) -> None:
        self.cassette = cassette
        p = cassette.provenance
        self.model, self.seed, self.temperature = p.model, p.seed, p.temperature
        self._by_sha: dict[str, list[LocalInteraction]] = {}
        for i in cassette.interactions:
            self._by_sha.setdefault(i.request_sha256, []).append(i)
        self.latencies: list[tuple[str, float]] = []

    @property
    def name(self) -> str:
        return f"cassette:{self.model}"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = chat_body(request, self.model, self.seed, self.temperature)
        sha = local_request_sha256(body, request.purpose)
        queue = self._by_sha.get(sha)
        if not queue:
            raise CassetteStaleError(f"no local_model recording for {request.purpose} request {sha[:12]}")
        hit = queue.pop(0) if len(queue) > 1 else queue[0]
        self.latencies.append((request.purpose, hit.latency_ms))
        return _response_of(hit.response)


def model_digest(model: str, url: str = OLLAMA_URL) -> str:
    """The local model's content digest from ``/api/tags`` (empty when Ollama is not reachable)."""
    try:
        import httpx

        tags = httpx.get(f"{url}/api/tags", timeout=5.0).json()
    except Exception:  # noqa: BLE001 - provenance only
        return ""
    for m in tags.get("models", []):
        if m.get("name") == model:
            return str(m.get("digest", ""))
    return ""
