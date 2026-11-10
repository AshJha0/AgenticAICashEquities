"""Pre-trade rules for orders staged from a flat book (``check_staged_orders``) and the halt source.

Worked numbers: equity 100 000, ``max_name_weight`` 0.10, so each name may hold 10 000 of value.
"""

from __future__ import annotations

import pytest

from ceap.paper import pretrade
from ceap.paper.pretrade import RiskLimits, check_staged_orders

EQUITY = 100_000.0
ORDERS = [
    ("BBB", 300, 50.0, "t1"),  # cap floor(10 000 / 50) = 200 shares: clipped 300 -> 200
    ("AAA", 50, 100.0, "t1"),  # cap 100 shares: 50 passes
    ("CCC", -10, 20.0, "htb"),  # short in a hard-to-borrow tier: dropped
    ("RRR", 5, 10.0, "t1"),  # restricted: dropped
    ("ZZZ", 3, 0.0, "t1"),  # no price: cap 0, dropped
    ("LLL", 7, 10.0, "htb"),  # a long in an HTB tier is fine
]
LIMITS = RiskLimits(restricted=("RRR",), htb_tiers=("htb",))


def _rules(blocks: list[dict]) -> list[tuple[str, str, float]]:
    return [(b["symbol"], b["detail"]["rule"], b["detail"]["allowed"]) for b in blocks]


def test_name_restricted_and_htb_rules_worked_example():
    r = check_staged_orders(ORDERS, EQUITY, LIMITS)
    assert r.deltas == {"AAA": 50.0, "BBB": 200.0, "LLL": 7.0}
    assert _rules(r.blocks) == [
        ("BBB", "max_name_weight", 200.0),
        ("CCC", "hard_to_borrow", 0.0),
        ("RRR", "restricted", 0.0),
        ("ZZZ", "max_name_weight", 0.0),
    ]
    bbb = r.blocks[0]
    # the blocked quantity is requested minus allowed
    assert bbb["quantity"] == 100.0 and bbb["detail"]["cap_shares"] == 200
    # gross 50*100 + 200*50 + 7*10 = 15 070 < 1.05 * 100 000: no pro-rata scaling
    assert not any(b["detail"]["rule"] == "max_gross" for b in r.blocks)


def test_gross_limit_scales_pro_rata_in_whole_shares_and_drops_zeros():
    limits = RiskLimits(restricted=("RRR",), htb_tiers=("htb",), max_gross=0.10)
    r = check_staged_orders(ORDERS, EQUITY, limits)
    # gross 15 070 over a 10 000 limit: scale 10 000 / 15 070 = 0.66357
    # AAA floor(50 * s) = 33, BBB floor(200 * s) = 132, LLL floor(7 * s) = 4
    assert r.deltas == {"AAA": 33.0, "BBB": 132.0, "LLL": 4.0}
    gross_blocks = [b for b in r.blocks if b["detail"]["rule"] == "max_gross"]
    assert [b["symbol"] for b in gross_blocks] == ["AAA", "BBB", "LLL"]
    assert gross_blocks[0]["detail"]["gross"] == pytest.approx(0.1507)
    # the scaled book is within the limit
    assert 33 * 100 + 132 * 50 + 4 * 10 <= 10_000


def test_gross_scaling_to_zero_removes_the_name():
    r = check_staged_orders(
        [("A", -1, 100.0, "t"), ("B", 100, 100.0, "t")], EQUITY, RiskLimits(max_gross=0.05)
    )
    # gross 100 + 10 000 = 10 100 over 5 000: s = 0.495; A floor(0.495) = 0 -> removed; B floor(49.5) = 49
    assert r.deltas == {"B": 49.0}
    assert ("A", "max_gross", 0.0) in _rules(r.blocks)  # -0.0 == 0.0: the short sign is kept, size 0


def test_zero_equity_skips_gross_scaling():
    r = check_staged_orders([("A", 1, 10.0, "t")], 0.0, RiskLimits())
    # cap floor(0.1 * 0 / 10) = 0: clipped by name weight, nothing left to scale
    assert r.deltas == {} and _rules(r.blocks) == [("A", "max_name_weight", 0.0)]


def test_halt_status_env_wins_then_db_then_none(monkeypatch):
    import ceap.persistence.killswitch as ks

    class FakeSwitch:
        reason_value: str | None = "desk halt"

        def __init__(self, db: object) -> None:
            pass

        def reason(self) -> str | None:
            return self.reason_value

    monkeypatch.setattr(ks, "KillSwitch", FakeSwitch)
    monkeypatch.setenv(pretrade.HALT_ENV, " ops ")
    assert pretrade.halt_status(object()) == ("ops", "env")  # type: ignore[arg-type]
    monkeypatch.delenv(pretrade.HALT_ENV)
    assert pretrade.halt_status(object()) == ("desk halt", "db")  # type: ignore[arg-type]
    assert pretrade.halt_reason(object()) == "desk halt"  # type: ignore[arg-type]
    FakeSwitch.reason_value = None
    assert pretrade.halt_status(object()) == (None, None)  # type: ignore[arg-type]
    assert pretrade.halt_status(None) == (None, None)


def test_from_json_tuples_and_htb():
    lim = RiskLimits.from_json({"restricted": ["X"], "hard_to_borrow": ["Y"], "htb_tiers": ["t9"]})
    assert lim.restricted == ("X",) and lim.is_htb("Y", "t0") and lim.is_htb("Z", "t9")
    assert not lim.is_htb("Z", "t0")
    assert RiskLimits.from_json(None) == RiskLimits()
