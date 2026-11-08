"""TCA as desks use it (2T.7): many parents, normalised by the pre-trade estimate.

* :func:`measure_parent` turns a stored parent (fills, quotes around them, arrival reference
  data) into Perold components, a beta-adjusted shortfall, the pre-trade estimate, the residual
  and its analytic standard error, size-weighted spreads, price improvement and markouts.
* :func:`link_parent_children` joins child orders and fills to their parent across sessions.
* :func:`compare_groups` compares brokers, algos or venues on the residual against the pre-trade
  estimate (inverse-variance weighted, robust SE), never on raw shortfall.
* :class:`OutlierQueue` holds parents whose residual is beyond 2.5 SE with reason codes; closing
  one goes through the approval gateway, so the principal who raised it cannot close it.
* :func:`best_execution_report` is a monthly best-execution style summary, labelled
  demonstration: it runs on synthetic data.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from ceap.analytics.implementation_shortfall import (
    DEFAULT_FEES,
    FeeSchedule,
    ShortfallComponents,
    schedule_price_variance,
    shortfall_components,
)
from ceap.analytics.peers import NO_CAUSAL_CLAIM, match_peers, peer_effect
from ceap.analytics.slippage import (
    effective_spread_bps,
    markout_bps,
    price_improvement_bps,
    realised_spread_bps,
)
from ceap.data.tca_universe import MARKOUT_HORIZONS, TcaParent, TcaUniverse
from ceap.domain.execution import Execution, Order
from ceap.ml.pretrade_cost import PretradeFeatures, PretradeModel, default_model
from ceap.policy.approvals import ApprovalDecision, ApprovalGateway, ApprovalRequest

OUTLIER_Z = 2.5
DEMONSTRATION_LABEL = "DEMONSTRATION - synthetic data, not a regulatory best-execution report"


def _normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


@dataclass(frozen=True)
class ParentTca:
    order_id: str
    symbol: str
    side: str
    algo: str
    broker: str
    sessions: tuple[str, ...]
    size_bucket: str
    q_over_adv: float
    components: ShortfallComponents
    market_component_bps: float
    adjusted_arrival_bps: float  # arrival shortfall minus beta * index move
    estimate_bps: float
    interval_bps: tuple[float, float]
    se_bps: float  # SE of the adjusted residual (idiosyncratic price risk + execution noise)
    residual_bps: float
    z: float
    effective_spread_bps: float
    realised_spread_bps: float
    price_improvement_bps: float
    markouts_bps: dict[int, float]
    raw_markouts_bps: dict[int, float]
    venues: tuple[str, ...]
    halted: bool = False

    @property
    def arrival_bps(self) -> float:
        return self.components.arrival_bps


def pretrade_features(p: TcaParent) -> PretradeFeatures:
    return PretradeFeatures(
        spread_bps=p.arrival_spread_bps,
        sigma_daily_bps=p.sigma_daily_bps,
        q_over_adv=p.quantity / p.adv_shares,
        algo=p.algo,
        schedule=p.schedule,
    )


def measure_parent(
    p: TcaParent,
    model: PretradeModel | None = None,
    fees: FeeSchedule = DEFAULT_FEES,
    halted_ids: frozenset[str] = frozenset(),
) -> ParentTca:
    model = model or default_model()
    fee_ps = np.array([fees.per_share(f) for f in p.fill_flags], dtype=float)
    comps = shortfall_components(
        p.side,
        p.decision_price,
        p.arrival_mid,
        p.fill_prices,
        p.fill_qty,
        p.fill_mids,
        float(p.quantity),
        p.final_mid,
        fee_ps,
    )
    s = p.side.sign
    norm = p.decision_price * p.quantity / 10_000.0
    filled = float(p.fill_qty.sum())
    unfilled = max(p.quantity - filled, 0.0)
    # market part of the timing and opportunity legs: beta * index return times the arrival price
    market = s * p.beta * p.arrival_mid * float((p.fill_qty * p.index_ret_to_fill).sum())
    market += s * p.beta * p.arrival_mid * p.index_ret_to_final * unfilled
    market_bps = market / norm
    adjusted = comps.arrival_bps - market_bps
    est = model.predict(pretrade_features(p))
    idio_var = (
        p.sigma_daily_bps**2
        * (1.0 + model.price_risk_inflation)
        * (1.0 - p.beta**2)
        * schedule_price_variance(p.schedule)
    )
    se = math.sqrt(idio_var + model.exec_noise_bps**2)
    resid = adjusted - est.estimate_bps
    aggressive = np.array([f == "REMOVE" for f in p.fill_flags], dtype=bool)
    continuous = np.array([f != "AUCTION" for f in p.fill_flags], dtype=bool)
    mk: dict[int, float] = {}
    raw: dict[int, float] = {}
    for h in MARKOUT_HORIZONS:
        later = p.later_mids.get(h, np.full(len(p.fill_prices), np.nan))
        raw[h] = markout_bps(p.side, p.fill_prices, later, p.fill_qty)
        mk[h] = markout_bps(p.side, p.fill_prices, later, p.fill_qty, p.index_ret_after.get(h), p.beta)
    later300 = p.later_mids.get(300, np.full(len(p.fill_prices), np.nan))
    return ParentTca(
        order_id=p.order_id,
        symbol=p.symbol,
        side=p.side.value,
        algo=p.algo,
        broker=p.broker,
        sessions=tuple(d.isoformat() for d in p.sessions),
        size_bucket=p.size_bucket,
        q_over_adv=p.quantity / p.adv_shares,
        components=comps,
        market_component_bps=market_bps,
        adjusted_arrival_bps=adjusted,
        estimate_bps=est.estimate_bps,
        interval_bps=(est.lower_bps, est.upper_bps),
        se_bps=se,
        residual_bps=resid,
        z=resid / se if se > 0 else float("nan"),
        effective_spread_bps=effective_spread_bps(
            p.fill_prices[aggressive], p.fill_mids[aggressive], p.side, p.fill_qty[aggressive]
        )
        if aggressive.any()
        else float("nan"),
        realised_spread_bps=realised_spread_bps(
            p.side,
            p.fill_prices[aggressive],
            p.fill_mids[aggressive],
            later300[aggressive],
            p.fill_qty[aggressive],
        )
        if aggressive.any()
        else float("nan"),
        price_improvement_bps=(
            price_improvement_bps(
                p.side, p.fill_prices[continuous], p.fill_far_touch[continuous], p.fill_qty[continuous]
            )
            if continuous.any()
            else float("nan")
        ),
        markouts_bps=mk,
        raw_markouts_bps=raw,
        venues=tuple(sorted(set(p.fill_venues))),
        halted=p.order_id in halted_ids,
    )


def halted_parent_ids(universe: TcaUniverse) -> frozenset[str]:
    out: set[str] = set()
    for h in universe.halts:
        for p in universe.parents:
            if p.symbol != h["symbol"] or h["session"] not in {d.isoformat() for d in p.sessions}:
                continue
            if p.start_s <= h["start_s"] + h["seconds"] and p.start_s + p.duration_s >= h["start_s"]:
                out.add(p.order_id)
    return frozenset(out)


def measure_universe(universe: TcaUniverse, model: PretradeModel | None = None) -> list[ParentTca]:
    halted = halted_parent_ids(universe)
    return [measure_parent(p, model, halted_ids=halted) for p in universe.parents if p.fill_qty.size]


# --------------------------------------------------------------------------- linkage
@dataclass(frozen=True)
class LinkedParent:
    parent: Order
    children: tuple[Order, ...]
    fills: tuple[Execution, ...]
    sessions: tuple[str, ...]

    @property
    def executed_quantity(self) -> int:
        return sum(f.quantity for f in self.fills)


def link_parent_children(
    orders: Iterable[Order], executions: Iterable[Execution], session_of: Callable[[Any], str] | None = None
) -> dict[str, LinkedParent]:
    """Join children and fills to their parent by id, whatever session each one traded in.

    A child whose parent is missing is dropped (orphan) rather than guessed; fills attach via the
    child's parent when ``parent_order_id`` is absent on the fill.
    """
    sess = session_of or (lambda x: x.timestamp.date().isoformat())
    orders = list(orders)
    parents = {o.order_id: o for o in orders if o.is_parent}
    child_parent = {o.order_id: o.parent_order_id for o in orders if not o.is_parent}
    kids: dict[str, list[Order]] = defaultdict(list)
    for o in orders:
        if not o.is_parent and o.parent_order_id in parents:
            kids[o.parent_order_id].append(o)
    fills: dict[str, list[Execution]] = defaultdict(list)
    for e in executions:
        pid = e.parent_order_id or child_parent.get(e.order_id)
        if pid in parents:
            fills[pid].append(e)
    out = {}
    for pid, parent in parents.items():
        ks = tuple(sorted(kids.get(pid, []), key=lambda o: o.timestamp))
        fs = tuple(sorted(fills.get(pid, []), key=lambda e: e.timestamp))
        sessions = tuple(sorted({sess(parent), *(sess(k) for k in ks), *(sess(f) for f in fs)}))
        out[pid] = LinkedParent(parent, ks, fs, sessions)
    return out


# --------------------------------------------------------------------------- comparisons
@dataclass(frozen=True)
class GroupStat:
    key: str
    n: int
    mean_residual_bps: float  # inverse-variance weighted
    se_bps: float  # robust (sandwich) SE of the weighted mean
    mean_arrival_bps: float
    mean_estimate_bps: float
    diff_vs_rest_bps: float
    z_vs_rest: float
    p_value: float
    flagged: bool

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _weighted(r: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    if r.size == 0:
        return float("nan"), float("nan")
    m = float((w * r).sum() / w.sum())
    se = float(math.sqrt((w**2 * (r - m) ** 2).sum()) / w.sum()) if r.size > 1 else float("nan")
    return m, se


def compare_groups(measures: Sequence[ParentTca], by: str = "broker", alpha: float = 0.05) -> list[GroupStat]:
    """Compare groups (``broker``, ``algo``, ``symbol`` ...) on the residual vs the pre-trade estimate.

    Each group's weighted mean residual is tested against the rest (Welch-style z with robust
    SEs); ``flagged`` uses a Bonferroni-corrected two-sided ``alpha`` across the groups.
    """
    groups: dict[str, list[ParentTca]] = defaultdict(list)
    for m in measures:
        if math.isfinite(m.residual_bps) and m.se_bps > 0:
            groups[str(getattr(m, by))].append(m)
    k = max(1, len(groups))
    out: list[GroupStat] = []
    for key in sorted(groups):
        g = groups[key]
        rest = [m for kk, v in groups.items() if kk != key for m in v]
        rg = np.array([m.residual_bps for m in g])
        wg = np.array([1.0 / m.se_bps**2 for m in g])
        rr = np.array([m.residual_bps for m in rest])
        wr = np.array([1.0 / m.se_bps**2 for m in rest])
        mg, sg = _weighted(rg, wg)
        mr, sr = _weighted(rr, wr)
        diff = mg - mr
        se_d = math.sqrt(sg**2 + sr**2) if math.isfinite(sg) and math.isfinite(sr) else float("nan")
        z = diff / se_d if se_d and math.isfinite(se_d) and se_d > 0 else float("nan")
        p = 2.0 * _normal_sf(abs(z)) if math.isfinite(z) else float("nan")
        out.append(
            GroupStat(
                key,
                len(g),
                mg,
                sg,
                float(np.mean([m.arrival_bps for m in g])),
                float(np.mean([m.estimate_bps for m in g])),
                diff,
                z,
                p,
                bool(math.isfinite(p) and p < alpha / k),
            )
        )
    return out


def venue_comparison(universe: TcaUniverse, fees: FeeSchedule = DEFAULT_FEES) -> list[dict[str, Any]]:
    """Fill-level venue statistics conditioned on order type (liquidity flag) and size bucket.

    Reports size-weighted cost versus the prevailing mid (spread leg, fees excluded) and the
    beta-adjusted 60 s markout per (venue, flag, bucket) cell, so a venue is only compared with
    the same kind of flow elsewhere.
    """
    cells: dict[tuple[str, str, str], list[tuple[float, float, float]]] = defaultdict(list)
    for p in universe.parents:
        later = p.later_mids.get(60)
        idx = p.index_ret_after.get(60)
        for i in range(len(p.fill_prices)):
            cost = p.side.sign * (p.fill_prices[i] - p.fill_mids[i]) / p.fill_mids[i] * 10_000.0
            mk = float("nan")
            if later is not None and idx is not None and math.isfinite(later[i]):
                mk = (
                    p.side.sign
                    * ((later[i] - p.fill_prices[i]) / p.fill_prices[i] - p.beta * idx[i])
                    * 10_000.0
                )
            cells[(p.fill_venues[i], p.fill_flags[i], p.size_bucket)].append((cost, mk, float(p.fill_qty[i])))
    rows = []
    for (venue, flag, bucket), vals in sorted(cells.items()):
        arr = np.array(vals)
        w = arr[:, 2]
        ok = np.isfinite(arr[:, 1])
        rows.append(
            {
                "venue": venue,
                "liquidity_flag": flag,
                "size_bucket": bucket,
                "fills": len(vals),
                "shares": int(w.sum()),
                "cost_vs_mid_bps": float((arr[:, 0] * w).sum() / w.sum()),
                "markout_60s_bps": float((arr[ok, 1] * w[ok]).sum() / w[ok].sum())
                if ok.any()
                else float("nan"),
            }
        )
    return rows


# --------------------------------------------------------------------------- outlier queue
class OutlierStatus(str, Enum):
    OPEN = "OPEN"
    PENDING_SIGN_OFF = "PENDING_SIGN_OFF"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


def reason_codes(m: ParentTca) -> tuple[str, ...]:
    codes = ["RESIDUAL_ABOVE_2_5_SE" if m.z > 0 else "RESIDUAL_BELOW_2_5_SE"]
    if m.halted:
        codes.append("LULD_PAUSE_DURING_ORDER")
    if len(m.sessions) > 1:
        codes.append("MULTI_SESSION_PARENT")
    if m.size_bucket == "L":
        codes.append("LARGE_ORDER")
    if m.algo == "MOC":
        codes.append("AUCTION_EXECUTION")
    if math.isfinite(m.markouts_bps.get(300, float("nan"))) and m.markouts_bps[300] < -10:
        codes.append("ADVERSE_MARKOUT")
    return tuple(codes)


@dataclass
class OutlierItem:
    order_id: str
    z: float
    residual_bps: float
    reasons: tuple[str, ...]
    status: OutlierStatus = OutlierStatus.OPEN
    raised_by: str | None = None
    approval_id: str | None = None
    decided_by: str | None = None
    comment: str = ""


@dataclass
class OutlierQueue:
    """Residuals beyond ``threshold`` SE. Closing needs a second principal via the gateway."""

    threshold: float = OUTLIER_Z
    items: dict[str, OutlierItem] = field(default_factory=dict)

    @staticmethod
    def build(measures: Sequence[ParentTca], threshold: float = OUTLIER_Z) -> OutlierQueue:
        q = OutlierQueue(threshold)
        for m in measures:
            if math.isfinite(m.z) and abs(m.z) > threshold:
                q.items[m.order_id] = OutlierItem(m.order_id, m.z, m.residual_bps, reason_codes(m))
        return q

    def open_items(self) -> list[OutlierItem]:
        return [i for i in self.items.values() if i.status is not OutlierStatus.CLOSED]

    def request_close(self, order_id: str, raised_by: str, comment: str = "") -> ApprovalRequest:
        """Build the sign-off request; ``raised_by`` is recorded as the requester (four-eyes)."""
        item = self.items[order_id]
        item.raised_by = raised_by
        item.comment = comment
        req = ApprovalRequest.create(
            task_id=f"tca-outlier-{order_id}",
            step_id="close-outlier",
            tool_id="tca.close_outlier",
            arguments={"order_id": order_id, "z": round(item.z, 4), "reasons": list(item.reasons)},
            reason=f"Close TCA outlier {order_id} (residual {item.residual_bps:+.1f} bps, z {item.z:+.2f}): {comment}",
            requested_by=raised_by,
        )
        item.approval_id = req.id
        item.status = OutlierStatus.PENDING_SIGN_OFF
        return req

    async def close(
        self, order_id: str, raised_by: str, gateway: ApprovalGateway, comment: str = ""
    ) -> ApprovalDecision:
        """Ask ``gateway`` for sign-off; the item closes only on an approval by someone else.

        The gateway enforces four-eyes (``check_four_eyes``): the raiser deciding raises
        :class:`~ceap.policy.approvals.FourEyesViolationError` and the item stays pending.
        """
        req = self.request_close(order_id, raised_by, comment)
        decision = await gateway.request(req)
        self.apply(order_id, decision)
        return decision

    def apply(self, order_id: str, decision: ApprovalDecision) -> None:
        item = self.items[order_id]
        if decision.approved and decision.decided_by != item.raised_by:
            item.status = OutlierStatus.CLOSED
            item.decided_by = decision.decided_by
        else:
            item.status = OutlierStatus.REOPENED


# --------------------------------------------------------------------------- report
def best_execution_report(universe: TcaUniverse, model: PretradeModel | None = None) -> dict[str, Any]:
    """Monthly best-execution style summary over the universe (labelled demonstration)."""
    model = model or default_model()
    measures = measure_universe(universe, model)
    queue = OutlierQueue.build(measures)
    by_peer = {m.order_id: m for m in measures}
    peer_rows: list[dict[str, Any]] = []
    for m in [x for x in measures if x.order_id in queue.items][:10]:
        peers = match_peers(
            m, measures, key=lambda x: (x.symbol, x.side, x.size_bucket), session=lambda x: x.sessions[0]
        )
        eff = peer_effect(m.residual_bps, [p.residual_bps for p in peers], seed=len(peer_rows))
        peer_rows.append({"order_id": m.order_id, **eff.as_dict()})
    total_notional_q = sum(p.quantity for p in universe.parents)

    def mean(xs: Iterable[float]) -> float:
        arr = np.array([x for x in xs if math.isfinite(x)])
        return float(arr.mean()) if arr.size else float("nan")

    return {
        "label": DEMONSTRATION_LABEL,
        "pretrade_model": model.label,
        "period": {"from": universe.sessions[0].isoformat(), "to": universe.sessions[-1].isoformat()},
        "sessions": len(universe.sessions),
        "parents": len(measures),
        "shares": int(total_notional_q),
        "summary_bps": {
            "arrival_shortfall": mean(m.arrival_bps for m in measures),
            "pretrade_estimate": mean(m.estimate_bps for m in measures),
            "residual": mean(m.residual_bps for m in measures),
            "delay": mean(m.components.delay_bps for m in measures),
            "spread": mean(m.components.spread_bps for m in measures),
            "timing": mean(m.components.timing_bps for m in measures),
            "opportunity": mean(m.components.opportunity_bps for m in measures),
            "fees": mean(m.components.fees_bps for m in measures),
            "effective_spread": mean(m.effective_spread_bps for m in measures),
            "realised_spread_300s": mean(m.realised_spread_bps for m in measures),
            "price_improvement": mean(m.price_improvement_bps for m in measures),
            **{f"markout_{h}s_beta_adj": mean(m.markouts_bps[h] for m in measures) for h in MARKOUT_HORIZONS},
        },
        "by_broker": [g.as_dict() for g in compare_groups(measures, "broker")],
        "by_algo": [g.as_dict() for g in compare_groups(measures, "algo")],
        "by_venue": venue_comparison(universe),
        "outliers": [
            {
                "order_id": i.order_id,
                "z": i.z,
                "residual_bps": i.residual_bps,
                "reasons": list(i.reasons),
                "status": i.status.value,
            }
            for i in sorted(queue.items.values(), key=lambda i: -abs(i.z))
        ],
        "outlier_peer_effects": peer_rows,
        "peer_note": NO_CAUSAL_CLAIM,
        "halts": universe.halts,
        "multi_session_parents": sum(1 for m in by_peer.values() if len(m.sessions) > 1),
    }


def render_report_markdown(report: dict[str, Any]) -> str:
    def f(x: Any) -> str:
        return f"{x:+.2f}" if isinstance(x, float) and math.isfinite(x) else "n/a"

    lines = [
        "# Monthly best-execution review",
        "",
        f"**{report['label']}**",
        "",
        f"Period {report['period']['from']} to {report['period']['to']} ({report['sessions']} sessions, "
        f"{report['parents']} parents). Pre-trade benchmark: {report['pretrade_model']}.",
        "",
        "## Cost summary (bps, positive = adverse)",
        "",
        "| Measure | bps |",
        "|---|---|",
    ]
    lines += [f"| {k.replace('_', ' ')} | {f(v)} |" for k, v in report["summary_bps"].items()]
    for title, key in (("Brokers", "by_broker"), ("Algos", "by_algo")):
        lines += [
            "",
            f"## {title}: residual versus pre-trade estimate",
            "",
            "| Group | n | residual | SE | vs rest | p | flagged |",
            "|---|---|---|---|---|---|---|",
        ]
        for g in report[key]:
            lines.append(
                f"| {g['key']} | {g['n']} | {f(g['mean_residual_bps'])} | {f(g['se_bps'])} | "
                f"{f(g['diff_vs_rest_bps'])} | {g['p_value']:.3f} | {'yes' if g['flagged'] else 'no'} |"
            )
    lines += [
        "",
        f"## Outlier review queue (|z| > {OUTLIER_Z})",
        "",
        "| Order | z | residual | reasons | status |",
        "|---|---|---|---|---|",
    ]
    for o in report["outliers"]:
        lines.append(
            f"| {o['order_id']} | {o['z']:+.2f} | {f(o['residual_bps'])} | {', '.join(o['reasons'])} | {o['status']} |"
        )
    lines += ["", f"_{report['peer_note']}_", ""]
    return "\n".join(lines)
