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
