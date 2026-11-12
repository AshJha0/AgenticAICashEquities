# Preregistration E1: industry momentum on the 49 Ken French industries (free data)

- Registered: 2026-11-09, before any industry or factor return used by this study was downloaded or
  read by this project. Signed by Claude as maintainer under delegated owner decision D3
  (`docs/review/2026-11-09/PLAN.md`, WS-E step E1; decisions D2 and D3).
- Lock: the SHA-256 of this file (CRLF normalised to LF) is recorded in
  `docs/research/e1/prereg_lock.json` in the commit that adds it. `ceap.studies.industry_momentum`
  calls `ceap.data.real.prereg.require_prereg_lock()` with these two paths and refuses to compute
  anything if the hash differs. Any later edit voids the study unless it is a separately dated and
  hashed amendment that leaves sections 1 to 9 unchanged.
- Why this study: the stock-level R5 study could not pass on free data (no delisted names; see
  `docs/research/e2_why_r5_could_not_pass.md`). Ken French industry portfolios are built from CRSP
  with delisting returns, so survivorship is handled by the data provider.

## 1. Hypotheses

**H1 (primary, confirmatory, holdout).** The mean monthly **net** return (10 bps per side) of the
long-short industry momentum book defined in section 2, over the holdout months 1990-01 to 2015-12,
is greater than zero. H0: it is less than or equal to zero.

**H2 (secondary, stated in advance, market-state conditioning).** Following Cooper, Gutierrez and
Hameed (2004, *Journal of Finance* 59(3)), the mean monthly gross return of the book in UP states is
greater than in DOWN states. A month's state is UP if the cumulative total market return (Ken French
`Mkt-RF + RF`) over the 36 months ending at the formation month is >= 0, otherwise DOWN. H2 is tested
in-sample and in the holdout, one-sided, by the Newey-West t on the coefficient of a DOWN dummy in
`r_t = a + b * DOWN_t + e_t` (H0: b >= 0, reject if t < -1.645). H2 does not change the H1 verdict,
and no conditioned strategy is traded or scored as a strategy.

## 2. Construction (fixed; no alternatives will be tried)

| Element | Specification |
|---|---|
| Data | Kenneth R. French data library, `49_Industry_Portfolios_CSV.zip`, first block (Average Value Weighted Returns, Monthly), percent converted to decimal. Values of -99.99 or -999 are missing. |
| Factors | `F-F_Research_Data_Factors_CSV.zip` (FF3 + RF, from 1926-07), `F-F_Research_Data_5_Factors_2x3_CSV.zip` (FF5, from 1963-07), `F-F_Momentum_Factor_CSV.zip` (UMD, from 1927-01). Monthly, percent to decimal. |
| Raw cache | `data/real_cache/kf/` (git-ignored). The SHA-256 of every downloaded zip is written to `docs/research/e1/data_hashes.json` on download. The study refuses to run if a cached file's hash differs from the recorded one. |
| Signal | For formation month t: compounded return of each industry over months t-5 .. t (6 months, no skip; Moskowitz and Grinblatt 1999, *Journal of Finance* 54(4)). An industry is eligible at t if all 6 returns and the month t+1 return are present. |
| Portfolio | Rank eligible industries by signal (ties broken by column order). Long the top 7, short the bottom 7, equal weight within leg: +1/7 each long, -1/7 each short (gross 2). |
| Holding | Month t+1 (one month), rebalanced monthly. The holding month labels the return. |
| Gross return | Mean of long-leg returns minus mean of short-leg returns. Self-financing, so no risk-free rate is subtracted. |
| Minimum breadth | If fewer than 30 industries are eligible in any scored month, that month is dropped and listed; if more than 5 % of a window's months are dropped, the window is VOID. |

## 3. Costs

Turnover in month m: `sum_i |w_i,m - w~_i,m-1|`, where `w~` is last month's weights after drift
through month m-1 returns (each leg drifts within itself, then the book is re-scaled to +1/-1 gross
per leg). The first month counts the full build (turnover 2). Net return = gross - c * turnover with
c = 10 bps per side (primary) and 20 bps per side (reported). Breakeven c* = mean gross / mean
turnover, reported in bps per side.

## 4. Windows

| Window | Holding months | Use |
|---|---|---|
| Warm-up | 1926-07 to 1926-12 | Signal history only. |
| In-sample | 1927-01 to 1989-12 (756 months) | Estimation, replication gate, H2 in-sample. Run first. |
| Holdout | 1990-01 to 2015-12 (312 months) | H1 verdict. Opened exactly once, after the in-sample result is committed. |
| Live | 2016-01 to the last month in the downloaded file | Descriptive only; no verdict. Reported once with the holdout. |

