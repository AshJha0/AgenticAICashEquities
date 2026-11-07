"""Research scenario catalogue; ground truth is derived from generator parameters (2.9, 2.11).

Ten scenario *templates* x three seeds = 30 evaluation scenarios. A template
states only generator parameters. Its ``expected_verdict`` and ``expected_flags``
are computed by :func:`ceap.data.ground_truth.derive_ground_truth` from those
parameters (premium against the detectable threshold, analytic costs, the
mechanism switches) - never by running the backtest. Scenarios that flip to
REJECT under t+1 execution stay REJECT (decision D5).

``LEGACY_TEMPLATES`` keep the 0.3.x scenarios on the legacy generator with
their old hand-written verdicts; they exist only for the execution-lag
regression (2.1) and the re-baseline diff table (2.11).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache

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
    expected_verdict: ResearchVerdict = ResearchVerdict.REJECT
    expected_flags: tuple[ResearchFlag, ...] = ()
    rebalance_days: int = 21
    long_short: bool = True
    generator: str = "realistic_v1"
    # premium mechanism: "drift" (latent AR(1) expected return), "transient" (latent AR(1) price component), "none"
    mechanism: str = "none"
    # drift: annualised cross-sectional sd of expected return, bps; legacy: bps per unit of the signal z-score
    premium_bps: float = 0.0
    half_life_days: float = 252.0
    decay_half_life_years: float = 0.0
    transient_share: float = 0.0
    transient_rho: float = 0.6
    # multiplier applied to the premium after the locked in-sample end (regime break when < 1)
    oos_premium_multiplier: float = 1.0
    crash: bool = False
    membership_events: bool = True
    # liquidity profile of the whole universe
    spread_multiplier: float = 1.0
    adv_multiplier: float = 1.0
    # alpha concentrated in the least liquid names only
    concentration_names: int = 0
    concentration_adv_multiplier: float = 1.0
    tags: tuple[str, ...] = field(default_factory=tuple)


_MOM = "momentum_12_1"
_REV = "reversal_5"

RESEARCH_TEMPLATES_RAW: tuple[ResearchScenarioSpec, ...] = (
    ResearchScenarioSpec(
        id="R01", template="momentum_premium", name="Momentum premium",
        description="A persistent latent expected-return component that 12-1 momentum detects through past returns.",
        signal=_MOM, seed=11, mechanism="drift", premium_bps=2400.0, half_life_days=252.0,
        tags=("strong_premium",),
    ),
    ResearchScenarioSpec(
        id="R02", template="no_alpha", name="No alpha",
        description="Pure factor-plus-noise returns with fat tails and volatility clustering; nothing to find.",
        signal=_MOM, seed=12,
    ),
    ResearchScenarioSpec(
        id="R03", template="reversal_premium", name="Short-term reversal premium",
        description="A transient price component that mean-reverts over days, traded weekly at the next close.",
        signal=_REV, seed=13, rebalance_days=5, mechanism="transient", transient_share=0.45, transient_rho=0.6,
    ),
    ResearchScenarioSpec(
        id="R04", template="regime_break", name="Regime break",
        description="The momentum premium works in-sample and reverses after the locked in-sample end.",
        signal=_MOM, seed=14, mechanism="drift", premium_bps=2400.0, oos_premium_multiplier=-0.5,
    ),
    ResearchScenarioSpec(
        id="R05", template="cost_drag", name="Cost drag",
        description="A genuine reversal premium that transaction costs consume: spreads are 15x wider.",
        signal=_REV, seed=15, rebalance_days=5, mechanism="transient", transient_share=0.30, transient_rho=0.6,
        spread_multiplier=15.0,
    ),
    ResearchScenarioSpec(
        id="R06", template="concentration", name="Concentrated alpha",
        description="The premium lives in four illiquid names; the book breaches participation limits.",
        signal=_MOM, seed=16, mechanism="drift", premium_bps=9000.0, concentration_names=4,
        concentration_adv_multiplier=0.05,
    ),
    ResearchScenarioSpec(
        id="R07", template="mixed", name="Momentum with moderate costs",
        description="A decaying momentum premium on a less liquid universe with 4x spreads; costs are real.",
        signal=_MOM, seed=17, mechanism="drift", premium_bps=2400.0, spread_multiplier=4.0, adv_multiplier=0.5,
        decay_half_life_years=30.0,
    ),
    ResearchScenarioSpec(
        id="R08", template="momentum_near_threshold", name="Momentum near the hurdle",
        description="A smaller momentum premium whose expected t-statistic sits within one standard error of the hurdle.",
        signal=_MOM, seed=18, mechanism="drift", premium_bps=1800.0, tags=("realistic_premium",),
    ),
    ResearchScenarioSpec(
        id="R09", template="reversal_near_threshold", name="Reversal near the hurdle",
        description="A weaker transient component; net Sharpe expected within one standard error of the hurdle.",
        signal=_REV, seed=19, rebalance_days=5, mechanism="transient", transient_share=0.40, transient_rho=0.6,
        tags=("realistic_premium",),
    ),
    ResearchScenarioSpec(
        id="R10", template="momentum_crash", name="Momentum with a crash",
        description="The momentum premium with a 30% market crash and a momentum crash in the rebound, out-of-sample.",
        signal=_MOM, seed=20, mechanism="drift", premium_bps=2400.0, crash=True,

    ),
)

LEGACY_TEMPLATES: tuple[ResearchScenarioSpec, ...] = (
    ResearchScenarioSpec("L01", "momentum_premium", "Momentum premium (0.3.x)", "legacy", _MOM, 11,
                         ResearchVerdict.PROMOTE, generator="legacy_v03", premium_bps=2000.0, membership_events=False),
    ResearchScenarioSpec("L02", "no_alpha", "No alpha (0.3.x)", "legacy", _MOM, 12, ResearchVerdict.REJECT,
                         (ResearchFlag.NO_ALPHA,), generator="legacy_v03", membership_events=False),
    ResearchScenarioSpec("L03", "reversal_premium", "Reversal premium (0.3.x)", "legacy", _REV, 13,
                         ResearchVerdict.PROMOTE, rebalance_days=5, generator="legacy_v03", premium_bps=5000.0,
                         membership_events=False),
    ResearchScenarioSpec("L04", "regime_break", "Regime break (0.3.x)", "legacy", _MOM, 14, ResearchVerdict.REJECT,
                         (ResearchFlag.OOS_DECAY,), generator="legacy_v03", premium_bps=2000.0,
                         oos_premium_multiplier=-0.5, membership_events=False),
    ResearchScenarioSpec("L05", "cost_drag", "Cost drag (0.3.x)", "legacy", _REV, 15, ResearchVerdict.REJECT,
                         (ResearchFlag.COST_DRAG,), rebalance_days=5, generator="legacy_v03", premium_bps=5000.0,
                         spread_multiplier=15.0, membership_events=False),
    ResearchScenarioSpec("L06", "concentration", "Concentrated alpha (0.3.x)", "legacy", _MOM, 16,
                         ResearchVerdict.REJECT, (ResearchFlag.CONCENTRATION, ResearchFlag.LIMIT_BREACH),
                         generator="legacy_v03", premium_bps=4000.0, concentration_names=4,
                         concentration_adv_multiplier=0.05, membership_events=False),
    ResearchScenarioSpec("L07", "mixed", "Momentum with moderate costs (0.3.x)", "legacy", _MOM, 17,
                         ResearchVerdict.PROMOTE, generator="legacy_v03", premium_bps=2000.0, spread_multiplier=4.0,
                         adv_multiplier=0.5, membership_events=False),
)


def with_ground_truth(spec: ResearchScenarioSpec) -> ResearchScenarioSpec:
    """Attach the parameter-derived verdict and flags (legacy specs keep their 0.3.x truth)."""
    if spec.generator != "realistic_v1":
        return spec
    from ceap.data.ground_truth import derive_ground_truth

    truth = derive_ground_truth(spec)
    return replace(spec, expected_verdict=truth.verdict, expected_flags=truth.flags)


def _templates() -> tuple[ResearchScenarioSpec, ...]:
    return RESEARCH_TEMPLATES



@lru_cache(maxsize=1)
def _all() -> tuple[ResearchScenarioSpec, ...]:
    out: list[ResearchScenarioSpec] = []
    n = 0
    for tmpl in RESEARCH_TEMPLATES_RAW:
        for k in range(SEEDS_PER_TEMPLATE):
            n += 1
            out.append(with_ground_truth(replace(tmpl, id=f"RS{n:02d}", seed=tmpl.seed * 100 + k)))
    return tuple(out)


def all_research_scenarios() -> list[ResearchScenarioSpec]:
    """30 concrete scenarios: every template with three distinct seeds."""
    return list(_all())


def get_research_scenario(scenario_id: str) -> ResearchScenarioSpec:
    for s in _templates():
        if s.id == scenario_id or s.template == scenario_id:
            return s
    for s in LEGACY_TEMPLATES:
        if s.id == scenario_id:
            return s
    for s in _all():
        if s.id == scenario_id:
            return s
    raise KeyError(f"Unknown research scenario: {scenario_id}")


def research_scenario_for(template: str, seed: int | None = None) -> ResearchScenarioSpec:
    base = get_research_scenario(template)
    return replace(base, seed=seed if seed is not None else base.seed)


def noise_scenario(seed: int, signal: str = _MOM, rebalance_days: int = 21) -> ResearchScenarioSpec:
    """A member of the pure-noise set used by the null sweep (2.10)."""
    return ResearchScenarioSpec(
        id=f"N{seed:04d}", template="pure_noise", name="Pure noise", description="No premium of any kind.",
        signal=signal, seed=10_000 + seed, rebalance_days=rebalance_days, expected_verdict=ResearchVerdict.REJECT,
        expected_flags=(ResearchFlag.NO_ALPHA,),
    )


# derived last: the ground-truth module reads the generator's constants, which import this module
RESEARCH_TEMPLATES: tuple[ResearchScenarioSpec, ...] = tuple(with_ground_truth(s) for s in RESEARCH_TEMPLATES_RAW)
