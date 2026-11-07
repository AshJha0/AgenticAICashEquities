"""Build ``data/reference/sp500_changes.csv`` (R5 item 5.2). Network script; tests never run it.

Sources (free, no sign-up):
- fja05680/sp500 (MIT): "S&P 500 Historical Components & Changes (Updated).csv", dated snapshots of
  the full constituent list. Diffs between consecutive snapshots give the change events.
- Wikipedia "Historical components of the S&P 500", changes table (CC BY-SA 4.0), used to reconcile each event:
  an event Wikipedia confirms within 7 days gets source "both".

Raw downloads go to ``data/real_cache/universe/`` (gitignored). Usage:

    python scripts/fetch_sp500_changes.py
"""

from __future__ import annotations

import csv
import io
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ceap.data.real.universe_pit import normalise_ticker  # noqa: E402

GH = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
WIKI = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
CACHE = ROOT / "data" / "real_cache" / "universe"
OUT = ROOT / "data" / "reference" / "sp500_changes.csv"
START = date(2003, 11, 1)
END = date(2025, 12, 31)
UA = {"User-Agent": "ceap-research-script/0.1 (open-source research)"}


def _get(url: str, name: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https URL
            path.write_bytes(resp.read())
    return path.read_text(encoding="utf-8")


def _snapshots(text: str) -> list[tuple[date, set[str]]]:
    rows = list(csv.reader(io.StringIO(text)))
    out = [
        (date.fromisoformat(r[0]), {normalise_ticker(t) for t in r[1].split(",") if t.strip()})
        for r in rows[1:]
    ]
    return sorted(out, key=lambda x: x[0])


def _wiki_events(html: str) -> set[tuple[str, str, date]]:
    m = re.search(r'id="changes".*?</table>', html, re.S)
    if not m:
        return set()
    events = set()
    last: date | None = None
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if not cells:
            continue
        try:
            last = datetime.strptime(cells[0], "%B %d, %Y").date()
            cells = cells[1:]
        except ValueError:
            pass  # date cell spans several rows (rowspan): reuse the previous date
        if last is None or len(cells) < 4:
            continue
        if cells[0]:
            events.add(("add", normalise_ticker(cells[0]), last))
        if cells[2]:
            events.add(("remove", normalise_ticker(cells[2]), last))
    return events


def main() -> None:
    snaps = _snapshots(_get(GH, "fja05680_components.csv"))
    wiki = _wiki_events(_get(WIKI, "wikipedia_sp500_changes.html"))
    base_i = max(i for i, (d, _) in enumerate(snaps) if d <= START)
    base_d, prev = snaps[base_i]
    rows = [(base_d.isoformat(), "initial", t, "github") for t in sorted(prev)]
    confirmed = 0
    wiki_in_range = {e for e in wiki if base_d < e[2] <= END}
    for d, cur in snaps[base_i + 1 :]:
        if d > END:
            break
        for action, names in (("add", cur - prev), ("remove", prev - cur)):
            for t in sorted(names):
                hit = any((action, t, d + timedelta(days=k)) in wiki for k in range(-7, 8))
                confirmed += hit
                rows.append((d.isoformat(), action, t, "both" if hit else "github"))
        prev = cur
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        fh.write(
            "# S&P 500 membership events, approximately point in time. See data/reference/ATTRIBUTION.md.\n"
        )
        fh.write(
            "# Built by scripts/fetch_sp500_changes.py from fja05680/sp500 (MIT), reconciled against the\n"
        )
        fh.write("# Wikipedia 'List of S&P 500 companies' changes table (CC BY-SA 4.0).\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(("date", "action", "ticker", "source"))
        w.writerows(rows)
    n_ev = sum(1 for r in rows if r[1] != "initial")
    print(
        f"initial {base_d}: {len(rows) - n_ev} names; events {n_ev}; wikipedia-confirmed {confirmed}; "
        f"wikipedia events in range {len(wiki_in_range)}"
    )


if __name__ == "__main__":
    main()
