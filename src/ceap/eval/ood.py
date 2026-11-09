"""Out-of-distribution golden split (B6).

The 45 signed-off golden items (``tests/golden/golden_set.json``) are drawn from the ten scenario
templates the attribution rules were written against. This split holds three templates the rules
never saw: cause *combinations* that no catalogue template contains, at magnitudes off the catalogue
points, on seeds outside every catalogue range.

* ``venue_and_spreads``        - a degraded BATS (the catalogue only degrades ARCA) plus 2.0x spreads;
* ``latency_and_large_order``  - 6x gateway latency with no rejects and no deployment, plus a 6x parent;
* ``illiquid_and_volatile``    - depth at 0.45x plus 2.2x volatility.

The items live in their own versioned file (``tests/golden/golden_ood_v1.json``) with their own hash,
so the existing sign-off stays valid. Truth was written from the scenario parameters and the
documented threshold table, before any run. The specs are rebuilt from the file, never from code
defaults, so the file alone defines the split.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from ceap.analytics.attribution import Cause
from ceap.data.repositories import DatasetStore
from ceap.data.scenarios import ScenarioSpec


def spec_from_item(item: dict[str, Any]) -> ScenarioSpec:
    """Rebuild the scenario spec an OOD item names (every field comes from the frozen file)."""
    params = dict(item["spec"])
    allowed = {f.name for f in fields(ScenarioSpec)}
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f"unknown scenario fields in {item['id']}: {sorted(unknown)}")
    params["ground_truth"] = tuple(Cause(c) for c in params["ground_truth"])
    params["tags"] = tuple(params.get("tags") or ("ood",))
    return ScenarioSpec(**params)


class OODDatasetStore(DatasetStore):
    """A dataset store that also resolves the OOD scenario ids (the catalogue stays untouched)."""

    def __init__(self, specs: list[ScenarioSpec]) -> None:
        super().__init__()
        self._extra = {s.id: s for s in specs}

    def get(self, scenario: str | ScenarioSpec = "T01") -> Any:
        if isinstance(scenario, str) and scenario in self._extra:
            scenario = self._extra[scenario]
        return super().get(scenario)


async def ood_scores(doc: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
    """Run every OOD item through a fresh evaluation platform and summarise per template."""
    from ceap.golden import score_ood, summarise_ood
    from ceap.harness.memory import CaseMemory
    from ceap.platform import Platform, default_request

    items = doc["items"][:limit] if limit else doc["items"]
    specs = [spec_from_item(i) for i in items]
    platform = Platform(store=OODDatasetStore(specs), case_memory=CaseMemory(enabled=False))
    rows = []
    for item, spec in zip(items, specs, strict=True):
        result = await platform.investigate(default_request(spec.symbol, spec.id))
        rows.append({**score_ood(item, result), "template": item["template"]})
    from ceap.eval.clients import evidence_class

    return {
        "version": doc["version"],
        "signoff": (doc.get("signoff") or {}).get("status"),
        "evidence_class": evidence_class(platform.llm),
        "cases_source": "hand_built",
        **summarise_ood(rows),
        "rows": rows,
    }
