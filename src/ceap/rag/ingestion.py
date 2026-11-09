"""Document loading, versioning and chunking.

Each knowledge document is a markdown file with an optional front-matter block::

    ---
    version: 1.2
    effective_date: 2026-10-01
    classification: internal
    ---

Version, effective date and classification travel onto every chunk, so a retrieval hit (and the
evidence later minted from it) says which revision of which document it came from. Documents that
live elsewhere in the repository (``docs/quant/quant.md``) are pulled in through ``manifest.json``
in the knowledge directory, which carries their metadata instead of front matter.

Admission (0.8.0). :func:`admit_documents` runs before indexing and rejects a document when

* its text contains instructions aimed at a model rather than a reader (:func:`screen_document`:
  "ignore previous instructions", role or system-prompt markers, chat-template tokens, requests to
  reveal prompts or call tools). A knowledge document is evidence, never a source of instructions,
  so a hit quarantines the whole document and logs why; or
* its metadata marks it superseded (``status: superseded`` / ``withdrawn``, or ``superseded_by``).

Query-time filtering (``effective_date <= as_of``, latest version per ``doc_key``) lives in
:mod:`ceap.rag.retrieval`.

Three chunking strategies are available; the default is the one the retrieval eval picked
(``ceap.rag.evaluation``; the table is in ``docs/ai-agents/ai-agents.md``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

ChunkingStrategy = Literal["section", "sentence", "fixed"]
CHUNKING_STRATEGIES: tuple[ChunkingStrategy, ...] = ("section", "sentence", "fixed")
DEFAULT_CHUNKING: ChunkingStrategy = "section"
MANIFEST = "manifest.json"
UNVERSIONED = "unversioned"
SUPERSEDED_STATUSES = frozenset({"superseded", "withdrawn", "retired"})

# Phrases that address a model, not a human reader. Kept narrow on purpose: a policy document may
# say "the system" or "instructions" in ordinary prose, so each pattern needs the imperative form.
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override", re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+)?"
        r"(?:previous|prior|above|earlier|preceding|your|system)\s+(?:instructions|prompts?|rules|guidelines)")),
    ("role_change", re.compile(r"(?i)\byou\s+are\s+now\s+(?:a|an|the|in)\b|\bact\s+as\s+(?:a|an)\s+(?:unrestricted|jailbroken)")),
    ("prompt_marker", re.compile(r"(?im)^\s*(?:system|assistant|developer)\s*:\s|<\|(?:im_start|im_end|system|endoftext)\|>|\[/?INST\]")),
    ("exfiltrate", re.compile(r"(?i)\b(?:reveal|print|repeat|output|show)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions|api\s+key|secrets?)\b")),
    ("tool_call", re.compile(r"(?i)\b(?:call|invoke|run|use)\s+the\s+\w+\s+tool\s+(?:to|and)\s+(?:approve|stage|send|delete|transfer)")),
    ("approval", re.compile(r"(?i)\b(?:auto-?approve|approve\s+(?:this|all)\s+(?:order|request|trade)s?\s+without)")),
)


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    path: str
    text: str
    category: str = "knowledge"
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return self.metadata.get("filename", Path(self.path).name)

    @property
    def version(self) -> str:
        return self.metadata.get("version", UNVERSIONED)

    @property
    def effective_date(self) -> str:
        return self.metadata.get("effective_date", "")

    @property
    def classification(self) -> str:
        return self.metadata.get("classification", "internal")


    @property
    def doc_key(self) -> str:
        """Identity across versions: ``doc_key`` metadata, else the filename. Two documents with the
        same key are two versions of one document; only the latest in force is served."""
        return self.metadata.get("doc_key", self.filename)

    @property
    def superseded(self) -> bool:
        status = self.metadata.get("status", "").strip().lower()
        return status in SUPERSEDED_STATUSES or bool(self.metadata.get("superseded_by", "").strip())


@dataclass(frozen=True)
class Rejection:
    document: str
    reason: str
    findings: tuple[str, ...] = ()


def screen_document(doc: Document) -> tuple[str, ...]:
    """Names of the injection patterns found in ``doc`` (title and body); empty when clean."""
    text = f"{doc.title}\n{doc.text}"
    return tuple(name for name, pattern in INJECTION_PATTERNS if pattern.search(text))


def admit_documents(documents: Iterable[Document]) -> tuple[list[Document], list[Rejection]]:
    """Split ``documents`` into those safe to index and those rejected (with the reason)."""
    admitted: list[Document] = []
    rejected: list[Rejection] = []
    for doc in documents:
        if doc.superseded:
            rejected.append(Rejection(doc.filename, "superseded"))
            log.warning("knowledge document %s is marked superseded; not indexed", doc.filename)
            continue
        findings = screen_document(doc)
        if findings:
            rejected.append(Rejection(doc.filename, "injected_instructions", findings))
            log.warning("knowledge document %s quarantined: instruction-like text (%s)", doc.filename,
                        ", ".join(findings))
            continue
        admitted.append(doc)
    return admitted, rejected


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    title: str
    section: str
    text: str
    order: int
    document: str = ""  # source filename
    version: str = UNVERSIONED
    effective_date: str = ""
    classification: str = "internal"
    sections: tuple[str, ...] = ()  # every section the chunk touches (fixed windows can span two)


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n+", re.DOTALL)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"`])|\n(?=\s*(?:[-*]|\d+\.|\|)\s)")


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split ``key: value`` front matter from the body; no front matter returns ``({}, text)``."""
    text = text.replace("\r\n", "\n")
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip()
    return meta, text[m.end() :]


