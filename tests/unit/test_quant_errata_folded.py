"""Q8 (R-18): the quant errata is folded into the pinned corpus page, with Q1's corrected value."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ceap.analytics.numerics import sharpe_standard_error

ROOT = Path(__file__).resolve().parents[2]
QUANT = (ROOT / "docs/quant/quant.md").read_text(encoding="utf-8")


def test_q8_errata_file_removed_and_not_linked():
    assert not (ROOT / "docs/quant/quant-errata-0.9.0.md").exists()
    assert "quant-errata" not in (ROOT / "docs/INDEX.md").read_text(encoding="utf-8")


def test_q8_corpus_page_carries_the_corrections():
    assert "measured, not chosen" not in QUANT and "29/30 overall" not in QUANT
    assert "INCONCLUSIVE" in QUANT and "**3.0** for a search" in QUANT and "**25/30**" in QUANT
    # the worked example uses Q1's daily Lo (2002) form: Sharpe 0.3 over 1,260 days
    se = sharpe_standard_error(0.3, 1260)
    assert f"= {se:.3f}`" in QUANT and round(se, 3) == 0.447


def test_q8_corpus_pin_bumped_and_matches_live_corpus():
    from ceap.eval.corpus import corpus_problems

    manifest = json.loads((ROOT / "data/reference/knowledge/manifest.json").read_text(encoding="utf-8"))
    assert manifest["external"][0]["version"] == "0.5.0"
    assert corpus_problems()["problems"] == []
    assert not re.search(r"ee8200cd3d90", (ROOT / "src/ceap/eval/corpus_pin.json").read_text(encoding="utf-8"))
