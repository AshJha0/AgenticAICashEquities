# Attribution for committed reference data

## `sp500_changes.csv` (S&P 500 membership events, approximately point in time)

Derived by `scripts/fetch_sp500_changes.py` from:

- **fja05680/sp500**, "S&P 500 Historical Components & Changes (Updated).csv",
  https://github.com/fja05680/sp500, MIT licence. Dated constituent snapshots; consecutive snapshots
  are diffed into add and remove events.
- **Wikipedia**, "Historical components of the S&P 500", changes table,
  https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500, by Wikipedia contributors,
  licensed under **CC BY-SA 4.0** (https://creativecommons.org/licenses/by-sa/4.0/). Used to
  reconcile event dates; rows tagged `both` are confirmed by this table within 7 days. Because this
  file is an adaptation that includes facts reconciled against CC BY-SA material, it is shared under
  CC BY-SA 4.0.

"S&P 500" is a trademark of S&P Dow Jones Indices LLC. This file is not provided or endorsed by S&P
Dow Jones Indices and is not the official constituent history.

## `ticker_map.csv`

Hand-curated dated renames from public company notices. CC BY-SA 4.0, same as above.

## `ff_factors.csv` and `rf.csv`

Kenneth R. French data library, Tuck School of Business at Dartmouth,
https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html (Fama-French 5 factors 2x3,
momentum factor, one-month T-bill). Free for research use; cite the library.

## Daily prices (not committed)

Yahoo Finance via the `yfinance` package and, when a person downloads them in a browser, Stooq
(https://stooq.com). Vendor price data is cached in `data/real_cache/`, which is gitignored and never
committed.
