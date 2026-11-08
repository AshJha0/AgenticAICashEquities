"""Real daily data for paper trading: a :class:`MarketPanel` from R5's local Stooq cache.

paper, synthetic or free daily data, not trading.

This is the integration shim between RP (paper trading) and R5 (real data). It reads the per-symbol
Stooq files a person placed in ``data/real_cache/stooq/`` (R5 never downloads them by script, D2) and
builds the panel the paper engine expects:

* ``close`` and ``low``: the prices in the Stooq file. Stooq publishes split-adjusted history, so the
  panel carries no :class:`~ceap.paper.feed.Split` rows and ``adjusted_close`` equals ``close``. If
  a caller has unadjusted files and a split list, pass ``splits=`` and the engine adjusts as usual.
* ``adv_20``: the mean volume of the previous 20 sessions, in shares, so it is lagged one day.
* ``spread_bps``: the full spread from R5's 5.4 schedule (twice ``real_costs.half_spread``), because
  free daily data has no quotes.
* ``members``: approximately point-in-time S&P 500 membership from R5 (``universe_pit``).
* ``tiers``: every name is tier ``"A"`` (large cap) for borrow costs.

The R5 pseudo-holdout is still UNSPENT (``docs/research/prereg_lock.json``). This feed refuses to
read any row on or before ``holdout_end``, so paper trading can never look at it. With the 12-1
momentum signal this means the first full signal appears about 252 sessions after the holdout ends.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import numpy as np

from ceap.analytics.real_costs import half_spread
from ceap.data.calendar import LAST_YEAR, trading_days
from ceap.data.real.prereg import load_lock
from ceap.data.real.prices import CACHE
from ceap.data.real.universe_pit import PointInTimeUniverse, stooq_symbol
from ceap.paper.feed import MarketPanel, Split, register_feed

ADV_WINDOW = 20
TIER = "A"


def parse_stooq_ohlcv(text: str, symbol: str) -> dict[date, tuple[float, float, float]]:
    """``{day: (close, low, volume)}`` from either Stooq format (per-symbol CSV or bulk ASCII)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError(f"empty Stooq file for {symbol}")
    head = [h.strip("<>").lower() for h in lines[0].split(",")]
    idx = {h: i for i, h in enumerate(head)}
    for need in ("date", "low", "close"):
        if need not in idx:
            raise ValueError(f"Stooq file for {symbol} has no {need!r} column")
    vk = "vol" if "vol" in idx else ("volume" if "volume" in idx else None)
    out: dict[date, tuple[float, float, float]] = {}
    for r in csv.reader(lines[1:]):
        raw = r[idx["date"]]
        d = date.fromisoformat(raw) if "-" in raw else datetime.strptime(raw, "%Y%m%d").date()
        vol = float(r[idx[vk]]) if vk is not None and r[idx[vk]] else 0.0
        out[d] = (float(r[idx["close"]]), float(r[idx["low"]]), vol)
    return out


class StooqFeed:
    """Paper feed over the local Stooq cache, from the day after the R5 holdout to ``end``."""

    def __init__(
        self,
        scenario: str | None = None,  # accepted for the get_feed signature; unused
        forward_start: str | None = None,
        *,
        cache: Path = CACHE,
        start: str | None = None,
        end: str | None = None,
        universe: PointInTimeUniverse | None = None,
        splits: Sequence[Split] = (),
    ) -> None:
        del scenario
        self.name = "stooq"
        self.cache = Path(cache)
        self.forward_start = forward_start
        self.splits = tuple(splits)
        self._universe = universe
        lock = load_lock()
        self.first_allowed = date.fromordinal(lock.holdout_end.toordinal() + 1)
        first = date.fromisoformat(start) if start else self.first_allowed
        if first < self.first_allowed:
            raise ValueError(
                f"start {first} reaches into the R5 pseudo-holdout (ends {lock.holdout_end}); "
                "the holdout is UNSPENT and paper trading may not read it"
            )
        self.start = first
        self.end = date.fromisoformat(end) if end else min(date.today(), date(LAST_YEAR, 12, 31))
        self._panel: MarketPanel | None = None

    def panel(self) -> MarketPanel:
        if self._panel is None:
            self._panel = self._build()
        return self._panel

    def _build(self) -> MarketPanel:
        dates = trading_days(self.start, self.end)
        if dates.shape[0] == 0:
            raise ValueError(f"no NYSE sessions between {self.start} and {self.end}")
        days = [date.fromisoformat(str(d)) for d in dates]
        uni = self._universe or PointInTimeUniverse.from_reference()
        member_sets = [uni.members(d) for d in days]
        symbols = tuple(sorted(set().union(*member_sets)))
        n, m = len(days), len(symbols)
        close = np.full((n, m), np.nan)
        low = np.full((n, m), np.nan)
        volume = np.zeros((n, m))
        members = np.zeros((n, m), dtype=bool)
        missing: list[str] = []
        for j, sym in enumerate(symbols):
            rows = self._read(uni.vendor_symbol(sym, days[-1]))
            if rows is None:
                missing.append(sym)
            else:
                for i, d in enumerate(days):
                    if d in rows:
                        close[i, j], low[i, j], volume[i, j] = rows[d]
            for i in range(n):
                members[i, j] = sym in member_sets[i]
        adv = np.full((n, m), np.nan)
        for i in range(ADV_WINDOW, n):
            adv[i] = volume[i - ADV_WINDOW : i].mean(axis=0)
        spread = np.array([2.0 * half_spread(d) * 1e4 for d in days])[:, None] * np.ones((1, m))
        return MarketPanel(
            name=self.name,
            symbols=symbols,
            tiers=tuple(TIER for _ in symbols),
            dates=dates,
            close=close,
            low=low,
            volume=volume,
            adv_20=adv,
            spread_bps=spread,
            members=members,
            splits=self.splits,
            meta={
                "source": "stooq local cache",
                "missing_files": missing,
                "holdout_excluded_until": str(self.first_allowed),
            },
        )

    def _read(self, ticker: str) -> dict[date, tuple[float, float, float]] | None:
        base = self.cache / "stooq"
        for name in (stooq_symbol(ticker), ticker):
            for ext in (".txt", ".csv"):
                path = base / f"{name}{ext}"
                if path.exists():
                    rows = parse_stooq_ohlcv(path.read_text(encoding="utf-8"), ticker)
                    return {d: v for d, v in rows.items() if self.start <= d <= self.end}
        return None


register_feed("stooq", StooqFeed)
