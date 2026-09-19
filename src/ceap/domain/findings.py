"""Findings: claims supported (or contradicted) by evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ceap.domain.common import new_id


@dataclass(frozen=True)
class Finding:
    id: str
    statement: str
    supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    confidence: float
    category: str = "GENERAL"
    produced_by: str = "unknown"
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence}")

    @staticmethod
    def create(
        statement: str,
        supporting: list[str] | tuple[str, ...],
        confidence: float,
        category: str = "GENERAL",
        produced_by: str = "unknown",
        contradicting: list[str] | tuple[str, ...] = (),
        attributes: dict[str, Any] | None = None,
    ) -> Finding:
        return Finding(
            id=new_id("F"),
            statement=statement,
            supporting_evidence=tuple(supporting),
            contradicting_evidence=tuple(contradicting),
            confidence=round(confidence, 3),
            category=category,
            produced_by=produced_by,
            attributes=dict(attributes or {}),
        )

    def with_confidence(self, confidence: float, **attrs: Any) -> Finding:
        return Finding(
            id=self.id,
            statement=self.statement,
            supporting_evidence=self.supporting_evidence,
            contradicting_evidence=self.contradicting_evidence,
            confidence=round(max(0.0, min(1.0, confidence)), 3),
            category=self.category,
            produced_by=self.produced_by,
            attributes={**self.attributes, **attrs},
        )
