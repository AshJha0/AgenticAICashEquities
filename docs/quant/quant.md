# Quant in CEAP

What the deterministic quant layer actually does, where it lives, the formulas and thresholds
behind it, and — just as importantly — what it does *not* do. This is the maths that
`docs/ai-agents/ai-agents.md`'s "the model never computes a metric" rule exists to protect: every
number quoted in a report or research proposal comes from here or from a tool call, never from
the LLM.

All of it lives in `ceap.analytics`: pure functions over NumPy/pandas arrays, no LLM, no I/O, no
side effects, each with known-answer unit tests.

## Two verticals, one discipline

| | Execution-quality (TCA), Stage 1 | Signal research, Stage 2 |
|---|---|---|
| Question | why did execution quality change? | does this signal actually work? |
| Deterministic scorer | `attribute_causes` → primary/secondary cause | `assess_research` → `PROMOTE`/`REJECT` + flags |
| Synthetic data | `ceap.data.synthetic` — tick-level quotes/fills, 6 symbols, 1 hour window | `ceap.data.historical` — daily bars, 30 names, 20 years, NYSE calendar |
| Ground truth | 10 templates × 5 symbols = 50 scenarios | 10 templates × 3 seeds = 30 scenarios, derived from generator parameters |
| Evaluation | `ceap evaluate --suite investigation` | `ceap evaluate --suite research` and `--suite null` |

Both scorers follow the same discipline: a small set of named hypotheses, an explicit numeric
threshold per hypothesis (not a learned weight), a saturating score, and a documented rationale
string attached to the result as evidence. Neither is a model — they're `if`/`elif` chains over
real numbers, versioned like code, in `ceap.analytics.attribution` and `ceap.analytics
.research_assessment` respectively.

## Execution-quality analytics (Stage 1)

