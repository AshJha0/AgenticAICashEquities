"""Build ``data/reference/ff_factors.csv`` (monthly FF5 + UMD + RF) from the Kenneth R. French data
library (free, no sign-up; R5 item 5.5). Network script; tests never run it.

Source: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
Files: F-F_Research_Data_5_Factors_2x3_CSV.zip and F-F_Momentum_Factor_CSV.zip (monthly, percent).
Output is decimal per month, 2003-11 to 2023-12 only (the pseudo-holdout months are not written).

    python scripts/fetch_ff_factors.py
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FF5 = BASE + "F-F_Research_Data_5_Factors_2x3_CSV.zip"
UMD = BASE + "F-F_Momentum_Factor_CSV.zip"
OUT = Path(__file__).resolve().parents[1] / "data" / "reference" / "ff_factors.csv"
FIRST, LAST = 200311, 202312


def _monthly(url: str, ncols: int) -> dict[int, list[float]]:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https URL
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read(zf.namelist()[0]).decode("latin-1")
    out: dict[int, list[float]] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == ncols + 1 and parts[0].isdigit() and len(parts[0]) == 6:
            ym = int(parts[0])
            if ym not in out:  # the monthly block comes first; the annual block uses 4-digit keys
                out[ym] = [float(p) / 100.0 for p in parts[1:]]
    return out


def main() -> None:
    ff5 = _monthly(FF5, 6)  # Mkt-RF SMB HML RMW CMA RF
    umd = _monthly(UMD, 1)
    months = sorted(m for m in ff5 if FIRST <= m <= LAST and m in umd)
    with OUT.open("w", newline="\n", encoding="utf-8") as fh:
        fh.write("# Monthly Fama-French 5 factors (2x3), momentum (UMD) and RF, decimal per month.\n")
        fh.write("# Source: Kenneth R. French data library, Tuck School of Business, Dartmouth.\n")
        fh.write("# https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html\n")
        fh.write("month,mkt_rf,smb,hml,rmw,cma,rf,umd\n")
        for m in months:
            v = ff5[m]
            fh.write(
                f"{m // 100:04d}-{m % 100:02d}," + ",".join(f"{x:.6f}" for x in v) + f",{umd[m][0]:.6f}\n"
            )
    print(f"wrote {len(months)} months to {OUT}")


if __name__ == "__main__":
    main()
