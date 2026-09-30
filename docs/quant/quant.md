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
| Synthetic data | `ceap.data.synthetic` — tick-level quotes/fills, 6 symbols, 1 hour window | `ceap.data.historical` — daily bars, 30 names, 4 years |
| Ground truth | 10 templates × 5 symbols = 50 scenarios | 7 templates × 3 seeds = 21 scenarios |
| Evaluation | `ceap evaluate --suite investigation` | `ceap evaluate --suite research` |

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

`ceap.analytics.signals` — a small fixed library (`momentum_12_1`, `reversal_5`, `low_vol_60`),
each a pure function over a `(n_days, n_symbols)` price matrix; not a factor zoo, and adding one
is a one-function, one-registry-entry change (`docs/quant/quant.md` intentionally does not claim
more sophistication here than exists).

`ceap.analytics.signal_statistics` — rank IC via Spearman correlation between the signal and
forward returns, IC t-statistic, information ratio, hit rate, turnover (via rank autocorrelation
at the rebalance lag) and decay across horizons. The IC used for the headline t-statistic is
**one-day and non-overlapping** specifically so the statistic isn't inflated by autocorrelated
overlapping-horizon returns — a subtlety that, if missed, would make a purely noise signal look
statistically significant.

`ceap.analytics.backtest` — an event-driven daily loop: rebalance on a fixed cadence, a
transaction-cost model (half the quoted spread plus square-root impact scaled by ADV
participation), and a walk-forward split into in-sample and out-of-sample periods. Every period
reports *both* gross and net Sharpe so cost drag is visible as its own number, not silently
absorbed into one Sharpe ratio.

`ceap.analytics.portfolio_risk` — the target portfolio is **recomputed** from the signal at the
report date; it never reads the backtest's own stored weights. This "recompute, don't trust a
derived number" instinct is the same one behind execution fill-rate using accepted quantity, one
level up. Reports gross/net exposure, single-name HHI concentration, market beta, ADV
participation per name, limit checks, and stress P&L under an instantaneous shock.

**`assess_research`** — the Stage 2 analogue of `attribute_causes`, and the actual verdict a
human approves or rejects (the LLM narrates the verdict; it never sets it):

| Flag | Condition |
|---|---|
| `NO_ALPHA` | in-sample IC t-statistic < 2.0, or fewer than 60 usable dates |
| `OVERFIT` | out-of-sample/in-sample Sharpe ratio < 0.5, or out-of-sample IC t-stat < 1.0 |
| `COST_DRAG` | gross Sharpe clears the bar but net doesn't — costs consume the edge |
| `CONCENTRATION` | top three names carry > 50 % of positive P&L |
| `LIMIT_BREACH` | any portfolio limit check (gross, net, single name, beta, HHI, ADV) fails |

`PROMOTE` only when none of the above fire. The alpha chain (`NO_ALPHA` → `OVERFIT` →
`COST_DRAG`) short-circuits at the first failure; `CONCENTRATION` and `LIMIT_BREACH` are
independent checks.

## Synthetic data — and why it's built this way

Neither dataset touches a real market feed; both are seeded and fully reproducible.

`SyntheticMarketGenerator` (Stage 1): a GBM mid-price path at 10 % annualised volatility sampled
every second, log-normal NBBO noise, Poisson trade prints across five venues, five-level order
books every 10 seconds, and a VWAP parent order sliced into 30-second children with fills that
walk the book by an amount driven by slice size, venue degradation and latency. Scenario effects
(a volatility spike, wider spreads, a degraded venue, a latency event, …) are injected only
inside the investigation window, so the preceding hour is always a clean baseline — which is also
how "deterioration" is defined.

`HistoricalGenerator` (Stage 2): a 30-name, four-year daily factor model
(`r = beta·market + k·z + eps`) where a scenario's embedded premium (`k`) is applied to `z`, the
cross-sectional z-score of the **same signal function** (`ceap.analytics.signals`) that later
measures it. That choice is deliberate: the data generator and the analytics that grade it share
one code path, so they cannot silently drift into disagreement the way a hand-tuned "expected
alpha" constant could.

## Evaluated honestly

`ceap evaluate --suite investigation`: 50 scenarios, 50/50 primary cause correct, one documented
(not tuned away) false positive.
`ceap evaluate --suite research`: 21 scenarios, 21/21 verdicts correct with the mock model; 19/21
with a weak real planner alone, 21/21 once the planner model was upgraded — see
`docs/ai-agents/ai-agents.md` and `docs/evaluation/evaluation.md` for the full story, since that
finding is really about model orchestration, not the analytics themselves (the analytics were
unchanged between those two runs).

Thresholds in both scorers are calibrated on synthetic data. On real execution or return history
they would need recalibration and version control like any other code — this is stated as a
residual risk in `docs/threat-model/threat-model.md`, not glossed over.

## What is *not* here

- **No portfolio optimiser.** Target weights come from a fixed top/bottom-tercile quantile
  construction (equal- or rank-weighted), not mean-variance optimisation, risk parity, or any
  solver.
- **No commercial risk model.** No Barra-style factor risk model, no vendor covariance matrix —
  beta is a simple OLS regression against the equal-weight universe.
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
