"""Drift monitors (PLAN 3E.4). Each returns a list of problems; empty means no drift.

* :func:`cassette_staleness` - every cassette interaction still hashes to what a fresh request
  would send (prompt registry hashes against the cassette's ``prompt_sha``);
* :func:`rag_recall_floor` - held-out retrieval recall at 3 and MRR stay above their CI floors;
* :func:`golden_set_version` - the golden set is frozen (hash matches its version);
* :func:`model_pins_vs_cassettes` - every model a cassette names is still pinned;
* :func:`sdk_vs_cassettes` - the pinned SDK version, the installed SDK and every cassette agree,
  so a Dependabot SDK bump fails until the cassettes are re-authored.

``tests/contract/test_drift_monitors.py`` runs each monitor on the repository (must be clean) and
on a failing fixture (must report).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ceap.golden import golden_set_problems, load_golden_set
from ceap.llm.pins import ModelPins, load_pins
from ceap.llm.recording import Cassette, load_cassette

REPO_ROOT = Path(__file__).resolve().parents[2]
CASSETTE_DIR = REPO_ROOT / "tests" / "cassettes"
RAG_RECALL_FLOOR = 0.80
RAG_MRR_FLOOR = 0.70
_PURPOSE_TO_PROMPT = {
    "planner": "planner",
    "research_planner": "research_planner",
    "critic": "critic",
    "report": "report",
    "research_report": "research_report",
}


def repository_cassettes(directory: Path = CASSETTE_DIR) -> list[tuple[str, Cassette]]:
    return [(p.name, load_cassette(p)) for p in sorted(directory.glob("investigation_*.json"))]


def cassette_staleness(cassettes: Iterable[tuple[str, Cassette]]) -> list[str]:
    from ceap.llm.prompts import get_prompt

    problems = []
    for name, cassette in cassettes:
        for purpose, sha in cassette.provenance.prompt_sha.items():
            prompt_id = _PURPOSE_TO_PROMPT.get(purpose)
            if prompt_id is None:
                continue
            current = hashlib.sha256(get_prompt(prompt_id).text.encode("utf-8")).hexdigest()
            if current != sha:
                problems.append(
                    f"{name}: STALE - the {prompt_id} prompt changed since the cassette was authored"
                )
    return problems


def rag_recall_floor(kb: Any = None, queries: list[dict[str, Any]] | None = None) -> list[str]:
    from ceap.rag.evaluation import load_eval, score_split

    if kb is None:
        from ceap.rag.ingestion import load_documents
        from ceap.rag.retrieval import DEFAULT_KNOWLEDGE_DIR, KnowledgeBase

        kb = KnowledgeBase(load_documents(DEFAULT_KNOWLEDGE_DIR))
    result = score_split(
        kb, queries if queries is not None else load_eval(), "heldout", min_support=kb.min_support
    )
    problems = []
    if result.recall_at_k < RAG_RECALL_FLOOR:
        problems.append(f"held-out recall@3 {result.recall_at_k:.2f} below floor {RAG_RECALL_FLOOR}")
    if result.mrr < RAG_MRR_FLOOR:
        problems.append(f"held-out MRR {result.mrr:.3f} below floor {RAG_MRR_FLOOR}")
    return problems


def golden_set_version(doc: dict[str, Any] | None = None) -> list[str]:
    return golden_set_problems(doc if doc is not None else load_golden_set())


def model_pins_vs_cassettes(
    cassettes: Iterable[tuple[str, Cassette]], pins: ModelPins | None = None
) -> list[str]:
    pins = pins or load_pins()
    problems = []
    for name, cassette in cassettes:
        models = {i.request.get("model") for i in cassette.interactions} | {
            i.response.get("model") for i in cassette.interactions
        }
        for model in sorted(m for m in models if m):
            if model not in pins.allowed:
                problems.append(f"{name}: model {model} is no longer pinned in model_pins.toml")
    return problems


def sdk_vs_cassettes(
    cassettes: Iterable[tuple[str, Cassette]], pins: ModelPins | None = None, installed: str | None = None
) -> list[str]:
    pins = pins or load_pins()
    pinned = f"{pins.sdk_package}=={pins.sdk_version}"
    problems = []
    if installed is None:
        try:
            import anthropic

            installed = str(anthropic.__version__)
        except ImportError:
            installed = None
    if installed is not None and installed != pins.sdk_version:
        problems.append(
            f"installed {pins.sdk_package} {installed} != pinned {pins.sdk_version}; re-author cassettes"
        )
    for name, cassette in cassettes:
        if cassette.provenance.sdk != pinned:
            problems.append(
                f"{name}: authored with {cassette.provenance.sdk}, pinned is {pinned}; re-author it"
            )
    return problems
