"""Cash dividends for paper trading from a free source (F13 data; 0.9.0). Opt-in, default off.

Plain idea. The paper engine books a dividend as cash when a panel carries one
(:class:`~ceap.paper.feed.Dividend`). This module gives a panel its dividends: it fetches a
per-symbol series from a free, no-sign-up source, caches it as CSV under
``data/real_cache/dividends/`` (git-ignored, like the Stooq cache), and keeps the panel's dates only.

Formal statement.

* **Source.** :class:`YFinanceDividends` reads ``yfinance.Ticker(symbol).dividends`` (Yahoo Finance's
  public chart data; no key, no account). ``yfinance`` is *not* a project dependency: it is imported
  only when a download is asked for, and a missing package is a clear error. Any object with
  ``fetch(symbol) -> list[tuple[date, float]]`` can stand in (tests use fixtures, never the network).
* **Units.** Yahoo's dividend series is split-adjusted, matching the split-adjusted Stooq closes the
  real feed reads. A feed of *unadjusted* prices would need unadjusted dividends; this is stated, not
  converted.
* **Vendor revisions.** Each fetched series is canonicalised (``YYYY-MM-DD,amount`` lines, amounts
  with ``repr``) and hashed with SHA-256. Every fetch appends ``{symbol, fetched_at, sha256, rows,
  first, last, revised}`` to ``revisions.jsonl``; ``revised`` is true when the hash differs from the
  previous record for that symbol. History is never rewritten, so a vendor restatement stays visible.

Worked example. A cached ``AAPL.csv`` with ``2026-02-09,0.26`` and a panel that holds 2026-02-09
gives ``Dividend("AAPL", "2026-02-09", 0.26)``; a long of 1,000 shares receives $260 that session.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

from ceap.paper.feed import Dividend, MarketPanel

DIVIDEND_CACHE = Path("data/real_cache/dividends")
REVISIONS = "revisions.jsonl"
_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9.^=-]*")


def _csv_path(cache: Path, symbol: str) -> Path:
    """``cache/<symbol>.csv``; a symbol that could leave the cache directory is refused."""
    if not _SYMBOL.fullmatch(symbol) or ".." in symbol:
        raise ValueError(f"not a ticker symbol: {symbol!r}")
    return cache / f"{symbol}.csv"


class DividendSource(Protocol):
    name: str

    def fetch(self, symbol: str) -> list[tuple[date, float]]: ...


class YFinanceDividends:
    """Dividends from Yahoo Finance through the optional ``yfinance`` package (free, no sign-up)."""

    name = "yfinance"

    def fetch(self, symbol: str) -> list[tuple[date, float]]:
        try:
            import yfinance  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError("dividends from yfinance need `pip install yfinance` (not a project "
                               "dependency; the feature is opt-in)") from exc
        series = yfinance.Ticker(symbol).dividends
        return [(ts.date(), float(v)) for ts, v in series.items() if float(v) > 0]


def canonical_series(rows: Sequence[tuple[date, float]]) -> str:
    return "".join(f"{d.isoformat()},{float(a)!r}\n" for d, a in sorted(rows))


def series_hash(rows: Sequence[tuple[date, float]]) -> str:
    return hashlib.sha256(canonical_series(rows).encode("ascii")).hexdigest()


def read_cached(cache: Path, symbol: str) -> list[tuple[date, float]]:
    p = _csv_path(cache, symbol)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="ascii").splitlines():
        if line.strip():
            d, a = line.split(",")
            out.append((date.fromisoformat(d), float(a)))
    return out


def _last_hash(cache: Path, symbol: str) -> str | None:
    p = cache / REVISIONS
    if not p.exists():
        return None
    last = None
    for line in p.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("symbol") == symbol:
            last = rec["sha256"]
    return last


def refresh(symbols: Sequence[str], source: DividendSource, cache: Path = DIVIDEND_CACHE,
            now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> list[dict[str, Any]]:
    """Fetch, cache and hash each symbol's series; append one revision record per symbol."""
    cache.mkdir(parents=True, exist_ok=True)
    records = []
    for sym in sorted(symbols):
        rows = sorted(source.fetch(sym))
        h = series_hash(rows)
        prev = _last_hash(cache, sym)
        _csv_path(cache, sym).write_text(canonical_series(rows), encoding="ascii", newline="\n")
        rec = {"symbol": sym, "source": source.name, "fetched_at": now().isoformat(timespec="seconds"),
               "sha256": h, "rows": len(rows), "first": rows[0][0].isoformat() if rows else None,
               "last": rows[-1][0].isoformat() if rows else None, "revised": prev is not None and prev != h}
        with (cache / REVISIONS).open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        records.append(rec)
    return records


def attach_dividends(panel: MarketPanel, cache: Path = DIVIDEND_CACHE) -> MarketPanel:
    """The panel with the cached dividends whose ex-date is one of its sessions."""
    sessions = {panel.day(i) for i in range(panel.n_days)}
    divs = [Dividend(sym, d.isoformat(), a) for sym in panel.symbols for d, a in read_cached(cache, sym)
            if d.isoformat() in sessions]
    meta = dict(panel.meta)
    meta["dividends"] = {"cache": str(cache), "count": len(divs),
                         "series_sha256": {s: series_hash(read_cached(cache, s)) for s in panel.symbols
                                           if _csv_path(cache, s).exists()}}
    return replace(panel, dividends=tuple(divs), meta=meta)
