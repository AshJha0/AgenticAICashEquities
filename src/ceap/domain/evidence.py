"""Evidence: the audit trail every finding must point at."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ceap.domain.common import new_id


class EvidenceType(str, Enum):
    MARKET_DATA = "MARKET_DATA"
    ORDER_DATA = "ORDER_DATA"
    EXECUTION_DATA = "EXECUTION_DATA"
    ORDER_BOOK = "ORDER_BOOK"
    SYSTEM_METRIC = "SYSTEM_METRIC"
    LOG = "LOG"
    DOCUMENT = "DOCUMENT"
    CALCULATION = "CALCULATION"
    CODE_CHANGE = "CODE_CHANGE"
    RISK = "RISK"
    SIGNAL = "SIGNAL"
    BACKTEST = "BACKTEST"


_PREFIX = {
    EvidenceType.MARKET_DATA: "MARKET",
    EvidenceType.ORDER_DATA: "ORDER",
    EvidenceType.EXECUTION_DATA: "EXEC",
    EvidenceType.ORDER_BOOK: "BOOK",
    EvidenceType.SYSTEM_METRIC: "ENG",
    EvidenceType.LOG: "LOG",
    EvidenceType.DOCUMENT: "DOC",
    EvidenceType.CALCULATION: "TCA",
    EvidenceType.CODE_CHANGE: "CHANGE",
    EvidenceType.RISK: "RISK",
    EvidenceType.SIGNAL: "SIG",
    EvidenceType.BACKTEST: "BT",
}


@dataclass(frozen=True)
class Evidence:
    id: str
    type: EvidenceType
    source: str
    description: str
    timestamp: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def create(
        type: EvidenceType,
        source: str,
        description: str,
        timestamp: datetime | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Evidence:
        return Evidence(
            id=new_id(_PREFIX[type]),
            type=type,
            source=source,
            description=description,
            timestamp=timestamp,
            attributes=dict(attributes or {}),
        )


def document_citation(
    chunk: dict[str, Any], cited_text: str, span: tuple[int, int], cited_sentence: str, corpus_hash: str
) -> Evidence:
    """R3C 3C.4: DOC evidence for one verified citation (contracts-change: new attribute keys).

    Minted only after the harness checked that ``cited_text`` equals the retrieved chunk's text.
    ``span`` is the ``[start, end)`` of the citing sentence in the report narrative.
    """
    return Evidence.create(
        EvidenceType.DOCUMENT,
        source="knowledge.citation",
        description=f"{chunk.get('title')} / {chunk.get('section')} (version {chunk.get('version')})",
        attributes={
            "chunk_id": chunk.get("chunk_id"),
            "document": chunk.get("document"),
            "section": chunk.get("section"),
            "version": chunk.get("version"),
            "effective_date": chunk.get("effective_date"),
            "cited_text": cited_text,
            "cited_sentence": cited_sentence,
            "narrative_span": [span[0], span[1]],
            "corpus_hash": corpus_hash,
        },
    )
