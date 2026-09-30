# Evaluation

## Purpose

Show, with structured ground truth, that the platform identifies the right cause(s) of execution
quality changes, completes reliably, and never lets an unsupported claim reach the report.

## Scenario catalogue

Ten templates, each generated for AAPL, MSFT, NVDA, AMZN and META with distinct seeds → 50
scenarios (`ceap scenarios --all`). Each `ScenarioSpec` carries its `ground_truth` causes.

| Template | Injected effect (14:00–15:00 London) | Ground truth |
|---|---|---|
| normal_vwap | – | NORMAL |
| high_volatility | per-second volatility x3 | MARKET_VOLATILITY |
| wide_spreads | quoted spread x2.5 | WIDE_SPREADS |
| low_liquidity | top-of-book and depth x0.3 | LOW_LIQUIDITY |
| venue_degradation | ARCA fill rate 0.45, +4 bps slippage, SOR WARN logs | VENUE_DEGRADATION |
| technology_latency | latency x10, 10 % rejects, SLO breach logs, SOR deployment at 13:52 | TECHNOLOGY_LATENCY |
| market_data_anomaly | 15 % stale-quote runs, 20 crossed quotes, feed-gap metrics and logs | MARKET_DATA_ANOMALY |
| large_parent_order | parent x10 (600k shares) → ~35 % participation, impact drift | LARGE_ORDER_IMPACT |
| unexpected_price_move | +200 bps adverse trend over 10 minutes at normal volatility | PRICE_MOVEMENT |
| mixed_market_technology | volatility x3 and latency x8 with rejects and deployment | MARKET_VOLATILITY, TECHNOLOGY_LATENCY |

## Synthetic market model

* Mid price: GBM at 10 % annualised volatility sampled every second (≈0.49 bps/s, ≈3.8 bps/min),
  with scenario-specific volatility multipliers, trends and participation-driven impact drift.
* Quotes: log-normal spread noise around a per-symbol base (1.6–2.4 bps), tick-rounded; sizes
  log-normal around a per-symbol top-of-book size.
* Trades: Poisson(1.5/s) prints, log-normal sizes (mean 150), five venues with fixed weights; our
  own fills are added to the tape.
* Orders: a 60k-share VWAP buy parent in the baseline hour and in the window hour, 120 child slices
  each; fills cross the spread and walk the book by an amount driven by slice size relative to
  displayed liquidity, venue degradation and latency.
* Engineering: per-minute gateway/router/market-data metrics, heartbeat and anomaly logs,
  deployments.

## Attribution thresholds (`ceap.analytics.attribution.Thresholds`)

| Cause | Signal | Threshold | Score saturation |
|---|---|---|---|
| MARKET_VOLATILITY | realised 1-min vol ratio | > 1.6 | scale 0.8 |
| WIDE_SPREADS | average spread ratio | > 1.4 | scale 0.5 |
| LOW_LIQUIDITY | min(depth ratio, top-of-book ratio) | < 0.65 | scale 0.2 |
| VENUE_DEGRADATION | fill-rate gap vs peers / slippage excess / reject excess (only when platform rejects are normal) | 0.25 / 2 bps / 10 pp | scale 0.15 / 2 / 0.1 |
| TECHNOLOGY_LATENCY | latency ratio (fills or gateway) / reject rate | > 3 / > 5 % | scale 2 / 0.05 |
| MARKET_DATA_ANOMALY | stale-quote fraction / crossed quotes | > 3 % (and 3x baseline) / > 5 | scale 0.05 / 10 |
| LARGE_ORDER_IMPACT | participation (+0.3 if parent ≥ 4x baseline) | > 20 % | scale 0.1 |
| PRICE_MOVEMENT | adverse drift (halved when volatility is elevated) | > 60 bps | scale 40 |

A cause is *material* when its score ≥ 0.35. `deteriorated` is true when IS or VWAP slippage
worsened by ≥ 1.5 bps versus the baseline.

## Metrics

`ceap evaluate` / `tests/evaluation/test_scenarios.py` report:

* **primary_accuracy** – primary cause ∈ ground truth (target ≥ 0.9)
* **coverage** – every ground-truth cause is material (target ≥ 0.9)
* **false_positive_rate** – scenarios with a material cause outside the ground truth (target ≤ 0.1)
* **completed** – harness reached COMPLETED (target 1.0)
* **unresolved_findings** – findings citing unknown evidence (target 0)
* **number_warnings** – narrative numbers not traceable to facts (target 0 with the mock; monitor with a real model)

## Current results (mock LLM, this build)

```
scenarios            50
primary_accuracy     1.00
coverage             1.00
false_positive_rate  0.02   (S50: a large drift inside a high-volatility window scored PRICE_MOVEMENT as secondary)
completed            1.00
unresolved_findings  0
number_warnings      0
mean_duration_ms     ~850
```

## Research evaluation (Stage 2)

Seven research templates × three seeds → 21 scenarios (`ceap research-scenarios --all`). Each
`ResearchScenarioSpec` carries `expected_verdict` and `expected_flags`.