`ceap.analytics`: VWAP, TWAP, implementation shortfall (Perold 1988, execution leg + opportunity
leg for unfilled quantity, sign-flipped for sells so positive is always adverse), slippage
(arrival / interval-VWAP / per-fill), effective spread, market impact split into permanent (mid
at last fill vs arrival) and temporary (execution VWAP vs mid at last fill), a square-root impact
*estimate* (Almgren-Chriss style — a benchmark to compare measured impact against, not a
replacement for it), realised and annualised volatility, price drift and jump detection,
displayed depth, order-book imbalance, and stale/crossed-quote feed-quality diagnostics.
`StandardExecutionAnalytics.calculate` composes these into `ExecutionMetrics` including
per-venue statistics (fill rate computed on *accepted* quantity — rejects are a technology
signal, not a venue one, so a venue isn't blamed for a platform-wide fault).

**`attribute_causes`** scores 8 causes by comparing a window against its baseline; each score is
`1 - exp(-excess/scale)` of how far a signal sits beyond threshold (saturates at 1, is 0 below
threshold); a score ≥ 0.35 is *material*.

| Cause | Signal | Threshold |
|---|---|---|
| MARKET_VOLATILITY | 1-minute realised vol ratio | > 1.6 |
| WIDE_SPREADS | mean quoted spread ratio | > 1.4 |
| LOW_LIQUIDITY | min(depth ratio, top-of-book ratio) | < 0.65 |
| VENUE_DEGRADATION | fill-rate gap / slippage excess / reject excess vs peers | 0.25 / 2 bps / 10 pp |
| TECHNOLOGY_LATENCY | latency ratio / reject rate | > 3 / > 5 % |
| MARKET_DATA_ANOMALY | stale-quote fraction / crossed quotes | > 3 % (and 3× baseline) / > 5 |
| LARGE_ORDER_IMPACT | participation (+0.3 if parent ≥ 4× baseline) | > 20 % |
| PRICE_MOVEMENT | adverse drift, halved when volatility is elevated | > 60 bps |

`deteriorated` is true when implementation shortfall *or* VWAP slippage worsens by ≥ 1.5 bps —
using both matters because hourly IS alone carries ≈30 bps of price-drift noise at 10 %
annualised volatility, which a VWAP algorithm cannot control.

## Signal-research analytics (Stage 2)

0.4.0 (round R2 of the 2026-11-07 review) rebuilt this layer around one question: would the
verdict survive realistic execution and an honest count of everything that was tried? Every
convention below is also written into each backtest's `assumptions` block, so a reader of a
proposal sees the rules it was computed under.

`ceap.analytics.signals` — a small fixed library (`momentum_12_1`, `reversal_5`, `low_vol_60`),
each a pure function over a `(n_days, n_symbols)` price matrix. `dataset_signal` applies the
membership mask: a name that is not in the universe on a date has no signal, so it is never
ranked or held (2.13).

### Execution, drift and financing (`ceap.analytics.backtest`)

| Convention | 0.4.0 rule | Why |
|---|---|---|
| Fill timing (2.1) | decide at the close of `t`, fill `execution_lag_days` later (default 1) at the close, or at the open with lag >= 1 | a market-on-close order must be in before the print; the 0.3.x same-close fill was a look-ahead. Legacy R03 reversal: net Sharpe 1.73 at lag 0, -0.61 at lag 1 (review: 1.80 to -0.67) |
| Drift (2.2) | positions move with prices between fills; turnover is measured from the drifted weights to the target | a free daily rebalance to constant weights understated turnover and cost |
| Excess returns (2.3) | Sharpe and Sortino on returns minus rf; cash = 1 - net exposure earns rf (the short rebate), negative cash pays rf + 50 bps | a long-only book's Sharpe falls by rf/sigma; a dollar-neutral book is self-financing |
| Borrow (2.3) | 0.5 % / 2 % / 10 % a year on shorts in liquidity tiers A / B / C (stated assumptions) | tier-C shorts are expensive; on the default 50 M book borrow is the largest cost line (about 200 bps a year) |
| Leverage (2.3) | gross leverage capped at 2x (Reg T) | |
| Reg SHO SSR (2.3) | no new short at the fill on a name down 10 % intraday; logged | |
| Caps (2.5) | 10 % max weight per name, 10 % of lagged ADV per trade; clipped trades are logged | a breach is never silently traded |
| ADV (2.5) | 20-day average volume lagged one day | the trade day's own volume is not known at decision time |
| Delistings (2.5) | merger: exit at the last price; performance: -30 % NYSE, -55 % Nasdaq (Shumway 1997); both bounds reported | |
| Missing price (2.5) | a held name with no price earns 0 that day and the day is logged | explicit NaN policy |

Trade cost per unit traded is half the quoted spread plus `0.6 * sigma_daily * sqrt(participation)`.
`cost_breakdown_bps_annual` reports spread, impact, borrow and financing separately, and the
event log records every capped, blocked or delisting trade.

### Statistics (`ceap.analytics.signal_statistics`, 2.7)

- **Gate statistic**: the t-statistic of the *non-overlapping* rank IC at the holding-period
  horizon `h` (the rebalance interval), with the forward return starting after the execution lag:
  `close[t+L+h] / close[t+L] - 1`. Non-overlapping samples keep the plain t honest under the null.
- The overlapping (daily-sampled) horizon IC is reported with a **Newey-West t** (Bartlett kernel,
  `h - 1` lags); the one-day IC is reported as well. Both are descriptive.
- In-sample statistics drop the last `L + h` in-sample dates, so no in-sample forward return reads
  an out-of-sample price.
- `ic_ir_annualised = mean / sd * sqrt(252 / h)`.
- `rank_autocorrelation_turnover` (renamed from `turnover`) is `1 - rank autocorrelation` of the
  signal at the rebalance lag, range 0 to 2. Portfolio turnover is the backtest's number.

Null check: on the pure-noise set the share of `|t| > 2` is **6.0 %** over 500 seeds x 6
signal/horizon families (momentum at 21 days: 5.6 %), inside the [3.0 %, 7.5 %] acceptance band.

### Multiple testing (2.8, 2.10, decision D4)

- **Enumerated search space**: 3 signals x rebalance {5, 21} x long-short or long-only x equal or
  rank weighting = 24 configurations. The backtest tool refuses anything else.
- **Trial ledger** (`ceap.analytics.trial_ledger`): append-only, written by the backtest tool on
  the server side, keyed by the dataset's content hash and the canonical hash of the parameters.
  A re-run of identical parameters adds nothing; a new configuration adds one trial. The caller
  cannot pass a trial count.
- **Deflated Sharpe** (Bailey and Lopez de Prado 2014) on the in-sample Sharpe *before trading
  costs* (costs belong to `COST_DRAG`, not to the alpha test), with the trial count and the
  cross-trial Sharpe variance from the ledger:
  `SR0 = sqrt(V) ((1 - g) Z(1 - 1/N) + g Z(1 - 1/(N e)))`,
  `DSR = Phi((SR - SR0) sqrt(T - 1) / sqrt(1 - skew SR + (kurt - 1) / 4 SR^2))`.
  The gate is `DSR >= 0.5`: the observed Sharpe must beat the expected best of the trials run.
- **Locked holdout**: `in_sample_end` is fixed per dataset; a caller may shorten the in-sample
  window but never move the boundary into the holdout.
- **The PROMOTE hurdle is measured, not chosen.** `ceap evaluate --suite null --seeds 500` runs
  all 24 configurations on 500 pure-noise datasets through the full gate chain. A seed is a false
  PROMOTE at hurdle `h` when any configuration passes every gate. The hurdle is the lowest `t` on
  a 0.05 grid whose Clopper-Pearson 95 % upper bound is at most 5 %.

| Sweep (committed in `data/reference/research_thresholds.json`) | Value |
|---|---|
| Seeds x configurations | 500 x 24 |
| Chosen in-sample IC t hurdle | **0.40** (0.65 before rf was committed) |
| False PROMOTEs at the hurdle | 15 / 500 = 3.0 %, 95 % band [1.7 %, 4.9 %] |
| False PROMOTEs with no IC hurdle at all | 17 / 500 = 3.4 % (upper bound 5.4 %) |
| Sweep hash | `ef474869e5fa503d593b460c7e8921a7d26f106d483a2c5c6c2708dc1faf57d4` |

What the number means: on twenty years of data the other gates (deflated Sharpe over 24 trials,
out-of-sample IC t >= 1, out-of-sample net Sharpe >= 0.5, costs below half of gross) already hold
the false-PROMOTE rate near 5 %, so the in-sample IC hurdle only has to remove the last few. A
fixed `t >= 3` would have been chosen before measuring and would have rejected the realistic
premia below. With the Ken French rf series committed, long-only books pay rf in their excess
return, fewer noise configurations clear the Sharpe gates, and the hurdle fell from 0.65 (rf = 0)
to 0.40 - the sweep was re-run, not the number edited.

### The assessment (`assess_research`)

| Flag | Condition |
|---|---|
| `NO_ALPHA` | in-sample non-overlapping IC t < 0.40 (null-sweep hurdle), fewer than 30 periods, or deflated Sharpe < 0.5 |
| `REGIME_BREAK` | out-of-sample IC t <= -2: the premium reversed |
| `OOS_DECAY` | out-of-sample IC t < 1, or net out-of-sample Sharpe < 0.5 without a cost explanation (0.4 name `OVERFIT`: nothing is fitted) |
| `COST_DRAG` | gross out-of-sample Sharpe >= 0.5 but net below it, or costs >= 50 % of gross |
| `CONCENTRATION` | top three names carry > 50 % of positive P&L |
| `LIMIT_BREACH` | a limit fails as of the report date or on any out-of-sample rebalance date |

Every assessment carries a `HypothesisTable`: each test run (in-sample IC t, deflated Sharpe,
out-of-sample IC t, out-of-sample Sharpe t) with its statistic, p-value, sample size and pass or
fail, and the trial count it was corrected for.

### Risk (`ceap.analytics.portfolio_risk`, 2.12)

The target portfolio is still **recomputed** from the signal at the report date. Beta is
estimated against the equal-weight market *excluding the name* and shrunk toward 1 with the
Vasicek weight `w = s2_prior / (s2_prior + se^2)`. Stress adds a reversal of the signal's own
factor (5 % per unit of z-score), a +20 % short squeeze and the cost and days of liquidating at
10 % of ADV. Limits are evaluated on every out-of-sample rebalance date and the worst date per
limit is reported.

### Capacity and profitability (2.6, 2.14)

`ceap profitability --dataset R03` prints, for one configuration: breakeven trading cost per unit
of turnover, cost share of gross P&L at 10 / 25 / 50 / 100 / 250 / 500 M, drawdown depth and
duration, calendar-year returns, the three worst months, and alpha after the generator's market
factor with its t-statistic. The capacity sweep re-runs the backtest at each AUM with the
participation cap active and reports the breakeven AUM and the AUM at which the net Sharpe halves.
On R03 (reversal) the net Sharpe falls monotonically with AUM (Spearman -1) and the 500 M Sharpe
is more than two standard errors below the 10 M Sharpe. The same table is attached to every
backtest tool result, so it reaches the proposal.

## Synthetic data — and why it's built this way

Neither dataset touches a real market feed; both are seeded and fully reproducible.

`SyntheticMarketGenerator` (Stage 1): a GBM mid-price path at 10 % annualised volatility sampled
every second, log-normal NBBO noise, Poisson trade prints across five venues, five-level order
books every 10 seconds, and a VWAP parent order sliced into 30-second children with fills that
walk the book by an amount driven by slice size, venue degradation and latency. Scenario effects
are injected only inside the investigation window, so the preceding hour is always a clean
baseline.

`HistoricalGenerator` (Stage 2, `realistic_v1`, 2.9): 5040 NYSE sessions (20 years, real holiday
calendar, 2.4) for 30 names, locked split: 252 warm-up, 3528 in-sample (14 years), 1260
out-of-sample (5 years).

`r[t, i] = beta_i m_t + mu[t, i] + (T[t, i] - T[t-1, i]) + e[t, i]`

- `m_t`: GARCH(1,1) market volatility with Student-t(5) shocks (clustering, fat tails), optional
  30 % crash with a momentum crash in the rebound.
- `mu`: a **latent** expected return, AR(1) per name (half-life one year), optional decay and an
  out-of-sample multiplier. Momentum sees it only through noisy past returns.
- `T`: a **latent** transient price component, AR(1) with coefficient 0.6, carrying a share of
  idiosyncratic variance: genuine short-term reversal, part of which decays before a t+1 fill.
- `e`: Student-t(4) idiosyncratic noise scaled by the volatility regime; spreads widen and volume
  rises with volatility; three names list late, one merges and one delists for performance.

The 0.3.x generator planted the premium on the *same signal function* that measured it, with the
exact lag the backtest used; the evaluation could only recover what was planted (review finding
3.2 #8). The 0.4.0 generator never calls the signal code. The 0.3.x generator survives as
`legacy_v03` for the lag regression and the re-baseline table only.

**Ground truth comes from parameters** (`ceap.data.ground_truth`): the expected IC follows in
closed form from the latent components' autocovariances (drift `s^2 phi^|k|`; transient
`Var(dT) = share sigma^2`, `Cov(dT_t, dT_t+k) = -share sigma^2 (1 - rho) rho^(k-1) / 2`), the
rank IC is `(6/pi) asin(rho/2)`, the expected t is `IC sqrt(n - 1) sqrt(N)`, the tercile book's
gross Sharpe is `1.09 rho sqrt(2k)` per period, and costs follow from the bivariate-normal
probability of staying in the tercile. The verdict follows the same gate chain with expectations
in place of observations. A scenario whose gate margins are within one standard error is marked
**near-threshold**: its realised verdict is partly luck and it is reported separately.

The power curve (`docs/quant_power_curve.json`, `python -m ceap.analytics.power`) compares
detection by the IC gates with the closed-form prediction across premia:

| Premium (bps a year, cross-sectional sd) | 0 | 600 | 900 | 1200 | 1500 | 1800 | 2400 |
|---|---|---|---|---|---|---|---|
| Expected in-sample t | 0.0 | 0.5 | 1.1 | 2.0 | 3.0 | 4.1 | 6.7 |
| Predicted detection | 4 % | 11 % | 26 % | 52 % | 77 % | 93 % | 100 % |
| Observed detection (40 seeds) | 13 % | 20 % | 35 % | 60 % | 80 % | 93 % | 100 % |

The closed form is slightly conservative (realised t runs about 15 % above expectation); the
0-premium point (5 of 40 seeds against an expected 1.6) is within sampling error but worth
watching.

## Evaluated honestly

`ceap evaluate --suite investigation`: 50 scenarios, 50/50 primary cause correct, one documented
(not tuned away) false positive.

`ceap evaluate --suite research`: 30 scenarios (10 templates x 3 seeds) against parameter-derived
truth. Deterministic pipeline: **23/24 clear scenarios** verdict-correct (the miss is a strong
momentum premium whose 5-year out-of-sample net Sharpe came in at 0.21 against an expected 1.34,
a 1.8-standard-error draw) and **6/6 near-threshold** scenarios; 29/30 overall. The full
pipeline with the mock model gives the same 29/30 (flags 27/30, every run completed and approved,
no unresolved finding, no narrative number warning); see `docs/evaluation/evaluation.md`. Under t+1 execution the
reversal and the costly-momentum templates flip from PROMOTE to REJECT and stay REJECT (decision
D5); the per-scenario table is in the CHANGELOG.

`ceap evaluate --suite null`: false-PROMOTE rate of the full search at the committed hurdle, with
its binomial band (50 seeds on PR, 500 nightly).

Thresholds are calibrated on synthetic data. On real return history they would need
recalibration and version control like any other code; R5 runs one preregistered real-data test.

## What is *not* here

- **No portfolio optimiser.** Target weights come from a fixed top/bottom-tercile quantile
  construction (equal- or rank-weighted), not mean-variance optimisation, risk parity, or any
  solver.
- **No commercial risk model.** No Barra-style factor risk model, no vendor covariance matrix —
  beta is an OLS regression against the equal-weight universe excluding the name, shrunk toward 1.
- **Risk-free series from the Ken French library** (`data/reference/rf.csv`, built by
  `scripts/fetch_rf.py`, attribution in its header). An installed wheel does not ship `data/`:
  there rf falls back to 0 and every backtest says so in `assumptions["risk_free"]`; the calendar
  regenerates from its rules and the hurdle falls back to the committed value shipped in code.
- **No live market data, no OMS, no venue connectivity.** Every number above is computed on
  synthetic data. `execution.stage_orders` (the one non-read-only tool in the whole platform)
  builds *paper* orders from the recomputed target portfolio into an in-memory store; nothing
  routes anywhere.
- **No machine learning.** See `docs/ai-agents/ai-agents.md` — the `ml` extra in `pyproject.toml`
  is unused by any code path today.

## Further reading

- `LEARN.md` §5–6 (TCA analytics, cause attribution) and §18 (Stage 2 signal research) — the same
  material with worked numeric examples.
- `docs/architecture/overview.md` §3.2–3.3 — the code-level component reference.
- `docs/evaluation/evaluation.md` — full scenario tables, thresholds, and current results for
  both suites.
- `docs/ai-agents/ai-agents.md` — how these numbers reach a report without the model touching
  them.
