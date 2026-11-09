"""Child-order logic: how much of the current slice to post, how much to take, and at what price (F4).

The scheduler says how many shares should be done by the end of each slice; this module turns
the gap into child orders against the book the bot can see:

* **Passive or aggressive.** Post at the near touch by default. Take liquidity at the far touch for
  the part of the gap that urgency says cannot wait (``urgency`` times the shares behind
  schedule, i.e. the gap beyond this slice's own size), when the spread
  is already at its tightest (crossing one tick costs little), or in the last slice.
* **I-would limit.** If the far touch is at or better than the trader's "I would" price, take the
  whole remaining parent up to that price: an opportunity the schedule did not plan for.
* **Cancel/replace.** A resting child that is no longer at the near touch is cancelled; the next
  decision re-posts it at the new touch (and to the back of that queue).
* **Minimum quantity.** Children under ``min_qty`` are not sent unless they finish the parent.
* **Randomisation.** Child sizes are jittered by up to ``randomise`` (uniform, seeded) so the
  footprint is less regular; the jitter never sends more than the gap.

All prices are integer ticks, as in :mod:`ceap.execution.lob`. Evidence class: standard desk
practice as described in Johnson (2010), *Algorithmic Trading and DMA*, ch. 7-8; the thresholds are
illustrative defaults, not tuned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from ceap.execution.lob import BookView, RestingOrder


@dataclass(frozen=True)
class ChildPolicy:
    urgency: float = 0.5
    tight_spread_ticks: int = 1
    iwould_price: int | None = None
    min_qty: int = 100
    randomise: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.urgency <= 1.0 or not 0.0 <= self.randomise < 1.0 or self.min_qty < 1:
            raise ValueError("need 0 <= urgency <= 1, 0 <= randomise < 1 and min_qty >= 1")


@dataclass(frozen=True)
class ChildAction:
    kind: str  # "PASSIVE" | "AGGRESSIVE" | "CANCEL"
    quantity: int = 0
    limit: int | None = None
    order_id: int | None = None


def decide(
    policy: ChildPolicy,
    side: int,
    gap: int,
    slice_qty: int,
    remaining_parent: int,
    view: BookView,
    resting: Sequence[RestingOrder],
    final: bool,
    rng: np.random.Generator | None = None,
) -> list[ChildAction]:
    """Child actions for one decision point.

    ``gap`` is what the schedule still wants done by the end of this slice beyond fills and
    working orders; ``slice_qty`` the slice's planned size (the yardstick for "behind");
    ``remaining_parent`` the parent's unfilled quantity, working orders included.
    """
    actions: list[ChildAction] = []
    near = view.bid if side > 0 else view.ask
    far = view.ask if side > 0 else view.bid
    still_working = 0
    for o in resting:
        if near is None or o.price != near or final:
            actions.append(ChildAction("CANCEL", order_id=o.order_id))
            gap += o.visible
        else:
            still_working += o.visible
    unworked = remaining_parent - still_working
    if far is not None and policy.iwould_price is not None and (policy.iwould_price - far) * side >= 0:
        if unworked > 0:
            actions.append(ChildAction("AGGRESSIVE", unworked, policy.iwould_price))
        return actions
    want = max(0, min(gap, unworked))
    if want == 0 or near is None or far is None:
        return actions
    spread = view.spread if view.spread is not None else 0
    if final or spread <= policy.tight_spread_ticks:
        take = want
    else:
        behind = max(0, want - slice_qty)  # 0 when exactly on schedule
        take = int(round(policy.urgency * behind))
    post = want - take

    def jitter(q: int) -> int:
        if rng is None or policy.randomise == 0.0 or q <= 0:
            return q
        return int(min(q, max(1, round(q * (1.0 - policy.randomise * rng.random())))))

    for kind, q, px in (("AGGRESSIVE", take, far), ("PASSIVE", post, near)):
        q = jitter(q) if kind == "PASSIVE" else q
        if q <= 0:
            continue
        if q < policy.min_qty and q < unworked:
            continue
        actions.append(ChildAction(kind, int(q), int(px)))
    return actions
