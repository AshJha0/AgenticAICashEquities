"""Generator constants shared by the generator and the analytic ground truth (no imports of the catalogue)."""

from __future__ import annotations

from datetime import date
from typing import Any

from ceap.data.scenarios import SYMBOLS

GENERATOR_REALISTIC = "realistic_v1"
GENERATOR_LEGACY = "legacy_v03"

N_DAYS = 5040
WARMUP_DAYS = 252
IN_SAMPLE_DAYS = 3528
LEGACY_N_DAYS = 1008
LEGACY_IN_SAMPLE_DAYS = 504
END_DATE = date(2026, 9, 18)
TRADING_DAYS_PER_YEAR = 252
MARKET_ANNUAL_RETURN = 0.06
MARKET_ANNUAL_VOL = 0.16
MIN_DAILY_RETURN = -0.5
GARCH_ALPHA = 0.08
GARCH_BETA = 0.90
MARKET_T_DF = 5.0
IDIO_T_DF = 4.0
SPREAD_VOL_ELASTICITY = 0.7
VOLUME_VOL_ELASTICITY = 0.5
CRASH_DAYS = 15
CRASH_TOTAL_RETURN = -0.30
CRASH_REBOUND_DAYS = 60
CRASH_PREMIUM_MULTIPLIER = -3.0
ADV_WINDOW = 20
ADV_MIN_OBS = 5
PERFORMANCE_DELIST_DRIFT = -0.008  # daily log drift over the 120 days before a performance delisting
PERFORMANCE_DELIST_RUNUP = 120
N_LATE_LISTINGS = 3

TIER_A: tuple[str, ...] = SYMBOLS + ("AVGO", "JPM", "XOM", "UNH")
TIER_B: tuple[str, ...] = ("ADBE", "CRM", "NKE", "SBUX", "INTC", "QCOM", "CVS", "GM", "F", "DAL", "UAL", "MRNA")
TIER_C: tuple[str, ...] = ("PLUG", "RIOT", "SOFI", "UPST", "AFRM", "RKLB", "JOBY", "OPEN")
RESEARCH_UNIVERSE: tuple[str, ...] = TIER_A + TIER_B + TIER_C
TIER_OF: dict[str, str] = {s: "A" for s in TIER_A} | {s: "B" for s in TIER_B} | {s: "C" for s in TIER_C}
NASDAQ: frozenset[str] = frozenset(
    {"AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "ADBE", "SBUX", "INTC", "QCOM", "MRNA", "PLUG",
     "RIOT", "SOFI", "UPST", "AFRM", "RKLB", "OPEN"}
)


# D5 (0.9.0): a 500-name synthetic universe, so that a decile is 50 names rather than 3.
# Names are ``X<tier><4 digits>`` (no real ticker has that shape); tier shares echo the 30-name
# universe in spirit (A 40 %, B 40 %, C 20 %: a choice, not a calibration) and every other name
# is listed on NASDAQ.
SYNTHETIC_TIER_SHARES: dict[str, float] = {"A": 0.4, "B": 0.4, "C": 0.2}


def synthetic_universe(n: int = 500, shares: dict[str, float] | None = None) -> tuple[str, ...]:
    """``n`` synthetic names split across tiers A/B/C by ``shares`` (largest remainder)."""
    if n < 3:
        raise ValueError("a synthetic universe needs at least 3 names")
    shares = shares or SYNTHETIC_TIER_SHARES
    raw = {t: shares[t] * n for t in ("A", "B", "C")}
    count = {t: int(v) for t, v in raw.items()}
    for t in sorted(raw, key=lambda t: raw[t] - count[t], reverse=True)[: n - sum(count.values())]:
        count[t] += 1
    return tuple(f"X{t}{k:04d}" for t in ("A", "B", "C") for k in range(1, count[t] + 1))


def _is_synthetic(sym: str) -> bool:
    return len(sym) == 6 and sym[0] == "X" and sym[1] in "ABC" and sym[2:].isdigit()


def tier_of(sym: str) -> str:
    """Liquidity tier of a research name: the table for the 30 named stocks, the encoded tier for a
    synthetic name, ``C`` otherwise (the generator's long-standing default)."""
    if sym in TIER_OF:
        return TIER_OF[sym]
    return sym[1] if _is_synthetic(sym) else "C"


def is_nasdaq(sym: str) -> bool:
    return sym in NASDAQ or (_is_synthetic(sym) and int(sym[2:]) % 2 == 1)


RESEARCH_UNIVERSE_500: tuple[str, ...] = synthetic_universe(500)
UNIVERSES: dict[str, tuple[str, ...]] = {"core30": RESEARCH_UNIVERSE, "synthetic500": RESEARCH_UNIVERSE_500}

# price range, average daily notional range (USD), quoted spread range (bps), idiosyncratic annual vol
TIER_PROFILE: dict[str, dict[str, Any]] = {
    "A": {"price": (150.0, 550.0), "adv_notional": (3.0e9, 3.0e10), "spread_bps": (1.5, 3.0), "idio_vol": 0.22},
    "B": {"price": (40.0, 300.0), "adv_notional": (8.0e7, 2.4e9), "spread_bps": (4.0, 8.0), "idio_vol": 0.28},
    "C": {"price": (30.0, 80.0), "adv_notional": (6.0e7, 3.2e8), "spread_bps": (15.0, 30.0), "idio_vol": 0.35},
}
