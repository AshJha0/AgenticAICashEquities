"""Recall and false-positive rate of the knowledge-ingestion injection screen (0.10.0 H3, R-08).

Plain idea: run :func:`ceap.rag.ingestion.screen_document` over text that should be refused and text
that should be admitted, and count.

Formal statement. Attacks: the 50 base strings of ``injection_corpus_v1`` (10 styles x 5 goals, the
``retrieved_doc`` surface), each screened as written and again with a zero-width space inserted
after every character, so the normalisation is exercised on all 50. Clean text: every document of
the shipped knowledge corpus (``data/reference/knowledge``) and every paragraph in it. Each rate is
reported with its 95 % Wilson interval (:func:`ceap.eval.stats.rate`).

The six patterns were written before this corpus existed and were not tuned to it, so the recall is
an honest out-of-sample figure for those patterns and a low one. A regex screen is a tripwire, not a
defence; the defence is that retrieved text reaches the model only inside nonce-tagged data blocks.

Regenerate the committed results with ``PYTHONPATH=src python -m ceap.eval.ingestion_screen``;
``tests/evaluation/test_r0100_h3_ingestion_screen.py`` fails when the file is stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ceap.eval.injection import load_corpus
from ceap.eval.stats import rate
from ceap.paths import data_path
from ceap.rag.ingestion import Document, load_documents, screen_document

ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE = data_path("reference", "knowledge")
RESULTS = ROOT / "docs" / "evaluation" / "ingestion_screen.json"
ZWSP = "​"


def _flagged(text: str) -> bool:
    return bool(screen_document(Document("screen", "", "screen", text)))


def measure(knowledge: Path = KNOWLEDGE) -> dict[str, Any]:
    attacks = [a for a in load_corpus()["attacks"] if a["surface"] == "retrieved_doc"]
    plain = [a["id"] for a in attacks if _flagged(a["text"])]
    hidden = [a["id"] for a in attacks if _flagged(ZWSP.join(a["text"]))]
    by_style: dict[str, int] = {}
    for a in attacks:
        by_style[a["style"]] = by_style.get(a["style"], 0) + (a["id"] in plain)
    docs = load_documents(knowledge)
    paragraphs = [p for d in docs for p in d.text.split("\n\n") if p.strip()]
    return {
        "screen": "ceap.rag.ingestion.screen_document",
        "attack_corpus": "injection_corpus_v1 retrieved_doc surface (50 base strings)",
        "recall": rate(len(plain), len(attacks)),
        "recall_zero_width": rate(len(hidden), len(attacks)),
        "recall_by_style": dict(sorted(by_style.items())),
        "false_positive_documents": rate(sum(_flagged(f"{d.title}\n{d.text}") for d in docs), len(docs)),
        "false_positive_paragraphs": rate(sum(_flagged(p) for p in paragraphs), len(paragraphs)),
        "note": "patterns not tuned to this corpus; a regex screen is a tripwire, not a defence",
    }


def main() -> None:  # pragma: no cover - CLI entry point
    RESULTS.write_text(json.dumps(measure(), indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(RESULTS.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    main()
