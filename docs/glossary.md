# Glossary

Terms as this repository uses them. Where a term has a formula, the code that computes it is named,
so the definition can be checked rather than trusted.

## Execution and TCA

| Term | Meaning in CEAP |
|---|---|
| bps | basis points, 1/100 of a percent. 10 bps on a 10.00 price is 0.01. |
| Arrival price | the mid when the parent order reaches the trading desk; the reference for most costs. |
| Decision price | the price when the portfolio manager decided to trade; earlier than arrival. |
| Implementation shortfall (IS) | cost of the executed order against the decision price, plus the cost of what was not filled, in bps of the decision notional. `ceap.analytics.implementation_shortfall`. Positive means adverse. |
| Perold split | IS broken into delay, spread, timing, opportunity and fees, which sum to the total (`shortfall_components`). |
| Opportunity cost | the IS leg for unfilled quantity, priced at a final price. With no finite final price it is left at 0 and flagged (`opportunity_priced = False`). |
| VWAP | volume-weighted average price, `sum(p q) / sum(q)` (`ceap.analytics.vwap`). Also an algorithm that trades along the expected volume curve. |
| TWAP | time-weighted average price; also an algorithm that trades evenly in time. |
| Participation rate | order volume over market volume in the same interval. |
| ADV | average daily volume; `Q/ADV` is the order's size as a fraction of a day. |
| Square-root impact | expected impact `c * sigma_daily * sqrt(Q/ADV)` (`square_root_impact_estimate_bps`). |
| Markout | price move after a fill, measured at fixed horizons, signed so positive is adverse (`ceap.execution.markouts`). |
| Almgren-Chriss | schedule that trades off impact cost against price risk; closed-form `sinh` trajectory (`ceap.execution`). |

## Research and statistics

| Term | Meaning in CEAP |
|---|---|
| IC | information coefficient: per-date rank correlation between a signal and the next return (`rowwise_rank_corr`). |
| Newey-West t | t-statistic with a standard error robust to autocorrelation; bandwidth by Andrews (`newey_west_t`). |
| Preregistration | the test, data, gates and thresholds written and committed before any data is read. |
| Holdout | data kept unread until the final test; it can be used once. |
| VOID | outcome when a preregistered gate fails: no statistic, no verdict (ADR 0005). |
| INCONCLUSIVE | the confidence interval covers both zero and the minimum Sharpe worth trading. |
| PBO | probability of backtest overfitting (CSCV): how often the in-sample best trial is below median out of sample. |
| Deflated Sharpe | Sharpe ratio corrected for the number of trials tried. |

## Platform

| Term | Meaning in CEAP |
|---|---|
| Harness | the control plane that runs a plan step by step, calls tools through policy and records evidence (`ceap.harness`). |
| Policy engine | decides allow, deny or approval for each tool call; the authority (ADR 0002). |
| MCP | Model Context Protocol: how tools are exposed; one server per domain (ADR 0004). |
| Evidence id | identifier of a tool result; every number in a report must cite one (ADR 0001). |
| Critic | an agent that reviews a draft for unsupported claims; it may lower confidence, never raise it. |
| Cassette | a recorded LLM exchange replayed in tests (`tests/cassettes/`). Changing one needs the `snapshot-change` label. |
| Snapshot | a recorded expected output (`tests/*/snapshots/`); same label rule. |
| Golden set | owner-signed questions and answers (`tests/golden/golden_set.json`); edits need `golden-change`. |
| Mutant | a copy of the code with one operator changed; a test suite should fail on it (docs/testing/test-pyramid.md). |
| Metamorphic test | checks how the output must change when the input changes in a known way. |
| Hashed lock | `requirements.lock`, installed with `--require-hashes` (ADR 0003). |