def _document(path: Path, extra: dict[str, str] | None = None) -> Document:
    meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    meta = {**meta, **(extra or {}), "filename": path.name}
    title = next((m.group(2).strip() for line in body.splitlines() if (m := _HEADING.match(line))), path.stem)
    doc_id = "DOC-" + hashlib.sha1(path.name.encode()).hexdigest()[:8]
    return Document(id=doc_id, title=title, path=str(path), text=body, metadata=meta)


def load_documents(directory: str | Path) -> list[Document]:
    """Load ``*.md`` under ``directory`` plus any ``manifest.json`` external sources, sorted by filename."""
    directory = Path(directory)
    docs = [_document(p) for p in sorted(directory.glob("*.md"))]
    manifest = directory / MANIFEST
    if manifest.exists():
        spec = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in spec.get("external", []):
            path = (directory / entry["path"]).resolve()
            if not path.exists():
                log.warning("knowledge manifest source missing, skipped: %s", path)
                continue
            extra = {k: str(v) for k, v in entry.items() if k != "path"}
            docs.append(_document(path, extra))
    return docs


def corpus_hash(documents: Iterable[Document]) -> str:
    """SHA-256 over (filename, version, effective date, body) of every document, order-independent."""
    h = hashlib.sha256()
    for d in sorted(documents, key=lambda d: d.filename):
        for part in (d.filename, d.version, d.effective_date, d.text):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
    return h.hexdigest()


def _sections(doc: Document) -> list[tuple[str, str]]:
    """``(section heading, section text including its heading line)`` in document order."""
    out: list[tuple[str, str]] = []
    section, buf = doc.title, list[str]()
    for line in doc.text.splitlines():
        m = _HEADING.match(line)
        if m:
            # A section holding only heading lines (a title directly followed by a sub-heading) is
            # folded into the next section instead of becoming a chunk with no content.
            if any(b.strip() and not _HEADING.match(b) for b in buf):
                out.append((section, "\n".join(buf).strip()))
                buf = []
            section = m.group(2).strip()
            buf.append(line)
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        out.append((section, "\n".join(buf).strip()))
    return out


def _pack(units: list[str], max_chars: int, sep: str) -> list[str]:
    out: list[str] = []
    current = ""
    for u in units:
        if current and len(current) + len(u) + len(sep) > max_chars:
            out.append(current)
            current = u
        else:
            current = f"{current}{sep}{u}" if current else u
    if current:
        out.append(current)
    return out


def chunk_document(
    doc: Document,
    max_chars: int = 900,
    strategy: ChunkingStrategy = DEFAULT_CHUNKING,
    overlap: int = 150,
) -> list[Chunk]:
    """Chunk ``doc``.

    - ``section``: one chunk per markdown section, split on paragraphs when longer than ``max_chars``.
    - ``sentence``: sentences (and list items) packed into chunks of at most ``max_chars // 3``
      characters, never crossing a section boundary.
    - ``fixed``: fixed windows of ``max_chars // 2`` characters with ``overlap`` characters of overlap,
      ignoring structure; a chunk is labelled with the section it starts in.
    """
    if strategy not in CHUNKING_STRATEGIES:
        raise ValueError(f"unknown chunking strategy {strategy!r}; expected one of {CHUNKING_STRATEGIES}")
    pieces: list[tuple[str, str, tuple[str, ...]]] = []
    sections = _sections(doc)
    if strategy == "section":
        for name, text in sections:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            pieces += [(name, t, (name,)) for t in _pack(paragraphs, max_chars, "\n\n")]
    elif strategy == "sentence":
        for name, text in sections:
            sentences = [s.strip() for s in _SENTENCE.split(text) if s.strip()]
            pieces += [(name, t, (name,)) for t in _pack(sentences, max(80, max_chars // 3), " ")]
    else:
        width = max(100, max_chars // 2)
        step = max(1, width - min(overlap, width - 1))
        joined, starts = "", []
        for name, text in sections:
            starts.append((len(joined), name))
            joined += text + "\n\n"
        for begin in range(0, max(1, len(joined)), step):
            text = joined[begin : begin + width].strip()
            if not text:
                continue
            touched = tuple(n for s, n in starts if s < begin + width and _next_start(starts, s) > begin)
            pieces.append((touched[0] if touched else doc.title, text, touched))
            if begin + width >= len(joined):
                break
    return [
        Chunk(
            id=f"{doc.id}#{i}",
            document_id=doc.id,
            title=doc.title,
            section=name,
            text=text,
            order=i,
            document=doc.filename,
            version=doc.version,
            effective_date=doc.effective_date,
            classification=doc.classification,
            sections=touched,
        )
        for i, (name, text, touched) in enumerate(pieces)
    ]


def _next_start(starts: list[tuple[int, str]], start: int) -> int:
    later = [s for s, _ in starts if s > start]
    return later[0] if later else 1 << 62
