"""Daily price loaders for the real-data study (R5 item 5.3). No network access here.

Two vendors, both read from the local cache ``data/real_cache/`` (gitignored, never committed):

- **Stooq**. Stooq's download endpoint now answers scripted requests with a JavaScript
  proof-of-work browser check. CEAP does not solve or bypass bot checks (D2), so Stooq files are only
  ever placed in ``data/real_cache/stooq/`` by a person downloading them in a browser. Both Stooq
  formats are parsed: the per-symbol CSV (``Date,Open,High,Low,Close,Volume``) and the bulk ASCII
  format (``<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>``).
- **yfinance** (Yahoo Finance), fetched by ``scripts/download_daily.py`` into
  ``data/real_cache/yahoo/<SYMBOL>.csv`` with columns ``date,close,adj_close,volume``.

Every loader calls :func:`require_prereg_lock` first and refuses dates past the in-sample end.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ceap.data.real.prereg import PreregLock, check_in_sample, require_prereg_lock
from ceap.paths import data_path

CACHE = data_path("real_cache")


@dataclass(frozen=True)
class DailySeries:
    symbol: str
    vendor: str
    dates: list[date]
    close: list[float]  # adjusted close used for returns
    volume: list[float]

    def returns(self) -> dict[date, float]:
        return {
            self.dates[i]: self.close[i] / self.close[i - 1] - 1.0
            for i in range(1, len(self.dates))
            if self.close[i - 1] > 0
        }


def _clip(series: DailySeries, lock: PreregLock, end: date | None) -> DailySeries:
    stop = end or lock.eval_end
    check_in_sample(stop, lock)
    keep = [i for i, d in enumerate(series.dates) if lock.warmup_start <= d <= stop]
    return DailySeries(
        series.symbol,
        series.vendor,
        [series.dates[i] for i in keep],
        [series.close[i] for i in keep],
        [series.volume[i] for i in keep],
    )


def parse_stooq(text: str, symbol: str) -> DailySeries:
    """Parse either Stooq format; rows are returned in file order (the quality gate checks order)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError(f"empty Stooq file for {symbol}")
    head = [h.strip("<>").lower() for h in lines[0].split(",")]
    rows = list(csv.reader(lines[1:]))
    idx = {h: i for i, h in enumerate(head)}
    dates, close, vol = [], [], []
    for r in rows:
        raw = r[idx["date"]]
        d = datetime.strptime(raw, "%Y%m%d").date() if "-" not in raw else date.fromisoformat(raw)
        dates.append(d)
        close.append(float(r[idx["close"]]))
        vk = "vol" if "vol" in idx else "volume"
        vol.append(float(r[idx[vk]]) if vk in idx and r[idx[vk]] else 0.0)
    return DailySeries(symbol, "stooq", dates, close, vol)


def parse_yahoo(text: str, symbol: str) -> DailySeries:
    rows = list(csv.DictReader(text.splitlines()))
    return DailySeries(
        symbol,
        "yahoo",
        [date.fromisoformat(r["date"]) for r in rows],
        [float(r["adj_close"]) for r in rows],
        [float(r["volume"] or 0) for r in rows],
    )


def load_stooq(symbol: str, end: date | None = None, cache: Path = CACHE) -> DailySeries | None:
    lock = require_prereg_lock()
    path = cache / "stooq" / f"{symbol}.txt"
    if not path.exists():
        path = path.with_suffix(".csv")
        if not path.exists():
            return None
    return _clip(parse_stooq(path.read_text(encoding="utf-8"), symbol), lock, end)


def load_yahoo(symbol: str, end: date | None = None, cache: Path = CACHE) -> DailySeries | None:
    lock = require_prereg_lock()
    path = cache / "yahoo" / f"{symbol}.csv"
    if not path.exists():
        return None
    return _clip(parse_yahoo(path.read_text(encoding="utf-8"), symbol), lock, end)