The study code returns only months <= 1989-12 unless it is called with `open_holdout=True`, which
first checks that `docs/research/e1/holdout_ledger.json` does not record an earlier opening and then
writes the opening (timestamp, git commit, data hashes) before any holdout number is computed.

## 5. Primary test (H1)

- Statistic: one-sided Newey-West t of the mean of the 312 holdout net returns (10 bps), Bartlett
  kernel, bandwidth by the Andrews (1991, *Econometrica* 59(3)) AR(1) plug-in rule
  (`1.1447 * (alpha * T)^(1/3)`, `alpha = 4 rho^2 / (1-rho^2)^2`), rounded down to an integer lag.
- Reject H0 at one-sided alpha 0.05, critical value 1.645: result **POSITIVE** if t > 1.645,
  otherwise **NULL**. One hypothesis, so no multiple-testing hurdle applies.
- Power: with 26 holdout years the minimum detectable annualised Sharpe at 80 % power is
  (1.645 + 0.842) / sqrt(26) = 0.49. A true Sharpe below about 0.49 will often give NULL.
- Point estimate: annualised net Sharpe (monthly mean / sd * sqrt(12)) with a 95 % percentile CI from
  a stationary block bootstrap (Politis and Romano 1994), mean block 6 months, 10 000 resamples,
  seed 20261109.

## 6. Secondary statistics (reported, never change the verdict)

- Alpha: OLS of gross returns on FF5 + UMD with Newey-West (Andrews) standard errors, one-sided t of
  the intercept. FF5 starts 1963-07, so in-sample alpha uses FF3 + UMD for 1927-01 to 1989-12 and
  FF5 + UMD for 1963-07 to 1989-12; the holdout uses FF5 + UMD. Expected: much of the alpha is
  absorbed by UMD; that is reported, not treated as a failure.
- The same statistics at 20 bps per side, gross, and the breakeven cost.
- H2 as defined in section 1.

## 7. Gates (evaluated in this order; each outcome recorded)

1. **Replication gate (in-sample).** In-sample gross annualised Sharpe within +/-0.15 of a published
   in-sample Sharpe for the *same* construction. Stated in advance: no published table for
   49 industries, 6-month formation, top 7 / bottom 7, 1927-1989 is known to the maintainer in a free,
   sign-up-free copy. Moskowitz and Grinblatt (1999) use 20 industries, 1963-1995 and top 3 / bottom
   3, so their number is not the same construction. Unless such a source is found and cited before
   the in-sample run, this gate is recorded **NOT RUN**, and every claim carries the qualifier
   "replication not verified".
2. **ETF-sector gate (run at the holdout opening, because 1999-2015 lies in the holdout).** Monthly
   total returns of the nine original Select Sector SPDR ETFs (yfinance adjusted closes, free, no
   sign-up) against the equal-weight mean of mapped Ken French industries, 1999-01 to 2015-12:
   XLB = Chems, Steel, Paper, Boxes, Mines; XLE = Oil; XLF = Banks, Insur, Fin; XLI = Mach, ElcEq,
   Aero, Trans; XLK = Hardw, Softw, Chips; XLP = Food, Soda, Beer, Smoke, Hshld; XLU = Util;
   XLV = Hlth, MedEq, Drugs; XLY = Autos, Rtail, Meals, Clths, Fun. Pass if the **median** of the
   nine Pearson correlations is >= 0.8. If the ETF data cannot be fetched without sign-up, the gate
   is recorded NOT RUN. A failed gate makes the holdout result VOID (the numbers are still recorded).
3. **Breadth** as in section 2.

## 8. Allowed claims (written in advance)

- POSITIVE: "In a preregistered test on free Ken French data, a 49-industry 6-month momentum book
  (top 7 / bottom 7) earned a positive mean net return at 10 bps per side in the 1990-2015 holdout
  (one-sided Newey-West t = X > 1.645), with annualised net Sharpe S [CI]." Plus the gate qualifiers.
- NULL: "The preregistered test did not reject H0 in the 1990-2015 holdout (t = X); the design could
  detect an annualised Sharpe of about 0.49 with 80 % power." Never "industry momentum does not exist".
- VOID: the reason and the recorded numbers, labelled not interpretable.
- Forbidden in every case: tradable, capacity, live-trading claims, PROMOTE, any claim about
  individual stocks, any claim built on the live window beyond description.

## 9. What is not allowed after the lock

No change of signal length, leg size, weighting, cost level, windows, test, bandwidth rule or gate
thresholds. No second look at the holdout. Additional analyses may be reported only in a section
headed "Not preregistered (exploratory)".
