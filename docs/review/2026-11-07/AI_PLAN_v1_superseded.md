# CEAP AI plan: LLM, agents, agentic AI, RAG, ML and evaluation

Date: 2026-11-07. Status: SAVED, NOT STARTED. This document replaces Round 3 of `PLAN.md` with
rounds 3A to 3E and adds an ML round (2B). Decisions D6 and D10 to D15 are recorded in `PLAN.md`
section 6. Under the owner's zero-spend constraint, D15 is (b): every API-spend figure below is the
cost of an optional live run that is NOT performed; the default build runs on the mock and on
hand-built cassettes in the real Messages API wire format, and the docs label the live numbers
NOT RUN LIVE. `PLAN.md` section 3 (totals) and section 4 (decisions)
carry the merged numbers. Read `REVIEW.md` first for what is wrong today.

## 1. Where the AI actually is today

Three LLM touchpoints (planner, critic, reporter) around a deterministic rules engine. In the default
mock mode there are zero model calls. Nine of twelve agents are threshold functions. The planner's
output barely steers anything because agents fetch their own data. RAG is hashed TF-IDF over eight
small files. No ML model exists despite the `ml` extra. The evaluation runs on the mock against
self-generated ground truth. Model output is parsed with a brace-trimming regex. The default model id
`claude-sonnet-4-5` is deprecated and retires on 2026-11-30.

The one thing worth protecting is the boundary: **the model reasons and writes; deterministic code
computes; every number traces to evidence.** Everything below adds AI capability without crossing it.
Where ML is added, it is treated as a deterministic, seeded analytics module that produces evidence,
never as something the LLM "trusts".

## 2. What the current Claude API offers that this project should use

Checked against the API reference on 2026-11-07. Items marked "use" go into the rounds below;
"skip" items are listed in section 5 with reasons.

| Capability | Use or skip | Why for CEAP |
|---|---|---|
| Current models: Opus 5.5 (`claude-opus-5-5`, default), Sonnet 5.5 (`claude-sonnet-5-5`), Haiku 5.5 (`claude-haiku-5-5`) | use | Replace deprecated `claude-sonnet-4-5`; planner on Opus 5.5, narrative and critique on Sonnet 5.5, triage and judges on Haiku 5.5 |
| Structured outputs (`output_config.format`, `client.messages.parse()` with Pydantic) | use | Kills the regex JSON path for planner, critic, reporter; schema-valid by construction |
| Strict tools (`strict: true` on tool definitions) | use | For the planner's tool-call proposals and for any agent that calls MCP tools through the model |
| Adaptive thinking plus `output_config.effort` | use | Thinking is on by default on 5.5 models; effort `low` for narrative, `high` for planning and critique. `temperature` is rejected on current models, so determinism comes from structured outputs and recorded cassettes, not sampling knobs |
| Prompt caching (`cache_control`, 1 h TTL) | use | The 37-tool catalogue and the system prompt are resent on every planning call; cache them, verify `cache_read_input_tokens` > 0 |
| Message Batches (50 % price, async) | use | The 71-scenario evaluation and the null sweep do not need latency; halve the cost of every live eval |
| Citations on `document` blocks | use | Grounded RAG: the reporter and the research agent cite runbook passages by `cited_text` and char range, which becomes a `DOC` evidence record with a verifiable span. Note: incompatible with structured outputs in the same call, so the narrative call uses citations and a separate small call extracts structure |
| Files API | use (small) | Upload the knowledge corpus once; reference by `file_id` in every call instead of inlining 9.6 KB each time |
| Token counting (`count_tokens`) | use | Pre-flight budget check per task; feeds the `CEAP_TASK_BUDGET_USD` cap |
| Server-side refusal fallbacks (`fallbacks: "default"`) | use | A refusal on a planner call should fall to another model, visibly, not to the mock |
| Mid-conversation `system` messages | use | The harness injects "critic found X, lower confidence" as an operator message without invalidating the cached prefix |
| Tool search (BM25) with `defer_loading` | use later | When the catalogue grows past 37 tools; not now |
| Code execution tool, programmatic tool calling | skip for now | Would let the model compute; crosses the boundary. Reconsider only for a sandboxed signal-prototyping agent whose output is re-run by deterministic code (round 3D, gated) |
| Web search / web fetch | skip | No real market data should enter via model-driven browsing; provenance would be unverifiable |
| Memory tool | use (bounded) | A case base of past investigations, read-only to the model, written only by the harness after human approval |
| Compaction, message threads | skip | Tasks are short; the harness owns context |
| Managed Agents, scheduled deployments | skip | Self-hosted harness is the product; a scheduled EOD TCA run is a cron plus the CLI, no spend |
| Advisor tool | skip | Cost without a measured need |
| Fine-tuning | not available and not wanted | Prompts plus evals plus cassettes are the right lever at this scale |

