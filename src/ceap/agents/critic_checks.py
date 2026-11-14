"""Deterministic statement-versus-evidence checks for the critic (0.9.0, lane L3).

Plain idea: a finding says something in words ("shortfall improved by 6.4 bps") and cites evidence
records with numbers (``is_change_bps = 6.4``). Three faults can be read off without a model:

- **contradicted**: the finding's own citations disagree (two cited records give different values
  for the same attribute), or the producer cited a resolvable contradicting record;
- **numeric_mismatch**: the statement names a metric the cited evidence carries, but none of the
  statement's numbers equals that metric's value;
- **direction_mismatch**: the statement's direction word ("rose", "improved", "narrowed") disagrees
  with the sign of the cited change attribute, taking the metric's polarity into account
  (shortfall up is bad; fill rate up is good).

Formal statement: metric mentions come from :data:`METRICS` (phrase -> attribute stem, polarity) or
from a literal attribute name in the statement. With a direction word present the candidates are
the ``*change*`` attributes of that stem; without one, the level attributes. Two numbers agree when
``|a - b| <= max(0.005, 0.01 * max(|a|, |b|))``, or when ``a`` as printed is within its own rounding
(half a unit in its last decimal: "1.1" agrees with 1.0864), comparing absolute values (the direction
check owns the sign) and also ``a / 100`` (a percent against a fraction). A metric found only by its
phrase matches an attribute only when the statement also names that attribute's other qualifiers
("slippage excess" is not ``vwap_slippage_delta_bps``). Attributes "disagree" across records
under the same tolerance widened to 10 %.

Worked example: "Implementation shortfall improved by 6.4 bps" citing ``is_change_bps = 6.4``. The
stem is ``is_change`` (lower is better); "improved" on a lower-is-better metric means the change
should be negative; it is +6.4, so the check returns ``direction_mismatch``. "Fill rate improved by
3.0 percentage points" citing ``fill_rate_change_pct = 3.0`` passes: fill rate is higher-is-better.

The checks only add flags; a statement they cannot parse passes unflagged.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: phrase -> (attribute stem, higher_is_better)
METRICS: dict[str, tuple[str, bool]] = {
    "implementation shortfall": ("is_change", False),
    "shortfall": ("is_change", False),
    "slippage": ("slippage", False),
    "spread": ("spread", False),
    "latency": ("latency", False),
    "fill rate": ("fill_rate", True),
    "depth": ("depth", True),
    "volatility": ("volatility", False),
}
_UP = {"rose", "risen", "rise", "rises", "increased", "increase", "increases", "widened", "widen",
       "climbed", "jumped", "grew", "higher", "up", "spiked", "surged"}
_DOWN = {"fell", "fallen", "fall", "falls", "dropped", "drop", "declined", "decline", "decreased",
         "decrease", "narrowed", "narrow", "tightened", "lower", "down", "shrank", "eased"}
_GOOD = {"improved", "improve", "improves", "better"}
_BAD = {"worsened", "worsen", "deteriorated", "deteriorate", "worse"}
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?")


def _close(a: float, b: float, rel: float = 0.01) -> bool:
    return abs(a - b) <= max(0.005, rel * max(abs(a), abs(b)))


def _numbers(statement: str) -> list[float]:
    return [float(x) for x in _NUMBER.findall(statement)]


def _printed(statement: str) -> list[tuple[float, float]]:
    """``(value, half a unit in the last printed place)`` for each number in ``statement``."""
    out = []
    for x in _NUMBER.findall(statement):
        decimals = len(x.split(".")[1]) if "." in x else 0
        out.append((float(x), 0.5 * 10.0**-decimals))
    return out


def _agrees(printed: tuple[float, float], value: float) -> bool:
    """A printed number agrees with ``value`` within the tolerance or its own rounding."""
    n, half_ulp = printed
    a, v = abs(n), abs(value)
    return _close(a, v) or _close(a / 100.0, v) or abs(a - v) <= half_ulp + 1e-12


#: attribute-name tokens that qualify nothing (units, and ``change``, which the direction check owns)
_NEUTRAL_TOKENS = {"bps", "bp", "pct", "ms", "us", "ns", "change", "ratio"}


def _qualifiers_named(key: str, stem: str, words: set[str]) -> bool:
    """True when every qualifier in ``key`` beyond ``stem`` and units appears in the statement:
    "slippage excess" names neither ``vwap`` nor ``delta``, so it is not ``vwap_slippage_delta_bps``."""
    extra = set(key.lower().split("_")) - set(stem.split("_")) - _NEUTRAL_TOKENS
    return extra <= words


def _words(statement: str) -> list[str]:
    return re.findall(r"[a-z_]+", statement.lower())


def _numeric_attrs(records: Iterable[Mapping[str, Any]]) -> list[tuple[str, float]]:
    out = []
    for attrs in records:
        for k, v in attrs.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                out.append((k, float(v)))
    return out


def _mention(statement: str, keys: set[str]) -> tuple[str | None, str | None, bool]:
    """``(literal attribute name, stem, higher_is_better)`` mentioned by ``statement``."""
    low = statement.lower()
    literal = next((k for k in sorted(keys, key=len, reverse=True) if k.lower() in low), None)
    for phrase, (stem, hib) in METRICS.items():
        if re.search(rf"\b{re.escape(phrase)}", low) or (literal and literal.startswith(stem)):
            return literal, stem, hib
    return literal, None, False


def direction(statement: str, higher_is_better: bool) -> int:
    """+1 (statement says the metric went up), -1 (down) or 0 (no or mixed direction words)."""
    signs = set()
    for w in _words(statement):
        if w in _UP:
            signs.add(1)
        elif w in _DOWN:
            signs.add(-1)
        elif w in _GOOD:
            signs.add(1 if higher_is_better else -1)
        elif w in _BAD:
            signs.add(-1 if higher_is_better else 1)
    return signs.pop() if len(signs) == 1 else 0


def check_statement(
    statement: str,
    supporting: Sequence[Mapping[str, Any]],
    contradicting: Sequence[Mapping[str, Any]] = (),
) -> list[tuple[str, str]]:
    """``(flag, note)`` pairs for one finding; ``supporting`` / ``contradicting`` are the cited,
    resolved evidence records' attribute dicts."""
    out: list[tuple[str, str]] = []
    if contradicting:
        out.append(("contradicted", f"the finding cites {len(contradicting)} contradicting evidence record(s)"))
    values: dict[str, list[float]] = {}
    for k, v in _numeric_attrs(supporting):
        values.setdefault(k, []).append(v)
    for k, vs in values.items():
        if len(vs) > 1 and any(not _close(vs[0], x, rel=0.10) for x in vs[1:]):
            out.append(("contradicted", f"cited records disagree on {k}: {vs}"))
            break
    literal, stem, hib = _mention(statement, set(values))
    if stem is None and literal is None:
        return out
    sign = direction(statement, hib)
    if literal is not None:
        cands = [literal]
    else:
        assert stem is not None
        cands = [k for k in values if k.startswith(stem) or f"_{stem}" in k]
        cands = [k for k in cands if ("change" in k) == (sign != 0)]
        words = set(_words(statement)) | set(re.findall(r"[a-z]+", statement.lower()))
        cands = [k for k in cands if _qualifiers_named(k, stem, words)]
    nums = _numbers(statement)
    printed = _printed(statement)
    cvals = [(k, v) for k in cands for v in values.get(k, [])]
    if nums and cvals:
        ok = any(_agrees(p, v) for p in printed for _, v in cvals)
        if not ok:
            out.append(
                ("numeric_mismatch", f"statement numbers {nums} do not match cited {dict(cvals)}")
            )
    if sign != 0:
        changes = [(k, v) for k, v in cvals if "change" in k and v != 0.0]
        if changes and all((v > 0) != (sign > 0) for _, v in changes):
            word = "up" if sign > 0 else "down"
            out.append(("direction_mismatch", f"statement says {word}; cited {dict(changes)} says otherwise"))
    return out


