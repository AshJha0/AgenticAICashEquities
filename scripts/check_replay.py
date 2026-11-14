"""CI replay gate for local-model results (0.10.0 T3, R-01).

Plain idea: a published local-model number is only trusted if CI can rebuild it from the recorded
replies alone. For every committed per-seed result file this script replays its cassette with no
model running, and fails when

* a request has no recording (a miss),
* a recording is never asked for (an unused interaction), or
* any number in the committed result file differs from the number the replay produces.

Formal statement. For each ``docs/evaluation/local_model/<slug>_s<seed>.json`` with an eval cassette
``tests/cassettes/local_model/<slug>_eval_s<seed>.json``, let ``R`` be the committed JSON and ``R'``
the result of :func:`ceap.eval.local_model.run_seed` over a :class:`CountingReplay` of the cassette.
The gate passes iff misses = 0, unused = 0 and every numeric leaf of ``R`` (booleans included) is
present in ``R'`` with the same value (floats to 1e-9).

Cassettes recorded before 0.10.0 carry no manifest (every interaction has an empty ``commit``); their
golden rows came from the mock fallback and are withdrawn (R-01). Those pairs are reported as
``not gated (pre-0.10.0, awaiting re-record)``, never silently passed; any cassette with a manifest
is gated, so a new recording cannot opt out.

Worked example: ``python scripts/check_replay.py`` prints one line per result file and exits 1 on
the first kind of failure listed above.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ceap.eval.local_model import CASSETTES, GOLDEN_LIMIT, RESULTS, _latency, run_seed
from ceap.llm.client import LLMRequest, LLMResponse
from ceap.llm.ollama import LocalCassette, LocalCassetteClient, load_local_cassette

Scorer = Callable[[LocalCassetteClient], Awaitable[dict[str, Any]]]
RESULT_RE = re.compile(r"^(?P<slug>.+)_s(?P<seed>\d+)\.json$")


class CountingReplay(LocalCassetteClient):
    """A cassette replay that also counts how many recorded interactions were served."""

    def __init__(self, cassette: LocalCassette) -> None:
        super().__init__(cassette)
        self._served: dict[str, int] = {}

    async def complete(self, request: LLMRequest) -> LLMResponse:
        from ceap.llm.ollama import chat_body, local_request_sha256

        sha = local_request_sha256(chat_body(request, self.model, self.seed, self.temperature), request.purpose)
        out = await super().complete(request)
        self._served[sha] = self._served.get(sha, 0) + 1
        return out

    def unused(self) -> int:
        recorded: dict[str, int] = {}
        for i in self.cassette.interactions:
            recorded[i.request_sha256] = recorded.get(i.request_sha256, 0) + 1
        return sum(max(0, n - self._served.get(sha, 0)) for sha, n in recorded.items())


def numeric_diffs(published: Any, replayed: Any, path: str = "") -> list[str]:
    """Every numeric leaf of ``published`` that ``replayed`` lacks or gives differently."""
    if isinstance(published, dict):
        out: list[str] = []
        for k, v in published.items():
            sub = replayed.get(k) if isinstance(replayed, dict) else None
            if isinstance(replayed, dict) and k not in replayed and _has_number(v):
                out.append(f"{path}/{k}: missing from replay")
                continue
            out += numeric_diffs(v, sub, f"{path}/{k}")
        return out
    if isinstance(published, list):
        if not isinstance(replayed, list) or len(replayed) != len(published):
            return [f"{path}: list differs in length"] if _has_number(published) else []
        return [d for i, (a, b) in enumerate(zip(published, replayed, strict=True)) for d in numeric_diffs(a, b, f"{path}[{i}]")]
    if isinstance(published, bool):
        same = replayed is published
    elif isinstance(published, (int, float)):
        same = (
            isinstance(replayed, (int, float))
            and not isinstance(replayed, bool)
            and math.isclose(float(published), float(replayed), rel_tol=0, abs_tol=1e-9)
        )
    else:
        return []
    return [] if same else [f"{path}: published {published!r}, replay {replayed!r}"]


def _has_number(v: Any) -> bool:
    if isinstance(v, dict):
        return any(_has_number(x) for x in v.values())
    if isinstance(v, list):
        return any(_has_number(x) for x in v)
    return isinstance(v, (int, float))


def is_pre_manifest(cassette: LocalCassette) -> bool:
    return all(not i.commit for i in cassette.interactions)


async def check_pair(result_path: Path, cassette_path: Path, scorer: Scorer) -> list[str]:
    """Problems found replaying ``cassette_path`` against ``result_path`` (empty list = pass)."""
    client = CountingReplay(load_local_cassette(cassette_path))
    try:
        replayed = await scorer(client)
    except Exception as exc:  # noqa: BLE001 - any replay failure is a gate failure, by name
        return [f"replay raised {type(exc).__name__}: {exc}"]
    problems: list[str] = []
    if client.misses:
        problems.append(f"{client.misses} request(s) had no recording")
    if (n := client.unused()):
        problems.append(f"{n} recorded interaction(s) never replayed")
    published = json.loads(result_path.read_text(encoding="utf-8"))
    problems += numeric_diffs(published, replayed)
    return problems


async def default_scorer(client: LocalCassetteClient) -> dict[str, Any]:
    result = await run_seed(client, GOLDEN_LIMIT)
    result["latency_ms"] = _latency(client)
    return json.loads(json.dumps(result, default=str))  # same normalisation as the written file


def pairs(results: Path = RESULTS, cassettes: Path = CASSETTES) -> list[tuple[Path, Path]]:
    out = []
    for p in sorted(results.glob("*_s*.json")):
        m = RESULT_RE.match(p.name)
        if m and (c := cassettes / f"{m['slug']}_eval_s{m['seed']}.json").exists():
            out.append((p, c))
    return out


def main(results: Path = RESULTS, cassettes: Path = CASSETTES, scorer: Scorer = default_scorer) -> int:
    failed = 0
    for result, cassette in pairs(results, cassettes):
        if is_pre_manifest(load_local_cassette(cassette)):
            print(f"{result.name}: not gated (pre-0.10.0, awaiting re-record)")
            continue
        problems = asyncio.run(check_pair(result, cassette, scorer))
        print(f"{result.name}: {'FAIL' if problems else 'ok'}")
        for line in problems[:40]:
            print(f"  {line}")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
