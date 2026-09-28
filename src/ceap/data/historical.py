"""Synthetic daily history for research: a 30-name universe over four years.

Seeded, fully deterministic factor model

    r[t, i] = beta_i * m_t + k_t * z[t-1, i] + eps[t, i]

where ``z`` is the scenario signal's clipped cross-sectional z-score computed
by ``ceap.analytics.signals`` on the path generated so far - the embedded
premium and the analytics that later measure it share one code path.

Timeline (1008 business days ending 2026-09-18):

    |-- warm-up 252 --|------ in-sample 504 ------|-- out-of-sample 252 --|
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np

from ceap.analytics.signals import cross_sectional_zscore, get_signal
from ceap.data.research_scenarios import ResearchScenarioSpec, get_research_scenario
from ceap.data.scenarios import BASE_PRICES, SYMBOLS

N_DAYS = 1008
WARMUP_DAYS = 252
IN_SAMPLE_DAYS = 504
END_DATE = date(2026, 9, 18)
TRADING_DAYS_PER_YEAR = 252
MARKET_ANNUAL_RETURN = 0.06
MARKET_ANNUAL_VOL = 0.16
MIN_DAILY_RETURN = -0.5

TIER_A: tuple[str, ...] = SYMBOLS + ("AVGO", "JPM", "XOM", "UNH")
TIER_B: tuple[str, ...] = ("ADBE", "CRM", "NKE", "SBUX", "INTC", "QCOM", "CVS", "GM", "F", "DAL", "UAL", "MRNA")
TIER_C: tuple[str, ...] = ("PLUG", "RIOT", "SOFI", "UPST", "AFRM", "RKLB", "JOBY", "OPEN")
RESEARCH_UNIVERSE: tuple[str, ...] = TIER_A + TIER_B + TIER_C
TIER_OF: dict[str, str] = {s: "A" for s in TIER_A} | {s: "B" for s in TIER_B} | {s: "C" for s in TIER_C}

# price range, average daily notional range (USD), quoted spread range (bps), idiosyncratic annual vol
TIER_PROFILE: dict[str, dict[str, Any]] = {
    "A": {"price": (150.0, 550.0), "adv_notional": (3.0e9, 3.0e10), "spread_bps": (1.5, 3.0), "idio_vol": 0.22},
    "B": {"price": (40.0, 300.0), "adv_notional": (8.0e7, 2.4e9), "spread_bps": (4.0, 8.0), "idio_vol": 0.28},
    "C": {"price": (30.0, 80.0), "adv_notional": (6.0e7, 3.2e8), "spread_bps": (15.0, 30.0), "idio_vol": 0.35},
}


def to_day(value: str | date | datetime | np.datetime64) -> np.datetime64:
    """Coerce an ISO date/datetime string, ``date``, ``datetime`` or numpy date to ``datetime64[D]``."""
    if isinstance(value, np.datetime64):
        return value.astype("datetime64[D]")
    if isinstance(value, datetime):
        return np.datetime64(value.date().isoformat(), "D")
    if isinstance(value, date):
        return np.datetime64(value.isoformat(), "D")
    text = str(value).strip()
    return np.datetime64(text[:10], "D")


@dataclass
class HistoricalDataset:
    scenario: ResearchScenarioSpec
    symbols: tuple[str, ...]
    tiers: tuple[str, ...]
    dates: np.ndarray  # datetime64[D], shape (n_days,)
    open: np.ndarray  # (n_days, n_symbols)
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    adv_20: np.ndarray
    spread_bps: np.ndarray
    beta: np.ndarray  # (n_symbols,)
    warmup_days: int = WARMUP_DAYS
    in_sample_days: int = IN_SAMPLE_DAYS

    # ------------------------------------------------------------ geometry
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

    def bars(self, symbol: str, i0: int, i1: int) -> list[dict[str, Any]]:
        j = self.col(symbol)
        return [
            {
                "date": str(self.dates[t]),
                "open": round(float(self.open[t, j]), 4),
                "high": round(float(self.high[t, j]), 4),
                "low": round(float(self.low[t, j]), 4),
                "close": round(float(self.close[t, j]), 4),
                "volume": int(self.volume[t, j]),
                "adv_20": int(self.adv_20[t, j]),
                "spread_bps": round(float(self.spread_bps[t, j]), 3),
            }
            for t in range(i0, i1 + 1)
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.id,
            "template": self.scenario.template,
            "symbols": self.n_symbols,
            "trading_days": self.n_days,
            "first_date": self.first_date,
            "start": self.start,
            "in_sample_end": self.in_sample_end,
            "end": self.end,
        }


class HistoricalGenerator:
    """Generates a :class:`HistoricalDataset` for a :class:`ResearchScenarioSpec`."""

    def __init__(
        self,
        n_days: int = N_DAYS,
        end_date: date = END_DATE,
        warmup_days: int = WARMUP_DAYS,
        in_sample_days: int = IN_SAMPLE_DAYS,
        symbols: tuple[str, ...] = RESEARCH_UNIVERSE,
    ) -> None:
        if warmup_days + in_sample_days >= n_days:
            raise ValueError("warm-up plus in-sample must leave an out-of-sample period")
        self.n_days = n_days
        self.end_date = end_date
        self.warmup_days = warmup_days
        self.in_sample_days = in_sample_days
        self.symbols = symbols

    def generate(self, spec: ResearchScenarioSpec) -> HistoricalDataset:
        rng = np.random.default_rng(spec.seed)
        n, m = self.n_days, len(self.symbols)
        dates = np.busday_offset(np.datetime64(self.end_date.isoformat(), "D"), -np.arange(n)[::-1])
        tiers = tuple(TIER_OF.get(s, "C") for s in self.symbols)

        # ---- per-symbol parameters (drawn in a fixed order for determinism)
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
        beta = rng.uniform(0.7, 1.3, m)

        premium_mask = np.ones(m)
        if spec.concentration_names > 0:
            tier_c = [j for j, t in enumerate(tiers) if t == "C"]
            chosen = sorted(tier_c, key=lambda j: adv_notional[j])[: spec.concentration_names]
            premium_mask = np.zeros(m)
            premium_mask[chosen] = 1.0
            adv_notional[chosen] *= spec.concentration_adv_multiplier

        # ---- noise (pre-drawn so the path loop is deterministic and cheap)
        market = rng.normal(
            MARKET_ANNUAL_RETURN / TRADING_DAYS_PER_YEAR,
            MARKET_ANNUAL_VOL / np.sqrt(TRADING_DAYS_PER_YEAR),
            n,
        )
        eps = rng.normal(0.0, 1.0, (n, m)) * idio_daily[None, :]
        gap = rng.normal(0.0, 1.0, (n, m))
        range_hi = np.abs(rng.normal(0.0, 1.0, (n, m)))
        range_lo = np.abs(rng.normal(0.0, 1.0, (n, m)))
        volume_noise = rng.lognormal(0.0, 0.35, (n, m))
        spread_noise = rng.lognormal(0.0, 0.15, (n, m))

        # ---- price path with the embedded premium
        signal = get_signal(spec.signal)
        lookback = signal.lookback_days
        k_daily = spec.premium_bps / 10_000.0 / TRADING_DAYS_PER_YEAR
        split = self.warmup_days + self.in_sample_days - 1
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

        # ---- bars, volume, liquidity
        open_ = np.empty_like(close)
        open_[0] = close[0]
        open_[1:] = close[:-1] * (1.0 + 0.3 * idio_daily[None, :] * gap[1:])
        high = np.maximum(open_, close) * (1.0 + 0.6 * idio_daily[None, :] * range_hi)
        low = np.minimum(open_, close) * (1.0 - 0.6 * idio_daily[None, :] * range_lo)
        low = np.minimum(low, np.minimum(open_, close))
        volume = np.maximum(100.0, np.round(adv_notional[None, :] * volume_noise / close))
        csum = np.cumsum(volume, axis=0)
        adv_20 = np.empty_like(volume)
        for t in range(n):
            lo = max(0, t - 19)
            total = csum[t] - (csum[lo - 1] if lo > 0 else 0.0)
            adv_20[t] = total / (t - lo + 1)
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
            in_sample_days=self.in_sample_days,
        )


class HistoricalStore:
    """Thread-safe cache of generated research datasets keyed by scenario id and seed."""

    def __init__(self, generator: HistoricalGenerator | None = None) -> None:
        self._generator = generator or HistoricalGenerator()
        self._datasets: dict[str, HistoricalDataset] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(spec: ResearchScenarioSpec) -> str:
        return f"{spec.id}:{spec.seed}"

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
