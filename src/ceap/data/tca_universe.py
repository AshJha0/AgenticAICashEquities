"""Multi-day, multi-order TCA universe (2T.1).

20 sessions x about 40 parents per session across six symbols, BUY and SELL, three brokers, four
algos (VWAP, POV, IS, MOC), passive and aggressive fills, odd lots, duplicate timestamps, a
closing auction (MOC fills are flagged auction prints), a LULD pause, a few parents that span two
sessions, and a synthetic market index for beta adjustment. Session 09:30 to 16:00
America/New_York with U-shaped activity.

The universe is array-based (no per-second ``Quote`` objects): each parent carries what a desk's
TCA store holds for it, namely the fills, the prevailing quote at each fill, the mid and index at
the markout horizons and the arrival-time reference data. Analytics turn those into costs
(:mod:`ceap.analytics.tca_aggregate`); the generator parameters behind each parent are kept apart
in ``truth`` so tests compare analytics against parameters, never against another analytics run.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np

from ceap.data.scenarios import BASE_PRICES, SYMBOLS, ScenarioSpec, get_scenario
from ceap.data.session import SESSION_SECONDS, previous_sessions
from ceap.data.synthetic import (
    ADV_SHARES,
    BASE_SPREAD_BPS,
    BASE_TOB_SIZE,
    DECISION_DELAY_SECONDS,
    IMPACT_GAMMA,
    SIGMA_DAILY_BPS,
    TICK,
    ParentPlan,
    Regime,
    expected_impact_drift_bps,
    intraday_profile,
    mid_path,
    quote_arrays,
    simulate_parent,
)
from ceap.domain.execution import Side

BROKERS: tuple[str, ...] = ("BRK_A", "BRK_B", "BRK_C")
ALGOS: tuple[str, ...] = ("VWAP", "POV", "IS", "MOC")
ALGO_WEIGHTS: tuple[float, ...] = (0.45, 0.2, 0.2, 0.15)
AGGRESSION = {"VWAP": 0.65, "POV": 0.55, "IS": 0.8, "MOC": 0.0}
IS_FRONT_LOAD = 2.0
POV_RATE = 0.10
MARKOUT_HORIZONS: tuple[int, ...] = (1, 10, 60, 300)
BETAS = {"AAPL": 0.95, "MSFT": 0.9, "NVDA": 1.0, "AMZN": 0.9, "META": 0.95, "GOOGL": 0.85}
AUCTION_NOISE_BPS = 2.0
LULD_SESSION, LULD_SYMBOL = 7, "NVDA"
LULD_START_S, LULD_SECONDS, LULD_JUMP_BPS = 5400, 300, 80.0  # 11:00 for 5 minutes
END_SESSION = date(2026, 9, 18)
GENERATOR_VERSION = "tca_universe_v1"


@dataclass(frozen=True)
class UniverseConfig:
    n_sessions: int = 20
    parents_per_session: int = 40
    symbols: tuple[str, ...] = SYMBOLS
    broker_gap_bps: float = 0.0
    gap_broker: str = "BRK_C"
    family: str = "T01"  # a scenario template whose regime holds for the whole universe
    luld: bool = True
    multi_session_fraction: float = 0.05
    u_shape: bool = True
    child_interval: int = 30
    #: permanent-impact law of the generator: "sqrt" (the default, the form the pre-trade model
    #: fits) or "linear" (a different functional form, used only to test the model off its form)
    impact_law: str = "sqrt"
    #: regime overrides applied on top of the family, e.g. ``(("order_size_multiplier", 2.0),)``;
    #: only the multipliers in ``REGIME_AXES`` (used to build calibration regime mixtures)
    regime: tuple[tuple[str, float], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@dataclass
class TcaParent:
    """One parent as a TCA store holds it. Prices in dollars, times in seconds since the open."""

    order_id: str
    symbol: str
    sessions: tuple[date, ...]
    side: Side
    quantity: int
    algo: str
    broker: str
    family: str
    start_s: int
    duration_s: int
    # arrival-time reference data
    adv_shares: float
    sigma_daily_bps: float
    beta: float
    arrival_spread_bps: float
    decision_price: float
    arrival_mid: float
    final_mid: float
    #: price-risk schedule as (duration_days, a, b) linear segments of the remaining fraction
    schedule: tuple[tuple[float, float, float], ...]
    # fills and what the market did around them
    fill_prices: np.ndarray = field(default_factory=lambda: np.zeros(0))
    fill_qty: np.ndarray = field(default_factory=lambda: np.zeros(0))
    fill_mids: np.ndarray = field(default_factory=lambda: np.zeros(0))
    fill_far_touch: np.ndarray = field(default_factory=lambda: np.zeros(0))
    fill_flags: tuple[str, ...] = ()
    fill_venues: tuple[str, ...] = ()
    fill_times: np.ndarray = field(default_factory=lambda: np.zeros(0))  # seconds since own open
    fill_session_index: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    #: index simple return from arrival to each fill (for beta-adjusted shortfall)
    index_ret_to_fill: np.ndarray = field(default_factory=lambda: np.zeros(0))
    later_mids: dict[int, np.ndarray] = field(default_factory=dict)
    index_ret_after: dict[int, np.ndarray] = field(default_factory=dict)
    index_ret_to_final: float = 0.0
    children: int = 0

    @property
    def size_bucket(self) -> str:
        return size_bucket(self.quantity / self.adv_shares)

    @property
    def multi_session(self) -> bool:
        return len(self.sessions) > 1


@dataclass
class TcaUniverse:
    config: UniverseConfig
    seed: int
    sessions: tuple[date, ...]
    parents: list[TcaParent]
    truth: dict[str, dict[str, Any]]
    halts: list[dict[str, Any]]

    @property
    def generator_hash(self) -> str:
        return generator_hash()


def size_bucket(q_over_adv: float) -> str:
    if q_over_adv < 0.005:
        return "S"
    if q_over_adv < 0.02:
        return "M"
    return "L"


def generator_hash() -> str:
    """Hash of the generator parameters the pre-trade model is fitted to (its model-card label)."""
    params = {
        "version": GENERATOR_VERSION,
        "sigma_daily_bps": round(SIGMA_DAILY_BPS, 9),
        "adv_shares": round(ADV_SHARES, 6),
        "impact_gamma": IMPACT_GAMMA,
        "spreads": BASE_SPREAD_BPS,
        "tob": BASE_TOB_SIZE,
        "aggression": AGGRESSION,
        "betas": BETAS,
        "decision_delay_s": DECISION_DELAY_SECONDS,
    }
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]


REGIME_AXES: tuple[str, ...] = (
    "volatility_multiplier",
    "spread_multiplier",
    "depth_multiplier",
    "order_size_multiplier",
    "latency_multiplier",
)
IMPACT_LAWS: tuple[str, ...] = ("sqrt", "linear")
#: participation at which the linear law equals the square-root law (1 % of ADV, near the median order)
LINEAR_LAW_CROSSOVER = 0.01


def impact_drift_bps(quantity: float, sigma_bps: float, law: str = "sqrt") -> float:
    """Generator truth: total permanent drift of a parent under the configured impact law.

    ``"sqrt"`` is :func:`ceap.data.synthetic.expected_impact_drift_bps`,
    ``gamma * sigma * sqrt(Q/ADV)``. ``"linear"`` is ``gamma * sigma * sqrt(x0) * (Q/ADV) / x0`` with
    ``x0 = LINEAR_LAW_CROSSOVER``: the Kyle / Almgren-Chriss linear permanent impact, scaled so
    both laws agree at 1 % of ADV. Below the crossover it is smaller, above it grows without the
    concavity, so a square-root model under-predicts large orders on it.
    """
    if law == "sqrt":
        return expected_impact_drift_bps(quantity, sigma_bps=sigma_bps)
    if law == "linear":
        x = max(quantity, 0.0) / ADV_SHARES
        return IMPACT_GAMMA * sigma_bps * math.sqrt(LINEAR_LAW_CROSSOVER) * x / LINEAR_LAW_CROSSOVER
    raise ValueError(f"unknown impact law {law!r}; expected one of {IMPACT_LAWS}")


def _family_spec(name: str, regime: tuple[tuple[str, float], ...] = ()) -> ScenarioSpec:
    bad = [k for k, _ in regime if k not in REGIME_AXES]
    if bad:
        raise ValueError(f"unknown regime axes {bad}; expected a subset of {REGIME_AXES}")
    spec = replace(get_scenario(name), **{k: float(v) for k, v in regime})
    # market-data defects do not change execution in the universe; deployments are irrelevant
    return replace(spec, stale_quote_fraction=0.0, crossed_quotes=0, deployment_in_window=False)


@dataclass
class _Plan:
    plan: ParentPlan
    symbol: str
    session_idx: int
    leg: int
    parent_id: str


def generate_tca_universe(seed: int, config: UniverseConfig | None = None) -> TcaUniverse:
    """Simulate a seeded universe (see the module docstring)."""
    cfg = config or UniverseConfig()
    if cfg.impact_law not in IMPACT_LAWS:
        raise ValueError(f"unknown impact law {cfg.impact_law!r}; expected one of {IMPACT_LAWS}")
    rng = np.random.default_rng([seed, 20_261_107])
    fam = _family_spec(cfg.family, cfg.regime)
    regime = Regime.of(fam)
    sessions = tuple(reversed(previous_sessions(END_SESSION, cfg.n_sessions - 1))) + (END_SESSION,)
    t = np.arange(SESSION_SECONDS + 1, dtype=float)
    profile = intraday_profile(t, cfg.u_shape)
    per_sec = SIGMA_DAILY_BPS / 10_000.0 / math.sqrt(SESSION_SECONDS)
    sigma_ref = SIGMA_DAILY_BPS * fam.volatility_multiplier

    # ---------------------------------------------------------------- plan the parents
    plans: dict[tuple[str, int], list[_Plan]] = {}
    truth: dict[str, dict[str, Any]] = {}
    meta: dict[str, dict[str, Any]] = {}
    pid_n = 0
    for d_idx in range(len(sessions)):
        for _ in range(cfg.parents_per_session):
            pid_n += 1
            sym = str(rng.choice(cfg.symbols))
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            algo = str(rng.choice(ALGOS, p=ALGO_WEIGHTS))
            broker = str(rng.choice(BROKERS))
            q_adv = float(np.clip(np.exp(rng.normal(math.log(0.008), 0.9)), 0.0005, 0.15))
            qty = int(max(100, round(q_adv * ADV_SHARES * fam.order_size_multiplier / 100) * 100))
            pid = f"U{seed}-{pid_n:05d}"
            extra = cfg.broker_gap_bps if broker == cfg.gap_broker else 0.0
            multi = algo == "VWAP" and d_idx < len(sessions) - 1 and rng.random() < cfg.multi_session_fraction
            if algo == "MOC":
                start, dur = int(rng.integers(5 * 3600, SESSION_SECONDS - 600)), 0
            else:
                start = int(rng.integers(300, SESSION_SECONDS - 3600))
                if algo == "VWAP":
                    dur = int(rng.integers(30, 121)) * 60
                elif algo == "IS":
                    dur = int(rng.integers(20, 61)) * 60
                else:  # POV: duration implied by the participation target and expected volume
                    exp_vol_per_s = ADV_SHARES / SESSION_SECONDS
                    dur = int(np.clip(qty / (POV_RATE * exp_vol_per_s), 600, 3 * 3600))
                dur = min(dur, SESSION_SECONDS - 60 - start)
            if multi:
                dur = SESSION_SECONDS - 60 - start
            legs: list[tuple[int, int, int, int]] = []  # (session_idx, start, dur, qty)
            if multi:
                half = int(round(qty / 2 / 100) * 100)
                legs = [
                    (d_idx, start, dur, half),
                    (d_idx + 1, 300, int(rng.integers(60, 181)) * 60, qty - half),
                ]
            else:
                legs = [(d_idx, start, dur, qty)]
            for leg_no, (si, st, du, lq) in enumerate(legs):
                plan = ParentPlan(
                    order_id=pid if len(legs) == 1 else f"{pid}-L{leg_no + 1}",
                    side=side,
                    quantity=lq,
                    start_index=st,
                    duration_seconds=du,
                    algo=algo,
                    aggressive_prob=AGGRESSION[algo],
                    broker=broker,
                    broker_extra_bps=extra,
                    child_interval=cfg.child_interval,
                    front_load=IS_FRONT_LOAD if algo == "IS" else 0.0,
                )
                plans.setdefault((sym, si), []).append(_Plan(plan, sym, si, leg_no, pid))
            meta[pid] = {
                "symbol": sym,
                "side": side,
                "algo": algo,
                "broker": broker,
                "quantity": qty,
                "legs": legs,
            }
            truth[pid] = {
                "broker_extra_bps": extra,
                "impact_drift_bps": sum(impact_drift_bps(lq, sigma_ref, cfg.impact_law) for *_, lq in legs),
                "aggressive_prob": AGGRESSION[algo],
                "family": cfg.family,
                "q_over_adv": qty / ADV_SHARES,
            }

    # ---------------------------------------------------------------- simulate sessions
    index_paths: list[np.ndarray] = []
    for _d in sessions:
        # the family's volatility regime is market-wide: the index carries it too (second reader D1)
        r_m = rng.normal(0.0, 1.0, SESSION_SECONDS + 1) * per_sec * np.sqrt(profile) * fam.volatility_multiplier
        index_paths.append(np.exp(np.cumsum(r_m)))  # index level, starts near 1 each session
    shock_symbol = [str(rng.choice(cfg.symbols)) for _ in sessions]
    halts: list[dict[str, Any]] = []
    legs_out: dict[str, list[dict[str, Any]]] = {}
    overnight_mid: dict[str, float] = {}
    for d_idx in range(len(sessions)):
        r_m_levels = index_paths[d_idx]
        r_m = np.diff(np.log(np.concatenate([[1.0], r_m_levels])))
        for sym in cfg.symbols:
            beta = BETAS.get(sym, 0.9)
            sym_plans = plans.get((sym, d_idx), [])
            drifts = [
                (
                    p.plan.start_index,
                    max(1, p.plan.duration_seconds),
                    p.plan.side.sign * impact_drift_bps(p.plan.quantity, sigma_ref, cfg.impact_law),
                )
                for p in sym_plans
                if p.plan.duration_seconds > 0
            ]
            halted = np.zeros(SESSION_SECONDS + 1, dtype=bool)
            if cfg.luld and d_idx == LULD_SESSION and sym == LULD_SYMBOL:
                halted[LULD_START_S : LULD_START_S + LULD_SECONDS] = True
                jump = LULD_JUMP_BPS * (1 if rng.random() < 0.5 else -1)
                drifts.append((LULD_START_S + LULD_SECONDS, 1, jump))
                halts.append(
                    {
                        "symbol": sym,
                        "session": sessions[d_idx].isoformat(),
                        "start_s": LULD_START_S,
                        "seconds": LULD_SECONDS,
                        "reopen_jump_bps": jump,
                    }
                )
            if fam.price_move_bps and sym == shock_symbol[d_idx]:
                # an unexpected move hits one name per session at a random time and direction
                i0 = int(rng.integers(3600, SESSION_SECONDS - 3600))
                drifts.append(
                    (i0, fam.price_move_minutes * 60, fam.price_move_bps * (1 if rng.random() < 0.5 else -1))
                )
            start_px = overnight_mid.get(sym, BASE_PRICES[sym]) * float(
                # idiosyncratic overnight gap of (0.5 sigma_ref)^2 * (1 - beta^2): matches the
                # 0.25-session segment of the multi-session schedule under the idiosyncratic SE
                np.exp(rng.normal(0.0, sigma_ref / 10_000.0 * 0.5 * math.sqrt(1.0 - beta * beta)))
            )
            vol_mult = np.full(SESSION_SECONDS + 1, fam.volatility_multiplier)
            mids = mid_path(rng, start_px, profile, vol_mult, drifts, market_returns=r_m, beta=beta)
            overnight_mid[sym] = float(mids[-1])
            vol_coupling = 1.0 + 0.15 * (fam.volatility_multiplier - 1.0)
            spread_mult = np.full(SESSION_SECONDS + 1, fam.spread_multiplier * vol_coupling)
            depth_mult = np.full(SESSION_SECONDS + 1, fam.depth_multiplier)
            bids, asks, bsz, asz = quote_arrays(
                rng, mids, BASE_SPREAD_BPS[sym], BASE_TOB_SIZE[sym], spread_mult, depth_mult
            )
            mask = np.ones(SESSION_SECONDS + 1, dtype=bool)
            close_mid = float(mids[-1])
            for p in sym_plans:
                plan = p.plan
                rec: dict[str, Any] = {
                    "session_idx": d_idx,
                    "leg": p.leg,
                    "start": plan.start_index,
                    "dur": plan.duration_seconds,
                }
                i_arr = plan.start_index
                rec["arrival_mid"] = float(mids[i_arr])
                rec["decision_mid"] = float(mids[max(0, i_arr - DECISION_DELAY_SECONDS)])
                rec["arrival_spread_bps"] = float((asks[i_arr] - bids[i_arr]) / mids[i_arr] * 10_000.0)
                rec["close_mid"] = close_mid
                rec["end_mid"] = float(mids[min(SESSION_SECONDS, i_arr + plan.duration_seconds)])
                rec["index_arrival"] = float(r_m_levels[i_arr])
                rec["index_close"] = float(r_m_levels[-1])
                if plan.algo == "MOC":
                    px = close_mid * (
                        1.0
                        + plan.side.sign
                        * (plan.broker_extra_bps + rng.normal(0.0, AUCTION_NOISE_BPS))
                        / 10_000.0
                    )
                    rec["fills"] = [
                        (
                            SESSION_SECONDS,
                            plan.quantity,
                            round(px, 4),
                            close_mid,
                            close_mid,
                            "AUCTION",
                            "XNAS",
                        )
                    ]
                    rec["children"] = 1
                else:
                    children, fills = simulate_parent(
                        rng, plan, mids, bids, asks, bsz, asz, mask, regime, halted
                    )
                    rec["children"] = len(children)
                    rec["fills"] = [
                        (
                            f.index,
                            f.quantity,
                            f.price,
                            float(mids[f.index]),
                            float(asks[f.index] if plan.side.sign > 0 else bids[f.index]),
                            f.liquidity_flag,
                            f.venue,
                        )
                        for f in fills
                    ]
                rec["later"] = {
                    h: [
                        float(mids[i + h]) if i + h <= SESSION_SECONDS else float("nan")
                        for i, *_ in rec["fills"]
                    ]
                    for h in MARKOUT_HORIZONS
                }
                rec["index_after"] = {
                    h: [
                        float(r_m_levels[i + h] / r_m_levels[i] - 1.0)
                        if i + h <= SESSION_SECONDS
                        else float("nan")
                        for i, *_ in rec["fills"]
                    ]
                    for h in MARKOUT_HORIZONS
                }
                rec["index_at_fill"] = [float(r_m_levels[i]) for i, *_ in rec["fills"]]
                legs_out.setdefault(p.parent_id, []).append(rec)

    # ---------------------------------------------------------------- assemble parents
    parents: list[TcaParent] = []
    for pid, m in meta.items():
        recs = sorted(legs_out.get(pid, []), key=lambda r: (r["session_idx"], r["leg"]))
        if not recs:
            continue
        first, last = recs[0], recs[-1]
        pside: Side = m["side"]
        fill_rows = [(r, fr) for r in recs for fr in r["fills"]]
        idx0 = first["index_arrival"]

        # index return from arrival to each fill; across the overnight the index restarts at 1,
        # so chain the first leg's close-to-arrival return with the next session's level
        def idx_ret(
            r: dict[str, Any], level: float, first: dict[str, Any] = first, idx0: float = idx0
        ) -> float:
            if r is first:
                return level / idx0 - 1.0
            return (first["index_close"] / idx0) * level - 1.0

        later = {h: np.array([v for r in recs for v in r["later"][h]]) for h in MARKOUT_HORIZONS}
        idx_after = {h: np.array([v for r in recs for v in r["index_after"][h]]) for h in MARKOUT_HORIZONS}
        idx_fill = np.array([idx_ret(r, lvl) for r in recs for lvl in r["index_at_fill"]])
        # schedule durations are in *volatility time* (session-days weighted by the intraday
        # profile), which is what the arrival-time clock and time of day imply for price risk
        if m["algo"] == "MOC":
            schedule: tuple[tuple[float, float, float], ...] = (
                (vol_time(first["start"], SESSION_SECONDS - first["start"]), 1.0, 1.0),
            )
        elif len(recs) == 1:
            if m["algo"] == "IS":
                schedule = _front_loaded_schedule(
                    first["start"], max(first["dur"], 1), IS_FRONT_LOAD, vol_time
                )
            else:
                schedule = ((vol_time(first["start"], max(first["dur"], 1)), 1.0, 0.0),)
        else:
            t1 = vol_time(first["start"], first["dur"])
            gap = vol_time(first["start"] + first["dur"], SESSION_SECONDS - first["start"] - first["dur"])
            gap += vol_time(0, last["start"])
            t2 = vol_time(last["start"], last["dur"])
            # the overnight gap is N(0, (0.5 sigma)^2) in this generator: 0.25 session-days
            schedule = ((t1, 1.0, 0.5), (gap + 0.25, 0.5, 0.5), (t2, 0.5, 0.0))
        parents.append(
            TcaParent(
                order_id=pid,
                symbol=m["symbol"],
                sessions=tuple(sessions[r["session_idx"]] for r in recs),
                side=pside,
                quantity=m["quantity"],
                algo=m["algo"],
                broker=m["broker"],
                family=cfg.family,
                start_s=first["start"],
                duration_s=first["dur"],
                adv_shares=ADV_SHARES,
                sigma_daily_bps=sigma_ref,
                beta=BETAS.get(m["symbol"], 0.9),
                arrival_spread_bps=first["arrival_spread_bps"],
                decision_price=round(first["decision_mid"], 4),
                arrival_mid=first["arrival_mid"],
                final_mid=last["close_mid"] if m["algo"] == "MOC" else last["end_mid"],
                schedule=schedule,
                fill_prices=np.array([fr[2] for _, fr in fill_rows], dtype=float),
                fill_qty=np.array([fr[1] for _, fr in fill_rows], dtype=float),
                fill_mids=np.array([fr[3] for _, fr in fill_rows], dtype=float),
                fill_far_touch=np.array([fr[4] for _, fr in fill_rows], dtype=float),
                fill_flags=tuple(fr[5] for _, fr in fill_rows),
                fill_venues=tuple(fr[6] for _, fr in fill_rows),
                fill_times=np.array([fr[0] for _, fr in fill_rows], dtype=float),
                fill_session_index=np.array([r["session_idx"] for r, _ in fill_rows], dtype=int),
                index_ret_to_fill=idx_fill,
                later_mids=later,
                index_ret_after=idx_after,
                index_ret_to_final=idx_ret(last, last["index_close"]),
                children=sum(r["children"] for r in recs),
            )
        )
    return TcaUniverse(cfg, seed, sessions, parents, truth, halts)


def vol_time(start_s: int, dur_s: int, u_shape: bool = True) -> float:
    """Session-days of volatility time in ``[start, start + dur)``: integral of the profile / session."""
    if dur_s <= 0:
        return 0.0
    t = np.arange(start_s, start_s + dur_s, dtype=float) + 0.5
    return float(intraday_profile(t, u_shape).sum() / SESSION_SECONDS)


def _front_loaded_schedule(
    start_s: int, dur_s: int, front_load: float, clock: Any, pieces: int = 8
) -> tuple[tuple[float, float, float], ...]:
    """Piecewise-linear approximation of the exponential IS schedule's remaining fraction."""
    edges = np.linspace(0.0, 1.0, pieces + 1)
    norm = 1.0 - math.exp(-front_load)
    remaining = [(math.exp(-front_load * x) - math.exp(-front_load)) / norm for x in edges]
    step = dur_s // pieces
    return tuple((clock(start_s + k * step, step), remaining[k], remaining[k + 1]) for k in range(pieces))


__all__ = [
    "ALGOS",
    "BROKERS",
    "MARKOUT_HORIZONS",
    "TICK",
    "TcaParent",
    "TcaUniverse",
    "UniverseConfig",
    "generate_tca_universe",
    "generator_hash",
    "size_bucket",
]
