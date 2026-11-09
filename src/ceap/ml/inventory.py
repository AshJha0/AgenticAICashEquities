"""Model inventory (0.10 D22): every model whose output reaches a person or an order, in one list.

Supervisory guidance on model risk (Federal Reserve SR 11-7, 2011) asks for an inventory with an owner, an
independent validator, a status, a risk tier and a review cadence for each model, and for a challenger to
compare against. CEAP is a research platform with no independent validation function, so most entries say
so plainly ("unassigned") rather than pretending otherwise. LLM purposes are listed because their output is
shown to an approver; they are routed, not fitted, so their "challenger" is the deterministic template.

Evidence class: governance (a statement of how the project is run; nothing here is measured).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

UNASSIGNED = "unassigned (no independent validation yet)"


@dataclass(frozen=True)
class InventoryEntry:
    name: str
    kind: str  # fitted | parametric | rule | llm
    purpose: str
    tier: str  # 1 drives orders, 2 shown to an approver, 3 research only
    status: str
    owner: str
    validator: str
    review_cadence_days: int
    challenger: str
    card: str  # where the model card lives

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MODEL_INVENTORY: tuple[InventoryEntry, ...] = (
    InventoryEntry("pretrade_cost", "parametric", "expected shortfall and conformal interval for a parent order "
                   "before it is staged (ceap.ml.pretrade_cost)", "2", "research", "CEAP maintainers", UNASSIGNED,
                   180, "spread/2 + square-root impact with a fixed coefficient (no fitting)",
                   "docs/ml/model_cards.md"),
    InventoryEntry("ridge_combination", "fitted", "ridge combination of the three research signals "
                   "(ceap.analytics.ml_signals)", "3", "research", "CEAP maintainers", UNASSIGNED, 365,
                   "equal-weight z-score composite (paired bootstrap and Diebold-Mariano test in every result)",
                   "docs/ml/model_cards.md"),
    InventoryEntry("change_detector", "fitted", "CUSUM anomaly detector on execution metrics (ceap.ml.anomaly)",
                   "2", "research", "CEAP maintainers", UNASSIGNED, 365,
                   "fixed 3-sigma threshold on the same features", "docs/ml/model_cards.md"),
    InventoryEntry("calibration_engineering_change_point", "fitted", "confidence calibration of the engineering "
                   "change-point finding", "2", "research", "CEAP maintainers", UNASSIGNED, 365,
                   "uncalibrated rule confidence", "docs/ml/model_cards.md"),
    InventoryEntry("research_assessment", "rule", "PROMOTE / REJECT / INCONCLUSIVE gate chain "
                   "(ceap.analytics.research_assessment)", "2", "research", "CEAP maintainers", UNASSIGNED, 180,
                   "single in-sample IC t-test at 1.645 with no deflation", "docs/research/README.md"),
    *(
        InventoryEntry(f"llm:{p}", "llm", f"LLM purpose '{p}' routed by ceap.llm.router; never computes a number",
                       "2", "research", "CEAP maintainers", UNASSIGNED, 90,
                       "deterministic template output (MockLLMClient)", "docs/ai-agents/ai-agents.md")
        for p in ("planning", "narrative", "critique", "judge")
    ),
)


def inventory() -> list[dict[str, Any]]:
    return [e.to_dict() for e in MODEL_INVENTORY]
