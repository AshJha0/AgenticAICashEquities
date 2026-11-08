"""Provenance-tagged cassettes for the Anthropic adapter (PLAN 3A.5). Never a live call (D15).

A cassette is one JSON file::

    {"provenance": {"provenance": "hand_built" | "recorded", "model": ..., "sdk": ...,
                    "request_sha256": ..., "prompt_sha": {purpose: sha256}, "note": ...},
     "interactions": [{"purpose": ..., "match": "exact" | "prefix",
                       "request_sha256": ..., "request": {...}, "response": {Message JSON}}]}

``tests/cassettes/schema.json`` is the JSON schema of that shape (generated from
:class:`Cassette`; a test keeps them in step).

* **Replay** goes through the real SDK: :class:`CassetteTransport` is an ``httpx2`` transport that
  answers each request with the next recorded ``Message`` *only if* the request body hashes to the
  recorded ``request_sha256``. A changed prompt, schema, model or message makes it ``STALE`` (the
  transport raises :class:`CassetteStaleError` and records the mismatch on ``stale``).
* ``match: "prefix"`` hashes only the stable prefix (model, system, tools, output format) for
  interactions whose message carries run-time values (the narrative facts include timestamps);
  a prompt change still makes them ``STALE``.
* **Writing** (:func:`write_cassette`) applies the scrubber (0.8) to every interaction first.
* **Metric gate**: :func:`assert_publishable` refuses to let a model-behaviour metric come from a
  ``hand_built`` cassette - a hand-built reply measures the harness, not the model.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ceap.util.redact import redact_obj, scrub_cassette

STALE = "STALE"
_PREFIX_KEYS = ("model", "system", "tools", "output_config", "thinking", "max_tokens")


class CassetteStaleError(RuntimeError):
    """The request no longer matches the recording: re-author or re-record the cassette."""

    status = STALE


class UnpublishableMetricError(RuntimeError):
    """A model-behaviour metric was about to be published from a hand-built cassette."""


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: Literal["hand_built", "recorded"]
    model: str
    sdk: str
    request_sha256: str
    prompt_sha: dict[str, str]
    note: str = ""


class Interaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    match: Literal["exact", "prefix"] = "exact"
    request_sha256: str
    request: dict[str, Any]
    response: dict[str, Any]


class Cassette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: Provenance
    interactions: list[Interaction]


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode(
        "utf-8"
    )


MATCH_SIG_DIGITS = 9
# Evidence digests hash raw (unrounded) tool output, so they move with the same last-ulp noise (and
# with Python 3.11 vs 3.12 float summation); digest integrity is covered by the evidence tests.
_DIGEST = re.compile(r'(\\?"digest\\?":\s*\\?")[0-9a-f]{8,64}(?=\\?")')
_DECIMAL = re.compile(r"(?<![\w.])-?\d+\.\d+(?:[eE][-+]?\d+)?(?![\w.])")


def _round_literal(m: re.Match[str]) -> str:
    text = m.group(0)
    digits = len(re.sub(r"[^0-9]", "", text.split("e")[0].split("E")[0]).lstrip("0"))
    return text if digits <= MATCH_SIG_DIGITS else f"{float(text):.{MATCH_SIG_DIGITS}g}"


def _round_floats(value: Any) -> Any:
    """Floats to ``MATCH_SIG_DIGITS`` significant digits, recursively.

    Request bodies carry raw tool output (for example TCA metrics), as JSON numbers and as decimal
    literals inside prompt text, whose last-ulp digits differ across OS and BLAS builds and between
    Python 3.11 and 3.12 float summation. The match key rounds both, and masks evidence digests of
    those values, so a cassette recorded on one OS replays on another; the stored body and the request
    sent on the wire stay exact.
    """
    if isinstance(value, str):
        return _DECIMAL.sub(_round_literal, _DIGEST.sub(r"\g<1><digest>", value))
    if isinstance(value, float):
        return float(f"{value:.{MATCH_SIG_DIGITS}g}") if math.isfinite(value) else value
    if isinstance(value, dict):
        return {k: ("<digest>" if k == "digest" else _round_floats(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_round_floats(v) for v in value]
    return value


def request_sha256(body: Mapping[str, Any], match: str = "exact") -> str:
    """sha256 of the canonical request body (``prefix``: only the stable, cacheable prefix).

    Floats are rounded to ``MATCH_SIG_DIGITS`` significant digits first (see ``_round_floats``).
    """
    scoped = {k: body.get(k) for k in _PREFIX_KEYS} if match == "prefix" else dict(body)
    return hashlib.sha256(canonical_json(_round_floats(scoped))).hexdigest()


def purpose_of(body: Mapping[str, Any]) -> str:
    """Purpose of a wire request, from its system prompt's registry header-free text."""
    from ceap.llm.prompts import registry

    system = body.get("system")
    text = system[-1]["text"] if isinstance(system, list) and system else str(system or "")
    for prompt in registry().values():
        if prompt.text == text:
            return prompt.id
    return "general"


