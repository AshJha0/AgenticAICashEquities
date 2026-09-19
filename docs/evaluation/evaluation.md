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

## Adversarial suite

`tests/adversarial` covers prompt injection, rogue plans (unknown/mutating tools, malformed
steps), unparsable model output, fabricated numbers and evidence ids, a critic that tries to raise
confidence, and oversized or out-of-universe tool arguments. See the threat model for the mapping.

## Evaluating with a real model

Set `ANTHROPIC_API_KEY` and run `ceap evaluate`. The deterministic parts (metrics, attribution,
critic checks, number audit) are unchanged; the evaluation then measures the model's plan quality
(rejected steps are reported by the planner) and narrative fidelity (number and evidence audits).
