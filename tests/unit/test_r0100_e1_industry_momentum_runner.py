"""Study E1 runner, statistics and ETF gate on synthetic Ken French files (0.10.0 E1 coverage).

No network and no real data: the four cached zips are written to ``tmp_path`` with a planted
momentum structure, ``yfinance`` is replaced by an in-memory module, and the holdout ledger used by
the crash-rerun path is a temporary file. The real ledger is only read, never written.
"""

from __future__ import annotations

import functools
import io
import json
import sys
import types
import zipfile

import numpy as np
import pandas as pd
import pytest

from ceap.data.real.prereg import PreregistrationError
from ceap.studies import industry_momentum as im

FILLERS = [f"Fill{i}" for i in range(49 - sum(len(v) for v in im.ETF_MAP.values()))]
INDUSTRIES = [n for names in im.ETF_MAP.values() for n in names] + FILLERS
MONTHS = pd.period_range("1985-01", "2017-12", freq="M")


def _csv(columns: list[str], values: np.ndarray, months: pd.PeriodIndex = MONTHS) -> str:
    lines = ["Synthetic Ken French style file", "," + ",".join(columns)]
    for m, row in zip(months, values, strict=True):
        lines.append(f"{m.year}{m.month:02d}," + ",".join(f"{v:.4f}" for v in row))
    lines += ["", " Annual Factors", "," + ",".join(columns), "1985," + ",".join("1.0" for _ in columns)]
    return "\n".join(lines) + "\n"


def _zip(path, text: str) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.CSV", text)
    path.write_bytes(buf.getvalue())


def _industry_returns(seed: int = 7) -> np.ndarray:
    """Persistent drift from -1 %/month to +2 %/month across industries, plus 3 % noise (percent units)."""
    rng = np.random.default_rng(seed)
    drift = np.linspace(-1.0, 2.0, len(INDUSTRIES))
    return drift + rng.normal(0.0, 3.0, (len(MONTHS), len(INDUSTRIES)))


@pytest.fixture
def cache(tmp_path):
    rng = np.random.default_rng(11)
    n = len(MONTHS)
    _zip(tmp_path / im.IND49, _csv(INDUSTRIES, _industry_returns()))
    ff3 = rng.normal([0.6, 0.1, 0.2, 0.3], [4.0, 2.0, 2.0, 0.1], (n, 4))
    _zip(tmp_path / im.FF3, _csv(["Mkt-RF", "SMB", "HML", "RF"], ff3))
    ff5 = rng.normal(0.2, 2.0, (n, 6))
    _zip(tmp_path / im.FF5, _csv(["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"], ff5))
    _zip(tmp_path / im.UMD, _csv(["Mom"], rng.normal(0.5, 3.0, (n, 1))))
    rec = tmp_path / "hashes.json"
    im.record_hashes(tmp_path, rec)
    return tmp_path, rec


def _use_cache_hashes(monkeypatch, rec) -> None:
    # run() checks the repo's recorded hashes; point that check at the synthetic record instead
    monkeypatch.setattr(im, "verify_hashes", functools.partial(im.verify_hashes, rec=rec))


def _fake_yfinance(monkeypatch, download) -> None:
    mod = types.ModuleType("yfinance")
    mod.download = download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", mod)


def test_in_sample_run_never_sees_holdout_months(cache, monkeypatch):
    cache_dir, rec = cache
    _use_cache_hashes(monkeypatch, rec)
    res = im.run(cache_dir=cache_dir)
    assert set(res) == {"prereg_sha256", "data_sha256", "n_industries", "in_sample"}  # no holdout keys
    assert res["n_industries"] == 49
    assert res["data_sha256"] == json.loads(rec.read_text(encoding="utf-8"))["sha256"]
    ins = res["in_sample"]
    # data start 1985-01 and a 6-month formation: the first holding month is 1985-07
    assert ins["months_scored"] == len(pd.period_range("1985-07", "1989-12", freq="M"))
    assert ins["months_dropped"] == [] and ins["breadth_void"] is False
    assert ins["net10"]["mean_monthly"] < ins["gross"]["mean_monthly"]
    assert ins["net20"]["mean_monthly"] < ins["net10"]["mean_monthly"]
    lo, hi = ins["net10"]["sharpe_ci95"]
    assert lo < ins["net10"]["sharpe_ann"] < hi
    assert set(ins["alpha"]) == {"ff3_umd", "ff5_umd"}
    assert set(ins["alpha"]["ff5_umd"]["betas"]) == {"Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"}
    assert ins["alpha"]["ff3_umd"]["end"] == "1989-12"
    # the planted drift spread makes the gross spread clearly positive
    assert ins["gross"]["mean_monthly"] > 0.01 and ins["gross"]["nw_t"] > 3