def prompt_sha_of(body: Mapping[str, Any]) -> str:
    system = body.get("system")
    text = system[-1]["text"] if isinstance(system, list) and system else str(system or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scrub(interaction: Mapping[str, Any]) -> dict[str, Any]:
    """Scrubber (0.8) on write: header and id scrub plus redaction of every request/response string.

    ``request_sha256`` is computed from the body actually sent, before redaction.
    """
    out = scrub_cassette(interaction)
    for key in ("request", "response"):
        if key in out:
            out[key] = redact_obj(out[key])
    return out


def build_cassette(
    interactions: list[dict[str, Any]], *, provenance: str, model: str, sdk: str, note: str = ""
) -> Cassette:
    items = [Interaction.model_validate(_scrub(i)) for i in interactions]
    header = Provenance(
        provenance=provenance,  # type: ignore[arg-type]
        model=model,
        sdk=sdk,
        request_sha256=hashlib.sha256("".join(i.request_sha256 for i in items).encode()).hexdigest(),
        prompt_sha={i.purpose: prompt_sha_of(i.request) for i in items},
        note=note,
    )
    return Cassette(provenance=header, interactions=items)


def write_cassette(path: Path, cassette: Cassette) -> None:
    """Scrub (again - idempotent) and write with stable formatting."""
    data = cassette.model_dump(mode="json")
    data["interactions"] = [_scrub(i) for i in data["interactions"]]
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def load_cassette(path: Path) -> Cassette:
    return Cassette.model_validate_json(path.read_text(encoding="utf-8"))


def assert_publishable(cassette: Cassette, metric: str) -> None:
    """CI gate: a model-behaviour metric must never be computed from a hand-built cassette."""
    if cassette.provenance.provenance == "hand_built":
        raise UnpublishableMetricError(
            f"metric {metric!r} would come from a hand_built cassette ({cassette.provenance.note or 'no note'}); "
            "it measures the harness, not the model - label it NOT RUN LIVE"
        )


def cassette_schema() -> dict[str, Any]:
    return Cassette.model_json_schema()


def _transport_base() -> Any:
    import httpx2

    return httpx2


class CassetteTransport:
    """``httpx2`` mock transport replaying a cassette in order, refusing stale requests."""

    def __new__(cls, cassette: Cassette) -> Any:
        httpx2 = _transport_base()
        state: dict[str, Any] = {"i": 0}
        stale: list[str] = []

        def handle(request: Any) -> Any:
            body = json.loads(request.content or b"{}")
            i = state["i"]
            if i >= len(cassette.interactions):
                stale.append(f"interaction {i}: cassette exhausted")
                raise CassetteStaleError(f"{STALE}: request {i} has no recording")
            rec = cassette.interactions[i]
            got = request_sha256(body, rec.match)
            if got != rec.request_sha256:
                stale.append(f"interaction {i} ({rec.purpose}): {got[:12]} != {rec.request_sha256[:12]}")
                raise CassetteStaleError(
                    f"{STALE}: request {i} ({rec.purpose}) changed since it was recorded"
                )
            state["i"] = i + 1
            return httpx2.Response(200, json=rec.response, headers={"request-id": "req_cassette"})

        transport = httpx2.MockTransport(handle)
        transport.stale = stale  # type: ignore[attr-defined]
        transport.state = state  # type: ignore[attr-defined]
        return transport


class RecordingTransport:
    """``httpx2`` mock transport that answers with ``responder`` and keeps each exchange for a cassette."""

    def __new__(
        cls, responder: Callable[[dict[str, Any]], dict[str, Any]], prefix_purposes: tuple[str, ...] = ()
    ) -> Any:
        httpx2 = _transport_base()
        recorded: list[dict[str, Any]] = []

        def handle(request: Any) -> Any:
            body = json.loads(request.content or b"{}")
            response = responder(body)
            purpose = purpose_of(body)
            match = "prefix" if purpose in prefix_purposes else "exact"
            recorded.append(
                {
                    "purpose": purpose,
                    "match": match,
                    "request_sha256": request_sha256(body, match),
                    "request": body,
                    "response": response,
                }
            )
            return httpx2.Response(200, json=response, headers={"request-id": "req_recording"})

        transport = httpx2.MockTransport(handle)
        transport.recorded = recorded  # type: ignore[attr-defined]
        return transport


def sdk_over(transport: Any) -> Any:
    """A real ``AsyncAnthropic`` (dummy key, no retries) whose HTTP goes to ``transport``."""
    import anthropic

    return anthropic.AsyncAnthropic(
        api_key="sk-cassette-not-a-real-key",
        base_url="https://api.invalid",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=transport),
    )


def llm_provenance(llm: Any) -> str:
    """Where model answers in a run came from: ``mock``, ``hand_built``, ``recorded`` or ``unknown``.

    Only ``recorded`` cassettes (a real model's replies, captured once) may back a published
    model-behaviour metric; nothing in this repository records one (D15), so evaluation output
    labels its model metrics ``NOT RUN LIVE``.
    """
    from ceap.llm.client import MockLLMClient

    inner = getattr(llm, "primary", llm)  # unwrap a router
    explicit = getattr(inner, "provenance", None)
    if isinstance(explicit, str):
        return explicit
    if isinstance(inner, MockLLMClient):
        return "mock"
    return "unknown"


def model_metrics_label(provenance: str) -> str:
    return "publishable (recorded cassette)" if provenance == "recorded" else f"NOT RUN LIVE ({provenance})"