# ------------------------------------------------------------------ H5 (R-03): one name per baseline
#: delta attributes that findings and the attribution both report, with their printed metric name
DELTA_METRICS: dict[str, str] = {
    "is_delta_bps": "implementation shortfall",
    "vwap_slippage_delta_bps": "slippage versus interval VWAP",
}
#: deltas are printed to 0.1 bps; two values within half of that are the same number
BASELINE_DELTA_TOLERANCE_BPS = 0.05
UNNAMED_BASELINE = "the baseline"
BASELINE_FLAG = "baseline_conflict"


def baseline_conflicts(
    findings: Iterable[Any], attribution: Mapping[str, Any] | None, attribution_baseline: str | None
) -> list[str]:
    """Two deltas of one metric in one report must name different baselines or agree (H5, R-03).

    Plain idea. T06 at 0.9.0 said "shortfall delta -3.7 bps" in the summary (against the matched peer
    windows) and "decreased by 28.6 bps relative to the baseline" in an observation (against the prior
    window). Each number was right; printed under one word they contradict each other.

    Every finding attribute in :data:`DELTA_METRICS` is read with its ``baseline`` attribute (a finding
    without one is under :data:`UNNAMED_BASELINE`), and the attribution's deltas with
    ``attribution_baseline``. Two values of the same metric under the same name that differ by more
    than :data:`BASELINE_DELTA_TOLERANCE_BPS` are a conflict; one line per conflicting pair.
    """
    stated: dict[str, list[tuple[str, str, float]]] = {key: [] for key in DELTA_METRICS}
    for f in findings:
        attrs = getattr(f, "attributes", None) or {}
        for key in DELTA_METRICS:
            value = attrs.get(key)
            if isinstance(value, int | float) and math.isfinite(value):
                stated[key].append((str(f.id), str(attrs.get("baseline") or UNNAMED_BASELINE), float(value)))
    for key in DELTA_METRICS:
        value = (attribution or {}).get(key)
        if isinstance(value, int | float) and math.isfinite(value):
            stated[key].append(("attribution", attribution_baseline or UNNAMED_BASELINE, float(value)))
    out: list[str] = []
    for key, rows in stated.items():
        for i, (a, name_a, va) in enumerate(rows):
            for b, name_b, vb in rows[i + 1 :]:
                if name_a == name_b and abs(va - vb) > BASELINE_DELTA_TOLERANCE_BPS:
                    out.append(
                        f"{a} and {b}: {DELTA_METRICS[key]} delta {va:+.1f} and {vb:+.1f} bps "
                        f"under one baseline name ({name_a!r})"
                    )
    return out