## 3. Capability analysis and plan items

Effort is hours. Cost: API spend in USD (hard-capped, from the key already in `.env`), session budget
S/M/L, external always $0. Owner decisions are listed in section 6 and mirrored into `PLAN.md`.

### 3A. LLM layer: correctness, determinism, cost. Branch `r3a-llm`

| Id | Item | Files | Effort | Cost | Decision | Done when |
|---|---|---|---|---|---|---|
| 3A.1 | Migrate model ids: default `claude-opus-5-5` for planning, `claude-sonnet-5-5` narrative and critique, `claude-haiku-5-5` triage and judges; remove `claude-sonnet-4-5` everywhere; model id recorded in every result | `config.py`, `llm/router.py`, `.env.example`, docs | 2 h | S, $0 | none | `git grep sonnet-4-5` empty; report footer shows the three ids used |
| 3A.2 | Structured outputs via `client.messages.parse()` with Pydantic schemas for `Plan`, `Critique`, `ReportFacts`; one repair retry; delete `extract_json` | `llm/client.py`, `llm/models.py`, `agents/planner.py`, `agents/critic.py`, `agents/reporter.py` | 8 h | M, under $0.50 smoke | none | No regex JSON path remains; malformed cassette triggers exactly one repair then `DEGRADED` |
| 3A.3 | Remove `temperature`; set `output_config.effort` per purpose (planning high, critique high, narrative low); check `stop_reason` for `max_tokens` and `refusal`; `fallbacks: "default"` on planner and critic calls, fallback recorded as a warning | `llm/client.py`, `llm/router.py` | 4 h | S, $0 | none | Test: `max_tokens` stop sets `DEGRADED`; refusal path records which model served |
| 3A.4 | Prompt caching: stable system prompt and tool catalogue first with `cache_control` (1 h TTL), volatile question last; assert `cache_read_input_tokens > 0` on the second call in the live smoke | `llm/prompts.py`, `llm/client.py` | 3 h | S, under $0.50 | none | Live smoke prints cache-read tokens > 0 |
| 3A.5 | Per-call record (model, SDK version, prompt hash, tokens, cache tokens, USD at list price, latency, request id); per-task cost span; `CEAP_TASK_BUDGET_USD` with `count_tokens` pre-flight; abort to `DEGRADED` on breach | `llm/client.py`, `llm/router.py`, `harness/engine.py`, `observability/metrics.py` | 6 h | M, $0 | none | Two concurrent tasks report separate costs; a $0.01 budget aborts before the call |
| 3A.6 | Prompt registry: version and sha256 per prompt, recorded in result and trace; prompts moved to versioned files under `llm/prompts/` | `llm/prompts.py`, `llm/prompts/*.md`, `domain/reports.py` | 3 h | S, $0 | none | Changing a prompt changes the golden snapshot hash |
| 3A.7 | Message Batches runner for evaluation and null sweeps (`ceap evaluate --live --batch`) keyed by `custom_id` | `evaluation.py`, `llm/client.py`, `cli.py` | 5 h | M, about $1 for one batched 71-scenario run | none | Batched live eval costs about half of the streaming run and produces the same table |
| 3A.8 | Client timeout per call, `race_cancellation` on LLM calls, typed error chain (`RateLimitError`, `APIStatusError` >= 500 retry, 4xx no retry) | `llm/client.py`, `harness/engine.py` | 3 h | S, $0 | none | Cancel mid-call returns in under 1 s; 400 is not retried |

Round 3A: 34 h, about 4 plan-days, 1.5 wall-clock days. API spend under $2.

### 3B. Agents and agentic AI: make the agency real. Branch `r3b-agents`

The honest finding is that the current "agents" are rules. The fix is not to make rules into model
calls. It is to give the model the decisions that are genuinely open-ended and keep rules for the
rest, then measure whether the model adds anything.

