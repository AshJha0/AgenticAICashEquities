"""Knowledge-corpus governance (C3): version pinning and a staleness check.

``corpus_pin.json`` (next to this module) records, for every corpus document, its version, its
effective date and the sha256 of its body. :func:`corpus_problems` compares the live corpus with the
pin:

* **problems** (fail the test): a document added or removed without re-pinning; a body edited
  without a version bump; a version or date changed without re-pinning; an unversioned document;
  an effective date in the future.
* **warnings** (reported, never fatal): a document older than ``MAX_AGE_DAYS`` (due for review), and
  release-note wording inside a corpus file ("since 0.5.0"), which belongs in the CHANGELOG
  (lesson from 0.7.0).

Re-pin after a reviewed corpus change with ``python -m ceap.eval.corpus --pin``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from ceap.rag.ingestion import UNVERSIONED, Document, corpus_hash, load_documents
from ceap.rag.retrieval import DEFAULT_KNOWLEDGE_DIR

PIN_PATH = Path(__file__).with_name("corpus_pin.json")
MAX_AGE_DAYS = 365
_RELEASE_NOTE = re.compile(
    r"\b(?:since|as of|from|new in|added in|changed in)\s+v?\d+\.\d+\.\d+\b", re.IGNORECASE
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pin_corpus(documents: list[Document] | None = None) -> dict[str, Any]:
    docs = documents if documents is not None else load_documents(DEFAULT_KNOWLEDGE_DIR)
    return {
        "corpus_hash": corpus_hash(docs),
        "documents": [
            {
                "filename": d.filename,
                "version": d.version,
                "effective_date": d.effective_date,
                "body_sha256": _sha(d.text),
            }
            for d in sorted(docs, key=lambda d: d.filename)
        ],
    }


def load_pin(path: Path = PIN_PATH) -> dict[str, Any]:
    pin: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return pin


def corpus_problems(
    documents: list[Document] | None = None, pin: dict[str, Any] | None = None, today: date | None = None
) -> dict[str, list[str]]:
    docs = documents if documents is not None else load_documents(DEFAULT_KNOWLEDGE_DIR)
    pin = pin if pin is not None else load_pin()
    today = today or date.today()
    pinned = {p["filename"]: p for p in pin.get("documents", [])}
    live = {d.filename: d for d in docs}
    problems: list[str] = []
    warnings: list[str] = []
    for name in sorted(set(pinned) - set(live)):
        problems.append(f"{name}: pinned but missing from the corpus (re-pin after review)")
    for name in sorted(set(live) - set(pinned)):
        problems.append(f"{name}: in the corpus but not pinned (re-pin after review)")
    for name in sorted(set(live) & set(pinned)):
        d, p = live[name], pinned[name]
        if d.version == UNVERSIONED or not d.version:
            problems.append(f"{name}: no version in front matter or manifest")
        body_changed = _sha(d.text) != p["body_sha256"]
        if body_changed and d.version == p["version"]:
            problems.append(f"{name}: body edited without a version bump (still {d.version})")
        elif d.version != p["version"] or d.effective_date != p["effective_date"] or body_changed:
            problems.append(
                f"{name}: now version {d.version} effective {d.effective_date}; pin says "
                f"{p['version']} effective {p['effective_date']} (re-pin after review)"
            )
        try:
            effective = date.fromisoformat(d.effective_date)
        except ValueError:
            problems.append(f"{name}: effective_date {d.effective_date!r} is not an ISO date")
            continue
        if effective > today:
            problems.append(f"{name}: effective_date {effective} is in the future")
        elif (today - effective).days > MAX_AGE_DAYS:
            warnings.append(f"{name}: effective {effective}, older than {MAX_AGE_DAYS} days; due for review")
        for m in _RELEASE_NOTE.finditer(d.text):
            warnings.append(
                f"{name}: release-note wording {m.group(0)!r}; keep release history in the CHANGELOG"
            )
    return {"problems": problems, "warnings": warnings}


def corpus_report(today: date | None = None) -> dict[str, Any]:
    docs = load_documents(DEFAULT_KNOWLEDGE_DIR)
    pin = load_pin()
    found = corpus_problems(docs, pin, today)
    return {
        "documents": len(docs),
        "pinned_corpus_hash": pin.get("corpus_hash"),
        "live_corpus_hash": corpus_hash(docs),
        "pinned": pin.get("corpus_hash") == corpus_hash(docs),
        "max_age_days": MAX_AGE_DAYS,
        **found,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Knowledge corpus pin and staleness check (C3).")
    parser.add_argument("--pin", action="store_true", help="rewrite corpus_pin.json from the live corpus")
    args = parser.parse_args(argv)
    if args.pin:
        PIN_PATH.write_text(json.dumps(pin_corpus(), indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"pinned {PIN_PATH}")
    print(json.dumps(corpus_report(), indent=1))


if __name__ == "__main__":
    main()
