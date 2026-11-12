"""Paper order staging (``ceap.mcp.execution.staging``) and the DB kill switch (``ceap.persistence.killswitch``).

These are the last controls before an order set exists: the staging id makes a retried tool call
idempotent, the portfolio hash catches a portfolio that changed after approval, and the kill switch
refuses new sets. The report below is built by hand so every number is visible.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from ceap.domain.execution import Side
from ceap.domain.research import PortfolioPosition, PortfolioRiskReport
from ceap.mcp.execution.staging import (
    PAPER_ACCOUNT,
    StagedOrderBook,
    StagedOrders,
    StagingRefusedError,
    build_paper_orders,
    portfolio_hash,
    report_hash,
    staging_id,
)
from ceap.persistence.db import Database
from ceap.persistence.killswitch import AUDIT_SUBJECT, KillSwitch
from ceap.persistence.repositories import StagedOrderRepository

KEY = "test-key-0123456789abcdef0123456789abcdef"


def _pos(sym: str, qty: int, price: float) -> PortfolioPosition:
    return PortfolioPosition(sym, weight=0.0, notional=qty * price, quantity=qty, price=price, adv_participation=0.0)


def _report(gross: float = 10_000.0, positions: tuple[PortfolioPosition, ...] | None = None) -> PortfolioRiskReport:
    pos = positions if positions is not None else (_pos("AAA", 10, 100.0), _pos("BBB", -20, 50.0), _pos("ZZZ", 0, 5.0))
    return PortfolioRiskReport(
        signal="momentum_12_1", dataset="ds1", as_of="2024-03-15T00:00:00", long_short=True,
        gross_notional=gross, positions=pos, gross=1.0, net=0.0, max_abs_weight=0.1, hhi=0.1,
        effective_names=2.0, beta=0.0, max_adv_participation=0.0,
    )


# ------------------------------------------------------------------ hashes and ids
def test_portfolio_hash_ignores_order_zero_legs_and_time_of_day():
    a = portfolio_hash("2024-03-15T09:00:00", [("AAA", 10), ("BBB", -20), ("ZZZ", 0)])
    b = portfolio_hash("2024-03-15", [("BBB", -20), ("AAA", 10)])
    assert a == b
    # any changed quantity, sign or date is a different portfolio
    assert portfolio_hash("2024-03-15", [("AAA", 11), ("BBB", -20)]) != a
    assert portfolio_hash("2024-03-15", [("AAA", 10), ("BBB", 20)]) != a
    assert portfolio_hash("2024-03-18", [("AAA", 10), ("BBB", -20)]) != a
    assert portfolio_hash(None, []) == portfolio_hash("", [("X", 0)])


def test_report_hash_matches_portfolio_hash_of_its_positions():
    r = _report()
    assert report_hash(r) == portfolio_hash("2024-03-15", [("AAA", 10), ("BBB", -20)])


def test_staging_id_is_deterministic_and_keyed_on_every_field():
    base = staging_id("sig", "ds", "2024-03-15", 1_000.0, True)
    assert base.startswith("STG-") and len(base) == 14
    assert staging_id("sig", "ds", "2024-03-15", 1_000.0, True) == base
    # the notional is keyed to the cent: a sub-cent difference is the same request
    assert staging_id("sig", "ds", "2024-03-15", 1_000.001, True) == base
    for other in (staging_id("sig2", "ds", "2024-03-15", 1_000.0, True),
                  staging_id("sig", "ds2", "2024-03-15", 1_000.0, True),
                  staging_id("sig", "ds", "2024-03-18", 1_000.0, True),
                  staging_id("sig", "ds", "2024-03-15", 1_000.01, True),
                  staging_id("sig", "ds", "2024-03-15", 1_000.0, False)):
        assert other != base


# ------------------------------------------------------------------ order construction
def test_build_paper_orders_sides_quantities_and_stamp():
    orders = build_paper_orders(_report(), "STG-x")
    assert [(o.order_id, o.symbol, o.side, o.quantity) for o in orders] == [
        ("PAPER-STG-x-001", "AAA", Side.BUY, 10),
        ("PAPER-STG-x-002", "BBB", Side.SELL, 20),  # ZZZ (zero) is skipped
    ]
    o = orders[0]
    assert o.account == PAPER_ACCOUNT and o.trader == "paper" and o.venue is None
    assert o.decision_price == 100.0
    assert (o.timestamp.date().isoformat(), o.timestamp.hour, o.timestamp.minute) == ("2024-03-15", 14, 30)
    assert o.timestamp.utcoffset() is not None  # London-aware, never naive


def test_build_paper_orders_uses_checked_quantities_when_given():
    # after the pre-trade check: AAA clipped to 4, BBB flipped is not possible, BBB absent = not traded
    orders = build_paper_orders(_report(), "S", {"AAA": 4.0, "ZZZ": -3.0})
    assert [(o.symbol, o.side, o.quantity) for o in orders] == [("AAA", Side.BUY, 4), ("ZZZ", Side.SELL, 3)]
    # the order number follows the report position, so ids stay stable after a clip
    assert [o.order_id for o in orders] == ["PAPER-S-001", "PAPER-S-003"]
    assert build_paper_orders(_report(), "S", {}) == ()


def test_staged_orders_payload_round_trip_and_notionals():
    report = _report()
    staged = StagedOrders("STG-1", "sig", "ds", "2024-03-15", 10_000.0, True, datetime(2024, 3, 15, 15, 0),
                          build_paper_orders(report, "STG-1"), risk_blocks=({"symbol": "AAA"},))
    payload = json.loads(json.dumps(staged.to_payload()))
    back = StagedOrders.from_payload(payload)
    assert back == staged
    assert staged.buy_notional == 1_000.0 and staged.sell_notional == 1_000.0
    # older payloads without status / risk_blocks still load with the defaults
    del payload["status"], payload["risk_blocks"]
    old = StagedOrders.from_payload(payload)
    assert old.status == "STAGED" and old.risk_blocks == ()


def test_notional_treats_a_missing_decision_price_as_zero():
    staged = StagedOrders.from_payload({**StagedOrders(
        "S", "s", "d", "2024-03-15", 1.0, True, datetime(2024, 3, 15), build_paper_orders(_report(), "S")
    ).to_payload()})
    payload = staged.to_payload()
    for o in payload["orders"]:
        o["decision_price"] = None
    assert StagedOrders.from_payload(payload).buy_notional == 0.0


# ------------------------------------------------------------------ in-memory book
def test_memory_book_is_idempotent_and_has_no_halt_source():
    book = StagedOrderBook()
    assert book.halt_reason() is None and book.get("STG-missing") is None
    first, again = book.stage(_report(), risk_blocks=[{"rule": "x"}])
    assert not again and first.risk_blocks == ({"rule": "x"},)
    second, again = book.stage(_report(), quantities={"AAA": 1.0})
    # a retry returns the first set unchanged, even with different quantities
    assert again and second is first and len(second.orders) == 2
    assert book.get(first.staging_id) is first and book.list() == [first]


def test_memory_book_evicts_the_oldest_set_beyond_keep():
    book = StagedOrderBook(keep=2)
    sets = [book.stage(_report(gross=g))[0] for g in (1.0, 2.0, 3.0)]
    assert [s.staging_id for s in book.list()] == [sets[1].staging_id, sets[2].staging_id]
    assert book.get(sets[0].staging_id) is None


# ------------------------------------------------------------------ database-backed book
def test_db_book_halt_refuses_new_sets_but_returns_an_existing_one():
    with Database(hmac_key=KEY) as db:
        book = StagedOrderBook(db=db)
        staged, _ = book.stage(_report())
        KillSwitch(db).set("ops", "venue outage")
        assert book.halt_reason() == "venue outage"
        # the retry of an already staged set is not a new order and still answers
        again, was = StagedOrderBook(db=db).stage(_report())
        assert was and again.staging_id == staged.staging_id
        with pytest.raises(StagingRefusedError, match="venue outage"):
            book.stage(_report(gross=20_000.0))
        assert len(db.query("SELECT * FROM staged_orders")) == 1
        KillSwitch(db).clear("ops")
        _, was = book.stage(_report(gross=20_000.0))
        assert not was


def test_db_book_skips_paper_job_rows_of_another_shape():
    with Database(hmac_key=KEY) as db:
        r = _report()
        sid = staging_id(r.signal, r.dataset, r.as_of, r.gross_notional, r.long_short)
        StagedOrderRepository(db).save(sid, {"session": "2024-03-15", "deltas": {}})
        book = StagedOrderBook(db=db)
        assert book.get(sid) is None
        assert book.list() == []
        # the id is taken by the foreign row, so the insert loses and nothing stored is usable:
        # the freshly built set is returned as new (and kept in memory only)
        staged, was = book.stage(r)
        assert not was and staged.staging_id == sid
        assert json.loads(db.query("SELECT payload_json FROM staged_orders")[0]["payload_json"])["session"]


def test_db_book_returns_the_concurrent_winner_when_the_insert_loses(monkeypatch: pytest.MonkeyPatch):
    with Database(hmac_key=KEY) as db:
        winner, _ = StagedOrderBook(db=db).stage(_report())
        book = StagedOrderBook(db=db)
        real_load = book._load
        calls = []

        def racy_load(sid: str):
            calls.append(sid)
            return None if len(calls) == 1 else real_load(sid)  # not there on the first look

        monkeypatch.setattr(book, "_load", racy_load)
        got, was = book.stage(_report())
        assert was and got.staging_id == winner.staging_id and got.created_at == winner.created_at
        assert len(calls) == 2


def test_db_book_list_reads_through_and_honours_keep():
    with Database(hmac_key=KEY) as db:
        writer = StagedOrderBook(db=db)
        ids = [writer.stage(_report(gross=g))[0].staging_id for g in (1.0, 2.0, 3.0)]
        fresh = StagedOrderBook(db=db, keep=2)  # a restarted process: empty cache
        listed = [s.staging_id for s in fresh.list()]
        assert len(listed) == 2 and set(listed) <= set(ids)
        assert sorted(s.staging_id for s in StagedOrderBook(db=db).list()) == sorted(ids)


# ------------------------------------------------------------------ kill switch
def test_kill_switch_latest_event_wins_and_history_is_append_only():
    with Database(hmac_key=KEY) as db:
        ks = KillSwitch(db)
        assert ks.reason() is None and ks.history() == []
        st = ks.set("alice", "  drill  ")
        assert st.active and st.reason == "drill" and st.actor == "alice" and st.at
        ks.set("bob", "second")
        assert ks.state().reason == "second" and ks.state().actor == "bob"
        cleared = ks.clear("carol", "   ")
        assert not cleared.active and cleared.reason is None and ks.reason() is None
        hist = ks.history()
        assert [(h["actor"], h["action"], h["reason"]) for h in hist] == [
            ("alice", "set", "drill"), ("bob", "set", "second"), ("carol", "clear", "cleared")]
        assert [h["seq"] for h in hist] == sorted(h["seq"] for h in hist)
        audit = db.query("SELECT actor, action, subject, payload_json FROM audit_log ORDER BY seq")
        assert [(a["action"], a["subject"]) for a in audit] == [
            ("kill_switch.set", AUDIT_SUBJECT), ("kill_switch.set", AUDIT_SUBJECT),
            ("kill_switch.clear", AUDIT_SUBJECT)]
        assert json.loads(audit[0]["payload_json"]) == {"reason": "drill"}


def test_kill_switch_refuses_a_blank_reason_without_writing():
    with Database(hmac_key=KEY) as db:
        with pytest.raises(ValueError, match="reason"):
            KillSwitch(db).set("ops", "")
        assert KillSwitch(db).history() == [] and db.query("SELECT * FROM audit_log") == []


def test_kill_switch_on_an_old_schema_reads_off_and_refuses_to_write():
    with Database(hmac_key=KEY) as db:
        db.run(lambda c: c.execute("DROP TABLE kill_switch_events"))
        ks = KillSwitch(db)
        assert not ks.state().active and ks.reason() is None
        with pytest.raises(RuntimeError, match="schema v4"):
            ks.set("ops", "stop")
        # the failed write left no orphan audit row
        assert db.query("SELECT * FROM audit_log") == []
        assert StagedOrderBook(db=db).halt_reason() is None


def test_kill_switch_events_cannot_be_rewritten():
    with Database(hmac_key=KEY) as db:
        KillSwitch(db).set("ops", "stop")
        with pytest.raises(sqlite3.DatabaseError):
            db.run(lambda c: c.execute("UPDATE kill_switch_events SET action='clear'"))
        assert KillSwitch(db).state().active