| Id | Item | Files | Effort | Cost | Decision | Done when |
|---|---|---|---|---|---|---|
| 3B.1 | Planner owns scope: the plan decides which agents run, which optional tools to fetch (venue stats, knowledge, deployments), the baseline window length and the hypotheses to test; agents consume plan-fetched data and stop self-serving (`ensure` calls from 28 to under 8). Required agents stay harness-enforced (PLAN 1.6) | `agents/planner.py`, `agents/base.py`, all `agents/*.py`, `llm/prompts/planner.md` | 10 h | M, $0 | none | Two different questions produce two different plans on cassettes; mock keeps a canonical plan |
| 3B.2 | Hypothesis-driven investigation: planner emits ranked hypotheses with the evidence that would confirm or refute each; agents attach evidence to hypotheses; critic scores each hypothesis (supported, refuted, untested); reporter writes from the hypothesis table | `domain/findings.py`, `agents/planner.py`, `agents/critic.py`, `agents/reporter.py` | 10 h | M, $0 | none | Report has an "untested hypotheses" section; T10 (mixed) lists both causes with separate evidence |
| 3B.3 | Critic with tools: the critic may re-call read-only analytics tools to verify a number or recompute a baseline (through the same policy path), and its `supported=False` verdict with reason lowers confidence and records `CRITIC_DISPUTE` evidence; its free text is stored, not forwarded | `agents/critic.py`, `harness/engine.py`, `llm/prompts/critic.md` | 8 h | M, $0 | none | Cassette where the critic recomputes a VWAP and overturns a finding |
| 3B.4 | Evidence binding: `evidence_refs: [{id, path, value}]` on every finding; validation checks value at path; agents cite the attribute they used, not every id touched; `knowledge` becomes `DOC` evidence | `domain/findings.py`, `harness/memory.py`, `mcp/adapter.py`, all `agents/*.py` | 8 h | M, $0 | none | A finding citing an id whose attribute does not match is dropped with a warning |
| 3B.5 | Injection hygiene: tool-derived text wrapped in delimited data blocks with ids; operator instructions via mid-conversation `system` messages; the adversarial corpus runs through real-model cassettes | `llm/prompts.py`, `agents/engineering.py`, `agents/reporter.py`, `tests/adversarial` | 6 h | M, about $0.50 to record | none | Injection pass rate measured on a real model and published |
| 3B.6 | Calibrated confidence: agent confidences fitted by isotonic regression against scenario ground truth over the 50 + null scenarios, with reliability diagram and Brier score published; `confidence_from_excess` renamed until calibrated | `agents/base.py`, `analytics/calibration.py`, `evaluation.py`, `docs/evaluation` | 6 h | M, $0 | none | Reliability diagram in docs; Brier score reported per release |
| 3B.7 | Case memory (bounded): after human approval, the harness writes a compact case record (question, primary cause, key metrics, outcome) to a store; the planner retrieves the three nearest past cases as read-only context; the model never writes memory | `harness/memory.py`, `rag/retrieval.py`, `agents/planner.py` | 6 h | M, $0 | **D10** | Second investigation of a similar window cites a prior case id; store is append-only and audited |
| 3B.8 | Red-team agent in the eval suite: a Haiku 5.5 agent generates adversarial questions and poisoned log lines against the planner and reporter; failures become new regression cassettes | `evaluation.py`, `tests/adversarial`, `llm/prompts/redteam.md` | 5 h | M, about $0.50 per run | none | Red-team run produces at least 20 cases; pass rate tracked across releases |
| 3B.9 | Stdio MCP lifecycle: `AsyncExitStack` owned by a dedicated task; one reconnect on child exit | `mcp/client.py` | 3 h | S, $0 | none | stdio test kills the child once and the next call succeeds |

Round 3B: 62 h, about 8 plan-days, 3 wall-clock days with two sessions. API spend about $1.

### 3C. RAG: from toy to grounded retrieval. Branch `r3c-rag`

