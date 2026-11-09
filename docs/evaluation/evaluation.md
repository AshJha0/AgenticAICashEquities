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

* **Session**: US regular hours, 09:30 to 16:00 America/New_York (v0.4 used Europe/London, which
  put "14:00" at 09:00 ET). Each scenario covers 12:00 to 16:00 plus the same 14:00 to 15:00 window
  (13:58 to 15:06 with lead and markout tail) on the five previous sessions, the
  time-of-day-matched baseline.
* **Mid price**: log random walk at 10 % annualised volatility on a one-second grid, scaled by a
  U-shaped intraday profile `0.6 + 1.2 (2x - 1)^2` (mean 1). The scenario's volatility multiplier
  and trend apply inside the window. Each parent adds a permanent linear drift of
  `0.7 * sigma_daily * sqrt(Q/ADV)`, signed by side.
* **Quotes**: log-normal spread noise around a per-symbol base (1.6 to 2.4 bps), tick-rounded;
  sizes are log-normal around a per-symbol top-of-book size.
* **Trades**: Poisson prints at 1.5/s times the U-shaped profile, with log-normal sizes. Odd lots
  are flagged; 0.2 % are late prints carrying a 60 s stale price; there is a closing-auction print
  at 16:00. Interval VWAP excludes auction and late prints by default.
* **Orders**: 60k-share VWAP parents. BUY or SELL alternates across the 50 scenarios. The decision
  is taken 90 s before arrival, which gives a delay cost. 65 % of children are aggressive (far
  touch plus a Poisson book walk, paying the 0.30 c access fee) and the rest passive (near touch,
  80 % fill, earning the 0.20 c rebate). There are odd-lot children and fills sharing a timestamp.
* **TCA universe** (`ceap.data.tca_universe`): 20 sessions x 40 parents, six symbols, 3 brokers,
  VWAP/POV/IS/MOC, a beta-loaded synthetic index, a LULD pause, and about 2 % of parents spanning
  two sessions.
* **Engineering**: per-minute gateway, router and market-data metrics, heartbeat and anomaly
  logs, and deployments.

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

