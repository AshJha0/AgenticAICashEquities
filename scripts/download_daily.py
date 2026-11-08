"""Download daily prices for every S&P 500 member of the R5 window (item 5.3). Network script.

- yfinance: fetched into ``data/real_cache/yahoo/<SYMBOL>.csv`` (gitignored, never committed).
- Stooq: NOT fetched. Its endpoint serves a JavaScript proof-of-work browser check and CEAP does not
  bypass bot checks (D2). A person may download Stooq files in a browser into
  ``data/real_cache/stooq/`` (per-symbol ``<sym>.us.txt`` or the bulk US daily ASCII files); the
  loaders and the cross-check pick them up from there.

The script refuses to run unless the preregistration lock matches, and never requests a date past
the in-sample end (2023-12-31), so the pseudo-holdout is not downloaded.

    pip install yfinance
    python scripts/download_daily.py
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ceap.data.real.prereg import require_prereg_lock  # noqa: E402
from ceap.data.real.universe_pit import PointInTimeUniverse, yahoo_symbol  # noqa: E402

OUT = ROOT / "data" / "real_cache" / "yahoo"
BATCH = 40


def vendor_roots(universe: PointInTimeUniverse, start, end) -> set[str]:  # type: ignore[no-untyped-def]
    roots = set()
    for t in universe.ever_members(start, end):
        roots.add(t)
        roots.update(m.vendor for m in universe._map if m.ticker == t)
    return roots


def main() -> None:
    import yfinance as yf  # optional dependency, scripts only

    lock = require_prereg_lock()
    uni = PointInTimeUniverse.from_reference()
    roots = sorted(vendor_roots(uni, lock.warmup_start, lock.eval_end))
    OUT.mkdir(parents=True, exist_ok=True)
    todo = [r for r in roots if not (OUT / f"{yahoo_symbol(r)}.csv").exists()]
    print(f"{len(roots)} symbols, {len(todo)} to fetch")
    end_exclusive = (lock.eval_end + timedelta(days=1)).isoformat()
    got = 0
    for i in range(0, len(todo), BATCH):
        syms = [yahoo_symbol(r) for r in todo[i : i + BATCH]]
        df = yf.download(
            syms,
            start=lock.warmup_start.isoformat(),
            end=end_exclusive,
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
        for s in syms:
            try:
                sub = df[s].dropna(subset=["Adj Close"])
            except KeyError:
                continue
            if sub.empty:
                continue
            with (OUT / f"{s}.csv").open("w", encoding="utf-8", newline="\n") as fh:
                fh.write("date,close,adj_close,volume\n")
                for d, row in sub.iterrows():
                    fh.write(
                        f"{d.date().isoformat()},{row['Close']:.6f},{row['Adj Close']:.6f},{row['Volume']:.0f}\n"
                    )
            got += 1
        print(f"batch {i // BATCH + 1}: total written {got}")
    print(f"done: {got} new files in {OUT}")


if __name__ == "__main__":
    main()