| Id | Item | Files | Effort | Cost | Decision | Done when |
|---|---|---|---|---|---|---|
| 3C.1 | Honest naming and BM25: rename "semantic search" to lexical; BM25 over sentence-level chunks with metadata (document, section, version, effective date) | `rag/*.py`, `mcp/knowledge/server.py` | 5 h | M, $0 | none | Tool description accurate; metadata filter test |
| 3C.2 | Retrieval eval: 30 hand-written queries with expected documents and passages; recall at 3 and MRR published; runs in CI | `tests/unit/test_rag_eval.py`, `data/reference/knowledge/eval.json` | 4 h | M, $0 | none | Table in docs; CI fails if recall at 3 drops below the committed floor |
| 3C.3 | Optional local embeddings (`bge-small-en-v1.5` via sentence-transformers) behind an `embeddings` extra; hybrid BM25 plus dense with reciprocal rank fusion; default stays BM25 unless the eval shows a gain | `rag/embeddings.py`, `rag/retrieval.py`, `pyproject.toml` | 6 h | M, $0 (130 MB weights) | **D6** | Eval table BM25 vs hybrid committed |
| 3C.4 | Citations: knowledge documents passed as `document` blocks with `citations: {enabled: true}` on the narrative call; each citation becomes a `DOC` evidence record with `cited_text` and char range; the number audit verifies the quoted span exists in the corpus | `llm/client.py`, `agents/reporter.py`, `agents/research.py`, `domain/evidence.py` | 8 h | M, under $0.50 | none | Report's runbook claims carry verifiable spans; a fabricated quote fails the audit |
| 3C.5 | Corpus growth: add the project's own `docs/quant/quant.md`, threshold tables and the TCA methodology as retrievable documents, versioned, so the model explains thresholds from the source of truth | `data/reference/knowledge/*`, `rag/ingestion.py` | 3 h | S, $0 | none | Query "why 1.5 bps" retrieves the thresholds section |
| 3C.6 | Files API: upload the corpus once per version; reference by `file_id`; re-upload on hash change | `llm/client.py`, `rag/ingestion.py` | 3 h | S, $0 | none | Second run reuses the `file_id`; input tokens per narrative call fall |

Round 3C: 29 h, about 4 plan-days, 1.5 wall-clock days. API spend under $1.

### 2B. ML: real models, deterministic and seeded, producing evidence. Branch `r2b-ml`

Each model is trained offline by a script, versioned by hash, loaded at runtime, and its outputs are
`CALCULATION` evidence with the model hash attached. The LLM never trains, tunes or trusts a model
beyond what the evidence record says. Runs after Round 2 (quant correctness).

| Id | Item | Files | Effort | Cost | Decision | Done when |
|---|---|---|---|---|---|---|
| 2B.1 | Pre-trade cost model: gradient boosting (LightGBM or scikit-learn HistGradientBoosting) on order features (participation, spread, vol, ADV, time of day, venue mix) predicting IS bps, monotone constraints on participation and spread; trained on synthetic now, swappable to real fills; conformal prediction intervals | `ml/pretrade_cost.py`, `analytics/execution_metrics.py`, `scripts/train_pretrade.py` | 10 h | M, $0 | **D11** | Residual (realised minus predicted) replaces raw IS delta as the deterioration signal; interval coverage test at 90 % |
| 2B.2 | Anomaly detection on the market and technology streams: robust z-scores and isolation forest on per-minute features (spread, depth, latency p99, reject rate, stale quote share); change-point detection (PELT or binary segmentation) to time the onset | `ml/anomaly.py`, `agents/market.py`, `agents/engineering.py` | 8 h | M, $0 | none | T06 onset detected within 2 minutes of the planted 13:52 deployment; T01 raises no anomaly in 95 % of seeds |
| 2B.3 | Causal attribution: difference-in-differences of the order's IS against a matched peer set (same symbol, size bucket, other windows) and against the pre-trade model residual; attribution scores become effect sizes with confidence intervals instead of threshold scores | `analytics/attribution.py`, `ml/peers.py` | 8 h | M, $0 | none | Attribution output has effect size and CI per cause; threshold catalogue kept as a fallback |
| 2B.4 | Confidence calibration shared with 3B.6: isotonic regression from agent scores to probability of being correct, fitted on scenario ground truth, refit per release | `analytics/calibration.py` | included in 3B.6 | S, $0 | none | Brier score in docs |
| 2B.5 | Signal research ML: purged and embargoed walk-forward cross-validation (Lopez de Prado), ridge and gradient-boosting combinations of the three signals with feature importance, deflated Sharpe over the trial count, meta-labeling for position sizing; all under the same cost model and t+1 execution | `analytics/ml_signals.py`, `analytics/signal_statistics.py`, `mcp/alpha/server.py`, `agents/alpha.py` | 12 h | L, $0 | **D12** | On the realistic synthetic set, the combined model's OOS deflated Sharpe is reported next to each single signal; on the null set it is not significant |
| 2B.6 | Regime detection: two-state Gaussian HMM on market returns and vol; regime label per date becomes evidence; research agent reports per-regime IC | `ml/regime.py`, `agents/research.py` | 6 h | M, $0 | none | R04 regime break is detected without the planted flag being read |
| 2B.7 | Leakage linter for any new signal or feature: static check that a feature at date t uses only data with timestamp < t (point-in-time), plus a shuffled-label test; wired into tests | `analytics/leakage.py`, `tests/unit/analytics/test_leakage.py` | 5 h | M, $0 | none | A deliberately leaky signal fails the linter |
| 2B.8 | Model registry: every trained artefact stored with hash, training data hash, metrics, date; loaded by hash; recorded in evidence | `ml/registry.py`, `data/models/` | 4 h | S, $0 | none | Changing the training seed changes the model hash in the report |

