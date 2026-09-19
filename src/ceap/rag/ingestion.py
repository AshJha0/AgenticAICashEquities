"""Document loading and chunking."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    path: str
    text: str
    category: str = "knowledge"
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    title: str
    section: str
    text: str
    order: int


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def load_documents(directory: str | Path) -> list[Document]:
    directory = Path(directory)
    docs: list[Document] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = next(
            (m.group(2).strip() for line in text.splitlines() if (m := _HEADING.match(line))), path.stem
        )
        doc_id = "DOC-" + hashlib.sha1(path.name.encode()).hexdigest()[:8]
        docs.append(
            Document(id=doc_id, title=title, path=str(path), text=text, metadata={"filename": path.name})
        )
    return docs


def chunk_document(doc: Document, max_chars: int = 900) -> list[Chunk]:
    """Split on markdown headings, then on paragraphs when a section is long."""
    chunks: list[Chunk] = []
    section = doc.title
    buf: list[str] = []
    order = 0

    def flush() -> None:
        nonlocal buf, order
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        current = ""
        for p in paragraphs:
            if current and len(current) + len(p) + 2 > max_chars:
                chunks.append(Chunk(f"{doc.id}#{order}", doc.id, doc.title, section, current, order))
                order += 1
                current = p
            else:
                current = f"{current}\n\n{p}" if current else p
        if current:
            chunks.append(Chunk(f"{doc.id}#{order}", doc.id, doc.title, section, current, order))
            order += 1

    for line in doc.text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            section = m.group(2).strip()
            buf.append(line)
        else:
            buf.append(line)
    flush()
    return chunks
