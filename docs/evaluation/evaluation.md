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

Ten templates x three seeds -> 30 scenarios (`ceap research-scenarios --all`). Since 0.4.0 a
template states generator parameters only; `expected_verdict` and `expected_flags` are derived in
closed form by `ceap.data.ground_truth` (see `docs/quant/quant.md`), and each scenario is marked
clear or near-threshold (a gate's expected margin within one standard error).

| Template | Mechanism | Truth (t+1 execution) | Class |
|---|---|---|---|
| momentum_premium | latent drift 2400 bps | PROMOTE | clear |
| no_alpha | none | REJECT · NO_ALPHA | clear |
| reversal_premium | transient share 0.45 | REJECT · COST_DRAG | clear |
| regime_break | drift 2400 bps, vanishes after the locked split | REJECT · OOS_DECAY | clear |
| cost_drag | transient 0.30, spreads x15 | REJECT · COST_DRAG | clear |
| concentration | drift in 4 illiquid names | REJECT · CONCENTRATION, LIMIT_BREACH | clear |
| mixed | decaying drift, spreads x4, ADV x0.5 | REJECT · COST_DRAG | near |
| momentum_near_threshold | drift 1800 bps (net Sharpe about 0.7) | PROMOTE | near |
| reversal_weak | transient 0.40 | REJECT · COST_DRAG | clear |
| momentum_crash | drift 2400 bps + 30 % crash | PROMOTE | clear |

History: 5040 NYSE sessions (252 warm-up, 3528 in-sample, 1260 out-of-sample), GARCH market with
Student-t shocks, latent premia the signal code never touches, delistings and late listings.

### Gates

Full chain in `docs/quant/quant.md`: IC t >= 0.40 (hurdle from the 500-seed null sweep, hash
`ef474869...`), deflated Sharpe >= 0.5 over ledger-counted trials, out-of-sample IC t >= 1
(REGIME_BREAK at <= -2), out-of-sample net Sharpe >= 0.5, costs < 50 % of gross, concentration,
and limits over the out-of-sample history.

### Metrics

`ceap evaluate --suite research` reports verdict accuracy overall, on clear and on near-threshold
scenarios, flags coverage, false-flag rate, completed, approved, unresolved findings and number
warnings. Targets: clear verdict accuracy >= 0.85, clear flags coverage >= 0.85, near-threshold
>= 0.5, completed/approved 1.0, unresolved and warnings 0. `ceap evaluate --suite null` reports
the false-PROMOTE rate at the committed hurdle (50 seeds on PR, 500 nightly; the 500-seed run gave
15/500 = 3.0 %, band [1.7 %, 4.9 %]).

### Current results (mock LLM, this build)

```
scenarios                 30
verdict_accuracy          0.97   (29/30)
clear_verdict_accuracy    0.96   (23/24; the miss: a strong premium whose 5-year OOS net Sharpe drew 0.21)
near_threshold_accuracy   1.00   (6/6)
flags_coverage            0.90
completed / approved      1.00 / 1.00
unresolved_findings       0
number_warnings           0
```

The 0.4 "21/21" was a plumbing result on a circular generator; the 0.5 numbers are against truth
the pipeline never sees. The real-model results below predate 0.4.0 and were not re-run (no live
calls).

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