| Template | Signal | Embedded effect | Ground truth |
|---|---|---|---|
| momentum_premium | momentum_12_1 | 2000 bps/yr per unit z-score | PROMOTE |
| no_alpha | momentum_12_1 | none | REJECT · NO_ALPHA |
| reversal_premium | reversal_5 | 5000 bps/yr, rebalance every 5 days | PROMOTE |
| regime_break | momentum_12_1 | premium ×−0.5 after the in-sample period | REJECT · OVERFIT |
| cost_drag | reversal_5 | 5000 bps/yr with spreads ×15 | REJECT · COST_DRAG |
| concentration | momentum_12_1 | 4000 bps/yr in the four least liquid names, at 5 % of their ADV | REJECT · CONCENTRATION, LIMIT_BREACH |
| mixed | momentum_12_1 | 2000 bps/yr with spreads ×4 and ADV ×0.5 | PROMOTE |

### Historical model

* 30 names in three liquidity tiers (10 mega, 12 mid, 8 small), 1008 business days ending
  2026-09-18: 252 warm-up, 504 in-sample, 252 out-of-sample.
* `r = beta·market + k·z + eps`: market 6 %/yr at 16 % vol, betas U(0.7, 1.3), idiosyncratic vol
  22/28/35 % by tier, `z` the clipped cross-sectional z-score of the scenario's own signal computed
  on the path so far by `ceap.analytics.signals` (no generator/analytics drift), `k` the premium.
* Dollar ADV is stationary per name (volume scales with 1/price); spreads are log-normal around a
  per-name base with the scenario's multiplier.

### Assessment thresholds (`ceap.analytics.research_assessment.ResearchThresholds`)

| Flag | Rule |
|---|---|
| NO_ALPHA | in-sample one-day IC t-stat < 2.0 (or < 60 dates) |
| OVERFIT | out-of-sample / in-sample net Sharpe < 0.5 (when in-sample > 0), or out-of-sample IC t-stat < 1.0, or out-of-sample net Sharpe < 0.5 not explained by costs |
| COST_DRAG | gross out-of-sample Sharpe ≥ 0.5 but net < 0.5, or costs ≥ 50 % of gross return |
| CONCENTRATION | top three names > 50 % of positive P&L |
| LIMIT_BREACH | any portfolio limit check breached (gross 1.05, net 0.25, single name 15 %, beta 0.5, HHI 0.15, ADV participation 10 %) |

`PROMOTE` iff no flag is raised. The alpha chain stops at the first failure (NO_ALPHA → OVERFIT →
COST_DRAG); CONCENTRATION and LIMIT_BREACH are independent.

### Metrics

`ceap evaluate --suite research` / `tests/evaluation/test_research_scenarios.py` report
**verdict_accuracy** (target ≥ 0.9), **flags_coverage** (every expected flag raised, ≥ 0.9),
**false_flag_rate** (reported), **completed**, **approved** (the proposal gate was passed),
**unresolved_findings** and **number_warnings** (targets 1.0 / 1.0 / 0 / 0).

### Current results (mock LLM, this build)

```
scenarios            21
verdict_accuracy     1.00
flags_coverage       1.00
false_flag_rate      0.10   (two concentration seeds also raise NO_ALPHA / OVERFIT: the four-name premium is thin)
completed            1.00
approved             1.00
unresolved_findings  0
number_warnings      0
```

### What changed against a real model (honest results, not projected)

Run against `claude-haiku-4-5` as both planner and reporter: `verdict_accuracy` **0.95** and
`flags_coverage` **0.81** — 5 of 21 scenarios came back `INCOMPLETE_ANALYSIS` because the model's
plan skipped tool or agent steps the canonical plan always includes. This is plan-quality
variance, not a platform failure: the harness handled the incomplete plan safely (no crash, a
correct conservative verdict, no unresolved evidence). Pairing `claude-haiku-4-5` as the cheap
narrator/critic with `claude-sonnet-5` as the planner (`CEAP_LLM_PLANNING_MODEL`) fixed it
completely — `verdict_accuracy` **1.00**, `flags_coverage` **1.00**, `false_flag_rate` **0.10**
on the second run. `number_warnings` stayed near zero after two real bugs the mock had never
exercised were fixed (a stale `temperature` kwarg the installed SDK rejects, and the narrative
audit being stricter than a real model's legitimate bps/per-mille arithmetic) — see CHANGELOG
0.3.1. Cost: the full 21-scenario run is well under $2 on Haiku pricing.

## Adversarial suite

`tests/adversarial` covers prompt injection, rogue plans (unknown/mutating tools, malformed
steps), unparsable model output, fabricated numbers and evidence ids, a critic that tries to raise
confidence, and oversized or out-of-universe tool arguments. `test_research_adversarial.py` adds:
an injected question that asks for order staging, a rogue research plan that schedules
`execution.stage_orders` first and reaches outside the universe, a hand-built staging step for a
quant (policy denies, task fails closed), and hand-built plans that try another symbol, signal or
split date (plan validation rejects). See the threat model for the mapping.

## Evaluating with a real model

Set `ANTHROPIC_API_KEY` and run `ceap evaluate --suite investigation|research|all`. The
deterministic parts (metrics, attribution, critic checks, number audit) are unchanged; the
evaluation then measures the model's plan quality (rejected steps are reported by the planner)
and narrative fidelity (number and evidence audits). For Stage 2, also set
`CEAP_LLM_PLANNING_MODEL` to a stronger planner than the narrator/critic model — see "What
changed against a real model" above for why that matters and what it costs.
