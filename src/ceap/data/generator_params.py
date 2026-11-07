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

# price range, average daily notional range (USD), quoted spread range (bps), idiosyncratic annual vol
TIER_PROFILE: dict[str, dict[str, Any]] = {
    "A": {"price": (150.0, 550.0), "adv_notional": (3.0e9, 3.0e10), "spread_bps": (1.5, 3.0), "idio_vol": 0.22},
    "B": {"price": (40.0, 300.0), "adv_notional": (8.0e7, 2.4e9), "spread_bps": (4.0, 8.0), "idio_vol": 0.28},
    "C": {"price": (30.0, 80.0), "adv_notional": (6.0e7, 3.2e8), "spread_bps": (15.0, 30.0), "idio_vol": 0.35},
}
