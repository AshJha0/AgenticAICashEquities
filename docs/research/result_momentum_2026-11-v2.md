# Result: R5 v2 attempt (Stooq primary) — VOID before registration

- Date: 2026-11-08.
- Outcome: **VOID for data reasons.** The in-sample coverage gate (gate 1) still fails with the
  hand-downloaded Stooq bulk file as the primary vendor, so the attempt stopped before step 3.
- **No v2 preregistration was written or locked**, no test statistic was computed, and no return
  series was built for a verdict.
- Pseudo-holdout (2024-01-01 to 2025-12-31): **UNSPENT.** Every loader still clips at 2023-12-31 and
  refuses later dates. The Stooq files on disk do contain holdout-period rows; none were read.
- The v1 study ([`result_momentum_2026-11.md`](result_momentum_2026-11.md)) stays VOID and closed.

Evidence class for every number below: **measured**, on real free daily data, in-sample dates only
(2003-11-01 to 2023-12-31). Source for every figure:
[`v2/insample_checks_v2.json`](v2/insample_checks_v2.json) and
[`v2/coverage_report_v2.csv`](v2/coverage_report_v2.csv), produced by
`scripts/r5v2_insample_checks.py`.

## What was done

1. The owner downloaded Stooq's bulk US daily ASCII file in a browser (`d_us_txt.zip`, kept under
   `data/real_cache/stooq/`, git-ignored; no price data is committed). The 9,481 US stock files
   (`nasdaq`, `nyse` and `nysemkt` stocks; ETFs skipped) were unpacked to
   `data/real_cache/stooq/<sym>.us.txt`, the layout `load_stooq` reads. No file name collided across
   exchanges.
2. The two data gates of the v1 preregistration (section 7) were re-run with Stooq as the primary
   vendor and yfinance as the secondary, over the same evaluation window: holding months 2005-01 to
   2023-12 (228 months). Gate definitions, the point-in-time membership and the portfolio builder are
   unchanged from v1. The day axis is the NYSE calendar (`data/reference/nyse_calendar.csv`), the
   calendar in which the preregistration counts lags.

## Gate 1, coverage: FAIL

Coverage is usable name-months over member name-months (v1 prereg section 7); the bar is 95 %.

| | Value |
|---|---|
| Coverage, Stooq only, 2005-01 to 2023-12 | **74.9 %** |
| Worst month, Stooq only | 49.7 % |
| Coverage by year, Stooq only | 52.8 % (2005), 64.2 % (2009), 75.8 % (2015), 95.2 % (2023) |
| Minimum eligible names in a month | 240 (the breadth gate needs 100) |
| Descriptive: Stooq, then yfinance where Stooq has no file | 75.1 % (worst month 50.3 %) |
| Index vendor roots ever needed in the window | 944 |
| Roots with a Stooq file | 606 |
| Roots with a yfinance file | 613 |
| Roots in neither vendor | 327 |
| Stooq series whose history starts after 2004-01-01 | 337 of 606 |

Why it fails. Two separate gaps, both in the free source:

- **No delisted names.** Stooq's bulk file holds currently listed securities only. Companies that
  left the index through bankruptcy, acquisition or going private (for example LEH, CELG, ESRX, YHOO,
  TWX) have no file. That is the same survivorship gap that voided v1 with yfinance, and the two
  vendors miss almost the same names: adding yfinance as a fallback moves coverage by 0.2 points.
- **Truncated histories.** Of the 606 Stooq series found, 337 start after 2004-01-01, many in early
  2005, even for companies listed much earlier. A name needs 252 trading days of history before it
  can be scored, so these names are unusable for the first year or more of the window.

Coverage reaches the bar only in 2023. No change of vendor order, symbol mapping or calendar that
stays within the free sources on disk closes a 20-point gap made of missing companies.

Comparison with v1. v1 reported 75.1 % on yfinance alone, with the day axis taken from the vendor's
own dates. On the NYSE-calendar axis used here, Stooq alone gives 74.9 % and both vendors together
75.1 %. The two figures are not strictly comparable (different axis), but either way the gap to 95 %
is about 20 points.

## Gate 2, Stooq vs yfinance cross-check: PASS (but cannot rescue the study)

This is the check v1 could not run.

| Check | Value | Bar |
|---|---|---|
| Overlapping name-days (daily simple returns) | 2,620,341 | |
| Share within 10 bps | **99.56 %** | at least 99.5 % |
| yfinance overlap of Stooq name-days | 99.66 % | at least 80 % |
| Known split AAPL 2014-06-09 (7:1) absorbed by Stooq | pass | |
| Known split AAPL 2020-08-31 (4:1) absorbed by Stooq | pass | |
| Stooq series with duplicate or unordered dates | 0 | |

Lowest per-symbol agreement (listed in the JSON) comes from tickers that were reused by a different
company over time, for example MI (71 overlapping days, 36.6 %), PLD (65.9 %) and WEN (76.1 %). That
is a symbol-history issue in the vendors, not a pricing error, and it is small at the aggregate level.
The agreement also says the Stooq bulk closes carry the same split and dividend adjustment as
yfinance adjusted closes to within 10 bps on 99.56 % of days.

Gates are evaluated in order and the first failure is the outcome, so gate 1 decides: **VOID**.

## What was not done, and why

Under the coordinator's instruction for this attempt, a gate 1 or gate 2 failure stops the work
before step 3. So:

- No v2 preregistration (`prereg_momentum_2026-11-v2.md`) and no new lock were written.
- Gate 3 (UMD replication) was not re-run on Stooq; it could not rescue a gate 1 failure.
- No gross or net book, Newey-West t, Sharpe ratio or bootstrap was computed.
- No date after 2023-12-31 was requested from any loader.

## Claims

Allowed: "With Stooq's free bulk file as primary vendor, point-in-time coverage was 74.9 % against
a required 95 %, so the momentum test still cannot be run on free data. The Stooq vs yfinance return
cross-check passed at 99.56 % within 10 bps."

Forbidden: any statement that momentum does or does not work on this universe, any Sharpe ratio,
PROMOTE, alpha, tradable, any dollar capacity.

## What would un-void a later attempt

Unchanged from v1: delisting-aware daily prices with delisting returns (CRSP-like, generally paid and
excluded by the zero-spend rule), or a free source that covers delisted and acquired members to 95 %
of member name-months. Two free vendors have now been tried and both miss the same companies. The
pseudo-holdout remains unspent and could still serve as unseen data under a new preregistration.

## Reproduce

```bash
python scripts/r5v2_insample_checks.py
```

It needs the Stooq bulk files unpacked in `data/real_cache/stooq/` and the yfinance cache from
`scripts/download_daily.py` in `data/real_cache/yahoo/`. It runs in about seven minutes on the
project laptop.
