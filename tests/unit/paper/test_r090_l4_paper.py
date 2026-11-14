"""0.9.0 lane 4: F5 paper half, F10, F11 paper part, F12 rest, F13 (all opt-in except F10 and halts)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ceap.analytics.backtest import CostModel, FinancingModel, PositionCaps
from ceap.paper.config import PaperConfig
from ceap.paper.feed import Dividend, MarketPanel
from ceap.paper.fills import BookState, FillParams, OpenOrder, limit_alignment, simulate_day
from ceap.paper.ledger import LEDGER_FIELDS, MONEY_KEY, PaperLedger, _ledger_body, to_micros
from ceap.paper.pretrade import RiskLimits, check_orders, sigma_band
from ceap.persistence.db import Database

DATES = np.array(["2026-01-05", "2026-01-06", "2026-01-07"], dtype="datetime64[D]")
VOL = np.array([100.0, 100.0])


def panel(close, adv=(100_000.0, 50_000.0), dividends=(), members=None) -> MarketPanel:  # type: ignore[no-untyped-def]
    c = np.asarray(close, dtype=float)
    n, m = c.shape
    return MarketPanel(
        name="hand", symbols=("AAA", "BBB")[:m], tiers=("A", "C")[:m], dates=DATES[:n], close=c, low=c.copy(),
        volume=np.tile(np.asarray(adv[:m]), (n, 1)), adv_20=np.tile(np.asarray(adv[:m], dtype=float), (n, 1)),
        spread_bps=np.full((n, m), 10.0),
        members=np.ones((n, m), dtype=bool) if members is None else np.asarray(members, dtype=bool),
        dividends=tuple(dividends),
    )


def flat_params(**kw) -> FillParams:  # type: ignore[no-untyped-def]
    return FillParams(financing=FinancingModel(enabled=False), costs=CostModel(), caps=PositionCaps(), **kw)


def held(positions, marks, cash=1_000_000.0) -> BookState:  # type: ignore[no-untyped-def]
    s = BookState.new(cash)
    s.positions, s.marks = dict(positions), dict(marks)
    s.equity = cash + sum(q * marks[k] for k, q in positions.items())
    s.day_index, s.first_index, s.last_date = 0, 0, "2026-01-05"
    return s


# ---------------------------------------------------------------- F13 dividends
def test_dividend_long_receives_short_pays_and_total_return_holds() -> None:
    # AAA pays 1.00 and drops by it on the ex-date; BBB pays 0.50 and drops by it; we are short 200 BBB
    p = panel([[100.0, 50.0], [99.0, 49.5]], dividends=[Dividend("AAA", "2026-01-06", 1.0),
                                                         Dividend("BBB", "2026-01-06", 0.5)])
    s = held({"AAA": 1000.0, "BBB": -200.0}, {"AAA": 100.0, "BBB": 50.0})
    r = simulate_day(s, p, 1, flat_params(), 0.0, VOL)
    divs = [e for e in r.events if e["kind"] == "dividend"]
    assert [(e["symbol"], e["cash_delta"]) for e in divs] == [("AAA", 1000.0), ("BBB", -100.0)]
    assert r.snapshot["dividends"] == pytest.approx(900.0)
    # total return: the price drop and the dividend cancel, so equity is unchanged
    assert r.state.equity == pytest.approx(s.equity)
    a = r.snapshot["attribution"]
    assert sum(a.values()) == pytest.approx(r.state.equity - s.equity)


def test_negative_dividend_is_refused() -> None:
    with pytest.raises(ValueError):
        Dividend("AAA", "2026-01-06", -1.0)


# ---------------------------------------------------------------- F5 auction
def test_moc_fill_is_capped_at_the_auction_fraction_and_pays_auction_impact() -> None:
    p = panel([[100.0, 50.0], [100.0, 50.0]])
    s = held({}, {})
    s.orders = {"AAA": OpenOrder("o1", "AAA", 5_000.0, "2026-01-05")}
    r = simulate_day(s, p, 1, flat_params(moc_auction=True), 0.0, VOL)
    fill = next(e for e in r.events if e["kind"] == "fill")
    assert fill["quantity"] == pytest.approx(0.10 * 0.06 * 100_000)  # 600 shares of a 6,000-share auction
    assert fill["detail"]["venue"] == "closing_auction"
    assert fill["detail"]["cost_bps"] >= 0.0
    assert r.state.orders["AAA"].quantity == pytest.approx(4_400.0)
    again = simulate_day(s, p, 1, flat_params(moc_auction=True), 0.0, VOL)
    assert again.events == r.events  # seeded: a session replays exactly


def test_limit_alignment_reports_sessions_to_fill() -> None:
    a = limit_alignment(flat_params())
    assert a["sessions_to_fill"] == 3 and a["consistent"]  # 25 % ADV at 10 % a session
    assert not limit_alignment(flat_params(order_ttl_days=2))["consistent"]
    assert limit_alignment(flat_params(moc_auction=True))["session_cap_adv"] == pytest.approx(0.006)


def test_pretrade_clips_to_work_sessions_times_fill_cap() -> None:
    p = panel([[100.0, 50.0], [100.0, 50.0]])
    chk = check_orders({"AAA": 30_000.0}, {}, 1e9, p, 1, RiskLimits(max_work_sessions=2), session_cap_fraction=0.10)
    assert chk.deltas["AAA"] == pytest.approx(20_000.0)
    assert chk.blocks[0]["detail"]["rule"] == "max_order_adv"
    # without the session limit the 25 % rule alone applies
    chk2 = check_orders({"AAA": 30_000.0}, {}, 1e9, p, 1, RiskLimits(), session_cap_fraction=0.10)
    assert chk2.deltas["AAA"] == pytest.approx(25_000.0)


# ---------------------------------------------------------------- F12 sigma band and halts
def _trend(n: int, vol: float, jump: float) -> MarketPanel:
    rng = np.random.default_rng(3)
    c = 100 * np.cumprod(1 + vol * rng.standard_normal(n))
    c[-1] = c[-2] * (1 + jump)
    dates = np.arange(np.datetime64("2026-01-05"), np.datetime64("2026-01-05") + np.timedelta64(n, "D"))
    return MarketPanel(name="t", symbols=("AAA",), tiers=("A",), dates=dates, close=c[:, None], low=c[:, None],
                       volume=np.full((n, 1), 1e6), adv_20=np.full((n, 1), 1e6), spread_bps=np.full((n, 1), 5.0),
                       members=np.ones((n, 1), dtype=bool))


def test_sigma_band_blocks_a_six_sigma_print_and_caps_at_ten_percent() -> None:
    lim = RiskLimits(price_band_sigma=6.0, price_band_cap=0.10)
    quiet = _trend(30, 0.01, 0.09)  # 9 % move on a 1 % name: about 9 sigma
    assert sigma_band(quiet.adjusted_close(29), 29, 0, lim) == pytest.approx(0.06, abs=0.02)
    chk = check_orders({"AAA": 100.0}, {}, 1e7, quiet, 29, lim)
    assert not chk.deltas and chk.blocks[0]["detail"]["rule"] == "sigma_band"
    loud = _trend(30, 0.03, 0.09)  # 9 % on a 3 % name: inside 6 sigma, so the 10 % cap binds
    assert sigma_band(loud.adjusted_close(29), 29, 0, lim) == pytest.approx(0.10)
    assert check_orders({"AAA": 100.0}, {}, 1e7, loud, 29, lim).deltas == {"AAA": 100.0}
    assert check_orders({"AAA": 100.0}, {}, 1e7, quiet, 29, RiskLimits()).deltas == {"AAA": 100.0}  # off by default


def test_halted_name_gets_no_new_order_and_fill_reject_is_marked() -> None:
    p = panel([[100.0, 50.0], [np.nan, 50.0]])
    chk = check_orders({"AAA": 100.0, "BBB": 100.0}, {}, 1e7, p, 1, RiskLimits())
    assert "AAA" not in chk.deltas and any(b["detail"]["rule"] == "halted" for b in chk.blocks)
    s = held({}, {})
    s.orders = {"AAA": OpenOrder("o1", "AAA", 10.0, "2026-01-05")}
    r = simulate_day(s, p, 1, flat_params(), 0.0, VOL)
    rej = next(e for e in r.events if e["kind"] == "reject")
    assert rej["detail"]["halted"] and r.state.orders["AAA"].quantity == 10.0


# ---------------------------------------------------------------- F10 lots
def test_lot_rounding_records_the_residual_and_cancels_sub_lot_orders() -> None:
    p = panel([[100.0, 50.0], [100.0, 50.0]], adv=(1_234.0, 50_000.0))
    s = held({}, {})
    s.orders = {"AAA": OpenOrder("o1", "AAA", 500.0, "2026-01-05"), "BBB": OpenOrder("o2", "BBB", 40.0, "2026-01-05")}
    r = simulate_day(s, p, 1, flat_params(lot_size=100), 0.0, VOL)
    fill = next(e for e in r.events if e["kind"] == "fill")
    assert fill["quantity"] == 100.0  # cap 123.4 -> one lot
    res = next(e for e in r.events if e["kind"] == "lot_residual")
    assert res["quantity"] == pytest.approx(23.4)
    assert r.state.orders["AAA"].quantity == pytest.approx(400.0)
    assert any(e["kind"] == "cancel" and e["detail"]["reason"] == "below_lot" for e in r.events)
    assert "BBB" not in r.state.orders


def test_lot_above_session_cap_cancels_instead_of_expiring() -> None:
    # 0.10.0 X6: cap 12.34 < one lot of 100, so the order can never fill; cancel it now
    p = panel([[100.0, 50.0], [100.0, 50.0]], adv=(123.4, 50_000.0))
    s = held({}, {})
    s.orders = {"AAA": OpenOrder("o1", "AAA", 500.0, "2026-01-05")}
    r = simulate_day(s, p, 1, flat_params(lot_size=100), 0.0, VOL)
    assert any(e["kind"] == "cancel" and e["detail"]["reason"] == "lot_above_cap" for e in r.events)
    assert "AAA" not in r.state.orders and not any(e["kind"] == "fill" for e in r.events)


# ---------------------------------------------------------------- F11 LOB engine
def test_lob_engine_measures_the_cost_and_is_deterministic() -> None:
    p = panel([[100.0, 50.0], [100.0, 50.0]])
    s = held({}, {})
    s.orders = {"AAA": OpenOrder("o1", "AAA", 1_000.0, "2026-01-05")}
    r1 = simulate_day(s, p, 1, flat_params(engine="lob"), 0.0, VOL)
    r2 = simulate_day(s, p, 1, flat_params(engine="lob"), 0.0, VOL)
    f = next(e for e in r1.events if e["kind"] == "fill")
    assert f["detail"]["engine"] == "lob" and f["detail"]["sim_filled"] > 0
    assert r1.events == r2.events
    with pytest.raises(ValueError):
        flat_params(engine="vwap")


def test_config_execution_block_reaches_fill_params() -> None:
    fp = PaperConfig(execution={"moc_auction": True, "lot_size": 1}).fill_params()
    assert fp.moc_auction and fp.lot_size == 1
    with pytest.raises(ValueError):
        PaperConfig(execution={"bogus": 1}).fill_params()


# ---------------------------------------------------------------- F10 ledger body v2
def _insert_v1(db: Database, led: PaperLedger, book: str, cash_delta: float) -> None:
    """A row exactly as 0.8.0 wrote it (float money, no money_v2)."""

    def go(conn):  # type: ignore[no-untyped-def]
        prev = led._head(conn, "SELECT hash FROM paper_ledger WHERE book=? ORDER BY seq DESC LIMIT 1", (book,))
        row = {"book": book, "date": "2026-01-05", "kind": "financing", "order_id": None, "symbol": None,
               "quantity": None, "price": None, "cash_delta": cash_delta, "cost": 0.0, "payload_json": "{}"}
        digest, alg = led._link(conn, prev, _ledger_body(row))
        conn.execute("INSERT INTO paper_ledger(book, date, kind, order_id, symbol, quantity, price, cash_delta, cost, "
                     "payload_json, prev_hash, hash, hash_alg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (*[row[k] for k in LEDGER_FIELDS], row["payload_json"], prev, digest, alg))

    db.run(go)


def test_v2_rows_continue_a_v1_chain_and_column_edits_break_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with Database(tmp_path / "p.db") as db:
        led = PaperLedger(db)
        _insert_v1(db, led, "b", 0.1 + 0.2)
        db.run(lambda c: led.append_events(c, "b", "2026-01-06", [
            {"kind": "fill", "symbol": "AAA", "quantity": 150.0, "price": 187.123456789,
             "cash_delta": -(150 * 187.123456789 + 2.5), "cost": 2.5, "detail": {"x": 1}}]))
        ok, _, reason = led.verify_keyed("ledger", "b")
        assert ok, reason
        rows = led.events("b")
        assert MONEY_KEY not in rows[0]["payload_json"]
        m = json.loads(rows[1]["payload_json"])[MONEY_KEY]
        assert m == {"cash_delta": -28071018518, "cost": 2_500_000, "price": 187123457}
        assert rows[1]["cash_delta"] == -28071.018518 and to_micros(rows[1]["cash_delta"]) == m["cash_delta"]
        seq = rows[1]["seq"]
        db.run(lambda c: c.execute("DROP TRIGGER paper_ledger_no_update"))
        db.run(lambda c: c.execute("UPDATE paper_ledger SET cash_delta = cash_delta + 1 WHERE seq=?", (seq,)))
        ok, where, _ = led.verify_keyed("ledger", "b")
        assert not ok and where == f"ledger seq {seq}"


def test_reserved_money_key_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with Database(tmp_path / "p.db") as db:
        led = PaperLedger(db)
        with pytest.raises(ValueError):
            db.run(lambda c: led.append_events(c, "b", "2026-01-06", [
                {"kind": "mark", "cash_delta": 0.0, "detail": {MONEY_KEY: {}}}]))


def _insert_v1_day(db: Database, led: PaperLedger, book: str, date: str, equity: float, state: BookState) -> None:
    """A day row exactly as 0.8.0 wrote it (float equity, no money_v2 in the snapshot)."""
    from ceap.paper.ledger import _day_body
    from ceap.util.canonical import canonical_json

    def go(conn):  # type: ignore[no-untyped-def]
        prev = led._head(conn, "SELECT hash FROM paper_days WHERE book=? ORDER BY date DESC LIMIT 1", (book,))
        d = {"date": date, "snapshot_json": canonical_json({"date": date, "equity": equity}).decode(),
             "state_json": canonical_json(state.to_json()).decode(), "ledger_head": "x", "equity": equity}
        digest, alg = led._link(conn, prev, _day_body(book, d))
        conn.execute("INSERT INTO paper_days(book, date, equity, snapshot_json, state_json, ledger_head, prev_hash, "
                     "hash, recorded_at, hash_alg) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (book, date, equity, d["snapshot_json"], d["state_json"], "x", prev, digest, "t", alg))

    db.run(go)


def test_day_rows_v1_then_v2_verify_reconcile_in_integers_and_resist_column_edits(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with Database(tmp_path / "p.db") as db:
        led = PaperLedger(db)
        s0 = BookState.new(1_000.0)
        _insert_v1_day(db, led, "b", "2026-01-05", 1_000.0 + 1e-7, s0)
        s1 = BookState.new(1_000.0)
        s1.cash = s1.equity = 1_000.0 + 0.1 + 0.2
        db.run(lambda c: led.append_events(c, "b", "2026-01-06", [{"kind": "financing", "cash_delta": 0.1},
                                                                  {"kind": "financing", "cash_delta": 0.2}]))
        db.run(lambda c: led.append_day(c, "b", {"date": "2026-01-06", "equity": s1.equity}, s1, "h", "t"))
        ok, _, reason = led.verify_keyed("days", "b")
        assert ok, reason
        rows = led.chain_rows("days", "b")
        assert MONEY_KEY not in rows[0]["snapshot_json"]
        assert json.loads(rows[1]["snapshot_json"])[MONEY_KEY] == {"equity": 1_000_300_000, "cash": 1_000_300_000}
        assert rows[1]["equity"] == 1000.3 and MONEY_KEY not in led.days("b")[1]
        rec = led.reconcile("b")
        assert rec["ok"], rec
        db.run(lambda c: c.execute("DROP TRIGGER paper_days_no_update"))
        db.run(lambda c: c.execute("UPDATE paper_days SET equity = equity + 1 WHERE date='2026-01-06'"))
        ok, where, _ = led.verify_keyed("days", "b")
        assert not ok and where == "day 2026-01-06"
