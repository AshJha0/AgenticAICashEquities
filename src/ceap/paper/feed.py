"""Daily market data for paper trading, behind one small interface.

A feed returns a :class:`MarketPanel`: aligned ``(n_days, n_symbols)`` arrays of *traded* (unadjusted)
prices, the lagged 20-day ADV in shares, quoted spreads, membership, and the split list. The engine
never reads a row after the session it is simulating (point in time); signals use the split-adjusted
close from :meth:`MarketPanel.adjusted_close`.

* :class:`SyntheticFeed` (now): a research scenario from :mod:`ceap.data.historical`, re-dated onto
  the committed NYSE calendar so that its first out-of-sample row falls on the forward start date.
  The forward record therefore runs on rows the in-sample Sharpe never saw.
* Real daily data (R5, at integration): an adapter builds a ``MarketPanel`` from R5's Stooq connector
  and calls :func:`register_feed`. Nothing in this module imports R5.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Protocol

import numpy as np

from ceap.data.calendar import LAST_YEAR, trading_days, trading_days_ending

LABEL = "paper, synthetic or free daily data, not trading"


@dataclass(frozen=True)
class Split:
    """A stock split effective at the open of ``ex_date``: ``ratio`` new shares per old share."""

    symbol: str
    ex_date: str
    ratio: float

    def __post_init__(self) -> None:
        if not self.ratio > 0:
            raise ValueError(f"split ratio must be positive, got {self.ratio}")


@dataclass(frozen=True)
class MarketPanel:
    name: str
    symbols: tuple[str, ...]
    tiers: tuple[str, ...]
    dates: np.ndarray  # datetime64[D]
    close: np.ndarray  # traded prices, NaN = no print (halted, not listed)
    low: np.ndarray
    volume: np.ndarray
    adv_20: np.ndarray  # shares, lagged one day
    spread_bps: np.ndarray  # quoted spread (full), bps
    members: np.ndarray  # bool
    splits: tuple[Split, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def n_days(self) -> int:
        return int(self.dates.shape[0])

    def index_of(self, day: str | date) -> int:
        d = np.datetime64(str(day)[:10], "D")
        i = int(np.searchsorted(self.dates, d))
        if i >= self.n_days or self.dates[i] != d:
            raise KeyError(f"{str(day)[:10]} is not a session in feed {self.name!r}")
        return i

    def day(self, i: int) -> str:
        return str(self.dates[i])

    def col(self, symbol: str) -> int:
        return self.symbols.index(symbol)

    def splits_on(self, i: int) -> list[Split]:
        d = self.day(i)
        return [s for s in self.splits if s.ex_date == d]

    def split_factor(self) -> np.ndarray:
        """``F[t, j]``: shares today per share held at row ``t`` (product of later ratios), for adjusting."""
        f = np.ones(self.close.shape)
        for s in self.splits:
            k = self.index_of(s.ex_date)
            f[:k, self.col(s.symbol)] *= s.ratio
        return f

    def adjusted_close(self, upto: int) -> np.ndarray:
        """Split-adjusted closes for rows ``0..upto`` in the share units of row ``upto`` (no look-ahead:
        only splits with ex-date <= ``upto`` are applied)."""
        c = self.close[: upto + 1].copy()
        for s in self.splits:
            k = self.index_of(s.ex_date)
            if k <= upto:
                c[:k, self.col(s.symbol)] /= s.ratio
        return c


class DailyFeed(Protocol):
    """Anything that can return a point-in-time daily panel."""

    name: str

    def panel(self) -> MarketPanel: ...


def panel_from_historical(ds: Any, name: str, dates: np.ndarray | None = None) -> MarketPanel:
    """A :class:`MarketPanel` from a ``HistoricalDataset`` (optionally re-dated)."""
    return MarketPanel(
        name=name,
        symbols=tuple(ds.symbols),
        tiers=tuple(ds.tiers),
        dates=ds.dates if dates is None else dates,
        close=ds.close,
        low=ds.low,
        volume=ds.volume,
        adv_20=ds.adv_20,
        spread_bps=ds.spread_bps,
        members=ds.member_mask,
        meta={"dataset_hash": ds.dataset_hash, "scenario": ds.scenario.id},
    )


def apply_splits(panel: MarketPanel, splits: Sequence[Split]) -> MarketPanel:
    """Turn a split-free panel into the traded series a split would produce (for tests and demos):
    prices from the ex-date on are divided by the ratio, volumes and ADV multiplied."""
    close, low = panel.close.copy(), panel.low.copy()
    vol, adv = panel.volume.copy(), panel.adv_20.copy()
    for s in splits:
        k, j = panel.index_of(s.ex_date), panel.col(s.symbol)
        close[k:, j] /= s.ratio
        low[k:, j] /= s.ratio
        vol[k:, j] *= s.ratio
        adv[k:, j] *= s.ratio
    return replace(panel, close=close, low=low, volume=vol, adv_20=adv, splits=tuple(panel.splits) + tuple(splits))


class SyntheticFeed:
    """A research scenario re-dated so that row ``in_sample_end_index + 1`` is ``forward_start``."""

    def __init__(self, scenario: str = "R01", forward_start: str | None = None,
                 splits: Sequence[Split] = ()) -> None:
        self.scenario = scenario
        self.forward_start = forward_start
        self.splits = tuple(splits)
        self.name = f"synthetic:{scenario}"
        self._panel: MarketPanel | None = None

    def panel(self) -> MarketPanel:
        if self._panel is None:
            from ceap.data.historical import HistoricalStore

            ds = HistoricalStore().get(self.scenario)
            panel = panel_from_historical(ds, self.name)
            if self.forward_start is not None:
                panel = _redate(panel, ds.in_sample_end_index + 1, date.fromisoformat(self.forward_start))
            panel.meta["in_sample_end_index"] = ds.in_sample_end_index
            panel.meta["start_index"] = ds.start_index
            self._panel = apply_splits(panel, self.splits) if self.splits else panel
        return self._panel


def _redate(panel: MarketPanel, anchor_row: int, anchor_day: date) -> MarketPanel:
    """Map rows onto NYSE sessions with ``anchor_row`` on ``anchor_day``; rows past the committed calendar
    are dropped (the panel simply ends there)."""
    before = trading_days_ending(anchor_day, anchor_row + 1)
    after = trading_days(anchor_day, date(LAST_YEAR, 12, 31))
    dates = np.concatenate([before[:-1], after])[: panel.n_days]
    n = dates.shape[0]
    if before[-1] != np.datetime64(anchor_day, "D"):
        raise ValueError(f"forward start {anchor_day} is not an NYSE session")

    def cut(a: np.ndarray) -> np.ndarray:
        return a[:n]

    return replace(
        panel, dates=dates, close=cut(panel.close), low=cut(panel.low), volume=cut(panel.volume),
        adv_20=cut(panel.adv_20), spread_bps=cut(panel.spread_bps), members=cut(panel.members),
        meta=dict(panel.meta),
    )


FeedFactory = Callable[..., DailyFeed]
_FEEDS: dict[str, FeedFactory] = {"synthetic": SyntheticFeed}


def register_feed(kind: str, factory: FeedFactory) -> None:
    """Integration hook: R5's Stooq adapter registers itself here (``register_feed("stooq", ...)``)."""
    _FEEDS[kind] = factory


def get_feed(kind: str, **kwargs: Any) -> DailyFeed:
    try:
        factory = _FEEDS[kind]
    except KeyError as exc:
        raise KeyError(f"unknown paper feed {kind!r}; registered: {sorted(_FEEDS)}") from exc
    return factory(**kwargs)
