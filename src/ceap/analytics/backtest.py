# ruff: noqa: N803, N806  (mathematical notation: R, P, T, A, B as in the formulas)
"""Daily backtest with realistic execution timing, drift, financing, borrow, caps and delistings.

Conventions (0.4.0; each one is a stated assumption recorded in ``BacktestResult.assumptions``):

* **Execution (2.1).** The target is built from the signal on decision date
  ``t`` (closes up to ``t``) and filled ``execution_lag_days`` later, at that
  day's close (``fill="close"``, default lag 1) or open (``fill="open"``, lag >= 1).
  ``lag=0`` with a close fill is the 0.3.x same-close look-ahead, kept only
  for the lag-sensitivity test.
* **Drift (2.2).** Between fills positions move with prices; turnover and cost
  are measured from the drifted weights to the target.
* **Financing (2.3).** Positions are in units of NAV. Cash is
  ``1 - net exposure`` (short proceeds are credited): positive cash earns the
  risk-free rate (the short rebate), negative cash pays rf plus a financing
  spread. Shorts pay a borrow fee by liquidity tier (A 0.5 %, B 2 %, C 10 % a
  year). Sharpe and Sortino are computed on returns in excess of rf, so a
  long-only book's Sharpe falls by rf/sigma and a dollar-neutral book is
  self-financing. Gross leverage is capped at 2x (Reg T).
* **Reg SHO SSR (2.3).** On a name down 10 % intraday from the prior close the
  book adds no new short at the fill; the blocked trade is logged.
* **Data policy (2.5).** ``adv_20`` is lagged one day. Per-name weight and ADV
  participation caps are applied inside the loop; every clipped trade is logged,
  never silently traded. A held name that delists realises a delisting return
  (``merger``: last price; ``performance``: -30 % NYSE, -55 % Nasdaq, Shumway
  1997) and its proceeds go to cash; both bounds are reported. A held name with
  a missing price earns 0 that day and the day is logged.
* **Membership (2.13).** Non-members have no signal, are never ranked and never held.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ceap.analytics.signals import cross_sectional_zscore, dataset_signal, get_signal
from ceap.domain.research import (
    DEFAULT_EXECUTION_LAG_DAYS,
    DEFAULT_FILL,
    DEFAULT_GROSS_NOTIONAL,
    FILLS,
    BacktestPeriodStats,
    BacktestResult,
)

if TYPE_CHECKING:  # pragma: no cover
    from ceap.data.historical import HistoricalDataset

TRADING_DAYS_PER_YEAR = 252
MIN_NAMES = 8
EQUITY_CURVE_POINTS = 24
TOP_CONTRIBUTORS = 5
MAX_EVENTS_PER_KIND = 25
RF_PATH = Path(__file__).resolve().parents[3] / "data" / "reference" / "rf.csv"
SSR_TRIGGER = -0.10
DELISTING_RETURNS: dict[str, dict[str, float]] = {
    "merger": {"NYSE": 0.0, "NASDAQ": 0.0},
    "performance": {"NYSE": -0.30, "NASDAQ": -0.55},
}


@dataclass(frozen=True)
class CostModel:
    impact_coefficient: float = 0.6
    vol_window: int = 20
    default_vol_bps: float = 150.0

    def impact_bps(self, daily_vol_bps: np.ndarray, participation: np.ndarray) -> np.ndarray:
        vol = np.where(np.isfinite(daily_vol_bps), daily_vol_bps, self.default_vol_bps)
        part = np.clip(np.nan_to_num(np.asarray(participation, dtype=float), nan=0.0, posinf=0.0), 0.0, None)
        return self.impact_coefficient * vol * np.sqrt(part)

    def trade_cost_bps(
        self, half_spread_bps: np.ndarray, daily_vol_bps: np.ndarray, participation: np.ndarray
    ) -> np.ndarray:
        hs = np.nan_to_num(np.asarray(half_spread_bps, dtype=float), nan=0.0)
        return hs + self.impact_bps(daily_vol_bps, participation)


@dataclass(frozen=True)
class FinancingModel:
    """Excess-return and financing conventions (2.3). ``enabled=False`` reproduces 0.3.x raw returns."""

    enabled: bool = True
    borrow_bps_annual: dict[str, float] = field(default_factory=lambda: {"A": 50.0, "B": 200.0, "C": 1000.0})
    financing_spread_bps_annual: float = 50.0
    gross_leverage_limit: float = 2.0  # Reg T initial margin
    ssr: bool = True


@dataclass(frozen=True)
class PositionCaps:
    """Per-name caps applied inside the loop (2.5); breaches are clipped and logged."""

    max_weight: float = 0.10
    max_participation: float = 0.10


@lru_cache(maxsize=4)
def _load_rf(path: str) -> tuple[np.ndarray, np.ndarray] | None:
    p = Path(path)
    if not p.exists():
        return None
    dates: list[str] = []
    vals: list[float] = []
    with p.open(encoding="utf-8") as fh:
        rows = (line for line in fh if not line.startswith("#"))
        for row in csv.DictReader(rows):
            dates.append(row["date"])
            vals.append(float(row["rf"]))
    return np.array(dates, dtype="datetime64[D]"), np.array(vals, dtype=float)


def risk_free_daily(dates: np.ndarray, path: Path = RF_PATH) -> tuple[np.ndarray, str]:
    """Daily risk-free rate (decimal) aligned to ``dates``; zeros with a stated source when unavailable."""
    loaded = _load_rf(str(path))
    if loaded is None:
        return np.zeros(dates.shape[0]), "unavailable: rf = 0 (data/reference/rf.csv not committed)"
    d, v = loaded
    idx = np.clip(np.searchsorted(d, dates, side="right") - 1, 0, d.size - 1)
    return v[idx], "Kenneth R. French data library, daily RF (see data/reference/rf.csv header)"


def target_weights(
    z_row: np.ndarray, long_short: bool = True, top_fraction: float = 1 / 3, weighting: str = "equal"
) -> np.ndarray:
    """Portfolio weights from one date's z-scores; ``sum(|w|) == 1`` when the cross-section is usable."""
    z = np.asarray(z_row, dtype=float)
    w = np.zeros(z.shape)
    valid = np.flatnonzero(np.isfinite(z))
    if valid.size < 2:
        return w
    k = max(1, int(round(valid.size * top_fraction)))
    k = min(k, valid.size // 2) if long_short else min(k, valid.size)
    order = valid[np.argsort(z[valid], kind="stable")]
    longs = order[-k:]
    shorts = order[:k]
    if weighting == "rank":
        long_w = np.arange(1, k + 1, dtype=float)
        short_w = np.arange(k, 0, -1, dtype=float)
    else:
        long_w = np.ones(k)
        short_w = np.ones(k)
    if long_short:
        w[longs] = 0.5 * long_w / long_w.sum()
        w[shorts] = -0.5 * short_w / short_w.sum()
    else:
        w[longs] = long_w / long_w.sum()
    return w


def daily_returns(close: np.ndarray) -> np.ndarray:
    c = np.asarray(close, dtype=float)
    out = np.full(c.shape, np.nan)
    if c.shape[0] > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[1:] = c[1:] / c[:-1] - 1.0
    return out


def rolling_vol_bps(returns: np.ndarray, window: int = 20) -> np.ndarray:
    r = np.asarray(returns, dtype=float)
    out = np.full(r.shape, np.nan)
    if r.shape[0] > window:
        windows = np.lib.stride_tricks.sliding_window_view(r[1:], window, axis=0)
        out[window:] = windows.std(axis=-1, ddof=1) * 10_000.0
    return out


def _downsample_indices(n: int, max_points: int) -> np.ndarray:
    if n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


def period_stats(
    name: str,
    dates: np.ndarray,
    i0: int,
    i1: int,
    net: np.ndarray,
    gross: np.ndarray,
    turnover: np.ndarray,
    n_rebalances: int,
    rf: np.ndarray | None = None,
) -> BacktestPeriodStats:
    """Statistics over ``[i0, i1]``. Sharpe and Sortino use returns in excess of ``rf`` (zero if omitted)."""
    sl = slice(i0, i1 + 1)
    r = net[sl]
    g = gross[sl]
    f = np.zeros_like(r) if rf is None else rf[sl]
    x = r - f
    gx = g - f
    n = int(r.size)
    nan = float("nan")
    cagr = ann_return = ann_vol = sharpe = sortino = max_dd = hit = nan
    gross_sharpe = gross_ann = excess_ann = rf_ann = skew = kurt = nan
    if n >= 2:
        ann_return = float(r.mean() * TRADING_DAYS_PER_YEAR)
        gross_ann = float(g.mean() * TRADING_DAYS_PER_YEAR)
        excess_ann = float(x.mean() * TRADING_DAYS_PER_YEAR)
        rf_ann = float(f.mean() * TRADING_DAYS_PER_YEAR)
        growth = 1.0 + r
        cagr = float(np.prod(growth) ** (TRADING_DAYS_PER_YEAR / n) - 1.0) if (growth > 0).all() else -1.0
        std = float(x.std(ddof=1))
        gstd = float(gx.std(ddof=1))
        ann_vol = float(r.std(ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)
        if std > 0:
            sharpe = float(x.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
            zx = (x - x.mean()) / float(x.std(ddof=0))
            skew = float((zx**3).mean())
            kurt = float((zx**4).mean())
        if gstd > 0:
            gross_sharpe = float(gx.mean() / gstd * np.sqrt(TRADING_DAYS_PER_YEAR))
        downside = float(np.sqrt(np.mean(np.minimum(x, 0.0) ** 2)))
        if downside > 0:
            sortino = float(x.mean() / downside * np.sqrt(TRADING_DAYS_PER_YEAR))
        equity = np.cumprod(growth)
        max_dd = float((1.0 - equity / np.maximum.accumulate(equity)).max())
        hit = float((r > 0).mean())
    years = n / TRADING_DAYS_PER_YEAR if n else nan
    return BacktestPeriodStats(
        name=name,
        start=str(dates[i0]),
        end=str(dates[i1]),
        n_days=n,
        cagr=cagr,
        ann_return=ann_return,
        ann_vol=float(ann_vol),
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        turnover_annual=float(turnover[sl].sum() / years) if years and years > 0 else nan,
        hit_rate=hit,
        gross_sharpe=gross_sharpe,
        gross_ann_return=gross_ann,
        cost_drag_bps_annual=float((gross_ann - ann_return) * 10_000.0) if n >= 2 else nan,
        n_rebalances=n_rebalances,
        excess_ann_return=excess_ann,
        rf_ann=rf_ann,
        skew=skew,
        kurtosis=kurt,
    )



@dataclass
class _Book:
    """Positions ``P`` and ``cash`` in currency units of the initial NAV (1.0)."""

    P: np.ndarray
    cash: float

    @property
    def nav(self) -> float:
        return float(self.P.sum() + self.cash)


@dataclass
class _Sim:
    ds: Any
    n: int
    m: int
    drift: bool
    rf: np.ndarray
    borrow_daily: np.ndarray
    spread_daily: float
    nav: np.ndarray
    costs: dict[str, np.ndarray]
    contrib: np.ndarray
    events: list[dict[str, Any]]
    counts: dict[str, int]

    def log(self, kind: str, t: int, j: int | None, **detail: Any) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if self.counts[kind] <= MAX_EVENTS_PER_KIND:
            ev: dict[str, Any] = {"kind": kind, "date": str(self.ds.dates[t])}
            if j is not None:
                ev["symbol"] = self.ds.symbols[j]
            ev.update({k: (round(v, 6) if isinstance(v, float) else v) for k, v in detail.items()})
            self.events.append(ev)

    def hold(
        self,
        book: _Book,
        rows: np.ndarray,
        days: list[int | None],
        rf_rows: np.ndarray,
        delist: dict[int, tuple[int, float]] | None = None,
    ) -> _Book:
        """Carry the book through ``rows`` of returns. ``days[k]`` is the day index whose close row ``k``
        ends on, or ``None`` for an intraday leg (overnight to the open). ``delist`` maps column -> (row,
        delisting return): that row's return is replaced and the position is cashed out after it."""
        if rows.shape[0] == 0:
            return book
        R = np.array(rows, dtype=float, copy=True)
        held = book.P != 0
        missing = ~np.isfinite(R)
        delist = delist or {}
        for j, (k, dr) in delist.items():
            missing[k:, j] = False
            R[k, j] = dr
            R[k + 1 :, j] = 0.0
        bad = missing & held[None, :]
        if bad.any():
            for k, j in zip(*np.nonzero(bad), strict=True):
                d = days[k]
                if d is not None:
                    self.log("nan_return", d, int(j))
        R[~np.isfinite(R)] = 0.0
        nav0 = book.nav
        if self.drift:
            G = np.cumprod(1.0 + R, axis=0)
            Pt = book.P[None, :] * G
            Pprev = np.vstack([book.P[None, :], Pt[:-1]])
            borrow = (np.abs(np.minimum(Pprev, 0.0)) * self.borrow_daily[None, :]).sum(axis=1)
            borrow = np.where([d is None for d in days], 0.0, borrow)
            s = self.spread_daily if book.cash < 0 else 0.0
            g = 1.0 + rf_rows + np.where([d is None for d in days], 0.0, s)
            Gc = np.cumprod(g)
            cash_t = Gc * (book.cash - np.cumsum(borrow / Gc))
            cash_prev = np.concatenate([[book.cash], cash_t[:-1]])
            fin = -np.minimum(cash_prev, 0.0) * (g - 1.0 - rf_rows)
            nav_t = Pt.sum(axis=1) + cash_t
            nav_prev = np.concatenate([[nav0], nav_t[:-1]])
            self.contrib += (Pprev * R / nav_prev[:, None]).sum(axis=0)
            P_end, cash_end = Pt[-1].copy(), float(cash_t[-1])
        else:
            w = book.P / nav0
            cw = book.cash / nav0
            intraday = np.array([d is None for d in days])
            b_rate = float((np.abs(np.minimum(w, 0.0)) * self.borrow_daily).sum())
            s = self.spread_daily if cw < 0 else 0.0
            growth = 1.0 + R @ w + cw * rf_rows - np.where(intraday, 0.0, b_rate + max(-cw, 0.0) * s)
            nav_t = nav0 * np.cumprod(growth)
            nav_prev = np.concatenate([[nav0], nav_t[:-1]])
            borrow = np.where(intraday, 0.0, nav_prev * b_rate)
            fin = np.where(intraday, 0.0, nav_prev * max(-cw, 0.0) * s)
            self.contrib += (w[None, :] * R).sum(axis=0)
            P_end, cash_end = w * nav_t[-1], cw * float(nav_t[-1])
        for k, d in enumerate(days):
            if d is not None:
                self.nav[d] = nav_t[k]
                self.costs["borrow"][d] += borrow[k]
                self.costs["financing"][d] += fin[k]
        for j in delist:
            cash_end += float(P_end[j])
            P_end[j] = 0.0
        return _Book(P_end, cash_end)


def _validate(
    rebalance: int, gross_notional: float, lag: int, fill: str, financing: FinancingModel, gross_leverage: float
) -> None:
    if rebalance <= 0:
        raise ValueError("rebalance_days must be positive")
    if gross_notional <= 0:
        raise ValueError("gross_notional must be positive")
    if fill not in FILLS:
        raise ValueError(f"fill must be one of {FILLS}")
    if lag < 0:
        raise ValueError("execution_lag_days must be >= 0")
    if fill == "open" and lag < 1:
        raise ValueError("an open fill needs execution_lag_days >= 1: the decision uses that day's close")
    if gross_leverage <= 0 or gross_leverage > financing.gross_leverage_limit:
        raise ValueError(f"gross leverage {gross_leverage} exceeds the {financing.gross_leverage_limit}x limit (Reg T)")


@dataclass(frozen=True)
class BacktestRun:
    """A backtest result plus the daily series it was computed from (for profitability and deflated Sharpe)."""

    result: BacktestResult
    net: np.ndarray  # daily net total return, full length, zero outside [i0, i1]
    gross: np.ndarray  # before trade, borrow and financing-spread costs
    rf: np.ndarray
    i0: int
    i1: int
    split: int
    fill_weights: dict[int, np.ndarray] = field(default_factory=dict)  # weights right after each fill


def run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
    """Run a backtest and return its :class:`BacktestResult` (see :func:`simulate_backtest` for arguments)."""
    return simulate_backtest(*args, **kwargs).result


def simulate_backtest(
    ds: HistoricalDataset,
    signal: str,
    start: str | None = None,
    end: str | None = None,
    in_sample_end: str | None = None,
    rebalance_days: int | None = None,
    long_short: bool = True,
    cost_multiplier: float = 1.0,
    gross_notional: float = DEFAULT_GROSS_NOTIONAL,
    weighting: str = "equal",
    costs: CostModel | None = None,
    top_fraction: float = 1 / 3,
    execution_lag_days: int = DEFAULT_EXECUTION_LAG_DAYS,
    fill: str = DEFAULT_FILL,
    drift: bool = True,
    financing: FinancingModel | None = None,
    caps: PositionCaps | None = None,
    delisting: str = "performance",
    gross_leverage: float = 1.0,
    report_bounds: bool = True,
    risk_free: np.ndarray | None = None,
) -> BacktestRun:
    spec = get_signal(signal)
    rebalance = int(spec.default_rebalance_days if rebalance_days is None else rebalance_days)
    lag = int(execution_lag_days)
    fin_model = financing if financing is not None else FinancingModel()
    _validate(rebalance, gross_notional, lag, fill, fin_model, gross_leverage)
    if delisting not in DELISTING_RETURNS:
        raise ValueError(f"delisting must be one of {sorted(DELISTING_RETURNS)}")
    costs = costs or CostModel()
    i0, i1 = ds.index_range(start, end)
    split = ds.locked_split(in_sample_end, i0, i1)
    n, m = ds.close.shape

    sig = dataset_signal(ds, signal)
    ret = daily_returns(ds.close)
    vol_bps = rolling_vol_bps(ret, costs.vol_window)
    if fin_model.enabled:
        if risk_free is not None:
            rf, rf_source = np.asarray(risk_free, dtype=float), "supplied by the caller"
        else:
            rf, rf_source = risk_free_daily(ds.dates)
        borrow_daily = np.array([fin_model.borrow_bps_annual.get(t, 1000.0) for t in ds.tiers]) / 10_000.0 / 252
        spread_daily = fin_model.financing_spread_bps_annual / 10_000.0 / 252
    else:
        rf, rf_source = np.zeros(n), "disabled: raw returns (0.3.x convention)"
        borrow_daily = np.zeros(m)
        spread_daily = 0.0
    sim = _Sim(
        ds=ds, n=n, m=m, drift=drift, rf=rf, borrow_daily=borrow_daily, spread_daily=spread_daily,
        nav=np.full(n, np.nan), costs={k: np.zeros(n) for k in ("spread", "impact", "borrow", "financing")},
        contrib=np.zeros(m), events=[], counts={},
    )
    turnover = np.zeros(n)
    members = ds.member_mask
    exchanges = ds.exchanges
    delist_days: dict[int, list[int]] = {}
    exit_kind: dict[tuple[int, int], str] = {}
    # every end of membership is an exit: a recorded delisting, or an index exit cashed out at the last price
    ends = np.argwhere(members[i0:i1] & ~members[i0 + 1 : i1 + 1])
    for k, j in ends:
        d = int(i0 + 1 + k)
        delist_days.setdefault(d, []).append(int(j))
        rec = ds.delistings.get(int(j))
        exit_kind[(d, int(j))] = rec[1] if rec is not None and rec[0] == d else "index_exit"

    def delist_return(j: int, d: int) -> float:
        kind = delisting if exit_kind.get((d, j)) == "performance" else "merger"
        return DELISTING_RETURNS[kind][exchanges[j]]

    # decisions and their fill days
    fills: dict[int, int] = {}
    for t in range(i0, i1 + 1):
        if (t - i0) % rebalance == 0 and t + lag <= i1:
            fills[t + lag] = t
    rebalances: list[int] = []
    fill_weights: dict[int, np.ndarray] = {}
    book = _Book(np.zeros(m), 1.0)
    sim.nav[i0] = 1.0
    cap = caps if caps is not None else PositionCaps()

    def trade(book: _Book, e: int, decision: int, price: np.ndarray, down: np.ndarray) -> _Book:
        nav = book.nav
        if nav <= 0:
            return book
        row = sig[decision]
        if np.isfinite(row).sum() < MIN_NAMES:
            return book
        tgt = target_weights(cross_sectional_zscore(row), long_short, top_fraction, weighting) * gross_leverage
        tgt[~members[e]] = 0.0
        over = np.abs(tgt) > cap.max_weight * gross_leverage + 1e-12
        for j in np.flatnonzero(over):
            sim.log("weight_cap", e, int(j), target=float(tgt[j]), cap=cap.max_weight)
        tgt = np.clip(tgt, -cap.max_weight * gross_leverage, cap.max_weight * gross_leverage)
        w_d = book.P / nav
        if fin_model.enabled and fin_model.ssr:
            blocked = down & (tgt < np.minimum(w_d, 0.0) - 1e-12)
            for j in np.flatnonzero(blocked):
                sim.log("ssr_blocked", e, int(j), target=float(tgt[j]), held=float(w_d[j]))
            tgt = np.where(blocked, np.minimum(w_d, 0.0), tgt)
        dw = tgt - w_d
        dollars_per_unit = gross_notional * nav
        with np.errstate(divide="ignore", invalid="ignore"):
            capacity = cap.max_participation * ds.adv_20[e] * price / dollars_per_unit
        capacity = np.where(np.isfinite(capacity), capacity, 0.0)
        clipped = np.abs(dw) > capacity + 1e-12
        for j in np.flatnonzero(clipped & (np.abs(dw) > 1e-12)):
            sim.log("participation_cap", e, int(j), wanted=float(dw[j]), allowed=float(capacity[j]))
        dw = np.sign(dw) * np.minimum(np.abs(dw), capacity)
        with np.errstate(divide="ignore", invalid="ignore"):
            participation = np.abs(dw) * dollars_per_unit / (ds.adv_20[e] * price)
        half_spread = np.nan_to_num(ds.spread_bps[e] / 2.0, nan=0.0)
        spread_cost = float((np.abs(dw) * half_spread).sum() / 10_000.0 * cost_multiplier * nav)
        impact_cost = float(
            (np.abs(dw) * costs.impact_bps(vol_bps[e], participation)).sum() / 10_000.0 * cost_multiplier * nav
        )
        sim.costs["spread"][e] += spread_cost
        sim.costs["impact"][e] += impact_cost
        turnover[e] += float(np.abs(dw).sum())
        rebalances.append(e)
        P_new = (w_d + dw) * nav
        cash = book.cash - float((dw * nav).sum()) - spread_cost - impact_cost
        fill_weights[e] = w_d + dw
        return _Book(P_new, cash)

    t_cur = i0
    if i0 in fills:  # lag 0 decision on the first day
        down0 = np.zeros(m, dtype=bool)
        book = trade(book, i0, fills[i0], ds.close[i0], down0)
        sim.nav[i0] = book.nav
    event_days = sorted(set(fills) | set(delist_days))
    for e in event_days:
        if e <= t_cur:
            continue
        open_fill = fill == "open" and e in fills
        last_close = e - 1 if open_fill else e
        # delistings strictly before e are handled at their own event day, so only e itself can delist here
        if last_close > t_cur:
            dl = {}
            if not open_fill:
                for j in delist_days.get(e, []):
                    if book.P[j] != 0:
                        dl[j] = (last_close - t_cur - 1, delist_return(j, e))
                        sim.log("delisting", e, j, delisting_kind=exit_kind[(e, j)], bound=delisting,
                                ret=delist_return(j, e))
            days: list[int | None] = list(range(t_cur + 1, last_close + 1))
            book = sim.hold(book, ret[t_cur + 1 : last_close + 1], days, rf[t_cur + 1 : last_close + 1], dl)
            t_cur = last_close
        if open_fill:
            with np.errstate(divide="ignore", invalid="ignore"):
                r_on = ds.open[e] / ds.close[e - 1] - 1.0
                r_id = ds.close[e] / ds.open[e] - 1.0
            dl = {}
            for j in delist_days.get(e, []):
                if book.P[j] != 0:
                    dl[j] = (0, delist_return(j, e))
                    sim.log("delisting", e, j, delisting_kind=exit_kind[(e, j)], bound=delisting,
                            ret=delist_return(j, e))

            book = sim.hold(book, r_on[None, :], [None], np.zeros(1), dl)
            down = np.nan_to_num(r_on, nan=0.0) <= SSR_TRIGGER
            book = trade(book, e, fills[e], ds.open[e], down)
            book = sim.hold(book, r_id[None, :], [e], rf[e : e + 1])
            t_cur = e
        elif e in fills:
            with np.errstate(divide="ignore", invalid="ignore"):
                down = np.nan_to_num(ds.low[e] / ds.close[e - 1] - 1.0, nan=0.0) <= SSR_TRIGGER
            book = trade(book, e, fills[e], ds.close[e], down)
            sim.nav[e] = book.nav
    if i1 > t_cur:
        days = list(range(t_cur + 1, i1 + 1))
        book = sim.hold(book, ret[t_cur + 1 : i1 + 1], days, rf[t_cur + 1 : i1 + 1])

    nav = sim.nav
    nav_prev = np.concatenate([[1.0], nav[i0:i1]])
    net = np.zeros(n)
    net[i0 : i1 + 1] = nav[i0 : i1 + 1] / nav_prev - 1.0
    total_cost = np.sum(np.vstack(list(sim.costs.values())), axis=0)

    gross = net.copy()
    gross[i0 : i1 + 1] += total_cost[i0 : i1 + 1] / nav_prev

    def count(a: int, b: int) -> int:
        return sum(1 for t in rebalances if a <= t <= b)

    oos0 = min(split + 1, i1)
    periods = {
        "in_sample": period_stats("in_sample", ds.dates, i0, split, net, gross, turnover, count(i0, split), rf),
        "out_of_sample": period_stats(
            "out_of_sample", ds.dates, oos0, i1, net, gross, turnover, count(oos0, i1), rf
        ),
        "full": period_stats("full", ds.dates, i0, i1, net, gross, turnover, len(rebalances), rf),
    }
    is_sharpe = periods["in_sample"].sharpe
    oos_sharpe = periods["out_of_sample"].sharpe
    ratio = (
        float(oos_sharpe / is_sharpe)
        if np.isfinite(is_sharpe) and is_sharpe > 0 and np.isfinite(oos_sharpe)
        else float("nan")
    )
    equity = np.cumprod(1.0 + net[i0 : i1 + 1])
    idx = _downsample_indices(equity.size, EQUITY_CURVE_POINTS)
    curve = tuple((str(ds.dates[i0 + i]), round(float(equity[i]), 6)) for i in idx)
    contributions = sim.contrib
    order = np.argsort(contributions)[::-1]
    top = tuple((ds.symbols[j], round(float(contributions[j]), 6)) for j in order[:TOP_CONTRIBUTORS])
    positive = contributions[contributions > 0]
    concentration = float(np.sort(positive)[::-1][:3].sum() / positive.sum()) if positive.size else float("nan")
    years = (i1 - i0 + 1) / TRADING_DAYS_PER_YEAR
    breakdown = {
        k: round(float((v[i0 : i1 + 1] / nav_prev).sum() / years * 10_000.0), 3) for k, v in sim.costs.items()
    }
    bounds: dict[str, dict[str, float]] = {}
    if report_bounds and sim.counts.get("delisting", 0) > 0:
        other = "merger" if delisting == "performance" else "performance"
        alt = simulate_backtest(
            ds, signal, start, end, in_sample_end, rebalance, long_short, cost_multiplier, gross_notional,
            weighting, costs, top_fraction, lag, fill, drift, financing, caps, other, gross_leverage, False,
            risk_free,
        ).result
        for label, res in ((delisting, None), (other, alt)):
            p = periods if res is None else res.periods
            bounds[label] = {"full_sharpe": p["full"].sharpe, "oos_sharpe": p["out_of_sample"].sharpe}
    assumptions: dict[str, Any] = {
        "execution": f"decision at close t, fill at the {fill} of t+{lag}",
        "drift": drift,
        "risk_free": rf_source,
        "borrow_bps_annual": dict(fin_model.borrow_bps_annual) if fin_model.enabled else {},
        "financing_spread_bps_annual": fin_model.financing_spread_bps_annual if fin_model.enabled else 0.0,
        "gross_leverage": gross_leverage,
        "gross_leverage_limit": fin_model.gross_leverage_limit,
        "ssr": "no new short on a name down 10% intraday" if fin_model.enabled and fin_model.ssr else "off",
        "caps": {"max_weight": cap.max_weight, "max_participation": cap.max_participation},
        "delisting": delisting,
        "adv": "20-day average volume lagged one day",
        "nan_policy": "held name with a missing price earns 0 and is logged",
    }
    result = BacktestResult(
        signal=signal,
        dataset=ds.scenario.id,
        rebalance_days=rebalance,
        long_short=long_short,
        gross_notional=float(gross_notional),
        periods=periods,
        sharpe_ratio_oos_is=ratio,
        equity_curve=curve,
        top_contributors=top,
        pnl_concentration_top3=concentration,
        execution_lag_days=lag,
        fill=fill,
        cost_breakdown_bps_annual=breakdown,
        event_counts=dict(sim.counts),
        events=tuple(sim.events),
        delisting_bounds=bounds,
        assumptions=assumptions,
    )
    return BacktestRun(result, net, gross, rf, i0, i1, split, fill_weights)


