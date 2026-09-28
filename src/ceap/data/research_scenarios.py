"""Research scenario catalogue with structured ground truth.

Seven scenario *templates* x three seeds = 21 evaluation scenarios. Each carries
the verdict and the flags the platform is expected to reach, which the research
evaluation suite compares against the deterministic assessment and the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ceap.domain.research import ResearchFlag, ResearchVerdict

SEEDS_PER_TEMPLATE = 3


@dataclass(frozen=True)
class ResearchScenarioSpec:
    id: str
    template: str
    name: str
    description: str
    signal: str
    seed: int
    expected_verdict: ResearchVerdict
    expected_flags: tuple[ResearchFlag, ...] = ()
    rebalance_days: int = 21
    long_short: bool = True
    # embedded effect: annualised bps of return per unit of the signal's cross-sectional z-score
    premium_bps: float = 0.0
    # multiplier applied to the premium after the in-sample period (regime break when < 1)
    oos_premium_multiplier: float = 1.0
    # liquidity profile of the whole universe
    spread_multiplier: float = 1.0
    adv_multiplier: float = 1.0
    # alpha concentrated in the least liquid names only
    concentration_names: int = 0
    concentration_adv_multiplier: float = 1.0
    tags: tuple[str, ...] = field(default_factory=tuple)


RESEARCH_TEMPLATES: tuple[ResearchScenarioSpec, ...] = (
    ResearchScenarioSpec(
        id="R01",
        template="momentum_premium",
        name="Momentum premium",
        description="A persistent 12-1 momentum premium across the universe; costs are modest.",
        signal="momentum_12_1",
        seed=11,
        expected_verdict=ResearchVerdict.PROMOTE,
        premium_bps=2000.0,
    ),
    ResearchScenarioSpec(
        id="R02",
        template="no_alpha",
        name="No alpha",
        description="Pure factor-plus-noise returns; the signal has no predictive power.",
        signal="momentum_12_1",
        seed=12,
        expected_verdict=ResearchVerdict.REJECT,
        expected_flags=(ResearchFlag.NO_ALPHA,),
    ),
    ResearchScenarioSpec(
        id="R03",
        template="reversal_premium",
        name="Short-term reversal premium",
        description="A five-day reversal premium traded weekly on a liquid universe.",
        signal="reversal_5",
        seed=13,
        expected_verdict=ResearchVerdict.PROMOTE,
        rebalance_days=5,
        premium_bps=5000.0,
    ),
    ResearchScenarioSpec(
        id="R04",
        template="regime_break",
        name="Regime break",
        description="Momentum works in-sample and reverses out-of-sample: the in-sample fit is overfit.",
        signal="momentum_12_1",
        seed=14,
        expected_verdict=ResearchVerdict.REJECT,
        expected_flags=(ResearchFlag.OVERFIT,),
        premium_bps=2000.0,
        oos_premium_multiplier=-0.5,
    ),
    ResearchScenarioSpec(
        id="R05",
        template="cost_drag",
        name="Cost drag",
        description="A genuine reversal premium that transaction costs consume: spreads are 15x wider.",
        signal="reversal_5",
        seed=15,
        expected_verdict=ResearchVerdict.REJECT,
        expected_flags=(ResearchFlag.COST_DRAG,),
        rebalance_days=5,
        premium_bps=5000.0,
        spread_multiplier=15.0,
    ),
    ResearchScenarioSpec(
        id="R06",
        template="concentration",
        name="Concentrated alpha",
        description="The premium lives in four illiquid names; the book breaches participation limits.",
        signal="momentum_12_1",
        seed=16,
        expected_verdict=ResearchVerdict.REJECT,
        expected_flags=(ResearchFlag.CONCENTRATION, ResearchFlag.LIMIT_BREACH),
        premium_bps=4000.0,
        concentration_names=4,
        concentration_adv_multiplier=0.05,
    ),
    ResearchScenarioSpec(
        id="R07",
        template="mixed",
        name="Momentum with moderate costs",
        description="The momentum premium on a less liquid universe with 4x spreads; costs are real but bearable.",
        signal="momentum_12_1",
        seed=17,
        expected_verdict=ResearchVerdict.PROMOTE,
        premium_bps=2000.0,
        spread_multiplier=4.0,
        adv_multiplier=0.5,
    ),
)


def all_research_scenarios() -> list[ResearchScenarioSpec]:
    """21 concrete scenarios: every template with three distinct seeds."""
    out: list[ResearchScenarioSpec] = []
    n = 0
    for tmpl in RESEARCH_TEMPLATES:
        for k in range(SEEDS_PER_TEMPLATE):
            n += 1
            out.append(replace(tmpl, id=f"RS{n:02d}", seed=tmpl.seed * 100 + k))
    return out


def get_research_scenario(scenario_id: str) -> ResearchScenarioSpec:
    for s in RESEARCH_TEMPLATES:
        if s.id == scenario_id or s.template == scenario_id:
            return s
    for s in all_research_scenarios():
        if s.id == scenario_id:
            return s
    raise KeyError(f"Unknown research scenario: {scenario_id}")


def research_scenario_for(template: str, seed: int | None = None) -> ResearchScenarioSpec:
    base = get_research_scenario(template)
    return replace(base, seed=seed if seed is not None else base.seed)
