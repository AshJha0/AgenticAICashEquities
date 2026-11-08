"""3C.3: the retrieval query and the readable classifications, built deterministically.

The planner model never writes the knowledge query: it would be a fixed string at best and an
injection channel at worst. ``retrieval_query`` derives it from the parsed question (its key terms,
the symbol and window) and the cause candidates (the pre-registered hypotheses the plan chose, plus
any cause the question names). Segments are separated by ``"; "``; ``KnowledgeBase.search`` scores
each segment on its own, so one focused segment per cause is not diluted by the others.

``allowed_classifications`` maps the principal's roles to the document classifications it may read.
The knowledge server defaults to the least-privileged set and the policy engine denies a search that
asks for more than the principal's roles allow (``policy.engine._rule_knowledge_classification``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ceap.rag.text import tokenize

SEGMENT_SEPARATOR = "; "
MAX_QUESTION_TERMS = 10
MAX_CAUSES = 4

PUBLIC = "public"
INTERNAL = "internal"
RESTRICTED = "restricted"
#: what a principal with no recognised role, or only ``viewer``, may read
DEFAULT_CLASSIFICATIONS: tuple[str, ...] = (PUBLIC, INTERNAL)
#: roles that may also read ``restricted`` documents (limits, policies with numbers that matter)
RESTRICTED_ROLES = frozenset({"trader", "quant", "engineer", "approver", "auditor", "admin"})

#: cause -> the words the knowledge corpus uses for it
CAUSE_PHRASES: dict[str, str] = {
    "MARKET_VOLATILITY": "volatility threshold realised volatility",
    "WIDE_SPREADS": "wide spreads quoted spread threshold",
    "LOW_LIQUIDITY": "low liquidity displayed depth",
    "VENUE_DEGRADATION": "venue degradation fill rate smart-order-router",
    "TECHNOLOGY_LATENCY": "latency ack SLO incident runbook",
    "MARKET_DATA_ANOMALY": "stale quotes crossed quotes feed gap",
    "LARGE_ORDER_IMPACT": "participation rate large order size",
    "PRICE_MOVEMENT": "adverse price drift movement",
}
#: question words that name a cause even when the plan did not choose it
_CAUSE_TRIGGERS: dict[str, tuple[str, ...]] = {
    "MARKET_VOLATILITY": ("volatility", "volatile"),
    "WIDE_SPREADS": ("spread",),
    "LOW_LIQUIDITY": ("liquidity", "depth", "illiquid"),
    "VENUE_DEGRADATION": ("venue", "router", "sor", "routing"),
    "TECHNOLOGY_LATENCY": ("latency", "slow", "ack", "outage", "reject"),
    "MARKET_DATA_ANOMALY": ("stale", "crossed", "feed"),
    "LARGE_ORDER_IMPACT": ("participation", "size", "large"),
    "PRICE_MOVEMENT": ("drift", "move", "movement", "rally", "selloff"),
}
#: question words allowed into the query: the domain vocabulary only. Anything else in the question
#: (including an injection attempt) never reaches a tool argument.
DOMAIN_TERMS = frozenset(
    tokenize(" ".join(CAUSE_PHRASES.values()))
    + [t for ts in _CAUSE_TRIGGERS.values() for t in tokenize(" ".join(ts))]
    + tokenize(
        "vwap twap pov implementation shortfall slippage arrival benchmark impact permanent temporary "
        "spread cost fill rate rejects reject child parent order orders algorithm strategy schedule "
        "participation ceiling limit limits position notional breach stress escalation explanation "
        "deterioration deteriorated threshold thresholds baseline window policy policies runbook "
        "incident deployment change rollback router sor dark pool lit auction open close opening "
        "closing intraday volume profile urgency passive aggressive tick ticks market data quote "
        "quotes nbbo feed gap tca methodology attribution cause causes evidence best execution "
        "approval promotion research backtest turnover drag out-of-sample capacity"
    )
)
_INVESTIGATION_TAIL = "execution policy escalation deterioration threshold"
_RESEARCH_TAIL = "research promotion criteria out-of-sample cost drag approval policy"


def cause_candidates(question: str, hypotheses: Iterable[str] = ()) -> list[str]:
    """Plan-chosen hypotheses first (in plan order), then causes the question names; known causes only."""
    out = [h for h in dict.fromkeys(str(h) for h in hypotheses) if h in CAUSE_PHRASES]
    words = set(tokenize(question))
    for cause, triggers in _CAUSE_TRIGGERS.items():
        if cause not in out and any(t in words for t in triggers):
            out.append(cause)
    return out[:MAX_CAUSES]


def retrieval_query(question: str, facts: Mapping[str, Any], hypotheses: Iterable[str] = ()) -> str:
    """Deterministic retrieval query: domain terms of the question, symbol and window, one segment per cause."""
    research = facts.get("kind") == "research"
    terms = [t for t in dict.fromkeys(tokenize(question)) if t in DOMAIN_TERMS][:MAX_QUESTION_TERMS]
    head = " ".join(terms)
    segments = [head] if head else []
    if not research:
        symbol = facts.get("symbol")
        window = f"{facts.get('window_start') or ''} {facts.get('window_end') or ''}".strip()
        context = " ".join(x for x in (str(symbol) if symbol else "", window) if x)
        if context:
            segments.append(context)
        segments += [CAUSE_PHRASES[c] for c in cause_candidates(question, hypotheses)]
    segments.append(_RESEARCH_TAIL if research else _INVESTIGATION_TAIL)
    return SEGMENT_SEPARATOR.join(segments)


def allowed_classifications(roles: Iterable[str]) -> tuple[str, ...]:
    """Document classifications readable by a principal holding ``roles``."""
    if RESTRICTED_ROLES & {str(r).lower() for r in roles}:
        return (*DEFAULT_CLASSIFICATIONS, RESTRICTED)
    return DEFAULT_CLASSIFICATIONS


def parse_classifications(value: object) -> tuple[str, ...] | None:
    """``"public,internal"`` -> ``("public", "internal")``; empty or missing -> ``None``; else ``ValueError``.

    Plan arguments must be scalars (harness rule), so the classifications travel as one string.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("classifications must be a comma-separated string")
    out = tuple(dict.fromkeys(c.strip().lower() for c in value.split(",") if c.strip()))
    return out or None
