"""Author ``tests/golden/golden_set.json`` (PLAN 3E.1). Run only for a deliberate ``golden-change``.

The expected findings are written per cause from the scenario specifications (what an execution
analyst would require a correct report to say and to cite), not from any platform output. Bump
``VERSION`` whenever the content changes; the owner signs off the ten ``SIGNOFF_SAMPLE`` items.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ceap.data.research_scenarios import all_research_scenarios  # noqa: E402
from ceap.data.scenarios import all_scenarios  # noqa: E402
from ceap.golden import GOLDEN_SET_PATH, items_sha256  # noqa: E402

VERSION = "1.0.0"
ALL_CAUSES = (
    "MARKET_VOLATILITY",
    "WIDE_SPREADS",
    "LOW_LIQUIDITY",
    "VENUE_DEGRADATION",
    "TECHNOLOGY_LATENCY",
    "MARKET_DATA_ANOMALY",
    "LARGE_ORDER_IMPACT",
    "PRICE_MOVEMENT",
)
# evidence a correct report must cite for each true cause (by the tool that produced it)
MUST_MENTION = {
    "NORMAL": ["execution.get_execution_metrics", "market_data.get_market_statistics"],
    "MARKET_VOLATILITY": ["market_data.get_market_statistics"],
    "WIDE_SPREADS": ["market_data.get_market_statistics"],
    "LOW_LIQUIDITY": ["market_data.get_order_book_statistics"],
    "VENUE_DEGRADATION": ["execution.get_venue_statistics"],
    "TECHNOLOGY_LATENCY": ["engineering.get_latency_metrics"],
    "MARKET_DATA_ANOMALY": ["market_data.get_market_statistics"],
    "LARGE_ORDER_IMPACT": ["execution.get_parent_orders", "execution.get_execution_metrics"],
    "PRICE_MOVEMENT": ["market_data.get_market_statistics"],
}
SIGNOFF_SAMPLE = ["G-INV-01", "G-INV-05", "G-INV-10", "G-INV-14", "G-INV-17",
                  "G-INV-22", "G-INV-28", "G-RES-02", "G-RES-08", "G-RES-13"]  # fmt: skip


def investigation_items() -> list[dict]:
    specs = all_scenarios()
    chosen = [s for i, s in enumerate(specs) if i % 5 < 3]  # 3 symbols x 10 templates
    items = []
    for n, s in enumerate(chosen, 1):
        truth = sorted(c.value for c in s.ground_truth)
        mention = sorted({t for c in truth for t in MUST_MENTION[c]})
        items.append(
            {
                "id": f"G-INV-{n:02d}",
                "kind": "investigation",
                "scenario": s.id,
                "template": s.template,
                "symbol": s.symbol,
                "question": f"Analyse our {s.symbol} execution between 14:00 and 15:00 and explain why implementation shortfall changed.",
                "primary": truth,
                "must_mention_tools": mention,
                "must_not_claim": [c for c in ALL_CAUSES if c not in truth],
            }
        )
    return items


def research_items() -> list[dict]:
    items = []
    for n, s in enumerate(all_research_scenarios()[::2], 1):
        verdict = s.expected_verdict.value
        flags = sorted(f.value for f in s.expected_flags)
        items.append(
            {
                "id": f"G-RES-{n:02d}",
                "kind": "research",
                "scenario": s.id,
                "template": s.template,
                "signal": s.signal,
                "verdict": verdict,
                "must_flag": flags,
                "must_not_flag": ["NO_ALPHA", "OOS_DECAY", "REGIME_BREAK"] if verdict == "PROMOTE" else [],
            }
        )
    return items


def main() -> None:
    items = investigation_items() + research_items()
    doc = {
        "version": VERSION,
        "frozen": "2026-11-08",
        "authored_from": "scenario specifications (ground truth and expected verdicts); not from platform output",
        "change_policy": "edits only in a PR labelled golden-change, with a version bump and owner sign-off",
        "signoff_sample": SIGNOFF_SAMPLE,
        "signoff": {"status": "PENDING", "by": None, "pr": None},
        "items_sha256": items_sha256(items),
        "items": items,
    }
    GOLDEN_SET_PATH.write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {len(items)} items, version {VERSION}, sha {doc['items_sha256'][:12]}")


if __name__ == "__main__":
    main()
