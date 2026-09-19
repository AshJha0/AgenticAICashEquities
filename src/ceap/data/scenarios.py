"""Scenario catalogue with structured ground truth.

Ten scenario *templates* x five symbols = 50 evaluation scenarios. Each
carries the causes the platform is expected to identify, which the
evaluation suite compares against the deterministic attribution and the
final report.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ceap.analytics.attribution import Cause

SYMBOLS: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL")
BASE_PRICES: dict[str, float] = {
    "AAPL": 230.0,
    "MSFT": 420.0,
    "NVDA": 125.0,
    "AMZN": 185.0,
    "META": 520.0,
    "GOOGL": 170.0,
}
VENUES: tuple[str, ...] = ("XNAS", "ARCA", "BATS", "IEX", "DARK1")
VENUE_WEIGHTS: tuple[float, ...] = (0.34, 0.22, 0.20, 0.12, 0.12)


@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    template: str
    name: str
    description: str
    symbol: str
    seed: int
    ground_truth: tuple[Cause, ...]
    # market regime inside the investigation window
    volatility_multiplier: float = 1.0
    spread_multiplier: float = 1.0
    depth_multiplier: float = 1.0
    # venue behaviour
    degraded_venue: str | None = None
    degraded_fill_rate: float = 0.45
    degraded_extra_slippage_bps: float = 4.0
    # technology
    latency_multiplier: float = 1.0
    reject_rate: float = 0.0
    deployment_in_window: bool = False
    # market data quality
    stale_quote_fraction: float = 0.0
    crossed_quotes: int = 0
    # order characteristics
    order_size_multiplier: float = 1.0
    # exogenous price move (trend over ``price_move_minutes``)
    price_move_bps: float = 0.0
    price_move_minutes: int = 10
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary_cause(self) -> Cause:
        return self.ground_truth[0]


SCENARIO_TEMPLATES: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        id="T01",
        template="normal_vwap",
        name="Normal VWAP execution",
        description="Benign market, healthy venues, no technology events.",
        symbol="AAPL",
        seed=1,
        ground_truth=(Cause.NORMAL,),
    ),
    ScenarioSpec(
        id="T02",
        template="high_volatility",
        name="High volatility",
        description="Realised volatility triples inside the window.",
        symbol="AAPL",
        seed=2,
        ground_truth=(Cause.MARKET_VOLATILITY,),
        volatility_multiplier=3.0,
    ),
    ScenarioSpec(
        id="T03",
        template="wide_spreads",
        name="Wide spreads",
        description="Quoted spreads widen ~2.5x inside the window.",
        symbol="AAPL",
        seed=3,
        ground_truth=(Cause.WIDE_SPREADS,),
        spread_multiplier=2.5,
    ),
    ScenarioSpec(
        id="T04",
        template="low_liquidity",
        name="Low liquidity",
        description="Displayed depth collapses to ~30% of baseline.",
        symbol="AAPL",
        seed=4,
        ground_truth=(Cause.LOW_LIQUIDITY,),
        depth_multiplier=0.3,
    ),
    ScenarioSpec(
        id="T05",
        template="venue_degradation",
        name="Venue degradation",
        description="ARCA fill rate halves and slippage increases while other venues are normal.",
        symbol="AAPL",
        seed=5,
        ground_truth=(Cause.VENUE_DEGRADATION,),
        degraded_venue="ARCA",
    ),
    ScenarioSpec(
        id="T06",
        template="technology_latency",
        name="Technology latency",
        description="Order-gateway latency spikes 10x with rejects after a router deployment.",
        symbol="AAPL",
        seed=6,
        ground_truth=(Cause.TECHNOLOGY_LATENCY,),
        latency_multiplier=10.0,
        reject_rate=0.10,
        deployment_in_window=True,
    ),
    ScenarioSpec(
        id="T07",
        template="market_data_anomaly",
        name="Market-data anomaly",
        description="Stale and crossed quotes from the consolidated feed inside the window.",
        symbol="AAPL",
        seed=7,
        ground_truth=(Cause.MARKET_DATA_ANOMALY,),
        stale_quote_fraction=0.15,
        crossed_quotes=20,
    ),
    ScenarioSpec(
        id="T08",
        template="large_parent_order",
        name="Large parent order",
        description="Parent order is 10x the usual size, driving participation and impact.",
        symbol="AAPL",
        seed=8,
        ground_truth=(Cause.LARGE_ORDER_IMPACT,),
        order_size_multiplier=10.0,
    ),
    ScenarioSpec(
        id="T09",
        template="unexpected_price_move",
        name="Unexpected price movement",
        description="A 200 bps adverse trend over ten minutes with otherwise normal volatility.",
        symbol="AAPL",
        seed=9,
        ground_truth=(Cause.PRICE_MOVEMENT,),
        price_move_bps=200.0,
    ),
    ScenarioSpec(
        id="T10",
        template="mixed_market_technology",
        name="Mixed market + technology event",
        description="High volatility coincides with a latency spike after a deployment.",
        symbol="AAPL",
        seed=10,
        ground_truth=(Cause.MARKET_VOLATILITY, Cause.TECHNOLOGY_LATENCY),
        volatility_multiplier=3.0,
        latency_multiplier=8.0,
        reject_rate=0.08,
        deployment_in_window=True,
    ),
)

_EVAL_SYMBOLS = SYMBOLS[:5]


def all_scenarios() -> list[ScenarioSpec]:
    """50 concrete scenarios: every template across five symbols with distinct seeds."""
    out: list[ScenarioSpec] = []
    n = 0
    for tmpl in SCENARIO_TEMPLATES:
        for k, sym in enumerate(_EVAL_SYMBOLS):
            n += 1
            out.append(replace(tmpl, id=f"S{n:02d}", symbol=sym, seed=tmpl.seed * 100 + k))
    return out


def get_scenario(scenario_id: str) -> ScenarioSpec:
    for s in SCENARIO_TEMPLATES:
        if s.id == scenario_id or s.template == scenario_id:
            return s
    for s in all_scenarios():
        if s.id == scenario_id:
            return s
    raise KeyError(f"Unknown scenario: {scenario_id}")


def scenario_for(template: str, symbol: str, seed: int | None = None) -> ScenarioSpec:
    base = get_scenario(template)
    return replace(base, symbol=symbol, seed=seed if seed is not None else base.seed)
