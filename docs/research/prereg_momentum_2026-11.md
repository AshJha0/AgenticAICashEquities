# Preregistration: 12-1 momentum on an approximately point-in-time S&P 500 (free data)

- Registered: 2026-11-07, before any price data was downloaded or read by this project.
- Plan reference: `docs/review/2026-11-07/PLAN.md`, round R5, items 5.1 to 5.7; decisions D2 and D4.
- Lock: the SHA-256 of this file is recorded in `docs/research/prereg_lock.json` in the commit that
  adds it. `ceap.data.real.prereg.require_prereg_lock()` recomputes the hash and refuses to load any
  price data if it differs. Any edit to this file after the lock voids the study unless it is a new,
  separately dated and hashed amendment that leaves sections 1 to 8 unchanged.

## 1. Hypothesis (one, and only one)

H1: the mean monthly **net** return of an equal-weight top-decile minus bottom-decile 12-1 momentum
portfolio, formed on an approximately point-in-time S&P 500, is greater than zero over the
evaluation window defined in section 3.

H0: that mean is less than or equal to zero.

## 2. Construction (fixed; no alternatives will be tried)

| Element | Specification |
|---|---|
| Universe | Members of the S&P 500 on the formation date (by each change's effective date), from the point-in-time membership built in item 5.2 (`data/reference/sp500_changes.csv`, `data/reference/ticker_map.csv`). |
| Prices | Stooq daily adjusted closes (split and dividend adjusted; if Stooq supplies only a split-adjusted series for a name, that series is used and the name is listed in the quality report); yfinance adjusted closes used only as a returns cross-check (item 5.3). |
| Formation date | Last NYSE trading day of each month (`data/reference/nyse_calendar.csv`). |
| Signal | `close[t-21] / close[t-252] - 1`, with t the formation date and lags counted in NYSE trading days. |
| Eligibility at formation | Member on t; adjusted close present on t-21 and t-252; no more than 5 missing closes in [t-252, t]; close on t at least USD 1. |
| Portfolio | Rank eligible names by signal. Long the top decile, short the bottom decile (decile size `floor(N/10)`, ties broken by ticker). Equal weight within each leg, +1 long and -1 short gross per leg. |
| Execution | At the close of the first NYSE trading day after t. |
| Membership between t and execution | A name removed between t and execution stays in the book. A name with no execution close is dropped and its leg is re-weighted equally. |
| Holding | One month: until the next execution close. Monthly return = close-to-close from execution to next execution. Holding month m is labelled by its execution date. |
| Minimum breadth | If fewer than 100 names are eligible on any formation date in the evaluation window, the study is VOID. |
| Delisting / missing data while held | Name earns its return to the last available close, then zero (cash) for the rest of the month. No delisting return is imputed. Stated in advance: for the short leg this omits the (usually negative) delisting return, so it biases against H1; it is reported as a limitation. |
| Excess return | The book is self-financing (long leg funded by short proceeds), so its return is already an excess return; no risk-free rate is subtracted. |
| Net return | Gross long-short return minus the item 5.4 cost schedule, stated in section 5. |

## 3. Windows

| Window | Dates | Use |
|---|---|---|
| Formation warm-up | 2003-11-01 onward (at least 252 NYSE trading days before the first formation date, 2004-12-31) | Price history only; no returns scored. |
| Evaluation (in-sample) | Returns for holding months 2005-01 to 2023-12 (228 months). The final holding period ends at the 2023-12-29 close (truncated); 2024-01-02 belongs to the pseudo-holdout. | The primary test and the replication gate. |
| Pseudo-holdout | 2024-01-01 to 2025-12-31 (frozen last date) | Looked at once, descriptively, after the in-sample verdict (item 5.7). Labelled *pseudo* because momentum factor returns for this period are public. |

No code in this project reads prices dated after 2023-12-31 until item 5.7. The loader enforces it.

## 4. Primary test

- Statistic: one-sided Newey-West t on the 228 monthly net returns, Bartlett kernel, 3 lags,
  H0: mean <= 0.
- Alpha: 0.05, one-sided; critical value 1.645 (standard normal). This is a single preregistered
  hypothesis, so the t >= 3 bar used for mined factors (D4) does not apply.
- Reject H0 (result **POSITIVE**) if t > 1.645. Otherwise the result is **NULL**.
- Point estimate reported with it: annualised net Sharpe (monthly mean / monthly sd * sqrt(12)) with a
  95 % two-sided percentile confidence interval for the annualised Sharpe from a stationary block bootstrap (mean block 6 months, 10 000
  resamples, seed 20261107).

## 5. Costs (fixed in advance; item 5.4)

- Half-spread: 10 bps per side for trades dated 2005 to 2009, 5 bps from 2010.
- Market impact: square-root, `Y * sigma_daily * sqrt(trade / ADV)` with Y = 0.7, at USD 100 million
  gross book (USD 50 million per leg). ADV = 20-day median dollar volume before the trade date;
  sigma_daily = standard deviation of the 60 daily returns before the trade date; trade = absolute
  change in dollar position per name. A name with missing ADV gets the 95th-percentile impact of that
  book. Costs are deducted from the return of the month in which the trade occurs.
- Borrow on the short leg: 0.3 % a year general collateral, plus 5 % a year for names in the bottom
  5 % of the formation-date universe by 20-day median dollar volume on t (proxy for market cap, which free data lacks).
- Turnover: actual weight changes between consecutive books, both legs.
- Corwin-Schultz spreads: sensitivity only, never the test.

## 6. Power analysis

For a test on monthly returns with annualised Sharpe S over Y years, the expected t is about
`S * sqrt(Y)`. Power 0.80 at one-sided alpha 0.05 needs `S * sqrt(Y) >= z(0.95) + z(0.80)
= 1.645 + 0.842 = 2.487`. With Y = 19:

**Minimum detectable annualised net Sharpe = 2.487 / sqrt(19) = 0.57.**

Caveats stated in advance:

- Positive autocorrelation raises the true long-run variance, which Newey-West estimates; with a
  long-run variance 1.2 times the iid variance the threshold becomes 0.57 * sqrt(1.2) = 0.62. Three
  lags on 228 observations understate the standard error if autocorrelation persists longer.
- Momentum returns are negatively skewed (2009 crash); the normal approximation overstates power.
- Published US large-cap 12-1 momentum net Sharpe after 2005 is well below 0.57, so a NULL is the
  expected outcome and will be published as such.

## 7. Gates that stop a verdict (checked before section 4 is computed)

The primary statistic is not computed, or not reported as a verdict, if any of these fail. Gates are
evaluated in order 1, 2, 3; the first failure is the reported outcome.

1. Coverage: usable name-months (an execution close and at least one later close in the month)
   divided by member name-months, over the evaluation
   window, below 95 % → **VOID** (study void).
2. Data quality (item 5.3): Stooq vs yfinance daily returns agree within 10 bps on fewer than 99.5 %
   of overlapping name-days (absolute difference in daily simple returns at most 10 bps), or yfinance
   overlap is below 80 % of name-days, or a known split (AAPL 2014-06-09, AAPL 2020-08-31) fails → **VOID**.
3. Replication (item 5.5): correlation between this pipeline's gross monthly long-short return and
   Ken French UMD over the evaluation window below 0.5 → **PIPELINE SUSPECT**, no verdict.

## 8. Exclusions and forking paths

- Exactly one specification (section 2) and one test (section 4). No change of lookback, skip,
  decile cut, weighting, holding period, universe, window or cost schedule after data is read.
- Reported alongside, labelled descriptive and never used for the verdict: gross returns, a
  survivor-only universe (today's members) to measure survivorship bias, Corwin-Schultz costs, the
  FF5 + UMD regression, per-year returns, crash months (2009, 2020-11) and breakeven cost.
- A data bug found after the lock may be fixed only if the fix restores behaviour already specified
  in section 2, it is logged in the result document with before and after, and the gates are re-run.

## 9. Allowed and forbidden claims

Allowed (filled in by item 5.6): "Net Sharpe X, 95 % CI [a, b], 2005 to 2023, approximately
point-in-time S&P 500, free data; UMD correlation Y; cannot detect Sharpe below 0.57." On NULL:
"No evidence of a positive net 12-1 momentum return at this power."

Forbidden: PROMOTE, alpha, tradable, any dollar capacity figure, any claim from the pseudo-holdout
beyond sign agreement and confidence-interval overlap, any claim about a specification not in
section 2.

## 10. Data and licences

Stooq and yfinance (Yahoo Finance) daily prices, cached locally and never committed; Kenneth R.
French data library (UMD, FF5, RF); S&P 500 change history from a free public CSV reconciled against
Wikipedia's "List of S&P 500 companies" change table (CC BY-SA 4.0, attribution in
`data/reference/ATTRIBUTION.md`). No sign-ups, no paid data, no CAPTCHA bypass (D2).
