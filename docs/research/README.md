# Research studies

This folder holds the project's real-data studies and the research tooling notes. Every study here
was preregistered and hash-locked before its data was read.

| Study | Data | Outcome | Page |
|---|---|---|---|
| R5, 12-1 momentum on an approximately point-in-time S&P 500 (0.7.0, v2 in 0.7.1) | free daily prices, git-ignored cache | **VOID** twice (coverage 75.1 % then 74.9 % against 95 %); no statistic computed | this page, below |
| E1, 49-industry momentum (0.9.0) | Ken French 49 industry portfolios, raw-file SHA-256 in `e1/data_hashes.json` | **NULL** on the 1990-2015 holdout (net 10 bps NW t 1.16 < 1.645); ETF gate PASS; replication gate NOT RUN | [e1/README.md](e1/README.md) |
| E2, why stock-level R5 was predictably VOID on free data (0.9.0) | analysis of the R5 inputs | explanation, no new test | [e2_why_r5_could_not_pass.md](e2_why_r5_could_not_pass.md) |

The 0.8.0 research realism toolkit is described [at the end of this page](#research-realism-toolkit-080-ws-d).

## Real-data study (round R5)

**Outcome: VOID** (owner decision, 2026-11-07). Coverage failed its gate and the cross-check could
not run, so no test statistic was computed and no verdict is issued. Items 5.6 and 5.7 were not
run, and the pseudo-holdout is unspent. See [`result_momentum_2026-11.md`](result_momentum_2026-11.md).
The sections below record the preparation (items 5.1 to 5.5) as it was done.

**v2 attempt (2026-11-08): VOID before registration.** The owner hand-downloaded Stooq's bulk US
file and the two data gates were re-run in-sample with Stooq as primary. Coverage was 74.9 %
(Stooq only; 75.1 % with yfinance as fallback) against 95 %, because Stooq also lacks delisted names
and 337 of its 606 matching series start after 2004. The Stooq vs yfinance cross-check passed at
99.56 % within 10 bps. No v2 preregistration was locked, no statistic was computed, and the
pseudo-holdout is still unspent. See [`result_momentum_2026-11-v2.md`](result_momentum_2026-11-v2.md).

**Rerun conditions (D20).** R5 stays VOID. D20 allowed a rerun if the owner downloaded the Stooq
bulk file by hand, under a new (v2) preregistration; that download happened and the v2 attempt above
stopped at the coverage gate before any preregistration was locked. A further attempt would need a
source that includes delisted names, and a new preregistration. The paper-trading Stooq feed
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
preregistration the study is **VOID**. At the time the owner decided against a manual Stooq
download; that was reversed on 2026-11-08 (the v2 attempt above).

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

## Research realism toolkit (0.8.0, WS-D)

These changes make the *synthetic* research evaluation harder to pass and easier to audit. None of
them is a result about real markets. Evidence class in brackets.

**Calibration (D1) [literature + simulation].** Published moments for US cross-sectional signals are
a monthly rank IC of about 0.02 to 0.03 and a gross long-short Sharpe of about 0.3 to 0.5 (Grinold and
Kahn 2000, ch. 6; Asness, Moskowitz and Pedersen 2013). The old "realistic" momentum template had a
rank IC of 0.062 and the strong one 0.100. Templates now carry a `calibration` label:
`momentum_realistic` (R08, rank IC 0.025, gross Sharpe 0.42) and `reversal_realistic` (R09, five-day
rank IC 0.024, gross Sharpe 0.84 before costs that consume it) match those moments; the other
templates say "Stylised" and state the multiple (about 4x for momentum). One honest wrinkle: in this
generator a 12-1 momentum signal estimates the latent drift with error, so matching the IC needs a
cross-sectional expected-return SD of about 11 %/yr, not the 3 to 6 %/yr often quoted.

**Verdicts (D3, D4) [method].** The IC hurdle is fixed in advance: t >= 1.645 for one preregistered
test, t >= 3.0 for anything chosen from a search (Harvey, Liu and Zhu 2016). The default research
flow counts the whole 24-configuration search, so it uses 3.0. A third verdict, `INCONCLUSIVE`,
replaces `REJECT` when every flag is statistical (`NO_ALPHA`, `OOS_DECAY`) and the 95 % interval of
the full-period net Sharpe (Lo 2002 standard error) covers both 0 and the 0.5 minimum: the data
cannot tell "no edge" from "a tradable edge". The realistic momentum template's expected verdict is
`INCONCLUSIVE`. No `MONITOR` verdict was added: nothing would act on it differently.

**Null sweep (D3, D20, D21) [simulation].** Re-run on 500 pure-noise seeds over the full search; it
reproduced the 0.4.0 sweep hash `ef474869e5fa…` exactly. At t >= 3.0 it found 0 false PROMOTEs (95 %
Clopper-Pearson upper bound 0.74 %); at 1.645, 5 of 500 (1.0 %; one seed's best statistic is 1.646). The per-seed vector is published in
[`null_sweep_per_seed.json`](null_sweep_per_seed.json) with a 50-seed quick check in
`data/reference/research_thresholds.json`, and every sweep run is appended to
`data/reference/sweep_ledger.jsonl` with its generator version.

**Power at the hurdles in force (0.10.0, Q5) [simulation].** [`../quant_power_curve.json`](../quant_power_curve.json)
gives the detection rate of the IC part of the gate (in-sample IC t at least the hurdle and
out-of-sample IC t at least 1.0) against a planted momentum drift premium, 40 seeds a point, on the
30-name synthetic universe over the generator's full horizon. Earlier files were at the retired
hurdle 0.40.

| Premium (bps a year) | 0 | 600 | 900 | 1200 | 1500 | 1800 | 2100 | 2400 |
|---|---|---|---|---|---|---|---|---|
| Expected gross Sharpe | 0.00 | 0.13 | 0.29 | 0.50 | 0.75 | 1.04 | 1.35 | 1.68 |
| Detection, single test (t >= 1.645) | 0 % | 5 % | 18 % | 48 % | 80 % | 93 % | 95 % | 100 % |
| Detection, mined result (t >= 3.0) | 0 % | 0 % | 3 % | 18 % | 60 % | 93 % | 95 % | 100 % |

In plain terms: with 80 % power the gate detects a premium of about 1500 bps a year (expected gross
Sharpe 0.75) when the signal is one preregistered test, and about 1800 bps (gross Sharpe 1.04) when
it was chosen from a search. A strategy with gross Sharpe 0.5 is found 18 % of the time at the mined
hurdle (the review's closed-form estimate was about 9 %). Above 1800 bps the out-of-sample condition,
not the hurdle, is what limits detection. These are statements about the synthetic generator, not
about real markets; "80 % power" follows the usual convention (Cohen 1988).

**Adversarial power curves (D2) [simulation].** [`adversarial_power.json`](adversarial_power.json):
detection rate of the IC gate against the planted premium for five generator families written without
reference to the signal code (8 seeds a point; a coarse first look, not a precise rate):

| Family | 0 | 600 | 1100 | 2400 bps (detection at t >= 3.0) |
|---|---|---|---|---|
| drift (baseline) | 0/8 | 0/8 | 1/8 | 8/8 |
| hidden factor (correlated noise) | 0/8 | 0/8 | 0/8 | 6/8 |
| regime switching (+1 / -0.5) | 0/8 | 0/8 | 0/8 | 4/8 |
| GARCH plus jumps | 0/8 | 0/8 | 0/8 | 7/8 |
| premium on market beta (all false positives) | 0/8 | 0/8 | 5/8 | 8/8 |

The last row is the important one: when expected return is planted on market beta, 12-1 momentum
"detects" it because past winners are high-beta names in a rising market. The IC gate cannot tell
beta from alpha; factor-neutral residuals (D16, `ceap.analytics.risk_research.residualise`) can.

**Other checks.** Leakage canary through `simulate_backtest` (D7): a feature equal to the realised
holding-period return, delayed d days, gives gross Sharpe 9.1, 7.7, 6.2, 3.9 at d = 0..3 and 0.0 at
d = 4 with a five-day hold and a one-day lag, collapsing exactly when d + lag >= hold. Trial
accounting (D8, `ceap.analytics.trial_accounting`), PBO by CSCV and stationary block bootstrap
intervals (D9, `ceap.analytics.overfitting`), Newey-West over gaps with the Andrews bandwidth (D11),
an equal-weight baseline with a paired bootstrap and Diebold-Mariano test for the ridge (D12),
Ledoit-Wolf covariance, factor regression with alpha t after factors (its R² is not reliable for a
constant series; see the known numerical issues in [docs/quant/quant.md](../quant/quant.md)) and
multi-factor stress (D13, D16), data-vintage hashes (D14, `ceap.data.real.vintage`), the IC term structure and half-life (D15,
`ceap.analytics.alpha_decay`), turnover-penalised construction and the cost frontier (D17, D18),
drawdown, CVaR and volatility-regime splits (D19), a robustness report per verdict (D6) and
`scripts/rebuild_study.py` (D20).

Moved from 0.8.0 and done in 0.9.0: a 500-name synthetic universe (D5,
`ceap.data.generator_params.RESEARCH_UNIVERSE_500`; membership events scale with the universe and
come from a separate random stream, so every 30-name dataset hash is unchanged) and delisting
sensitivity on the real-data momentum book (D10, `ceap.data.real.momentum.delisting_band`, a grid of
0, -30 %, -55 % and -100 % delisting returns; a sensitivity analysis, never the primary test).
Details and tests: `CHANGELOG.md` [0.9.0], lane L4.