Round 2B: 53 h, about 7 plan-days, 2.5 wall-clock days. API spend $0. Dependencies: `scikit-learn`,
optional `lightgbm`, `ruptures` or an in-house PELT, `hmmlearn` or an in-house two-state EM. All free.

### 3D. Signal-prototyping agent (gated experiment). Branch `r3d-proto`

The one place where letting the model write code is worth testing: proposing a new signal. The model
writes a signal function in a sandbox; deterministic code runs the leakage linter, the backtest, the
null sweep and the multiple-testing hurdle; the model never sees the holdout; every trial increments
`n_trials`. This is the closest thing to how a real quant research pod uses LLMs today.

| Id | Item | Files | Effort | Cost | Decision | Done when |
|---|---|---|---|---|---|---|
| 3D.1 | Sandboxed execution of model-written signal code: subprocess with no network, CPU and memory limits, restricted imports (numpy, pandas only), 10 s timeout | `research/sandbox.py` | 6 h | M, $0 | **D13** | Escape attempts in tests (network, file write, import os) all fail |
| 3D.2 | Prototyping loop: structured-output proposal (name, rationale, code, expected horizon) from Opus 5.5, linter, backtest on in-sample only, deflated Sharpe with the running trial count, feedback to the model, at most 5 iterations, holdout never exposed | `agents/prototyper.py`, `llm/prompts/prototyper.md`, `analytics/research_assessment.py` | 10 h | M, about $2 per full run | **D13** | A run on the realistic synthetic set produces a proposal that passes or fails the hurdle with the trial count shown |
| 3D.3 | Human approval before any prototype is promoted to the signal library, with the full trial history in the approval payload | `harness/engine.py`, `policy/approvals.py` | 3 h | S, $0 | none | Promotion without approval is impossible by test |

Round 3D: 19 h, about 2.5 plan-days, 1 wall-clock day. API spend about $2 per run.

### 3E. Evaluation: measure the model, not the mock. Branch `r3e-eval`

| Id | Item | Files | Effort | Cost | Decision | Done when |
|---|---|---|---|---|---|---|
| 3E.1 | Cassettes: record one real run per scenario (request and response JSON, committed, key stays in `.env`); CI replays them so the real client path is tested offline | `llm/client.py` (recording client), `tests/cassettes/`, `tests/conftest.py` | 6 h | M, about $1.50 once via Batches | none | CI with no key exercises `AnthropicLLMClient` parsing on every cassette |
| 3E.2 | Golden set: 30 investigation and 15 research questions with human-written expected findings (primary cause, must-mention evidence, must-not-claim statements) | `tests/golden/*.json`, `docs/evaluation` | 6 h | M, $0 | none | Golden set reviewed by a second session; stored with a version |
| 3E.3 | LLM-as-judge with Haiku 5.5 for narrative quality (faithfulness to facts, completeness against the golden set, no unsupported claims), with a 20-item human-labelled agreement check; judge prompt versioned | `evaluation.py`, `llm/prompts/judge.md` | 6 h | M, under $0.50 per run | none | Judge agreement with human labels at or above 85 % on the 20 items |
| 3E.4 | Live evaluation report: verdict and cause accuracy, degraded count, p50 and p95 latency, tokens, cache hit rate, USD, run-to-run variance over 3 seeds, injection pass rate, judge scores, Brier score; one run per round via Batches, published with model ids and prompt hashes | `evaluation.py`, `cli.py`, `docs/evaluation` | 5 h | M, up to $2 per run | none | Table in docs reproducible by one command |
| 3E.5 | Regression tracking: eval results appended to `docs/evaluation/history.csv` per release; a release note must explain any metric that moved by more than its run-to-run variance | `evaluation.py`, `docs/evaluation/history.csv`, `CHANGELOG.md` | 3 h | S, $0 | none | History has one row per release from v0.4.0 |
| 3E.6 | Mock parity test: canonical plan and templated narrative compared against the cassette outputs so the mock stays a faithful stand-in | `tests/agent/test_mock_parity.py` | 3 h | S, $0 | none | Structural diff between mock and cassette plan is empty for the 10 template scenarios |

