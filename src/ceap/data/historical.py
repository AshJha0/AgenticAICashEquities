# ruff: noqa: N803, N806  (mathematical notation: R, P, T, A, B as in the formulas)
"""Synthetic daily history for research: a 30-name universe over twenty years.

Two generators share one :class:`HistoricalDataset` shape.

``realistic_v1`` (default, 0.5.0) - 5040 NYSE trading days ending 2026-09-18::

    r[t, i] = beta_i * m_t + mu[t, i] + (T[t, i] - T[t-1, i]) + e[t, i]

* ``m_t``: market factor, GARCH(1,1) volatility with Student-t(5) innovations
  (volatility clustering and fat tails); an optional crash window.
* ``mu``: a *latent* expected-return component, AR(1) per name with the
  scenario's half-life and cross-sectional standard deviation (``premium_bps``
  per year), optionally decaying over time and multiplied after the locked
  in-sample end (regime break). Momentum detects it only through noisy past
  returns: the generator never calls the signal code, so the evaluation is not
  circular.
* ``T``: a *latent* transient price component, AR(1) with coefficient
  ``transient_rho``, carrying ``transient_share`` of idiosyncratic variance;
  it produces genuine short-horizon reversal that partly decays before a t+1 fill.
* ``e``: Student-t(4) idiosyncratic noise scaled by the volatility regime.
* Spreads widen and volume rises with volatility. Some names list late and
  some delist (one merger, one performance delisting); prices are NaN outside
  membership and ``members`` records it.

``legacy_v03`` - the 0.3.x generator, kept verbatim for the execution-lag
regression (2.1) and the re-baseline diff table (2.11): 1008 business days and
a premium planted on the signal's own z-score (circular by construction).

Timeline (realistic_v1)::

    |-- warm-up 252 --|------ in-sample 3528 (14 y) ------|-- out-of-sample 1260 (5 y) --|
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from ceap.analytics.signals import cross_sectional_zscore, get_signal
from ceap.data.calendar import trading_days_ending
from ceap.data.generator_params import (  # noqa: F401  (re-exported)
    ADV_MIN_OBS,
    ADV_WINDOW,
    CRASH_DAYS,
    CRASH_PREMIUM_MULTIPLIER,
    CRASH_REBOUND_DAYS,
    CRASH_TOTAL_RETURN,
    END_DATE,
    GARCH_ALPHA,
    GARCH_BETA,
    GENERATOR_LEGACY,
    GENERATOR_REALISTIC,
    IDIO_T_DF,
    IN_SAMPLE_DAYS,
    LEGACY_IN_SAMPLE_DAYS,
    LEGACY_N_DAYS,
    MARKET_ANNUAL_RETURN,
    MARKET_ANNUAL_VOL,
    MARKET_T_DF,
    MIN_DAILY_RETURN,
    N_DAYS,
    N_LATE_LISTINGS,
    NASDAQ,
    PERFORMANCE_DELIST_DRIFT,
    PERFORMANCE_DELIST_RUNUP,
    RESEARCH_UNIVERSE,
    SPREAD_VOL_ELASTICITY,
    TIER_A,
    TIER_B,
    TIER_C,
    TIER_OF,
    TIER_PROFILE,
    TRADING_DAYS_PER_YEAR,
    VOLUME_VOL_ELASTICITY,
    WARMUP_DAYS,
)
from ceap.data.research_scenarios import ResearchScenarioSpec, get_research_scenario
from ceap.data.scenarios import BASE_PRICES


def to_day(value: str | date | datetime | np.datetime64) -> np.datetime64:
    """Coerce an ISO date string, ``date``, aware ``datetime`` or numpy date to ``datetime64[D]``.

    A naive ``datetime`` raises: research dates are session dates and a naive
    timestamp has no defined session (2.4).
    """
    if isinstance(value, np.datetime64):
        return value.astype("datetime64[D]")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"naive datetime {value.isoformat()} is not allowed; pass a date or an aware datetime")
        from ceap.data.calendar import session_date

        return np.datetime64(session_date(value).isoformat(), "D")
    if isinstance(value, date):
        return np.datetime64(value.isoformat(), "D")
    text = str(value).strip()
    return np.datetime64(text[:10], "D")


def lagged_adv(volume: np.ndarray, window: int = ADV_WINDOW, min_obs: int = ADV_MIN_OBS) -> np.ndarray:
    """Average daily volume over the ``window`` days *before* each date (2.5: never includes the trade day)."""
    v = np.asarray(volume, dtype=float)
    finite = np.isfinite(v)
    vals = np.where(finite, v, 0.0)
    csum = np.vstack([np.zeros((1, v.shape[1])), np.cumsum(vals, axis=0)])
    ccnt = np.vstack([np.zeros((1, v.shape[1])), np.cumsum(finite, axis=0)])
    n = v.shape[0]
    hi = np.arange(n)  # exclusive end: rows < t
    lo = np.maximum(0, hi - window)
    total = csum[hi] - csum[lo]
    count = ccnt[hi] - ccnt[lo]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(count >= min_obs, total / np.maximum(count, 1), np.nan)
    return out


@dataclass
class HistoricalDataset:
    scenario: ResearchScenarioSpec
    symbols: tuple[str, ...]
    tiers: tuple[str, ...]
    dates: np.ndarray  # datetime64[D], shape (n_days,)
    open: np.ndarray  # (n_days, n_symbols); NaN outside membership
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    adv_20: np.ndarray  # lagged one day (2.5)
    spread_bps: np.ndarray
    beta: np.ndarray  # (n_symbols,)
    warmup_days: int = WARMUP_DAYS
    in_sample_days: int = IN_SAMPLE_DAYS
    members: np.ndarray | None = None  # bool (n_days, n_symbols); None = everyone, every day
    exchanges: tuple[str, ...] = ()
    delistings: dict[int, tuple[int, str]] = field(default_factory=dict)  # col -> (delisting day index, kind)
    market_return: np.ndarray | None = None  # generator market factor m_t
    generator: str = GENERATOR_REALISTIC

    def __post_init__(self) -> None:
        if self.members is None:
            self.members = np.isfinite(self.close)
        if not self.exchanges:
            self.exchanges = tuple("NASDAQ" if s in NASDAQ else "NYSE" for s in self.symbols)

    # ------------------------------------------------------------ geometry
    @property
    def member_mask(self) -> np.ndarray:
        assert self.members is not None
        return self.members

    @property
    def n_days(self) -> int:
        return int(self.dates.shape[0])

    @property
    def n_symbols(self) -> int:
        return len(self.symbols)

    @property
    def start_index(self) -> int:
        return self.warmup_days

    @property
    def in_sample_end_index(self) -> int:
        """The locked in-sample end (2.8): no caller can move the holdout boundary later."""
        return self.warmup_days + self.in_sample_days - 1

    @property
    def start(self) -> str:
        return str(self.dates[self.start_index])

    @property
    def in_sample_end(self) -> str:
        return str(self.dates[self.in_sample_end_index])

    @property
    def end(self) -> str:
        return str(self.dates[-1])

    @property
    def first_date(self) -> str:
        return str(self.dates[0])

    @property
    def dataset_hash(self) -> str:
        """Content address of the dataset: generator, scenario parameters and shape (for the trial ledger)."""
        from ceap.util.canonical import content_hash

        return content_hash(
            {
                "generator": self.generator,
                "scenario": {k: v for k, v in _spec_dict(self.scenario).items() if k not in ("id", "name", "description")},
                "n_days": self.n_days,
                "symbols": list(self.symbols),
                "in_sample_end": self.in_sample_end,
            }
        )

    # ------------------------------------------------------------- lookups
    def col(self, symbol: str) -> int:
        try:
            return self.symbols.index(symbol)
        except ValueError as exc:
            raise KeyError(f"{symbol} is not in dataset {self.scenario.id}") from exc

    def index_after(self, value: str | date | datetime | np.datetime64) -> int:
        """Index of the first trading day on or after ``value``."""
        i = int(np.searchsorted(self.dates, to_day(value), side="left"))
        if i >= self.n_days:
            raise ValueError(f"{to_day(value)} is after the last trading day {self.end}")
        return i

    def index_at_or_before(self, value: str | date | datetime | np.datetime64) -> int:
        """Index of the last trading day on or before ``value``."""
        i = int(np.searchsorted(self.dates, to_day(value), side="right")) - 1
        if i < 0:
            raise ValueError(f"{to_day(value)} is before the first trading day {self.first_date}")
        return i

    def index_range(
        self, start: str | date | datetime | None, end: str | date | datetime | None
    ) -> tuple[int, int]:
        """Inclusive ``(i0, i1)`` for ``[start, end]``; defaults to the post-warm-up history."""
        i0 = self.index_after(start) if start else self.start_index
        i1 = self.index_at_or_before(end) if end else self.n_days - 1
        if i1 < i0:
            raise ValueError("end must be on or after start")
        return i0, i1

    def locked_split(self, in_sample_end: str | None, i0: int, i1: int) -> int:
        """In-sample end index honouring the lock: a caller may shorten in-sample, never extend it."""
        split = self.in_sample_end_index
        if in_sample_end:
            split = min(split, self.index_at_or_before(in_sample_end))
        return min(max(split, i0), i1)

    def bars(self, symbol: str, i0: int, i1: int) -> list[dict[str, Any]]:
        j = self.col(symbol)

        def num(x: float, nd: int) -> float | None:
            return round(float(x), nd) if np.isfinite(x) else None

        return [
            {
                "date": str(self.dates[t]),
                "open": num(self.open[t, j], 4),
                "high": num(self.high[t, j], 4),
                "low": num(self.low[t, j], 4),
                "close": num(self.close[t, j], 4),
                "volume": int(self.volume[t, j]) if np.isfinite(self.volume[t, j]) else None,
                "adv_20": int(self.adv_20[t, j]) if np.isfinite(self.adv_20[t, j]) else None,
                "spread_bps": num(self.spread_bps[t, j], 3),
                "member": bool(self.member_mask[t, j]),
            }
            for t in range(i0, i1 + 1)
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.id,
            "template": self.scenario.template,
            "generator": self.generator,
            "symbols": self.n_symbols,
            "trading_days": self.n_days,
            "first_date": self.first_date,
            "start": self.start,
            "in_sample_end": self.in_sample_end,
            "end": self.end,
            "delistings": {self.symbols[j]: {"date": str(self.dates[t]), "kind": k} for j, (t, k) in self.delistings.items()},
        }


def _spec_dict(spec: ResearchScenarioSpec) -> dict[str, Any]:
    import dataclasses

    return {
        f.name: (getattr(spec, f.name).value if hasattr(getattr(spec, f.name), "value") else getattr(spec, f.name))
        for f in dataclasses.fields(spec)
        if f.name not in ("expected_verdict", "expected_flags", "tags")
    }


def _std_t(rng: np.random.Generator, df: float, shape: Any) -> np.ndarray:
    """Student-t draws rescaled to unit variance."""
    return rng.standard_t(df, shape) / np.sqrt(df / (df - 2.0))


class HistoricalGenerator:
    """Generates a :class:`HistoricalDataset` for a :class:`ResearchScenarioSpec`."""

    def __init__(
        self,
        n_days: int | None = None,
        end_date: date = END_DATE,
        warmup_days: int = WARMUP_DAYS,
        in_sample_days: int | None = None,
        symbols: tuple[str, ...] = RESEARCH_UNIVERSE,
    ) -> None:
        self._n_days = n_days
        self._in_sample_days = in_sample_days
        self.end_date = end_date
        self.warmup_days = warmup_days
        self.symbols = symbols
        n, ins = self.geometry(GENERATOR_REALISTIC)
        if warmup_days + ins >= n:
            raise ValueError("warm-up plus in-sample must leave an out-of-sample period")

    def geometry(self, generator: str) -> tuple[int, int]:
        legacy = generator == GENERATOR_LEGACY
        n = self._n_days or (LEGACY_N_DAYS if legacy else N_DAYS)
        ins = self._in_sample_days or (LEGACY_IN_SAMPLE_DAYS if legacy else IN_SAMPLE_DAYS)
        return n, ins

    def generate(self, spec: ResearchScenarioSpec) -> HistoricalDataset:
        if spec.generator == GENERATOR_LEGACY:
            return self._generate_legacy(spec)
        if spec.generator != GENERATOR_REALISTIC:
            raise ValueError(f"unknown generator {spec.generator!r}")
        return self._generate_realistic(spec)

    # --------------------------------------------------------------- shared
    def _symbol_params(
        self, rng: np.random.Generator, spec: ResearchScenarioSpec
    ) -> tuple[tuple[str, ...], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        m = len(self.symbols)
        tiers = tuple(TIER_OF.get(s, "C") for s in self.symbols)
        base_price = np.empty(m)
        adv_notional = np.empty(m)
        spread_base = np.empty(m)
        idio_daily = np.empty(m)
        for j, (sym, tier) in enumerate(zip(self.symbols, tiers, strict=True)):
            profile = TIER_PROFILE[tier]
            drawn = rng.uniform(*profile["price"])
            base_price[j] = BASE_PRICES.get(sym, drawn)
            adv_notional[j] = rng.uniform(*profile["adv_notional"]) * spec.adv_multiplier
            spread_base[j] = rng.uniform(*profile["spread_bps"]) * spec.spread_multiplier
            idio_daily[j] = profile["idio_vol"] / np.sqrt(TRADING_DAYS_PER_YEAR)
        return tiers, base_price, adv_notional, spread_base, idio_daily

    @staticmethod
    def _premium_mask(spec: ResearchScenarioSpec, tiers: tuple[str, ...], adv_notional: np.ndarray) -> np.ndarray:
        m = len(tiers)
        mask = np.ones(m)
        if spec.concentration_names > 0:
            tier_c = [j for j, t in enumerate(tiers) if t == "C"]
            chosen = sorted(tier_c, key=lambda j: adv_notional[j])[: spec.concentration_names]
            mask = np.zeros(m)
            mask[chosen] = 1.0
            adv_notional[chosen] *= spec.concentration_adv_multiplier
        return mask

    # ------------------------------------------------------------ realistic
    def _generate_realistic(self, spec: ResearchScenarioSpec) -> HistoricalDataset:
        rng = np.random.default_rng(spec.seed)
        n, in_sample_days = self.geometry(GENERATOR_REALISTIC)
        m = len(self.symbols)
        dates = trading_days_ending(self.end_date, n)
        tiers, base_price, adv_notional, spread_base, idio_daily = self._symbol_params(rng, spec)
        beta = rng.uniform(0.7, 1.3, m)
        premium_mask = self._premium_mask(spec, tiers, adv_notional)
        split = self.warmup_days + in_sample_days - 1

        # ---- noise, drawn in a fixed order
        z_market = _std_t(rng, MARKET_T_DF, n)
        z_idio = _std_t(rng, IDIO_T_DF, (n, m))
        z_mu = rng.normal(0.0, 1.0, (n, m))
        z_tr = rng.normal(0.0, 1.0, (n, m))
        gap = rng.normal(0.0, 1.0, (n, m))
        range_hi = np.abs(rng.normal(0.0, 1.0, (n, m)))
        range_lo = np.abs(rng.normal(0.0, 1.0, (n, m)))
        volume_noise = rng.lognormal(0.0, 0.35, (n, m))
        spread_noise = rng.lognormal(0.0, 0.15, (n, m))
        events = rng.random(16)

        # ---- market: GARCH(1,1), Student-t innovations
        var_bar = (MARKET_ANNUAL_VOL / np.sqrt(TRADING_DAYS_PER_YEAR)) ** 2
        omega = var_bar * (1.0 - GARCH_ALPHA - GARCH_BETA)
        h = np.empty(n)
        shock = np.empty(n)
        h[0] = var_bar
        shock[0] = np.sqrt(h[0]) * z_market[0]
        for t in range(1, n):
            h[t] = omega + GARCH_ALPHA * shock[t - 1] ** 2 + GARCH_BETA * h[t - 1]
            shock[t] = np.sqrt(h[t]) * z_market[t]
        market = MARKET_ANNUAL_RETURN / TRADING_DAYS_PER_YEAR + shock
        vol_ratio = np.sqrt(h / var_bar)
        crash_mult = np.ones(n)
        if spec.crash:
            c0 = split + 1 + int(events[0] * (n - split - CRASH_DAYS - CRASH_REBOUND_DAYS - 2))
            daily = (1.0 + CRASH_TOTAL_RETURN) ** (1.0 / CRASH_DAYS) - 1.0
            market[c0 : c0 + CRASH_DAYS] = daily + shock[c0 : c0 + CRASH_DAYS] * 2.0
            vol_ratio[c0 : c0 + CRASH_DAYS + CRASH_REBOUND_DAYS] *= 2.5
            crash_mult[c0 + CRASH_DAYS : c0 + CRASH_DAYS + CRASH_REBOUND_DAYS] = CRASH_PREMIUM_MULTIPLIER
        idio_scale = np.sqrt(vol_ratio)[:, None] * idio_daily[None, :]

        # ---- latent drift mu: AR(1) per name, cross-sectional sd = premium_bps per year
        mu = np.zeros((n, m))
        if spec.mechanism == "drift" and spec.premium_bps > 0:
            s = spec.premium_bps / 10_000.0 / TRADING_DAYS_PER_YEAR
            phi = 0.5 ** (1.0 / spec.half_life_days)
            innov = np.sqrt(1.0 - phi * phi) * s * z_mu
            mu[0] = s * z_mu[0]
            for t in range(1, n):
                mu[t] = phi * mu[t - 1] + innov[t]
            years = np.arange(n) / TRADING_DAYS_PER_YEAR
            decay = 0.5 ** (years / spec.decay_half_life_years) if spec.decay_half_life_years > 0 else np.ones(n)
            mu = mu * decay[:, None]
            # after the locked in-sample end the drift is multiplied. A *negative* multiplier does not make
            # 12-1 momentum fail for long: once the window lies in the new regime, past winners (now names
            # with low mu) keep winning. A lasting momentum failure needs the premium to vanish (multiplier 0).
            mu[split + 1 :] *= spec.oos_premium_multiplier
            mu = mu * crash_mult[:, None] * premium_mask[None, :]



        # ---- latent transient component T: AR(1); Var(dT) = share * idio variance
        dT = np.zeros((n, m))
        share = spec.transient_share if spec.mechanism == "transient" else 0.0
        if share > 0:
            rho = spec.transient_rho
            u = z_tr * idio_scale * np.sqrt(share * (1.0 + rho) / 2.0) * premium_mask[None, :]
            T = np.zeros((n, m))
            for t in range(1, n):
                T[t] = rho * T[t - 1] + u[t]
            dT[1:] = T[1:] - T[:-1]
            if spec.oos_premium_multiplier != 1.0:
                dT[split + 1 :] *= max(spec.oos_premium_multiplier, 0.0)
        eps = z_idio * idio_scale * np.sqrt(1.0 - share * premium_mask)[None, :]

        r = beta[None, :] * market[:, None] + mu + dT + eps

        # ---- membership: late listings and delistings
        members = np.ones((n, m), dtype=bool)
        delistings: dict[int, tuple[int, str]] = {}
        if spec.membership_events:
            concentrated = spec.concentration_names > 0
            pool = [j for j, t in enumerate(tiers) if t in ("B", "C") and not (concentrated and premium_mask[j] > 0)]
            order = [pool[int(events[1 + k] * len(pool)) % len(pool)] for k in range(8)]
            chosen: list[int] = []
            for j in order:
                if j not in chosen:
                    chosen.append(j)
            late, rest = chosen[:N_LATE_LISTINGS], chosen[N_LATE_LISTINGS:]
            lo_l, hi_l = self.warmup_days + 252, self.warmup_days + in_sample_days // 2
            for k, j in enumerate(late):
                start = lo_l + int(events[9 + k] * (hi_l - lo_l))
                members[:start, j] = False
            if len(rest) >= 2:
                j_merge, j_perf = rest[0], rest[1]
                d_merge = self.warmup_days + 500 + int(events[12] * (n - self.warmup_days - 700))
                d_perf = self.warmup_days + 500 + int(events[13] * (n - self.warmup_days - 700))
                r[d_perf - PERFORMANCE_DELIST_RUNUP : d_perf, j_perf] += PERFORMANCE_DELIST_DRIFT
                for j, d, kind in ((j_merge, d_merge, "merger"), (j_perf, d_perf, "performance")):
                    members[d:, j] = False
                    delistings[j] = (d, kind)

        r = np.maximum(r, MIN_DAILY_RETURN)
        close = base_price[None, :] * np.cumprod(1.0 + r, axis=0)
        # late listers: rebase so the listing price equals the base price
        for j in range(m):
            first = int(np.argmax(members[:, j])) if members[:, j].any() else n
            if first > 0 and first < n and j not in delistings:
                close[:, j] *= base_price[j] / close[first, j]

        open_ = np.empty_like(close)
        open_[0] = close[0]
        open_[1:] = close[:-1] * (1.0 + 0.3 * idio_scale[1:] * gap[1:])
        high = np.maximum(open_, close) * (1.0 + 0.6 * idio_scale * range_hi)
        low = np.minimum(open_, close) * (1.0 - 0.6 * idio_scale * range_lo)
        low = np.minimum(low, np.minimum(open_, close))
        volume = np.maximum(
            100.0, np.round(adv_notional[None, :] * volume_noise * vol_ratio[:, None] ** VOLUME_VOL_ELASTICITY / close)
        )
        spread_bps = spread_base[None, :] * spread_noise * vol_ratio[:, None] ** SPREAD_VOL_ELASTICITY
        for arr in (open_, high, low, close, volume, spread_bps):
            arr[~members] = np.nan
        adv_20 = lagged_adv(volume)

        return HistoricalDataset(
            scenario=spec,
            symbols=tuple(self.symbols),
            tiers=tiers,
            dates=dates,
            open=np.round(open_, 4),
            high=np.round(high, 4),
            low=np.round(low, 4),
            close=np.round(close, 4),
            volume=volume,
            adv_20=np.round(adv_20),
            spread_bps=spread_bps,
            beta=beta,
            warmup_days=self.warmup_days,
            in_sample_days=in_sample_days,
            members=members,
            delistings=delistings,
            market_return=market,
            generator=GENERATOR_REALISTIC,
        )

    # --------------------------------------------------------------- legacy
    def _generate_legacy(self, spec: ResearchScenarioSpec) -> HistoricalDataset:
        """The 0.3.x generator, unchanged except for the lagged ADV (kept for regression tests)."""
        rng = np.random.default_rng(spec.seed)
        n, in_sample_days = self.geometry(GENERATOR_LEGACY)
        m = len(self.symbols)
        dates = np.busday_offset(np.datetime64(self.end_date.isoformat(), "D"), -np.arange(n)[::-1])
        tiers, base_price, adv_notional, spread_base, idio_daily = self._symbol_params(rng, spec)
        beta = rng.uniform(0.7, 1.3, m)
        premium_mask = self._premium_mask(spec, tiers, adv_notional)
        market = rng.normal(
            MARKET_ANNUAL_RETURN / TRADING_DAYS_PER_YEAR, MARKET_ANNUAL_VOL / np.sqrt(TRADING_DAYS_PER_YEAR), n
        )
        eps = rng.normal(0.0, 1.0, (n, m)) * idio_daily[None, :]
        gap = rng.normal(0.0, 1.0, (n, m))
        range_hi = np.abs(rng.normal(0.0, 1.0, (n, m)))
        range_lo = np.abs(rng.normal(0.0, 1.0, (n, m)))
        volume_noise = rng.lognormal(0.0, 0.35, (n, m))
        spread_noise = rng.lognormal(0.0, 0.15, (n, m))
        signal = get_signal(spec.signal)
        lookback = signal.lookback_days
        k_daily = spec.premium_bps / 10_000.0 / TRADING_DAYS_PER_YEAR
        split = self.warmup_days + in_sample_days - 1
        close = np.empty((n, m))
        close[0] = base_price
        for t in range(1, n):
            lo = max(0, t - lookback - 2)
            history = close[lo:t]
            if history.shape[0] > lookback:
                z = cross_sectional_zscore(signal.fn(history, None)[-1])
                z = np.where(np.isfinite(z), z, 0.0)
            else:
                z = np.zeros(m)
            regime = spec.oos_premium_multiplier if t > split else 1.0
            r = beta * market[t] + k_daily * regime * premium_mask * z + eps[t]
            close[t] = close[t - 1] * (1.0 + np.maximum(r, MIN_DAILY_RETURN))
        open_ = np.empty_like(close)
        open_[0] = close[0]
        open_[1:] = close[:-1] * (1.0 + 0.3 * idio_daily[None, :] * gap[1:])
        high = np.maximum(open_, close) * (1.0 + 0.6 * idio_daily[None, :] * range_hi)
        low = np.minimum(open_, close) * (1.0 - 0.6 * idio_daily[None, :] * range_lo)
        low = np.minimum(low, np.minimum(open_, close))
        volume = np.maximum(100.0, np.round(adv_notional[None, :] * volume_noise / close))
        adv_20 = lagged_adv(volume)
        adv_20[0] = volume[0]
        spread_bps = spread_base[None, :] * spread_noise
        return HistoricalDataset(
            scenario=spec,
            symbols=tuple(self.symbols),
            tiers=tiers,
            dates=dates,
            open=np.round(open_, 4),
            high=np.round(high, 4),
            low=np.round(low, 4),
            close=np.round(close, 4),
            volume=volume,
            adv_20=np.round(adv_20),
            spread_bps=spread_bps,
            beta=beta,
            warmup_days=self.warmup_days,
            in_sample_days=in_sample_days,
            members=np.ones((n, m), dtype=bool),
            market_return=market,
            generator=GENERATOR_LEGACY,
        )


class HistoricalStore:
    """Thread-safe cache of generated research datasets keyed by scenario id and seed."""

    def __init__(self, generator: HistoricalGenerator | None = None) -> None:
        self._generator = generator or HistoricalGenerator()
        self._datasets: dict[str, HistoricalDataset] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(spec: ResearchScenarioSpec) -> str:
        return f"{spec.id}:{spec.seed}:{spec.generator}"

    def get(self, scenario: str | ResearchScenarioSpec = "R01") -> HistoricalDataset:
        spec = scenario if isinstance(scenario, ResearchScenarioSpec) else get_research_scenario(scenario)
        key = self._key(spec)
        with self._lock:
            ds = self._datasets.get(key)
            if ds is None:
                ds = self._generator.generate(spec)
                self._datasets[key] = ds
        return ds

    def put(self, dataset: HistoricalDataset) -> None:
        with self._lock:
            self._datasets[self._key(dataset.scenario)] = dataset

    def clear(self) -> None:
        with self._lock:
            self._datasets.clear()
