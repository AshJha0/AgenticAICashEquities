"""R5 real-data study: offline tests with small synthetic fixtures (no network, no vendor data)."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from ceap.analytics import factor_check as fc
from ceap.analytics import real_costs as rc
from ceap.data.real import momentum as mom
from ceap.data.real import prereg
from ceap.data.real.prices import DailySeries, load_yahoo, parse_stooq, parse_yahoo
from ceap.data.real.quality import check_series, cross_check
from ceap.data.real.universe_pit import (
    Event,
    MapRow,
    PointInTimeUniverse,
    load_events,
    load_ticker_map,
    normalise_ticker,
    stooq_symbol,
    yahoo_symbol,
)

# ---------------------------------------------------------------- 5.1 preregistration lock


def _lock(tmp: Path, sha: str) -> Path:
    p = tmp / "lock.json"
    p.write_text(
        json.dumps(
            {
                "sha256": sha,
                "windows": {
                    "warmup_start": "2003-11-01",
                    "eval_start": "2005-01-01",
                    "eval_end": "2023-12-31",
                    "holdout_start": "2024-01-01",
                    "holdout_end": "2025-12-31",
                },
            }
        ),
        encoding="utf-8",
    )
    return p


def test_committed_prereg_matches_lock() -> None:
    lock = prereg.require_prereg_lock()
    assert lock.eval_end == date(2023, 12, 31) and lock.holdout_start == date(2024, 1, 1)


def test_edited_prereg_is_refused(tmp_path: Path) -> None:
    doc = tmp_path / "prereg.md"
    doc.write_text("hypothesis\n", encoding="utf-8")
    lock = _lock(tmp_path, prereg.file_sha256(doc))
    assert prereg.require_prereg_lock(doc, lock).sha256
    doc.write_text("hypothesis, edited after seeing data\n", encoding="utf-8")
    with pytest.raises(prereg.PreregistrationError, match="hash mismatch"):
        prereg.require_prereg_lock(doc, lock)


def test_hash_ignores_line_endings(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x\ny\n")
    b.write_bytes(b"x\r\ny\r\n")
    assert prereg.file_sha256(a) == prereg.file_sha256(b)


def test_missing_lock_refused(tmp_path: Path) -> None:
    with pytest.raises(prereg.PreregistrationError):
        prereg.require_prereg_lock(tmp_path / "p.md", tmp_path / "none.json")


def test_holdout_read_refused(tmp_path: Path) -> None:
    (tmp_path / "yahoo").mkdir()
    (tmp_path / "yahoo" / "AAA.csv").write_text(
        "date,close,adj_close,volume\n2005-01-03,1,1,1\n", encoding="utf-8"
    )
    with pytest.raises(prereg.PreregistrationError, match="pseudo-holdout"):
        load_yahoo("AAA", end=date(2024, 1, 2), cache=tmp_path)


def test_loader_clips_to_window(tmp_path: Path) -> None:
    (tmp_path / "yahoo").mkdir()
    rows = "date,close,adj_close,volume\n2003-01-02,1,1,1\n2005-01-03,2,2,1\n2024-01-02,3,3,1\n"
    (tmp_path / "yahoo" / "AAA.csv").write_text(rows, encoding="utf-8")
    s = load_yahoo("AAA", cache=tmp_path)
    assert s is not None and s.dates == [date(2005, 1, 3)]


# ---------------------------------------------------------------- 5.2 universe


def test_ticker_formats() -> None:
    assert normalise_ticker("brk-b") == normalise_ticker("BRK/B") == "BRK.B"
    assert yahoo_symbol("BRK.B") == "BRK-B" and stooq_symbol("BRK.B") == "brk-b.us"


def test_membership_replay_and_renames() -> None:
    ev = [Event(date(2004, 1, 2), "initial", t) for t in ("A", "B", "FB")]
    ev += [Event(date(2010, 6, 1), "remove", "B"), Event(date(2010, 6, 1), "add", "C")]
    u = PointInTimeUniverse(ev, [MapRow("FB", "META", date(2012, 5, 18), date(2022, 6, 8))])
    assert u.members(date(2010, 5, 31)) == {"A", "B", "FB"}
    assert u.members(date(2010, 6, 1)) == {"A", "C", "FB"}
    assert u.ever_members(date(2005, 1, 1), date(2020, 1, 1)) == {"A", "B", "C", "FB"}
    assert u.vendor_symbol("FB", date(2015, 1, 1)) == "META" and u.vendor_symbol("A", date(2015, 1, 1)) == "A"
    with pytest.raises(ValueError):
        u.members(date(2003, 1, 1))


def test_committed_reference_universe_is_sane() -> None:
    u = PointInTimeUniverse(load_events(), load_ticker_map())
    for d in (date(2005, 1, 3), date(2014, 6, 2), date(2023, 12, 29)):
        assert 480 <= len(u.members(d)) <= 510
    assert "META" in u.members(date(2023, 12, 29)) and "LEH" not in u.members(date(2009, 1, 2))


def test_attribution_file_present() -> None:
    text = (prereg.REPO_ROOT / "data" / "reference" / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "CC BY-SA" in text and "Kenneth R. French" in text and "fja05680" in text


# ---------------------------------------------------------------- 5.3 parsing and quality gate


def _series(sym: str, closes: list[float], start: date = date(2010, 1, 4), vol: float = 1e6) -> DailySeries:
    days, d = [], start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return DailySeries(sym, "t", days, closes, [vol] * len(closes))


def test_parse_both_stooq_formats() -> None:
    a = parse_stooq("Date,Open,High,Low,Close,Volume\n2010-01-04,1,1,1,10,5\n2010-01-05,1,1,1,11,6\n", "x")
    b = parse_stooq(
        "<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"
        "X.US,D,20100104,000000,1,1,1,10,5,0\nX.US,D,20100105,000000,1,1,1,11,6,0\n",
        "x",
    )
    assert a.dates == b.dates and a.close == b.close == [10.0, 11.0]
    y = parse_yahoo("date,close,adj_close,volume\n2010-01-04,10,9,5\n", "x")
    assert y.close == [9.0]


def test_clean_series_passes() -> None:
    rep = check_series(_series("AAA", [100 + i * 0.1 for i in range(30)]))
    assert not rep.hard_fail and not rep.stale_runs and not rep.big_moves and rep.zero_volume_days == 0


def test_planted_defects_are_caught() -> None:
    s = _series("AAA", [100.0] * 6 + [101.0, 160.0, 161.0])
    s.dates[3] = s.dates[2]  # duplicate date
    s.volume[1] = 0.0
    rep = check_series(s)
    assert rep.duplicate_dates and rep.stale_runs and rep.zero_volume_days == 1 and rep.big_moves
    assert rep.hard_fail
    rev = _series("AAA", [1.0, 2.0, 3.0])
    rev.dates.reverse()
    assert check_series(rev).unordered


def test_calendar_alignment() -> None:
    s = _series("AAA", [1.0, 1.1, 1.2])
    cal = {s.dates[0], s.dates[2], s.dates[2] + timedelta(days=1)}
    rep = check_series(s, cal)
    assert rep.off_calendar == [s.dates[1]] and rep.missing_calendar_days == 0


def test_known_split_unadjusted_fails_adjusted_passes() -> None:
    start = date(2014, 6, 2)
    raw = _series("AAPL", [645.0, 647.0, 650.0, 648.0, 645.6, 92.2, 93.7], start)  # 7:1 on 2014-06-09
    assert raw.dates[5] == date(2014, 6, 9)
    assert check_series(raw).split_failures == [date(2014, 6, 9)]
    adj = _series("AAPL", [92.1, 92.4, 92.9, 92.6, 92.2, 92.2, 93.7], start)
    assert check_series(adj).split_failures == []


def test_cross_check_on_returns_not_levels() -> None:
    a = _series("AAA", [100.0, 101.0, 102.0, 103.0])
    b = _series("AAA", [50.0, 50.5, 51.0, 51.5])  # half the level, same returns
    cc = cross_check(a, b)
    assert cc.common_days == 3 and cc.agreement == 1.0
    c = _series("AAA", [100.0, 101.0, 150.0, 151.5])  # planted unconfirmed 48 % jump
    cc2 = cross_check(c, a)
    assert cc2.agreement < 1.0 and cc2.unconfirmed_big_moves == [c.dates[2]]


# ---------------------------------------------------------------- 5.4 costs


def test_half_spread_schedule() -> None:
    assert rc.half_spread(date(2009, 12, 31)) == 0.0010 and rc.half_spread(date(2010, 1, 4)) == 0.0005
    with pytest.raises(ValueError):
        rc.half_spread(date(2004, 12, 31))


def test_impact_known_answer() -> None:
    # 0.7 * 2 % * sqrt(1M / 100M) = 0.0014 (14 bps)
    assert rc.impact_rate(0.02, 1e6, 1e8) == pytest.approx(0.0014)
    assert rc.impact_rate(0.02, 0.0, 1e8) == 0.0


def test_rebalance_cost_known_answer_and_missing_adv() -> None:
    names = {"A": rc.NameInput(0.01, 0.02, 1e8), "B": rc.NameInput(-0.01, 0.02, None)}
    c = rc.rebalance_cost(date(2012, 1, 3), names)
    assert c.spread == pytest.approx(0.02 * 0.0005)
    assert c.impact == pytest.approx(0.02 * 0.0014)  # B gets the book's 95th percentile (A's rate)


def test_borrow_and_htb() -> None:
    dv = {f"N{i:02d}": float(i) for i in range(40)}
    htb = rc.hard_to_borrow_set(dv)
    assert htb == {"N00", "N01"}
    fee = rc.borrow_cost({"N00": 0.5, "N39": 0.5}, htb, 30)
    assert fee == pytest.approx((0.5 * 0.053 + 0.5 * 0.003) * 30 / 360)


def test_breakeven_and_sensitivity() -> None:
    assert rc.breakeven_cost_bps([0.01, 0.01], [1.0, 1.0]) == pytest.approx(100.0)
    table = rc.sensitivity_table([0.01, 0.01], [1.0, 1.0], (0, 100))
    assert table[0][1] == pytest.approx(0.12) and table[1][1] == pytest.approx(0.0)


def test_corwin_schultz_nonnegative_and_zero_without_range() -> None:
    assert rc.corwin_schultz_spread([10, 10], [10, 10]) == [0.0]
    s = rc.corwin_schultz_spread([10.2, 10.3, 10.1], [9.9, 10.0, 9.8])
    assert all(x >= 0 for x in s)


# ---------------------------------------------------------------- momentum book and 5.5 gate


def _synthetic_book(n_names: int = 120, n_days: int = 700, seed: int = 7):  # type: ignore[no-untyped-def]
    rng = np.random.default_rng(seed)
    days, d = [], date(2004, 1, 2)
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    drift = np.linspace(-0.001, 0.001, n_names)
    px = {
        f"S{i:03d}": 50 * np.exp(np.cumsum(drift[i] + 0.01 * rng.standard_normal(n_days)))
        for i in range(n_names)
    }
    return days, px


def test_momentum_book_picks_winners_and_respects_membership() -> None:
    days, px = _synthetic_book()
    excluded = "S119"  # strongest drift, but never a member
    members = frozenset(k for k in px if k != excluded)
    months = mom.build_months(days, lambda t, _d: px[t], lambda _d: members, days[260], days[-1])
    assert months and all(excluded not in m.longs for m in months)
    assert all(len(m.longs) == len(m.shorts) == m.eligible // 10 for m in months)
    assert np.mean([m.gross for m in months]) > 0
    assert months[0].formation.month != months[0].execution.month or months[0].execution > months[0].formation


def test_delisted_name_earns_cash_after_last_close() -> None:
    days, px = _synthetic_book(n_names=110)
    months = mom.build_months(days, lambda t, _d: px[t], lambda _d: frozenset(px), days[260], days[-1])
    m = months[0]
    victim = m.longs[0]
    e = days.index(m.execution)
    cut = px[victim].copy()
    cut[e + 3 :] = np.nan
    px2 = dict(px) | {victim: cut}
    m2 = mom.build_months(days, lambda t, _d: px2[t], lambda _d: frozenset(px2), days[260], days[-1])[0]
    expect = cut[e + 2] / cut[e] - 1
    others = [px[t][days.index(m.exit)] / px[t][e] - 1 for t in m.longs[1:]]
    assert m2.long_ret == pytest.approx((expect + sum(others)) / len(m.longs))


def test_coverage_counts_usable_name_months() -> None:
    m = mom.Month(date(2005, 1, 31), date(2005, 2, 1), date(2005, 3, 1), 90, 100, 96, 0, 0, 0, (), ())
    assert mom.coverage([m, m]) == (0.96, 0.96)


def _ff(months: list[str], umd: np.ndarray) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(1)
    return {
        k: {"umd": float(u), **{f: float(rng.normal(0, 0.03)) for f in fc.FACTORS if f != "umd"}}
        for k, u in zip(months, umd, strict=True)
    }


def test_replication_gate_passes_and_blocks() -> None:
    keys = [f"{2005 + i // 12}-{i % 12 + 1:02d}" for i in range(120)]
    rng = np.random.default_rng(3)
    umd = rng.normal(0.005, 0.04, 120)
    ff = _ff(keys, umd)
    good = fc.replication_gate(dict(zip(keys, umd * 1.2 + rng.normal(0, 0.02, 120), strict=True)), ff)
    assert good.passed and good.outcome == "PASS" and good.loadings["umd"] > 0.8
    bad = fc.replication_gate(dict(zip(keys, rng.normal(0, 0.04, 120), strict=True)), ff)
    assert not bad.passed and bad.outcome == "PIPELINE SUSPECT"
    with pytest.raises(ValueError):
        fc.replication_gate({keys[0]: 0.1}, ff)


def test_per_year_and_crash_table() -> None:
    keys = ["2009-03", "2009-04", "2020-11"]
    ff = {k: {"umd": -0.1} for k in keys}
    gross = {"2009-03": -0.2, "2009-04": -0.3, "2020-11": -0.1}
    yr = dict((y, (a, b)) for y, a, b in fc.per_year(gross, ff))
    assert yr["2009"][0] == pytest.approx(0.8 * 0.7 - 1) and yr["2009"][1] == pytest.approx(0.81 - 1)
    assert [r[0] for r in fc.crash_table(gross, ff)] == keys


def test_committed_ff_factors_stop_before_holdout() -> None:
    ff = fc.load_ff()
    assert max(ff) == "2023-12" and min(ff) <= "2004-12"
    assert all(math.isfinite(v["umd"]) for v in ff.values())


def test_study_recorded_void_and_holdout_unspent() -> None:
    lock = json.loads(prereg.LOCK_PATH.read_text(encoding="utf-8"))
    assert lock["status"]["outcome"] == "VOID" and lock["status"]["pseudo_holdout"] == "UNSPENT"
    assert lock["status"]["test_statistic_computed"] is False
    doc = (prereg.REPO_ROOT / "docs" / "research" / "result_momentum_2026-11.md").read_text(encoding="utf-8")
    assert lock["sha256"] in doc and "fb9858e" in doc and "UNSPENT" in doc
    with pytest.raises(prereg.PreregistrationError):
        prereg.check_in_sample(date(2024, 1, 2), prereg.require_prereg_lock())
