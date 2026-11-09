"""Interval estimates for evaluation rates.

The Wilson score interval is used for every proportion: it stays inside [0, 1] and is honest at the
boundaries, where the normal approximation claims zero uncertainty for 10/10. For example 10 of 10
gives a 95 % interval of about [0.72, 1.00].
"""

from __future__ import annotations

import math
from typing import Any

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Two-sided Wilson score interval for ``successes`` out of ``n`` (``(0, 1)`` when ``n == 0``)."""
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError(f"successes {successes} outside [0, {n}]")
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, round(centre - half, 4)), min(1.0, round(centre + half, 4)))


def rate(successes: int, n: int) -> dict[str, Any]:
    """A rate record: ``k``, ``n``, the point estimate and its 95 % Wilson interval."""
    lo, hi = wilson(successes, n)
    return {
        "k": successes,
        "n": n,
        "rate": round(successes / n, 4) if n else None,
        "ci95": [lo, hi],
    }
