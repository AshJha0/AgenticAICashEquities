"""Pre-trade risk checks, borrow availability and the kill switch (desk-style controls, daily frequency).

Every staged order passes :func:`check_orders` before it reaches the approval gateway. A breach never
silently trades: the offending quantity is removed (or the order dropped) and a ``risk_block`` ledger
event plus an audit entry record the rule, the requested and the allowed quantity.

Rules (limits in ``config/paper.json`` under ``risk``):

* **restricted list**: no order may increase the absolute position in a restricted name; reducing it is allowed.
* **hard to borrow**: names on the list (or in a listed liquidity tier) cannot have their short increased.
* **fat-finger price band**: the decision close must be within ``price_band`` of the previous close
  (split-adjusted); outside it the print is suspect and the order is dropped. At fill time the fill price
  must be within ``fill_price_band`` of the decision price, else the remaining order is cancelled.
* **max order % ADV**: ``|order| <= max_order_adv * ADV``; larger orders are clipped.
* **per-name limit**: ``|post-trade value| / equity <= max_name_weight``; clipped.
* **gross limit**: ``sum |post-trade value| / equity <= max_gross``; when breached, every order that
  increases gross is dropped (reducing orders still go).

The kill switch has two sources (either one halts): the DB-backed switch in the shared database
(:class:`ceap.persistence.killswitch.KillSwitch`, set and cleared with ``ceap db kill-switch``, both
audited; 0.8.0) and the older environment variable ``CEAP_PAPER_HALT`` (its value is the reason). When
halted the job stages nothing, cancels every open order and logs why; ``stage_orders`` refuses.

:func:`check_staged_orders` applies the rules that need no market panel (restricted list, hard to
borrow, per-name and gross limits) to an order list staged from a research portfolio (MCP
``stage_orders``), where there is no ADV or previous close to check against.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ceap.paper.feed import MarketPanel

if TYPE_CHECKING:
    from ceap.persistence.db import Database

HALT_ENV = "CEAP_PAPER_HALT"


@dataclass(frozen=True)
class RiskLimits:
    max_name_weight: float = 0.10
    max_gross: float = 1.05
    max_order_adv: float = 0.25
    price_band: float = 0.25
    fill_price_band: float = 0.25
    restricted: tuple[str, ...] = ()
    hard_to_borrow: tuple[str, ...] = ()
    htb_tiers: tuple[str, ...] = ()
    drift_tolerance: float = 0.005  # unexplained target-vs-actual gap, fraction of equity per name

    @staticmethod
    def from_json(d: dict[str, Any] | None) -> RiskLimits:
        d = dict(d or {})
        for k in ("restricted", "hard_to_borrow", "htb_tiers"):
            if k in d:
                d[k] = tuple(d[k])
        return RiskLimits(**d)

    def is_htb(self, symbol: str, tier: str) -> bool:
        return symbol in self.hard_to_borrow or tier in self.htb_tiers


def halt_status(db: Database | None = None) -> tuple[str | None, str | None]:
    """``(reason, source)`` of an active halt, source ``"env"`` or ``"db"``; ``(None, None)`` when trading."""
    raw = os.getenv(HALT_ENV, "").strip()
    if raw:
        return raw, "env"
    if db is not None:
        from ceap.persistence.killswitch import KillSwitch

        reason = KillSwitch(db).reason()
        if reason:
            return reason, "db"
    return None, None


def halt_reason(db: Database | None = None) -> str | None:
    return halt_status(db)[0]


def check_staged_orders(orders: list[tuple[str, float, float, str]], equity: float,
                        limits: RiskLimits) -> CheckResult:
    """Pre-trade rules for an order list staged from flat: ``(symbol, signed quantity, price, tier)``.

    The book is flat, so each order is the whole post-trade position. Restricted names are dropped,
    hard-to-borrow shorts are dropped, each name is clipped to ``max_name_weight * equity`` and, when
    the gross still exceeds ``max_gross * equity``, every order is scaled down pro rata (whole shares).
    """
    out: dict[str, float] = {}
    blocks: list[dict[str, Any]] = []
    prices: dict[str, float] = {}
    for sym, qty, price, tier in sorted(orders):
        d = float(qty)
        prices[sym] = price
        if sym in limits.restricted:
            blocks.append(_block(sym, "restricted", d, 0.0))
            continue
        if d < 0 and limits.is_htb(sym, tier):
            blocks.append(_block(sym, "hard_to_borrow", d, 0.0))
            continue
        cap = math.floor(limits.max_name_weight * equity / price) if price > 0 else 0.0
        if abs(d) > cap:
            allowed = math.copysign(cap, d)
            blocks.append(_block(sym, "max_name_weight", d, allowed, cap_shares=cap))
            d = allowed
        if d != 0:
            out[sym] = d
    gross = sum(abs(q) * prices[s] for s, q in out.items())
    limit = limits.max_gross * equity
    if equity > 0 and gross > limit * (1 + 1e-9):
        scale = limit / gross
        for sym in sorted(out):
            allowed = math.copysign(math.floor(abs(out[sym]) * scale), out[sym])
            blocks.append(_block(sym, "max_gross", out[sym], allowed, gross=gross / equity))
            if allowed:
                out[sym] = allowed
            else:
                del out[sym]
    return CheckResult(out, blocks)


@dataclass
class CheckResult:
    deltas: dict[str, float]
    blocks: list[dict[str, Any]] = field(default_factory=list)


def _block(sym: str, rule: str, requested: float, allowed: float, **detail: Any) -> dict[str, Any]:
    return {"kind": "risk_block", "symbol": sym, "quantity": requested - allowed, "price": None, "cash_delta": 0.0,
            "cost": 0.0, "order_id": None, "detail": {"rule": rule, "requested": requested, "allowed": allowed,
                                                      **detail}}


def check_orders(deltas: dict[str, float], positions: dict[str, float], equity: float, panel: MarketPanel, i: int,
                 limits: RiskLimits, gross_leverage: float = 1.0) -> CheckResult:
    """Apply the pre-trade rules to the order deltas staged at the close of session ``i``."""
    out: dict[str, float] = {}
    blocks: list[dict[str, Any]] = []
    adj = panel.adjusted_close(i)
    for sym in sorted(deltas):
        j = panel.col(sym)
        d = deltas[sym]
        q = positions.get(sym, 0.0)
        price = float(panel.close[i, j])
        if sym in limits.restricted and (abs(q + d) > abs(q) or (q + d) * q < 0):
            allowed = -q if q * d < 0 else 0.0  # flattening is allowed, nothing beyond it
            blocks.append(_block(sym, "restricted", d, allowed))
            d = allowed
        if q + d < min(q, 0.0) and limits.is_htb(sym, panel.tiers[j]):
            allowed = min(q, 0.0) - q
            blocks.append(_block(sym, "hard_to_borrow", d, allowed))
            d = allowed
        prev = float(adj[i - 1, j]) if i > 0 else float("nan")
        if np.isfinite(prev) and prev > 0 and abs(price / prev - 1.0) > limits.price_band:
            blocks.append(_block(sym, "price_band", d, 0.0, close=price, prev_close=prev))
            d = 0.0
        adv = float(panel.adv_20[i, j])
        if np.isfinite(adv) and adv > 0 and abs(d) > limits.max_order_adv * adv:
            allowed = math.copysign(limits.max_order_adv * adv, d)
            blocks.append(_block(sym, "max_order_adv", d, allowed, adv=adv))
            d = allowed
        cap = limits.max_name_weight * gross_leverage * equity / price if price > 0 else 0.0
        new = q + d
        if abs(new) > cap * (1 + 1e-9) and abs(new) > abs(q):
            # a position already over the cap (price drift) may not grow; otherwise clip to the cap
            allowed = 0.0 if abs(q) > cap and new * q > 0 else math.copysign(cap, new) - q
            blocks.append(_block(sym, "max_name_weight", d, allowed, cap_shares=cap))
            d = allowed
        if abs(d) > 1e-9:
            out[sym] = d
    gross = 0.0
    for sym in set(out) | set(positions):
        p = float(panel.close[i, panel.col(sym)])
        if np.isfinite(p):
            gross += abs(positions.get(sym, 0.0) + out.get(sym, 0.0)) * p
    limit = limits.max_gross * gross_leverage * equity
    if equity > 0 and gross > limit * (1 + 1e-9):
        for sym in sorted(out):
            q = positions.get(sym, 0.0)
            if abs(q + out[sym]) > abs(q):
                blocks.append(_block(sym, "max_gross", out[sym], 0.0, gross=gross / equity))
                del out[sym]
    return CheckResult(out, blocks)