def test_crash_rerun_opens_holdout_and_live_windows(cache, monkeypatch, tmp_path_factory):
    cache_dir, rec = cache
    _use_cache_hashes(monkeypatch, rec)
    study = tmp_path_factory.mktemp("study")
    ledger = study / "holdout_ledger.json"
    ledger.write_text(json.dumps({"opened": True}), encoding="utf-8")
    monkeypatch.setattr(im, "LEDGER", ledger)
    monkeypatch.setattr(im, "STUDY_DIR", study)

    def unreachable(*_a, **_k):
        raise ConnectionError("no network in tests")

    _fake_yfinance(monkeypatch, unreachable)
    res = im.run(cache_dir=cache_dir, crash_rerun_reason="worker died before writing")
    assert (
        json.loads(ledger.read_text(encoding="utf-8"))["crash_rerun"]["reason"]
        == "worker died before writing"
    )
    assert res["holdout"]["start"] == "1990-01" and res["holdout"]["end"] == "2015-12"
    assert res["holdout"]["months_scored"] == 26 * 12
    assert res["live"]["start"] == "2016-01" and res["live"]["end"] == "2017-12"
    assert res["live"]["months_scored"] == 24
    assert set(res["holdout"]["alpha"]) == {"ff5_umd"}
    assert res["etf_gate"]["status"] == "NOT RUN" and "ConnectionError" in res["etf_gate"]["reason"]
    t = res["holdout"]["net10"]["nw_t"]
    assert res["h1_verdict"] == ("POSITIVE" if t > 1.645 else "NULL") == "POSITIVE"
    with pytest.raises(PreregistrationError, match="crash rerun was used"):
        im.run(cache_dir=cache_dir, crash_rerun_reason="again")


def test_second_holdout_opening_is_refused_before_anything_is_written(cache, monkeypatch):
    cache_dir, rec = cache
    _use_cache_hashes(monkeypatch, rec)
    before = im.LEDGER.read_bytes()
    assert json.loads(before)["opened"] is True  # the committed ledger records the one opening
    with pytest.raises(PreregistrationError, match="second look is not allowed"):
        im.run(open_holdout=True, cache_dir=cache_dir)
    assert im.LEDGER.read_bytes() == before


def test_run_refuses_tampered_cache(cache, monkeypatch):
    cache_dir, rec = cache
    _use_cache_hashes(monkeypatch, rec)
    (cache_dir / im.UMD).write_bytes(b"not the recorded file")
    with pytest.raises(PreregistrationError, match=im.UMD):
        im.run(cache_dir=cache_dir)


def _industry_frame() -> pd.DataFrame:
    return pd.DataFrame(_industry_returns() / 100.0, index=MONTHS, columns=INDUSTRIES)


def _prices(returns: pd.DataFrame) -> pd.DataFrame:
    """Monthly closes whose pct_change reproduces ``returns`` from 1999-01, indexed by month-start dates."""
    sub = returns[(returns.index >= pd.Period("1998-12", "M")) & (returns.index <= pd.Period("2016-01", "M"))]
    px = (1.0 + sub).cumprod() * 100.0
    px.index = sub.index.to_timestamp()
    return px