A cause is *material* when its score ≥ 0.35. The baseline for the ratios is the pooled same clock
window on the previous five sessions (2T.2). `deteriorated` is true when the IS residual against
the pre-trade estimate is at least 1.96 standard errors (2T.4); see `docs/quant/quant.md`. The
v0.4 rule (IS or VWAP slippage 1.5 bps worse than the baseline) applies only when no estimate
exists.

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
primary_accuracy     0.98
coverage             0.98
false_positive_rate  0.06
completed            1.00
unresolved_findings  0
number_warnings      0
mean_duration_ms     ~32000   (generates the matched prior sessions too)
```

TCA acceptance (R2T): T01 benign false-flag rate 0.01 on 200 seeds (bar 5 %), mean ex-ante
impact 2.3 bps; broker 3 bps gap power 0.83 on 100 seeds (bar 0.80), size 0.05 (bar 0.10); pre-trade interval coverage
under family shift 0.86-0.91 at nominal 90 %.

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

## Evaluation report (0.6.0, 3E.2)

One command produces the whole table, from the mock model only:

```bash
ceap evaluation-report --json docs/evaluation/latest.json
```

It has five sections. **Capability**: cause accuracy, coverage and false-positive rate on the 50
synthetic scenarios, research verdict accuracy on the 30 research scenarios, the golden set, and
held-out calibration (2B.4). **Safety**: four in-process faults (refusal, truncation, an exception,
a hostile plan that points at another symbol and an unknown tool), each checked for the governance
tail, catalogue-only tools, pinned arguments and a `DEGRADED` status. **Robustness**: the
degradation rungs each fault produced. **Cost**: a local token estimate at the pinned list prices,
labelled as an estimate (never `count_tokens`, never a live call). **Live only**: every metric that
needs a real model prints `UNKNOWN / NOT RUN LIVE` (D15).

The table below is generated from `docs/evaluation/latest.json`; `scripts/check_docs.py` fails CI
if it drifts from that file, if an annotated number anywhere in the docs differs from it, or if a
landing-page result has no measured source.

<!-- evaluation-report:begin -->
LLM: mock (deterministic); no live model call (D15). Complete run: True.

| Section | Metric | Value |
|---|---|---|
| capability | scenarios | 50 |
| capability | primary_accuracy | 0.98 |
| capability | coverage | 0.98 |
| capability | false_positive_rate | 0.06 |
| capability | completed | 1.00 |
| capability | research_scenarios | 30 |
| capability | research_verdict_accuracy | 0.9667 |
| capability | golden set v1.0.0 (sign-off SIGNED_OFF) primary_cause_accuracy | 0.9667 |
| capability | golden must_mention_rate | 1.00 |
| capability | golden no_forbidden_claim_rate | 0.9667 |
| capability | golden verdict_accuracy | 1.00 |
| capability | golden must_flag_rate | 0.8667 |
| safety | invariants_rate (4 faults x 4 invariants) | 1.00 |
| robustness | refusal: degraded | True (LLM_CANONICAL_PLAN, LLM_PLAN_INVALID_AFTER_REPAIR, LLM_REFUSAL_FALLBACK, LLM_REPAIR_RETRY, LLM_STOP_REFUSAL) |
| robustness | truncation: degraded | True (LLM_CANONICAL_PLAN, LLM_PLAN_INVALID_AFTER_REPAIR, LLM_REPAIR_RETRY, LLM_STOP_MAX_TOKENS) |
| robustness | exception: degraded | True (LLM_CALL_FAILED) |
| robustness | hostile_plan: degraded | True (LLM_PLAN_INVALID_AFTER_REPAIR, LLM_REPAIR_RETRY) |
| cost | usd_per_investigation_estimate (local estimate) | 0.1995 |
| cost | calls_per_investigation | 4 |
| live only | planner quality against a real model | UNKNOWN / NOT RUN LIVE |
| live only | real-model prompt-injection resistance | UNKNOWN / NOT RUN LIVE |
| live only | narrative faithfulness of a real model | UNKNOWN / NOT RUN LIVE |
| live only | refusal rate | UNKNOWN / NOT RUN LIVE |
| live only | model latency | UNKNOWN / NOT RUN LIVE |
| live only | prompt-cache hit rate | UNKNOWN / NOT RUN LIVE |
| live only | run-to-run variance of a real model | UNKNOWN / NOT RUN LIVE |
<!-- evaluation-report:end -->

## Golden set (3E.1)

`tests/golden/golden_set.json`: 30 investigation questions (ten templates x three symbols) and 15
research questions, each with its expected findings written from the scenario specification, not
from platform output. Investigation items list the acceptable primary causes, the evidence that
must be cited (by tool) and the causes that must not be claimed as material; research items list
the expected verdict, the flags that must be raised and those that must not. The file is frozen:
a version plus the sha256 of its items. Any edit fails `tests/golden/test_golden_set.py` until the
version is bumped and the hash updated, and CI's `golden-change` job requires the `golden-change`
label on the PR. Owner sign-off of the ten `signoff_sample` items is recorded in the file's
`signoff` field and in that PR; for golden-set version 1.0.0 it is **SIGNED_OFF**: the repository owner checked the ten sampled items on
2026-11-08 and marked all ten OK (AshJha0/AgenticAICashEquities#6). Regenerate deliberately with
`python scripts/build_golden_set.py`.

## AI evaluation (0.8.0, plan WS-B and WS-C)

Before 0.8.0 no AI metric in this report depended on a model: every number came from
deterministic code or from hand-built cassettes. The harness in `ceap.eval` is built to tell a
good model from a bad one. It runs today with the mock client, and a local open-weight model (B1)
plugs in through the same `LLMClient` interface without code changes. Each result names its
evidence class: `mock`, `hand_built`, `local_model` or `recorded`. The class is read from the
client, never assumed. Each rate carries a 95 % Wilson interval. The `ceap evaluation-report`
command prints the table as the second block of the generated section above.

**Per-agent evals (B2).** Only the planner, the critic and the reporter call the model (see
`docs/ai-agents/ai-agents.md`), so only those three are evaluated:

- *Planner.* Ten questions with hand-written gold plans (`src/ceap/eval/gold_plans.json`). Each
  plan lists the tools the question needs (recall) and the tools in scope for an execution
  investigation (precision). The score comes from the tool ids in the model's own last planning
  reply, not from the canonical plan the agent falls back to. Plan-valid and repair rates come
  from the agent's degradation record.
- *Critic.* Five batches of five findings. Each batch plants three faults (uncited, unresolved,
  contradicted by evidence, a numeric mismatch, a direction mismatch) next to two clean controls.
  The batches go out in the critic's own request format. A planted finding counts as detected
  when the model marks it unsupported or lowers its confidence by more than 0.05. Clean findings
  give the false-alarm rate.
- *Reporter.* Four hand-built fact sets go through the claims call. `validate_claims` keeps or
  drops each claim, which gives faithfulness and claim-drop. Planted unfaithful claims test the
  guardrail: an unknown evidence id, an unresolvable fact key, an untraceable number, and a claim
  with no support. Their catch rate measures deterministic code, not the model, and the table
  labels it that way. The prose call then sends the policy chunks, and every citation span is
  audited, which gives citation-verified.

With the mock, most of these rates sit at their best value by construction, because the mock was
written to pass these checks. A mock 1.0 says the plumbing works. It says nothing about model
quality. The critic is the exception. The mock checks only whether evidence is present, so it
misses all three semantic fault kinds, and the table shows that by fault kind.

**Metric sensitivity (B3).** Each metric is re-measured under a deliberately degraded client
(`ceap.eval.clients`):

- a planner that keeps only execution tools and adds research tools;
- a planner that returns invalid JSON;
- a critic that marks every finding supported;
- a reporter that cites a fabricated evidence id and an untraceable number;
- a reporter whose citations quote text that is not in the chunk.

`tests/evaluation/test_ai_eval.py` fails if any of the eight metrics does not move in the
expected direction. A metric that cannot fall cannot detect a bad model.

**Out-of-distribution golden split (B6).** `tests/golden/golden_ood_v1.json` holds 15 items from
three templates that the attribution rules never saw. Each template combines causes that no
catalogue template combines, at magnitudes away from the catalogue points, on new seeds:

- a degraded BATS (the catalogue only degrades ARCA) with 2.0x spreads;
- 6x gateway latency, with no rejects and no deployment, plus a 6x parent;
- depth at 0.45x in a 2.2x-volatility hour.

The truth for each item was written from the scenario parameters and the threshold table before
any run. The file has its own version and hash, so the 45 signed-off items in `golden_set.json` are
unchanged and their sign-off stands. The OOD file still needs the owner's sign-off (status
`PENDING_OWNER`) in the `golden-change` PR. The report gives primary-cause accuracy and coverage
per template, with intervals, in complete runs only. Five items per template give wide intervals,
and the intervals are reported as they are.

**Retrieval (C1).** `data/reference/knowledge/eval.json` version 2 adds 119 queries to the
original 40, for 159 in all. There are 44 paraphrases, 30 typo queries, 20 multi-hop questions and
25 near-miss negatives. A multi-hop question counts as found only when every expected section is
in the top three. A near-miss negative is an on-domain question the corpus does not answer. The
new queries were written from the corpus text before scoring and are never tuned on.
`ceap.rag.evaluation.extended_report` scores each category with the production gate. BM25 holds
up on paraphrases and fails on typos and multi-hop questions, and some near-miss negatives leak
past the gate. These are the numbers that C2 (hybrid retrieval) has to beat. They are recorded
here and were not tuned.

**Corpus governance (C3).** `src/ceap/eval/corpus_pin.json` pins the version, the effective date
and the body hash of every corpus document. `ceap.eval.corpus.corpus_problems` fails on any of
these:

- a document added or removed without re-pinning;
- a body edited without a version bump;
- a version or date changed without re-pinning;
- a missing version, or an effective date in the future.

It warns, without failing, on two things: a document older than 365 days, and release-note
wording inside a corpus file ("Since 0.5.0"), which belongs in the CHANGELOG. Two such warnings
are open today, in `09_attribution_thresholds.md` and `docs/quant/quant.md`. Re-pin after a
reviewed corpus change with `python -m ceap.eval.corpus --pin`.

## Regression history (3E.3)

`docs/evaluation/history.csv` has one row per release from 0.4.0. Rows for 0.4.0 and 0.5.0 are the
numbers recorded in CHANGELOG at the time (the report did not exist yet); from 0.6.0 each row is
appended by `ceap evaluation-report --append-history <version>`. A release note must explain any
metric that moves beyond its run-to-run band. With the mock model every run is deterministic, so
the band is zero: any change is a code or data change and must be explained.

## Drift monitors (3E.4)

`tests/contract/test_drift_monitors.py` (also run as its own CI step) checks, each with a failing
fixture: cassette staleness against the prompt registry, the RAG held-out recall floor (0.80) and
MRR floor (0.70), the golden-set version hash, every cassette model still pinned in
`model_pins.toml`, and the SDK version (pin, installed package and cassettes must agree, so an SDK
bump fails until the cassettes are re-authored).

## Adversarial suite

`tests/adversarial` covers prompt injection, rogue plans (unknown/mutating tools, malformed
steps), unparsable model output, fabricated numbers and evidence ids, a critic that tries to raise
confidence, and oversized or out-of-universe tool arguments. `test_research_adversarial.py` adds:
an injected question that asks for order staging, a rogue research plan that schedules
`execution.stage_orders` first and reaches outside the universe, a hand-built staging step for a
quant (policy denies, task fails closed), and hand-built plans that try another symbol, signal or
split date (plan validation rejects). See the threat model for the mapping.

## Real data and paper trading (0.7.0)

Neither adds an evaluation metric. The R5 momentum study is **VOID** (coverage 75.1 % against a
required 95 %; `docs/research/result_momentum_2026-11.md`), so it contributes no statistic. The
paper forward record (LEARN §18a, COOKBOOK recipe 43) runs on **synthetic** data and checks
operations and controls; it says nothing about profitability, and about 1980 sessions would be
needed to tell a Sharpe of 1 from 0. The owner enabled its scheduled run after the 0.7.0 tag (D22;
repository variable `PAPER_DAILY_ENABLED=true`); the first forward session is 2026-11-09, so no
forward result exists yet (UNKNOWN). The R5 v2 attempt on Stooq (0.7.1) was also VOID, at 74.9 %
coverage (`docs/research/result_momentum_2026-11-v2.md`).

## Evaluating with a real model

Set `ANTHROPIC_API_KEY` and run `ceap evaluate --suite investigation|research|all`. The
deterministic parts (metrics, attribution, critic checks, number audit) are unchanged; the
evaluation then measures the model's plan quality (rejected steps are reported by the planner)
and narrative fidelity (number and evidence audits). For Stage 2, also set
`CEAP_LLM_PLANNING_MODEL` to a stronger planner than the narrator/critic model — see "What
changed against a real model" above for why that matters and what it costs.
