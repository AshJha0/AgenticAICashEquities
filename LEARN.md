# LEARN.md — A Guided Tour of the Agentic AI Cash Equities Platform

This document walks through the platform the way a textbook would: the concept first, then
how this repository implements it, always with the repository's *real, measured numbers* as
worked examples. Everything cited here can be reproduced from the committed code and seeds —
`pytest` and `ceap evaluate` regenerate every figure.

If you only have an hour, read §3 (the architectural boundary), §7 (the harness as a control
plane) and §10 (the critic and the audits) — they carry the platform's central ideas.

Contents:

1. [The problem: explaining execution quality](#1-the-problem-explaining-execution-quality)
2. [Cash-equity microstructure primer](#2-cash-equity-microstructure-primer)
3. [The architectural boundary](#3-the-architectural-boundary)
4. [The domain model](#4-the-domain-model)
5. [Deterministic TCA analytics](#5-deterministic-tca-analytics)
6. [Cause attribution](#6-cause-attribution)
7. [The harness as a control plane](#7-the-harness-as-a-control-plane)
8. [MCP as the capability interface](#8-mcp-as-the-capability-interface)
9. [The agents](#9-the-agents)
10. [The critic and the audits](#10-the-critic-and-the-audits)
11. [Policy, approvals and RBAC](#11-policy-approvals-and-rbac)
12. [RAG and enterprise knowledge](#12-rag-and-enterprise-knowledge)
13. [The synthetic market and the 50 scenarios](#13-the-synthetic-market-and-the-50-scenarios)
14. [Evaluation done honestly](#14-evaluation-done-honestly)
15. [Observability and operations](#15-observability-and-operations)
16. [Ten pitfalls this platform is built to avoid](#16-ten-pitfalls-this-platform-is-built-to-avoid)
17. [Ten interview questions (with answers from this repo)](#17-ten-interview-questions-with-answers-from-this-repo)
18. [Further reading](#18-further-reading)

---

## 1. The problem: explaining execution quality

A cash-equities desk runs algorithmic parent orders (VWAP, TWAP, IS, POV) that are sliced
into child orders and routed to venues. After the fact, transaction-cost analysis (TCA)
compares what was paid against benchmarks. When a number moves — *implementation shortfall
was 5 bps worse this hour than last* — someone has to explain why, and the candidate causes
span four very different worlds:

| World | Typical question | Evidence lives in |
|---|---|---|
| Market | Did volatility, spreads or liquidity change? | quotes, prints, order books |
| Execution | Did our algorithm or a venue behave differently? | orders, fills, venue statistics |
| Technology | Did latency, rejects or a deployment interfere? | metrics, logs, change records |
| Policy | Was this consistent with how the algo is configured? | runbooks, configuration |

Answering well means pulling from all four, computing the numbers correctly, weighing
alternatives, and producing an explanation an auditor can check. That is a natural task for
an agentic system — and a dangerous one, because a language model that is allowed to compute
numbers or invent evidence will produce confident, wrong TCA.

The MVP use case in this repository is exactly that question:

> "Analyse our AAPL execution between 14:00 and 15:00 and explain why implementation shortfall increased."

`ceap investigate` answers it in about 0.9 seconds with the offline model, over synthetic
data in which we *know* the true cause.

## 2. Cash-equity microstructure primer

**NBBO and the touch.** The best bid and ask across venues. `Quote` carries bid/ask price and
size; `mid = (bid + ask)/2`, `spread_bps = (ask − bid)/mid × 10⁴`. On a liquid US large cap the
quoted spread is a few basis points (the generator uses 1.6–2.4 bps per symbol).

**Prints and venues.** Trades print on lit exchanges (XNAS, ARCA, BATS), a speed-bump venue
(IEX) and a dark mid-point pool (DARK1). Our own fills are part of the tape.

**Depth.** An `OrderBookSnapshot` holds five price levels per side; `displayed_depth` sums
the quantity, `imbalance` is `(bid − ask)/(bid + ask)`. Thin books mean each child slice walks
further through the book.

**Parent and child orders.** A 60,000-share VWAP parent over one hour becomes 120 child slices
every 30 seconds. Each child is routed to a venue, acknowledged after some latency
(≈800 µs at p50 in the generator), and filled, partially filled, cancelled or rejected.

**Benchmarks.** Arrival (decision) price is the mid when the parent is accepted. Interval VWAP
is the volume-weighted average of all prints in the horizon. A VWAP algorithm is judged
mainly against interval VWAP because arrival-price cost carries price drift the algo cannot
control — on a 10 %-volatility name that drift is ≈30 bps per hour, which is why §5 reports
both.

## 3. The architectural boundary

Everything in this repository follows one rule, stated in `src/ceap/__init__.py` and
enforced by code in every layer:

```
LLM              = reasoning and orchestration assistance
Python analytics = deterministic computation
MCP              = capability interface
Harness          = control plane
Policy engine    = authority
Evidence         = auditability
```

Concretely:

* The model never computes a metric. `ExecutionMetrics` is produced by
  `StandardExecutionAnalytics.calculate` inside the execution MCP server; the reporter
  receives the numbers as facts.
* The model never invents evidence. Evidence ids are created by the tool adapter and by
  agents; the narrative is audited against the set that exists (§10).
* The model never controls the loop. The harness decides what runs; agents ask for tools
  through a `ToolInvoker` that routes back into the policy-gated executor (§7).
* Authority is a separate object. `RulePolicyEngine.evaluate` returns ALLOW / DENY /
  REQUIRE_APPROVAL and names the rule (§11).

The adversarial tests (`tests/adversarial/test_adversarial.py`) are the executable form of
this section.

## 4. The domain model

`src/ceap/domain/` is framework-independent: frozen dataclasses and abstract interfaces,
no MCP, no LLM SDK, no web framework.

| Object | Role | Notable design choice |
|---|---|---|
| `Task` | the user request | structured `input` (symbol, windows, dataset) parsed deterministically before any model sees the text |
| `Plan`, `PlanStep`, `StepType` | typed investigation plan | `TOOL_CALL` needs a `ToolRequest`, `AGENT_CALL` an `agent_id`; the harness validates both |
| `Tool`, `ToolMetadata`, `ToolRegistry` | a capability | metadata carries `read_only`, `risk_level`, `required_capabilities` — what policy reads |
| `Evidence`, `EvidenceType` | audit record | ids are prefixed by type (`EXEC-`, `MARKET-`, `TCA-`, `ENG-`, `LOG-`, `DOC-`, `CHANGE-`, `RISK-`) so reports read naturally |
| `Finding` | a claim | must cite supporting evidence; confidence validated to [0, 1]; `with_confidence` clamps |
| `ExecutionMetrics`, `VenueStatistics`, `MarketStatistics` | deterministic results | every field is a number a test can assert on |
| `PolicyContext`, `PolicyDecision`, `PolicyEngine` | authority | context carries principal, roles, capabilities and per-request attributes |
| `Agent`, `AgentContext`, `AgentResult`, `ToolInvoker` | the agent contract | agents receive evidence and findings so far and a tool invoker, never the registry directly |

`to_jsonable` (`domain/common.py`) is the one serialisation point: dataclasses, enums,
datetimes and NumPy scalars become JSON at the MCP and API boundaries; `serialization.py`
rebuilds `ExecutionMetrics` and `MarketStatistics` from that JSON on the agent side.

## 5. Deterministic TCA analytics

`src/ceap/analytics/` is pure functions over NumPy arrays with known-answer tests.

**Implementation shortfall** (Perold) for a buy:

```
IS = [ Σ p_i q_i − P_d · Q_filled ]  +  [ (P_final − P_d) · Q_unfilled ]
     ───────────────── execution ──────    ──────── opportunity ────────
normalised by P_d · Q_target, in bps; sells flip the sign so positive is always adverse.
```

`implementation_shortfall_bps(Side.BUY, 100, [101], [100], 100) == 100.0`, and filling half
at the decision price while the price rises 2 % costs `0.5 × 200 = 100 bps` of opportunity —
both are unit tests.

**Slippage** against arrival and against interval VWAP; **effective spread** =
`2 · sign · (fill − mid)/mid`; **market impact** split into permanent (mid at last fill vs
arrival) and temporary (execution VWAP vs mid at last fill); **realised volatility** as the
standard deviation of 1-second log returns scaled to a one-minute horizon in bps
(`3.4 bps/min` is the baseline on the synthetic data); **liquidity** as mean displayed depth
and top-of-book size; **feed quality** as the fraction of byte-identical consecutive quotes
and the count of crossed quotes.

`StandardExecutionAnalytics.calculate` composes them into `ExecutionMetrics`, including a
per-venue table (fill rate on *accepted* quantity — rejects are a technology signal and are
excluded so a venue is not blamed for a platform fault).

Worked example (scenario T06, AAPL, 14:00–15:00): execution VWAP 230.4221 vs interval VWAP
230.3858 → slippage +1.6 bps; arrival 230.8381 → IS −22.0 bps (the price fell, which is why
IS alone would mislead); fill rate 81.7 %; participation 4.7 %; reject rate 10 %;
mean ack latency 8,261 µs.

## 6. Cause attribution

`attribute_causes` compares the window with its baseline and scores eight hypotheses. Each
score is `1 − exp(−excess/scale)` of how far a signal sits beyond its threshold, so it
saturates at 1 and is 0 below threshold; a score ≥ 0.35 is *material*.

| Cause | Signal | Threshold |
|---|---|---|
| MARKET_VOLATILITY | 1-min realised vol ratio | > 1.6 |
| WIDE_SPREADS | mean quoted spread ratio | > 1.4 |
| LOW_LIQUIDITY | min(depth ratio, top-of-book ratio) | < 0.65 |
| VENUE_DEGRADATION | fill-rate gap / slippage excess / reject excess vs peers | 0.25 / 2 bps / 10 pp |
| TECHNOLOGY_LATENCY | latency ratio (fills or gateway) / reject rate | > 3 / > 5 % |
| MARKET_DATA_ANOMALY | stale-quote fraction / crossed quotes | > 3 % (and 3× baseline) / > 5 |
| LARGE_ORDER_IMPACT | participation (+0.3 if parent ≥ 4× baseline) | > 20 % |
| PRICE_MOVEMENT | adverse drift, halved when vol is elevated | > 60 bps |

Two guards matter in practice and both came out of testing: when the platform-wide reject
rate is elevated the venue rule ignores rejects (otherwise random clustering of rejects blamed
DARK1 in the latency scenario), and a price move inside a high-volatility window is
discounted because a sustained vol regime already explains the drift.

The result carries `deteriorated` (IS *or* VWAP slippage worse by ≥ 1.5 bps), a ranked list
with rationale strings, and `primary`/`secondary`. It is written to a CALCULATION evidence
record so the report can cite it.

## 7. The harness as a control plane

`AgentHarness.execute(task, policy_context, cancellation)` runs an explicit state machine:

```
CREATED → PLANNING → VALIDATING_PLAN → EXECUTING ⇄ AWAITING_APPROVAL
        → CRITIQUING → VALIDATING_EVIDENCE → FINALISING → COMPLETED  (| FAILED | CANCELLED)
```

`TRANSITIONS` in `harness/state_machine.py` is the whole legal graph; an illegal transition
raises. The history of transitions is returned with every result and exposed by the API.

What the harness guarantees regardless of what the plan says:

1. **Plan validation before execution.** Unknown tools or agents, duplicate ids and — via a
   policy pre-check of every tool call — anything DENIED fails the investigation *before* a
   single tool runs (`test_unknown_tool_in_plan_fails_validation`,
   `test_policy_denial_at_validation_time`).
2. **Governance steps.** If the plan lacks a critic, a validation or a finalise step they are
   appended (`gov-critic`, `gov-validation`, `gov-finalise`). If a plan reaches finalise
   without having run the critic, the harness runs it anyway.
3. **One path to a tool.** `StepExecutor.execute_tool` is policy → approval → retry with
   backoff → timeout bounded by the task deadline → tool → memory → trace. Agents get a
   `HarnessToolInvoker` that calls the same method with a derived step id.
4. **Bounded everything.** Per-step timeout, task deadline, retry budget, cooperative
   `CancellationToken`, and agent exceptions contained as warnings
   (`test_agent_failure_is_contained`).
5. **Concurrency where it is safe.** Consecutive independent `TOOL_CALL` steps form a batch
   run under `asyncio.TaskGroup` with a semaphore; the canonical plan's 15 tool calls execute
   as one batch.

## 8. MCP as the capability interface

`MCPServerDefinition` registers async functions as tools with a decorator; the JSON schema is
derived from the Python signature, so the in-process representation and the network
representation cannot drift. Annotations carry `readOnlyHint`, a risk level and required
capabilities. The same definition is exported to a real `FastMCP` server (`to_fastmcp()`,
`run_stdio()`), and `python -m ceap.mcp.market_data` starts it over stdio.

Five servers, 26 tools:

| Server | Tools |
|---|---|
| market_data | get_quote, get_quotes, get_order_book, get_order_book_statistics, get_trades, get_market_statistics, get_reference_data, get_coverage |
| execution | get_parent_orders, get_child_orders, get_executions, get_execution_metrics, get_venue_statistics, get_strategy_configuration |
| risk | get_position, get_exposure, check_limit, calculate_stress (MEDIUM risk) |
| engineering | search_logs, get_service_metrics, get_deployments, get_latency_metrics, get_services |
| knowledge | search_documents, get_document, list_documents |

`MCPClient` has two implementations: `InProcessMCPClient` (tests, CLI, API) and
`StdioMCPClient` (official SDK sessions to subprocesses; `tests/mcp` includes a real
round-trip). `MCPToolAdapter` makes a discovered descriptor a domain `Tool` and attaches an
evidence record — tool id, arguments, correlation id, SHA-256 digest of the payload — to
every successful call.

A deliberate choice: **analytics live next to the data**. `execution.get_execution_metrics`
computes TCA inside the server, so agents receive a few hundred bytes of metrics rather than
shipping 14,400 quotes and 22,000 prints into model context.

## 9. The agents

Each specialist agent answers one question, reads the plan's tool outputs from
`InvestigationMemory`, fetches anything missing through `ensure()` (which goes through
policy), and returns findings that cite evidence.

| Agent | Question | Produces |
|---|---|---|
| Planner | what should we investigate? | a sanitised, typed `Plan` (unknown tools/agents/arguments stripped; canonical fallback) |
| Market | what happened in the market? | volatility, spread, depth, drift and feed-quality findings vs baseline |
| Execution | what happened to our orders? | IS and VWAP-slippage deltas, fill/participation, venue peer comparison, configuration consistency (participation vs the 20 % ceiling in the runbook) |
| Quant | what does the data show and why? | the attribution result as findings, rejected hypotheses included |
| Risk | unusual exposure or limit usage? | limit utilisation/breach and post-window exposure |
| Engineering | did technology behave normally? | latency ratio and SLO, deployments in scope, error logs, feed gaps |
| Critic | are the conclusions supported? | adjusted findings and a critique (§10) |
| Reporter | write it up | `InvestigationReport` with narrative and audits |

Findings are deliberately produced for *normal* states too ("Execution latency remained
within the normal range (1.02× baseline)") because a report that only lists anomalies cannot
say what was ruled out.

## 10. The critic and the audits

The critic is the platform's independent reviewer and it is mostly deterministic so a model
cannot talk it out of its job:

1. Every cited evidence id must resolve; otherwise confidence → 0.1 and the finding is marked
   unsupported.
2. A finding that asserts an anomaly (`anomaly=WIDE_SPREADS`, say) must be corroborated by
   the attribution score; otherwise confidence drops by 0.3 and the attribution record is
   attached as *contradicting* evidence.
3. Cross-agent contradictions are flagged: quant attributing to technology while engineering
   reports normal latency and no errors caps the quant finding at 0.4.
4. Single-evidence findings are capped at 0.7.
5. An optional LLM critique then runs — and may only **lower** confidence
   (`test_critic_with_llm_can_only_lower_confidence`).

After the critic, `VALIDATING_EVIDENCE` drops any finding whose evidence still does not
resolve and records why.

The reporter then hands *structured facts* to the model and audits what comes back:

* **Number audit** — every number in the narrative must match some fact value (allowing for
  rounding, `×100` percentage forms and `(ratio − 1) × 100`); list numbering is ignored.
* **Evidence-id audit** — every `PREFIX-xxxxxxxx` token must exist.

`test_fabricated_numbers_and_evidence_ids_are_flagged` feeds the reporter a narrative
claiming "0.0000 bps", "99.9 %" and "EXEC-deadbeef" and asserts all are flagged while the
deterministic report fields are untouched.

## 11. Policy, approvals and RBAC

`RulePolicyEngine` evaluates ordered rules; the first DENY wins, then REQUIRE_APPROVAL,
otherwise ALLOW, and the evaluation names the rule:

| Rule | Effect |
|---|---|
| denied-tools | explicit deny list |
| read-only | mutating tools need `tools:write`; reading needs `tools:read` |
| required-capabilities | tool's declared capabilities ⊆ principal's |
| argument-guard | page limits ≤ 5,000; no string argument over 2,000 chars |
| symbol-universe | symbol must be in the permitted universe (global or per request) |
| risk-level | CRITICAL never; HIGH needs `risk:high` or approval; MEDIUM needs `risk:medium` or approval |

Roles map to capabilities (viewer, trader, quant, engineer, admin). Approval gateways:
`AutoApprovalGateway` (dev, records what it approved), `QueuedApprovalGateway` (parks the step,
transitions the harness to `AWAITING_APPROVAL`, resolved via `POST /approvals/{id}` or
timeout) and `DenyApprovalGateway`. A denied approval marks the step DENIED and the
investigation continues without it.

## 12. RAG and enterprise knowledge

Eight Markdown documents under `data/reference/knowledge/` — VWAP runbook, TCA methodology,
venue configuration, trading limits, execution policy, market-data architecture, incident
runbook, algorithm documentation — are chunked by heading and paragraph (34 chunks) and
embedded with a dependency-free hashed TF-IDF embedder (feature hashing, sublinear TF, corpus
IDF, L2-normalised). Retrieval is cosine similarity.

The knowledge base is an MCP server, so a retrieved passage is a DOCUMENT evidence record
like any other tool result. The canonical plan searches for the VWAP configuration and
escalation policy; the execution agent compares observed participation with the configured
ceiling and cites both the configuration tool output and the runbook; the report gets a
POLICY CONTEXT section.

## 13. The synthetic market and the 50 scenarios

`SyntheticMarketGenerator` builds a seeded dataset per scenario over 12:00–16:00 London:

* mid price: GBM at 10 % annualised vol sampled every second (≈0.49 bps/s), with the
  scenario's volatility multiplier, trend and participation-driven impact drift applied only
  inside 14:00–15:00;
* NBBO: log-normal spread noise around a per-symbol base, tick-rounded, log-normal sizes;
* prints: Poisson(1.5/s), log-normal sizes, five venues with fixed weights, our fills added;
* books: five levels every 10 s scaling from top-of-book;
* orders: a 60k VWAP parent in 13:00–14:00 (baseline) and one in 14:00–15:00 (window), 120
  slices each, fills that cross the spread and walk the book by an amount driven by slice
  size vs displayed liquidity, venue degradation and latency;
* engineering: per-minute gateway/router/feed metrics, heartbeat and anomaly logs,
  deployments; risk: positions and limits.

Ten templates × five symbols with distinct seeds give 50 scenarios, each with structured
ground truth. Because effects are injected only in the window, the previous hour is always a
clean baseline — which is also how the attribution model is defined.

## 14. Evaluation done honestly

`ceap evaluate` runs the full platform over all 50 scenarios and reports:

```
scenarios            50
primary_accuracy     1.00
coverage             1.00
false_positive_rate  0.02
completed            1.00
unresolved_findings  0
number_warnings      0
mean_duration_ms     ~850
```

Things to be honest about:

* These numbers are with the deterministic mock model. The analytics, attribution, critic
  checks and audits are identical with a real model; what changes is plan quality (the
  planner reports rejected steps) and narrative fidelity (the audits report warnings).
* The one false positive is S50 (META, mixed market + technology): a large drift inside a
  3× volatility window scored PRICE_MOVEMENT as secondary. It is defensible and it is
  reported rather than tuned away.
* Thresholds are calibrated on synthetic data. On real TCA history they must be
  recalibrated and versioned like code.
* Hourly IS carries ≈30 bps of drift noise at 10 % vol; that is why `deteriorated` also
  considers VWAP slippage and why the executive summary distinguishes "headline cost improved
  because the price moved" from "execution quality was fine".

## 15. Observability and operations

* `InMemoryTracer` records nested spans (`harness.execute`, `harness.plan`, `tool:*`,
  `agent:*`) with events for policy decisions, retries and approvals; the API returns them
  under `/investigations/{id}/trace`. Set `OTEL_EXPORTER_OTLP_ENDPOINT` and the same spans
  mirror to OpenTelemetry.
* `MetricsRegistry` exposes counters and histograms in Prometheus text format at `/metrics`
  (`ceap_tool_calls_total{tool,status}`, `ceap_agent_duration_ms`, …).
* Logging is JSON lines with task/execution/step/tool/agent ids.
* `HarnessResult` carries the state history, policy log, step results and warnings, so a
  failed or degraded investigation explains itself.

## 16. Ten pitfalls this platform is built to avoid

1. **Letting the model do arithmetic.** Numbers come from `ceap.analytics`; the narrative is
   audited against them.
2. **Findings without evidence.** Validation drops them; the critic flags them first.
3. **Agents that control the loop.** The harness runs the state machine; agents return
   results.
4. **Tools outside the catalogue.** The planner strips them; the harness fails the plan if
   any survive.
5. **Prompt injection via the question.** The question is passed as JSON data with an
   explicit instruction; the catalogue and policy make escape impossible anyway.
6. **A critic that can be flattered.** Deterministic checks first; the model can only lower
   confidence.
7. **Blaming a venue for a platform fault.** Venue fill rate uses accepted quantity; the
   reject rule is disabled when platform rejects are elevated.
8. **Judging a VWAP algo on arrival price alone.** Both IS and VWAP slippage drive the
   deterioration flag; the summary explains drift.
9. **Silent tool failure.** Failures are step results and warnings; agents fetch missing
   data through policy or state explicitly that nothing was found.
10. **An n/a report for an unanswerable request.** Symbol, window, baseline ordering and
    dataset coverage are validated up front and return 422.

## 17. Ten interview questions (with answers from this repo)

1. *How do you stop an LLM agent from fabricating TCA numbers?* — Compute them in
   `StandardExecutionAnalytics`, pass them as facts, and audit the narrative
   (`audit_numbers`) so any untraceable number is a warning on the report.
2. *What is implementation shortfall and why can it mislead?* — Perold's paper-vs-actual
   cost; it includes price drift, ≈30 bps/hour at 10 % vol, so a VWAP algo can show negative
   IS in a bad hour. Compare with interval-VWAP slippage.
3. *How does the harness prevent a runaway agent?* — State machine with a fixed transition
   graph, per-step timeout bounded by a task deadline, retry budget, cancellation token, and
   agent exceptions contained as warnings.
4. *Why MCP rather than direct function calls?* — A capability interface with schemas and
   annotations that policy can read, the same definition served in-process and over stdio,
   and evidence with a payload digest on every call.
5. *How does policy differ from validation?* — Validation checks structure; policy is
   authority: read-only, capabilities, argument guards, symbol universe, risk level, with the
   firing rule recorded. It runs at plan validation and again on every call.
6. *What does the critic actually check?* — Evidence resolution, corroboration by
   attribution, cross-agent contradictions, thin evidence; the LLM pass can only lower
   confidence.
7. *How would you attribute a deterioration to a venue?* — Peer comparison of fill rate on
   accepted quantity and slippage excess; ignore rejects when they are platform-wide.
8. *How is the evaluation ground truth defined?* — Each scenario template injects a known
   effect inside the window only; the spec lists the causes; the suite asserts primary
   accuracy, coverage, false positives, completion, unresolved evidence and audit warnings.
9. *Where would RAG go wrong here?* — Trusted-content risk: retrieved passages influence the
   narrative, so the corpus is controlled and passages are evidence, not instructions.
10. *What changes for a real model?* — Only plan quality and narrative fidelity; both are
    measured by the same run (`rejected_steps`, `narrative_number_warnings`).

## 18. Further reading

* Perold, A. (1988). *The Implementation Shortfall: Paper versus Reality.* Journal of
  Portfolio Management.
* Almgren, R. & Chriss, N. (2000). *Optimal Execution of Portfolio Transactions.*
* Kissell, R. (2013). *The Science of Algorithmic Trading and Portfolio Management.*
* Harris, L. (2003). *Trading and Exchanges: Market Microstructure for Practitioners.*
* Model Context Protocol specification — https://modelcontextprotocol.io
* This repository: `docs/architecture/overview.md`, `docs/threat-model/threat-model.md`,
  `docs/evaluation/evaluation.md`, `docs/api/api.md`, `docs/DIAGRAMS.md`,
  `docs/SPECIFICATION.md`, `COOKBOOK.md`.
