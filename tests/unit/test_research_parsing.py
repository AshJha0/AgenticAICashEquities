"""Rule-based parsing of research questions."""

from __future__ import annotations

from datetime import date

from ceap.api.parsing import parse_research_question


def test_signal_keywords_and_explicit_ids():
    assert parse_research_question("Does 12-1 momentum work on R01?").signal == "momentum_12_1"
    assert parse_research_question("Test short-term reversal on this universe").signal == "reversal_5"
    assert parse_research_question("Is there a mean reversion premium?").signal == "reversal_5"
    assert parse_research_question("Evaluate the low-vol anomaly").signal == "low_vol_60"
    assert parse_research_question("Run low_vol_60 please").signal == "low_vol_60"
    assert parse_research_question("Momentum or reversal?").signal == "momentum_12_1"  # first keyword wins
    assert parse_research_question("What about our execution quality?").signal is None


def test_dataset_and_date_range():
    parsed = parse_research_question("Evaluate momentum on RS07 from 2024-01-02 to 2026-06-30")
    assert parsed.dataset == "RS07" and parsed.start == date(2024, 1, 2) and parsed.end == date(2026, 6, 30)
    assert parse_research_question("momentum on r03").dataset == "R03"
    assert parse_research_question("momentum on T01").dataset is None  # T01 is an execution scenario
    bad = parse_research_question("momentum from 2024-13-40 to 2025-01-01")
    assert bad.start is None and bad.end is None
    reversed_range = parse_research_question("momentum between 2025-01-01 and 2024-01-01")
    assert reversed_range.start is None and reversed_range.end is None