Round 3E: 29 h, about 4 plan-days, 1.5 wall-clock days. API spend under $5 in total.

## 4. How the pieces fit the boundary

```
question ──> Planner (Opus 5.5, structured output, cached catalogue, case memory read-only)
               │  plan: agents, tools, baseline, hypotheses
               ▼
         specialist agents (rules + ML modules producing CALCULATION evidence with model hashes)
               │  findings with evidence_refs {id, path, value}
               ▼
         Critic (Sonnet 5.5, structured output, may re-call read-only tools, supported=False lowers)
               │  hypothesis table, CRITIC_DISPUTE evidence
               ▼
         deterministic validation (evidence values, leakage linter, assessment hurdle, calibration)
               ▼
         Reporter (Sonnet 5.5, citations on runbook documents -> DOC evidence with spans)
               │  number audit + span audit + judge (Haiku 5.5) in eval only
               ▼
         human approval (four-eyes) ──> optional paper staging
```

The model still never computes a metric. What changes: it chooses scope, states hypotheses, disputes
findings with tool-backed checks, cites sources with verifiable spans, and (gated) proposes code that
deterministic machinery grades. ML modules compute, but as seeded, hashed, evidence-producing code.

## 5. What not to add, and why

- **Autonomous trading or live OMS**: out of scope for a public demonstration project; no change.
- **Web search in the agent loop**: unverifiable provenance; the evidence model cannot bind to it.
- **Fine-tuning**: not offered for these models and not needed; prompts, evals and cassettes are the lever.
- **Managed Agents or scheduled cloud deployments**: the harness is the product; a nightly TCA batch is a cron entry calling the CLI, at no spend.
- **Replacing rule agents with model calls**: rules are correct, cheap and testable; the model adds value only where the decision is open-ended (3B.1, 3B.2, 3B.3, 3D).
- **Vector database services**: eight to twenty documents do not need one; BM25 plus optional local embeddings is honest and free.
- **Paid data or sign-ups**: standing rule; real-data work stays on Stooq and yfinance.

## 6. Owner decisions added by this plan

| Id | Decision | Options | Recommendation | Blocks |
|---|---|---|---|---|
| D10 | Case memory | (a) add bounded, harness-written, audited case store; (b) no memory | (a) with the store append-only and the model read-only | 3B.7 |
| D11 | Pre-trade cost model library | (a) scikit-learn HistGradientBoosting (already an extra); (b) LightGBM (adds a dependency, faster, native monotone constraints) | (a) first; (b) only if 2B.1 needs it | 2B.1 |
| D12 | ML signal combination in the research pipeline | (a) include with purged CV and deflated Sharpe; (b) defer until the real-data study exists | (a): it is where "ML" in the project name becomes true, and it is fully testable on synthetic data | 2B.5 |
| D13 | Signal-prototyping agent (model writes code in a sandbox) | (a) build as a gated experiment with human approval before promotion; (b) do not let the model write code | (a) as an experiment; the sandbox and the trial counter are the safeguards | 3D |
| D14 | Model routing | (a) Opus 5.5 planner, Sonnet 5.5 narrative and critique, Haiku 5.5 judges; (b) Sonnet 5.5 everywhere | (a): the 0.3.1 live run showed planner quality matters most; cost difference is cents per task | 3A.1 |
| D15 | Live evaluation cadence and cap | (a) one batched live run per round, $2 cap; (b) only at release | (a): regressions found per round are cheaper than at release | 3E.4 |

## 7. Merged totals (replaces PLAN.md Round 3 line)

| Round | Hours | Plan-days | Wall-clock days | API spend | Decisions |
|---|---|---|---|---|---|
| 2B ML | 53 | 7 | 2.5 | $0 | D11, D12 |
| 3A LLM | 34 | 4 | 1.5 | under $2 | D14 |
| 3B agents | 62 | 8 | 3 | about $1 | D10 |
| 3C RAG | 29 | 4 | 1.5 | under $1 | D6 |
| 3D prototyper | 19 | 2.5 | 1 | about $2 per run | D13 |
| 3E evaluation | 29 | 4 | 1.5 | under $5 | D15 |
| **AI total** | **226** | **29.5** | **about 6 with three sessions** | **$0 by default; under $12 if the owner ever funds the live runs** | 6 |

Order inside the AI work: 3A first (everything else depends on structured outputs and the cost
records), then 3B and 3C in parallel, then 3E, then 2B after Round 2 lands, then 3D last and only if
D13 is yes.
