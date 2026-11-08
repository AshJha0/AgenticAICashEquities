"""Cost schedule for the real-data study (R5 item 5.4; preregistration section 5).

A standalone module, so the R2 backtest is not edited: it prices one rebalance of a long-short book
from weight changes and per-name inputs and returns costs as a fraction of gross book.

- Half-spread: 10 bps per side for trades dated 2005 to 2009, 5 bps from 2010.
- Square-root impact: ``Y * sigma_daily * sqrt(trade_usd / adv_usd)``, Y = 0.7, gross book USD 100M.
  A name without ADV gets the 95th-percentile impact rate of the book.
- Borrow on shorts: 0.3 % a year general collateral, plus 5 % a year for names in the bottom 5 % of
  the formation universe by 20-day median dollar volume; charged pro rata for the holding period.
- Corwin-Schultz (2012) high-low spread estimator: sensitivity only, never used by the test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

GROSS_BOOK_USD = 100e6
IMPACT_Y = 0.7
GC_BORROW = 0.003
HTB_BORROW_EXTRA = 0.05
HTB_BOTTOM_FRACTION = 0.05


def half_spread(trade_date: date) -> float:
    """Half-spread as a fraction of traded notional."""
    if trade_date.year < 2005:
        raise ValueError("cost schedule starts in 2005 (no trades are scored before)")
    return 0.0010 if trade_date.year <= 2009 else 0.0005


def impact_rate(sigma_daily: float, trade_usd: float, adv_usd: float, y: float = IMPACT_Y) -> float:
    """Square-root impact as a fraction of traded notional."""
    if trade_usd <= 0:
        return 0.0
    if adv_usd <= 0 or sigma_daily < 0:
        raise ValueError("adv_usd must be positive and sigma_daily non-negative")
    return y * sigma_daily * math.sqrt(trade_usd / adv_usd)


@dataclass(frozen=True)
class NameInput:
    weight_change: float  # signed change in weight; weights are fractions of gross book
    sigma_daily: float
    adv_usd: float | None


@dataclass(frozen=True)
class RebalanceCost:
    spread: float
    impact: float

    @property
    def total(self) -> float:
        return self.spread + self.impact


def rebalance_cost(
    trade_date: date, names: dict[str, NameInput], gross_book_usd: float = GROSS_BOOK_USD
) -> RebalanceCost:
    """Spread plus impact for one rebalance, as a fraction of gross book."""
    hs = half_spread(trade_date)
    spread = sum(abs(n.weight_change) for n in names.values()) * hs
    rates: dict[str, float] = {}
    for k, n in names.items():
        if n.adv_usd:
            rates[k] = impact_rate(n.sigma_daily, abs(n.weight_change) * gross_book_usd, n.adv_usd)
    fill = _percentile(sorted(rates.values()), 0.95) if rates else 0.0
    impact = sum(abs(n.weight_change) * rates.get(k, fill) for k, n in names.items())
    return RebalanceCost(spread, impact)


def borrow_cost(short_weights: dict[str, float], hard_to_borrow: set[str], days: int) -> float:
    """Borrow fee for the holding period, as a fraction of gross book (weights are positive sizes)."""
    year_frac = days / 360.0
    return sum(
        abs(w) * (GC_BORROW + (HTB_BORROW_EXTRA if k in hard_to_borrow else 0.0)) * year_frac
        for k, w in short_weights.items()
    )


def hard_to_borrow_set(dollar_volume: dict[str, float]) -> set[str]:
    """Bottom 5 % of the formation universe by 20-day median dollar volume (at least one name)."""
    if not dollar_volume:
        return set()
    n = max(1, math.floor(len(dollar_volume) * HTB_BOTTOM_FRACTION))
    return {k for k, _ in sorted(dollar_volume.items(), key=lambda kv: (kv[1], kv[0]))[:n]}


def breakeven_cost_bps(gross_monthly: list[float], turnover_monthly: list[float]) -> float:
    """One-way cost per unit traded (bps) at which mean net return is zero."""
    mean_turn = sum(turnover_monthly) / len(turnover_monthly)
    if mean_turn <= 0:
        return math.inf
    return sum(gross_monthly) / len(gross_monthly) / mean_turn * 1e4


def corwin_schultz_spread(high: list[float], low: list[float]) -> list[float]:
    """Corwin-Schultz (2012) two-day high-low spread estimates (negative estimates set to 0)."""
    k = 3.0 - 2.0 * math.sqrt(2.0)
    out = []
    for i in range(1, len(high)):
        beta = math.log(high[i - 1] / low[i - 1]) ** 2 + math.log(high[i] / low[i]) ** 2
        gamma = math.log(max(high[i - 1], high[i]) / min(low[i - 1], low[i])) ** 2
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
        s = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        out.append(max(0.0, s))
    return out


def _percentile(sorted_vals: list[float], q: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def sensitivity_table(
    gross_monthly: list[float],
    turnover_monthly: list[float],
    one_way_bps: tuple[float, ...] = (0, 5, 10, 20, 40),
) -> list[tuple[float, float]]:
    """(one-way cost bps, annualised mean net return) for a flat cost grid."""
    rows = []
    for c in one_way_bps:
        net = [g - t * c / 1e4 for g, t in zip(gross_monthly, turnover_monthly, strict=True)]
        rows.append((c, 12.0 * sum(net) / len(net)))
    return rows
