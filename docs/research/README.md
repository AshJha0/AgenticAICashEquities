# Real-data study (round R5)

**Outcome: VOID** (owner decision, 2026-11-07). Coverage failed its gate and the cross-check could
not run, so no test statistic was computed and no verdict is issued. Items 5.6 and 5.7 were not
run, and the pseudo-holdout is unspent. See [`result_momentum_2026-11.md`](result_momentum_2026-11.md).
The sections below record the preparation (items 5.1 to 5.5) as it was done.

**Rerun conditions (D20).** R5 stays VOID. It is rerun only if the owner downloads the Stooq bulk
file by hand, and then under a new (v2) preregistration. The paper-trading Stooq feed
(`ceap.paper.feeds_real`) refuses every row up to 2025-12-31, so the paper record cannot spend the
pseudo-holdout either.

One preregistered test of 12-1 momentum on an approximately point-in-time S&P 500, using free data
only (D2). This page covers the preparation: items 5.1 to 5.5. The primary test (5.6) and the one
look at the pseudo-holdout (5.7) were planned for after 0.6.0 and were not run (the study is VOID; decision D20). **No hypothesis-test statistic has been computed.**

## 5.1 Preregistration (locked before any data was read)

- Document: [`prereg_momentum_2026-11.md`](prereg_momentum_2026-11.md). An independent second reader
  reviewed it before the lock. Six required fixes were applied: warm-up start, final-month
  truncation, membership timing, cost inputs, gate definitions and order, minimum breadth.
- Lock: [`prereg_lock.json`](prereg_lock.json), SHA-256
  `7ce84a671ffe24484078a15f23cfbf8a4eb8c983982eaffe1b0cb7946a059da4`, committed in `fb9858e` and
  pushed before any price, factor or membership file was downloaded.
- Enforcement: `ceap.data.real.prereg.require_prereg_lock()` runs in every price loader and in
  `scripts/download_daily.py`. Loaders refuse any date after 2023-12-31, so the pseudo-holdout can't
  be read in this round.
- Minimum detectable annualised net Sharpe: 0.57 (one-sided alpha 0.05, power 0.80, 19 years).

## 5.2 Point-in-time universe and coverage

- Membership: `data/reference/sp500_changes.csv` holds 494 initial names at 2003-10-24 and 1,031
  dated events to 2025-12-31. The snapshots come from fja05680/sp500 (MIT). Of the events, 646 are
  confirmed by the Wikipedia change table within 7 days (source `both`). Before 2010 the Wikipedia
  table is sparse, so most early events are `github` only. The ticker map `data/reference/ticker_map.csv`
  handles dated renames (for example FB to META, and GOOG to GOOGL before 2014). BRK.B formatting
  and Stooq/Yahoo suffixes are handled in code. Attribution: `data/reference/ATTRIBUTION.md`.
- Coverage report: [`coverage_report.csv`](coverage_report.csv) has one row per month. Coverage is
  usable name-months divided by member name-months, as defined in prereg section 7.

| | Value |
|---|---|
| Overall coverage, 2005-01 to 2023-12 | **75.1 %** |
| Worst month | 53.9 % (Dec 2004 formation) |
| Coverage by year | 55 % (2005) rising to 95 % (2023) |
| Minimum eligible names in a month | 264 (the breadth gate needs 100 or more) |

**Gate 1 (coverage at least 95 %) fails on current free data.** yfinance drops the history of
delisted and acquired companies, for example LEH, CELG, ESRX, and EA after it was taken private.
That is exactly the survivorship gap point-in-time membership is meant to expose. Under the
preregistration the study is **VOID**. The owner decided against a manual Stooq download.

- Survivor-only universe (today's members used throughout), descriptive only: correlation with the
  point-in-time gross book 0.93; annualised gross difference -0.75 %.

## 5.3 Prices and the quality gate

- yfinance: 613 symbols cached in `data/real_cache/yahoo/`. The cache is gitignored and never
  committed.
- Stooq: its download endpoint now serves a JavaScript proof-of-work browser check to scripts. CEAP
  does not solve or bypass bot checks (D2), so Stooq is not fetched automatically.
  `ceap.data.real.prices.parse_stooq` reads both the per-symbol and bulk formats, from files a person
  places in `data/real_cache/stooq/`.
- Quality report: [`quality_report.json`](quality_report.json).

| Check | Result |
|---|---|
| Hard-fail series (duplicate or unordered dates, off-calendar days, failed known split) | 0 of 613 |
| Series with stale runs (5 or more identical closes) | 15 |
| Zero-volume name-days | 8,456 of 2.75 M |
| Name-days missing against the NYSE calendar | 114 |
| Moves beyond 40 % (flagged for cross-check) | 421 |
| Known splits AAPL 2014-06-09 and 2020-08-31 absorbed | pass, pass |
| Stooq vs yfinance returns within 10 bps on at least 99.5 % of name-days | **NOT RUN** (no Stooq files) |

**Gate 2 can't be evaluated without Stooq files.** Under the preregistration it counts as failed.

## 5.4 Cost schedule

`ceap.analytics.real_costs` prices one rebalance:

- Half-spread: 10 bps a side for 2005 to 2009, 5 bps from 2010.
- Square-root impact: Y = 0.7 at USD 100M gross. A name with no ADV gets the book's 95th-percentile
  impact rate.
- Borrow: 0.3 % a year, plus 5 % a year for the bottom 5 % of names by dollar volume.
- Breakeven and a flat-cost sensitivity table.
- Corwin-Schultz spread estimate, as a sensitivity only.

Known-answer tests are in `tests/unit/test_real_data.py`. It is a standalone module, so the R2
backtest is not edited. Item 5.6 wires it in.

## 5.5 Replication gate against Ken French UMD (in-sample only)

Source: [`replication_report.json`](replication_report.json). Gross monthly decile long-short,
holding months 2005-01 to 2023-12 (228 months).

| | Value |
|---|---|
| Correlation with UMD | **0.869** (gate at 0.5 or above: **PASS**) |
| FF5 + UMD loadings | UMD 1.41, Mkt -0.14, HML -0.13, RMW -0.09, CMA -0.03, SMB -0.00; R² 0.77 |
| Crash months, pipeline vs UMD | 2009-03 -39.9 % vs -11.8 %; 2009-04 -50.0 % vs -34.4 %; 2009-05 -23.2 % vs -12.5 %; 2020-11 -23.3 % vs -12.6 % |
| Mean monthly turnover (both legs, gross 2) | 1.15 |
| Breakeven one-way cost | -38 bps (the gross mean is negative on this coverage-deficient sample, so no cost level breaks even) |

The intercept t and the mean-return t are not computed. They belong to item 5.6.

## Reproduce

```bash
pip install yfinance
python scripts/fetch_sp500_changes.py
python scripts/fetch_ff_factors.py
python scripts/download_daily.py
python scripts/fetch_real_reports.py
```