def test_etf_gate_passes_when_etfs_track_their_industries(monkeypatch):
    ind = _industry_frame()
    mapped = pd.DataFrame({tk: ind[list(names)].mean(axis=1) for tk, names in im.ETF_MAP.items()})
    calls: list[dict] = []

    def download(tickers, **kw):
        calls.append({"tickers": tickers, **kw})
        return {"Close": _prices(mapped)}

    _fake_yfinance(monkeypatch, download)
    gate = im.etf_gate(ind)
    assert gate["status"] == "PASS" and gate["median_corr"] == pytest.approx(1.0)
    assert set(gate["corr"]) == set(im.ETF_MAP) and gate["months"] == "1999-01..2015-12"
    assert calls[0]["tickers"] == list(im.ETF_MAP) and calls[0]["interval"] == "1mo"


def test_etf_gate_fails_on_unrelated_etfs(monkeypatch):
    ind = _industry_frame()
    rng = np.random.default_rng(3)
    noise = pd.DataFrame(
        rng.normal(0, 0.05, (len(MONTHS), len(im.ETF_MAP))), index=MONTHS, columns=list(im.ETF_MAP)
    )
    _fake_yfinance(monkeypatch, lambda *_a, **_k: {"Close": _prices(noise)})
    gate = im.etf_gate(ind)
    assert gate["status"] == "FAIL" and gate["median_corr"] < 0.8


@pytest.mark.parametrize("closes", [None, pd.DataFrame()])
def test_etf_gate_not_run_on_empty_download(monkeypatch, closes):
    _fake_yfinance(monkeypatch, lambda *_a, **_k: {"Close": closes})
    assert im.etf_gate(_industry_frame()) == {"status": "NOT RUN", "reason": "ETF fetch returned no data"}


def test_alpha_regression_recovers_planted_alpha_and_betas():
    rng = np.random.default_rng(5)
    idx = pd.period_range("2000-01", periods=240, freq="M")
    f = pd.DataFrame(rng.normal(0, 0.04, (240, 2)), index=idx, columns=["Mkt-RF", "UMD"])
    y = 0.003 + 0.5 * f["Mkt-RF"] - 0.3 * f["UMD"] + rng.normal(0, 0.001, 240)
    res = im.alpha_regression(y.iloc[5:], f)  # inner join drops the first five factor months
    assert res["n"] == 235 and res["start"] == "2000-06" and res["end"] == "2019-12"
    assert res["alpha_monthly"] == pytest.approx(0.003, abs=2e-4)
    assert res["betas"]["Mkt-RF"] == pytest.approx(0.5, abs=0.01)
    assert res["betas"]["UMD"] == pytest.approx(-0.3, abs=0.01)
    assert res["t_alpha"] > 10


def test_drift_rescales_each_leg_to_gross_one_and_treats_nan_as_flat():
    w = np.array([0.5, 0.5, 0.0, -0.5, -0.5])
    out = im._drift(w, np.array([0.10, -0.10, 0.3, np.nan, 0.20]))
    np.testing.assert_allclose(out, [0.55, 0.45, 0.0, -0.5 / 1.1, -0.6 / 1.1])
    assert out[out > 0].sum() == pytest.approx(1.0) and out[out < 0].sum() == pytest.approx(-1.0)


def test_sharpe_and_state_test_worked_values():
    x = np.array([0.01, 0.03, 0.01, 0.03])
    assert im.sharpe_ann(x) == pytest.approx(0.02 / np.std(x, ddof=1) * np.sqrt(12))
    idx = pd.period_range("2001-01", periods=40, freq="M")
    rng = np.random.default_rng(2)
    down = pd.Series([i % 2 == 0 for i in range(40)], index=idx, dtype=float)
    gross = pd.Series(np.where(down == 1, -0.02, 0.02) + rng.normal(0, 0.002, 40), index=idx)
    res = im.state_test(gross, down)
    assert res["n_up"] == res["n_down"] == 20
    assert res["b_down"] == pytest.approx(-0.04, abs=0.003) and res["h2_reject"] is True


def test_git_head_outside_a_repository_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(im, "REPO_ROOT", tmp_path / "missing")
    assert im._git_head() == "unknown"


def test_crash_rerun_needs_a_recorded_opening(tmp_path):
    with pytest.raises(PreregistrationError, match="no holdout opening recorded"):
        im.record_crash_rerun(tmp_path / "absent.json", tmp_path / "result.json", "x")
