"""Build ``data/reference/rf.csv`` from the Kenneth R. French data library (free, no sign-up).

Source: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
File:   F-F_Research_Data_Factors_daily_CSV.zip (column RF, percent per day).

Usage (the maintainer runs it once and commits the small CSV with this attribution):

    python scripts/fetch_rf.py

Until the CSV is committed the backtest uses rf = 0 and says so in ``assumptions["risk_free"]``;
for the dollar-neutral books in the scenario catalogue the short rebate offsets rf, so only
long-only Sharpe ratios move when it lands.
"""

from __future__ import annotations

import csv
import io
import urllib.request
import zipfile
from pathlib import Path

URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip"
OUT = Path(__file__).resolve().parents[1] / "data" / "reference" / "rf.csv"
FIRST = 20000101


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as resp:  # noqa: S310 - fixed https URL
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read(zf.namelist()[0]).decode("latin-1")
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5 and parts[0].isdigit() and len(parts[0]) == 8 and int(parts[0]) >= FIRST:
            d = parts[0]
            rows.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(parts[4]) / 100.0))
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        fh.write("# Daily risk-free rate (one-month T-bill), decimal per day.\n")
        fh.write("# Source: Kenneth R. French data library, F-F_Research_Data_Factors_daily (column RF).\n")
        fh.write("# https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(("date", "rf"))
        w.writerows((d, f"{v:.8f}") for d, v in rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
