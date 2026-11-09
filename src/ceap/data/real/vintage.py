"""Data vintage: hash every downloaded series and diff it on re-download (0.10 D14).

Free vendors re-adjust history without notice: a new split or dividend adjustment rescales every earlier
adjusted close, and a correction can change single days. A study that re-downloads its data months later
may then be run on different numbers than the ones it was preregistered on. This module records, per
series, a SHA-256 of the canonical ``date,close`` rows and one hash per calendar year, in a JSON manifest
next to the cache. :func:`diff_vintage` compares a fresh download with the manifest and names the years
that changed, so a silent re-adjustment becomes a visible, dated event.

Evidence class: method (content hashing). The manifest lives in ``data/real_cache/`` (gitignored); the
study's preregistration lock records the manifest's own hash.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ceap.data.real.prices import DailySeries

CLOSE_DECIMALS = 6


def _rows(series: DailySeries) -> list[str]:
    return [f"{d.isoformat()},{c:.{CLOSE_DECIMALS}f}" for d, c in zip(series.dates, series.close, strict=True)]


def series_vintage(series: DailySeries) -> dict[str, Any]:
    """Hash of the whole series and of each calendar year (rows rounded to 6 decimals)."""
    rows = _rows(series)
    by_year: dict[str, list[str]] = defaultdict(list)
    for d, r in zip(series.dates, rows, strict=True):
        by_year[str(d.year)].append(r)
    return {
        "symbol": series.symbol,
        "vendor": series.vendor,
        "n": len(rows),
        "first": series.dates[0].isoformat() if rows else None,
        "last": series.dates[-1].isoformat() if rows else None,
        "sha256": hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest(),
        "years": {y: hashlib.sha256("\n".join(v).encode("utf-8")).hexdigest()[:16] for y, v in sorted(by_year.items())},
    }


def diff_vintage(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """What changed between two vintages of one series: changed, added and removed years."""
    oy, ny = old.get("years", {}), new.get("years", {})
    changed = sorted(y for y in set(oy) & set(ny) if oy[y] != ny[y])
    return {
        "symbol": new.get("symbol"),
        "identical": old.get("sha256") == new.get("sha256"),
        "changed_years": changed,
        "added_years": sorted(set(ny) - set(oy)),
        "removed_years": sorted(set(oy) - set(ny)),
    }


def record_vintages(manifest: Path, series: list[DailySeries]) -> list[dict[str, Any]]:
    """Write the vintages of ``series`` into ``manifest`` and return the diff against what it held before."""
    old = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    diffs = []
    for s in series:
        key = f"{s.vendor}:{s.symbol}"
        v = series_vintage(s)
        if key in old:
            diffs.append(diff_vintage(old[key], v))
        old[key] = v
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(old, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return diffs
