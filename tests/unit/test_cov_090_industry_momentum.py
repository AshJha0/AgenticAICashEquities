"""Coverage for the industry-momentum study's data guards: cached-file hashes, zip reading, the
monthly-block parser's error path and the two-state H2 regression. Synthetic bytes only."""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from ceap.data.real.prereg import PreregistrationError
from ceap.studies import industry_momentum as im

CSV = "Title line\n,Ind1,Ind2\n202001,1.00,-2.00\n202002,-99.99,0.50\n\n Annual\n,Ind1,Ind2\n2020,9,9\n"


def _write_cache(tmp_path):
    for n in im.KF_FILES:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("x.CSV", CSV)
        (tmp_path / n).write_bytes(buf.getvalue())


def test_hashes_round_trip_and_tamper_is_refused(tmp_path):
    _write_cache(tmp_path)
    rec = tmp_path / "hashes.json"
    hashes = im.record_hashes(tmp_path, rec)
    assert set(hashes) == set(im.KF_FILES) and all(len(h) == 64 for h in hashes.values())
    assert json.loads(rec.read_text(encoding="utf-8"))["sha256"] == hashes
    assert im.verify_hashes(tmp_path, rec) == hashes
    first = im.KF_FILES[0]
    (tmp_path / first).write_bytes(b"tampered")
    with pytest.raises(PreregistrationError, match=f"cached {first} hash"):
        im.verify_hashes(tmp_path, rec)


def test_read_zip_parses_first_monthly_block_only(tmp_path):
    _write_cache(tmp_path)
    df = im.read_zip(im.KF_FILES[0], tmp_path)
    assert list(df.columns) == ["Ind1", "Ind2"]
    assert [str(p) for p in df.index] == ["2020-01", "2020-02"]
    # percent -> decimal; -99.99 is a missing value
    assert df.loc[pd.Period("2020-01", "M"), "Ind2"] == pytest.approx(-0.02)
    assert np.isnan(df.loc[pd.Period("2020-02", "M"), "Ind1"])


@pytest.mark.parametrize("text", ["no header here\n202001,1,2\n", "x\n,A,B\nnot,a,row\n"])
def test_parse_without_a_block_raises(text):
    with pytest.raises(ValueError, match="no monthly block"):
        im.parse_first_monthly_block(text)


def test_state_test_two_states_worked_example():
    idx = pd.period_range("2020-01", periods=8, freq="M")
    # UP months return 0.02, DOWN months -0.01 (plus a tiny alternating term so residuals are not 0)
    down = pd.Series([0, 1, 0, 1, 0, 1, 0, 1], index=idx)
    eps = np.array([1, -1, -1, 1, 1, -1, -1, 1]) * 1e-4
    gross = pd.Series(np.where(down == 1, -0.01, 0.02) + eps, index=idx)
    out = im.state_test(gross, down)
    assert (out["n_up"], out["n_down"]) == (4, 4)
    assert out["mean_up"] == pytest.approx(0.02) and out["mean_down"] == pytest.approx(-0.01)
    # OLS on [1, d]: the DOWN coefficient is the mean difference, -0.03, strongly negative
    assert out["b_down"] == pytest.approx(-0.03)
    assert out["t_b_down"] < -1.645 and out["h2_reject"] is True
