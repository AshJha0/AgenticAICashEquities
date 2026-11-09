# LEARN.md — A Guided Tour of the Agentic AI Cash Equities Platform

This document walks through the platform the way a textbook would: the concept first, then
how this repository implements it, always with the repository's *real, measured numbers* as
worked examples. Everything cited here can be reproduced from the committed code and seeds —
`pytest` and `ceap evaluate` regenerate every figure.

If you only have an hour, read §3 (the architectural boundary), §7 (the harness as a control
plane) and §10 (the critic and the audits) — they carry the platform's central ideas.

**Learning path (0.9.0).** Three passes, each building on the last:

1. *Orientation (about 2 hours).* §1-§4, then [the glossary](docs/glossary.md) for any term you
   do not know, then the five [ADRs](docs/adr/README.md): they are the decisions the rest follows.
2. *Mechanics (about a day).* §5-§12 with the code open beside them; do exercises 1-6 in
   [docs/exercises.md](docs/exercises.md). Read [the test pyramid](docs/testing/test-pyramid.md)
   before changing anything.
3. *Research and extension (a few days).* §13-§18b, [COOKBOOK.md](COOKBOOK.md), then
   [how to extend](docs/how-to-extend.md) and exercises 7-9.

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
17. [Knowledge check (ten questions, answered from this repo)](#17-knowledge-check)
18. [Stage 2: signal research](#18-stage-2-signal-research)
    (18a. [Paper trading](#18a-paper-trading-and-the-forward-record) ·
    18b. [Running an execution, not only measuring it](#18b-running-an-execution-not-only-measuring-it-080))
19. [Further reading](#19-further-reading)

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

**Implementation shortfall** (Perold, with a delay leg and fees since 0.5.0) for a buy, in bps
of the decision notional `P_d · Q`; sells flip the sign so positive is always adverse:

```
delay        s (P_arr − P_d) · Q             decision → arrival (90 s in the generator)
spread       s Σ q_i (p_i − m_i)             each fill against the mid prevailing at it
timing       s Σ q_i (m_i − P_arr)           drift from arrival to each fill
opportunity  s (P_end − P_arr) · Q_unfilled  priced when the order's life ends
fees         Σ q_i f_i                       commission + access fee − maker rebate
```

The legs sum to the total to 1e-9, and `implementation_shortfall_bps` (no delay or fees)
is still the classic two-leg version; both are unit tests.

**How noisy is one order?** For a linear schedule over `T` session-days the price-risk
standard error is `σ_daily · sqrt(T/3)`, about 14 bps for one hour at 10 % annual volatility.
The 1.5 bps deterioration trigger of v0.4 sat deep inside that noise. Now an order is
*deteriorated* only when its shortfall exceeds a **pre-trade estimate** by 1.96 standard errors.
The estimate comes from a fitted square-root model, `c0 + c1 · spread + b · σ · sqrt(Q/ADV)`,
with conformal intervals and a model card (`docs/quant/pretrade_model_card.md`).

**Slippage** against arrival and interval VWAP (auction and late prints excluded);
**size-weighted effective and realised spreads**, with effective = realised + price impact;
**price improvement** against the far touch; **markouts** at 1 s, 10 s, 60 s and 5 min
(beta-adjusted against an index where one exists). `market_impact_bps` is the *expected*
square-root impact `c · σ_daily · sqrt(Q/ADV)`: one order's drift is noise, not impact.
**Realised volatility** is the standard deviation of 1-second log returns scaled to one minute;
**liquidity** is mean displayed depth and top-of-book size; **feed quality** is the fraction of
byte-identical consecutive quotes and the count of crossed quotes.

`StandardExecutionAnalytics.calculate` composes them into `ExecutionMetrics`, including a
per-venue table (fill rate on *accepted* quantity — rejects are a technology signal and are
excluded so a venue is not blamed for a platform fault).

Worked example: run `ceap investigate --dataset T06` and read the execution agent's findings.
It prints the IS by leg, the pre-trade estimate and residual z, and the markouts.
`ceap tca-report` runs the same analytics over a 20-session, 800-parent universe and compares
brokers and algos on the residual against the pre-trade estimate, never on raw shortfall.

## 6. Cause attribution

`attribute_causes` compares the window with its baseline and scores eight hypotheses. The
baseline is the **same clock window on the previous five sessions**, because intraday
volume and volatility are U-shaped and the hour before is not like the hour after. Each
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

The result carries `deteriorated` (residual z ≥ 1.96 against the pre-trade estimate; the old
1.5 bps delta is only a fallback), a ranked list with rationale strings, `primary`/`secondary`,
a bps decomposition by Perold leg, and for each cause a peer-relative effect with a 90 %
bootstrap CI. The peer effect measures how unusual the order is; it does not claim a cause. It is written to a CALCULATION evidence
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

Eight servers, 37 tools — every one read-only except `execution.stage_orders` (Stage 2):

| Server | Tools |
|---|---|
| market_data | get_quote, get_quotes, get_order_book, get_order_book_statistics, get_trades, get_market_statistics, get_reference_data, get_coverage |
| execution | get_parent_orders, get_child_orders, get_executions, get_execution_metrics, get_venue_statistics, get_strategy_configuration, **stage_orders** (HIGH risk, `trading:execute`, not read-only — paper orders only), get_staged_orders |
| risk | get_position, get_exposure, check_limit, calculate_stress (MEDIUM risk), get_portfolio_exposure, check_portfolio_limits, calculate_portfolio_stress (MEDIUM risk) — the last three recompute the target portfolio |
| engineering | search_logs, get_service_metrics, get_deployments, get_latency_metrics, get_services |
| knowledge | search_documents, get_document, list_documents |
| research_data *(Stage 2)* | get_universe, get_daily_bars, get_universe_summary |
| alpha *(Stage 2)* | list_signals, evaluate_signal |
| backtest *(Stage 2)* | run_backtest |

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
`InvestigationMemory` through `fetched()`, and returns findings that cite evidence, down to the
attribute (`evidence_refs`). An agent never fetches for itself: the plan chooses the agents, and
the harness adds the data each chosen agent declares in `ceap/agents/requirements.py`, so every
fetch passes plan validation and policy. If a fetch fails the agent fails loudly and the harness
re-plans once.

The model decides scope, within enumerated choices (which agents and optional tools, which of
three baselines, how to rank the pre-registered hypotheses); deterministic code decides truth (the
hypothesis verdicts, and whether a critic dispute holds). Ask yourself why the critic model is not
allowed to lower a confidence even though lowering looks "safe": an injected critic could erase a
real finding, so a dispute must name a check the platform can run.

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
   the attribution score; otherwise confidence drops by 0.3 and the attribution record is <!-- source: src/ceap/agents/critic.py -->
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
* **Citation audit (0.8.0)** — a policy claim needs a verified citation on its own sentence.

Since 0.8.0 the model is asked to name figures as typed placeholders (`{fact.key}`) that code
renders with their units, so most numbers never pass through the model at all. The free-text audit
remains for anything else and is stricter: unit-matched conversions only, no sign flip unless a
direction word agrees, and no small-integer exemption. Prose that fails any audit is **not shipped**:
the report carries the deterministic template narrative and the degradation
`LLM_NARRATIVE_SUPPRESSED`, and the rejected prose goes only to the audit log.

`test_fabricated_numbers_and_evidence_ids_are_flagged` feeds the reporter a narrative
claiming "0.0000 bps", "99.9 %" and "EXEC-deadbeef" and asserts all are flagged while the
deterministic report fields are untouched; since 0.8.0 the adversarial tests also assert that the
flagged prose is suppressed.

## 11. Policy, approvals and RBAC

`RulePolicyEngine` evaluates ordered rules; the first DENY wins, then REQUIRE_APPROVAL,
otherwise ALLOW, and the evaluation names the rule:

| Rule | Effect |
|---|---|
| denied-tools | explicit deny list |
| untrusted-server (0.8.0) | every tool of an MCP server outside `CEAP_MCP_TRUSTED_SERVERS` is denied, whatever it declares |
| agent-allowlist (0.8.0) | each agent has its own capability set and tool allowlist (`ceap.policy.agents`), intersected with the principal's |
| read-only | mutating tools need `tools:write`; reading needs `tools:read` |
| required-capabilities | tool's declared capabilities ⊆ principal's |
| argument-guard | page limits ≤ 5,000 and a whole number; strings ≤ 2,000 chars, ≤ 1,000 items, depth ≤ 8, at every level of the argument tree |
| input-schema (0.8.0) | arguments validate against the tool's JSON schema |
| symbol-universe | every symbol-bearing field (lists, nested objects, `x-ceap-symbol` properties) must be in the permitted universe; with no universe configured, a request that names a symbol is denied (fail closed, 0.8.0) |
| risk-level | CRITICAL never; HIGH always needs a human approval (no capability skips it since 0.8.0); MEDIUM needs `risk:medium` or approval |

Four-eyes binds to a person: an API key mapped with `subject=NAME` acts as `user:NAME`, and an
admin cannot decide approvals at all, so an admin's HIGH-risk request waits for a separate
approver. Diagram: [docs/DIAGRAMS.md §13](docs/DIAGRAMS.md#13-policy-and-approval-flow-080).

Roles map to capabilities (viewer, trader, quant, engineer, admin). Approval gateways:
`AutoApprovalGateway` (dev, records what it approved), `QueuedApprovalGateway` (parks the step,
transitions the harness to `AWAITING_APPROVAL`, resolved via `POST /approvals/{id}` or
timeout) and `DenyApprovalGateway`. A denied approval marks the step DENIED and the
investigation continues without it.

Since 0.5.0 every approval is a row in SQLite and a decision is one conditional statement,
`UPDATE approvals SET status=? WHERE id=? AND status='PENDING'`. Whoever's update matches the row
wins; the other approver, or anyone deciding after a timeout or a restart, matches nothing and gets
409. That is the standard way to make "exactly one winner" hold across threads and processes
without a lock. The staging approval also carries a hash of the portfolio the approver saw, and
staging recomputes it and refuses on a mismatch: a time-of-check / time-of-use gap closed by
binding the approval to content, not to a step name. Reads are owner-scoped; the `auditor` role
reads everything and changes nothing.

## 12. RAG and enterprise knowledge

Ten documents make up the corpus: nine Markdown files under `data/reference/knowledge/` (VWAP
runbook, TCA methodology, venue configuration, trading limits, execution policy, market-data
architecture, incident runbook, algorithm documentation, attribution thresholds) plus
`docs/quant/quant.md` through `manifest.json`. Since 0.6.0 they are chunked by section (65 chunks
with the default `section` strategy) and searched with Okapi BM25 from the standard library; each
document carries a version and classification, and the corpus has one hash. The hashed TF-IDF
embedder of 0.5.x and earlier is gone.

The knowledge base is an MCP server, so a retrieved passage is a DOCUMENT evidence record
like any other tool result. The canonical plan searches for the VWAP configuration and
escalation policy; the execution agent compares observed participation with the configured
ceiling and cites both the configuration tool output and the runbook; the report gets a
POLICY CONTEXT section.


**Retrieval you can audit (R3C).** Retrieval here is keyword search (BM25), measured on a held-out
query set, not "semantic search". Three ideas carry over to any RAG system:

- *Let code write the query.* The model plans; the harness builds the retrieval query from the
  parsed question (domain words only), the symbol, the window and the cause candidates. A model
  cannot steer retrieval, and an injected sentence cannot become a tool argument.
- *Filter by who is asking, twice.* The planner pins the classifications a principal may read, and
  the policy engine denies a request that asks for more. Defaults are least privilege.
- *Check every quote.* The writer cites document blocks; code compares each quoted span with the
  exact chunk that was sent and only then records it as evidence, with the document version and the
  corpus hash. "No policy found" is a valid answer; a confident citation of nothing is not.

## 13. The synthetic market and the 50 scenarios

`SyntheticMarketGenerator` builds a seeded dataset per scenario over 12:00 to 16:00
America/New_York (regular session 09:30 to 16:00), plus the same 14:00 to 15:00 window on the
five previous sessions:

* mid price: a log random walk at 10 % annualised volatility on a one-second grid, scaled by
  a U-shaped intraday profile, with the scenario's volatility multiplier and trend inside
  14:00 to 15:00 and a permanent impact drift `0.7 · σ · sqrt(Q/ADV)` for every parent;
* NBBO: log-normal spread noise around a per-symbol base, tick-rounded, log-normal sizes;
* prints: U-shaped Poisson arrivals, odd lots flagged, a few late prints, a 16:00 closing
  auction print, five venues, our fills added;
* books: five levels every 10 s, scaling from top of book;
* orders: 60k VWAP parents, BUY or SELL, decided 90 s before arrival; children are aggressive
  (far touch, access fee) or passive (near touch, rebate), with odd lots and duplicate
  timestamps;
* engineering: per-minute gateway, router and feed metrics, heartbeat and anomaly logs,
  deployments; risk: positions and limits.

Ten templates × five symbols with distinct seeds give 50 scenarios, each with structured
ground truth. Because effects are injected only in the window, the previous hour is always a
clean baseline — which is also how the attribution model is defined.

## 14. Evaluation done honestly

`ceap evaluate` runs the full platform over all 50 scenarios and reports:

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

**Since 0.6.0: one report, and the rest labelled.** `ceap evaluation-report` prints capability
(the numbers above plus a frozen 45-item golden set), safety (governance invariants under injected
faults), robustness (which degradation rung each fault hit) and cost (a labelled local estimate).
Everything that needs a real model (planner quality, injection resistance, narrative faithfulness,
refusal rate, latency, cache hit rate, run-to-run variance) prints `UNKNOWN / NOT RUN LIVE`. The
docs check (`scripts/check_docs.py`) fails CI when a number in the docs does not trace to the
committed report, and drift monitors fail when cassettes, pins, the SDK or the golden set move.
See `docs/evaluation/evaluation.md`.

## 15. Observability and operations

* `InMemoryTracer` records nested spans (`harness.execute`, `harness.plan`, `tool:*`,
  `agent:*`) with events for policy decisions, retries and approvals; the API returns them
  under `/investigations/{id}/trace`. Set `OTEL_EXPORTER_OTLP_ENDPOINT` and the same spans
  mirror to OpenTelemetry.
* `MetricsRegistry` exposes counters and histograms in Prometheus text format at `/metrics`
  (`ceap_tool_calls_total{tool,status}`, `ceap_agent_duration_seconds`, …; durations are exported in seconds).
* Logging is JSON lines with task/execution/step/tool/agent ids.
* `HarnessResult` carries the state history, policy log, step results and warnings, so a
  failed or degraded investigation explains itself.
* Durable state is SQLite behind one writer thread (WAL, forward-only migrations, backup before
  migrate). The audit log is a hash chain: each row's hash covers the previous hash and the row's
  canonical bytes, so editing or deleting any row breaks `ceap db verify-chain` from that row on.
  A plain hash chain only proves consistency: anyone who can write the file can rewrite a row and
  recompute every later hash. Since 0.8.0 the link is HMAC-SHA256 under `CEAP_AUDIT_HMAC_KEY`,
  which never lives in the database, and `ceap db anchor` writes the chain heads to a file you commit
  to git, so `ceap db verify-chain --anchor FILE` also catches a rewritten or cut-off tail
  ([diagram](docs/DIAGRAMS.md#14-audit-chain-and-external-anchor-080)).
* CPU-heavy work (request validation, dataset generation) runs off the event loop since 0.8.0, and
  under the API server CPU-bound tools run in a killable process pool, so a step timeout really
  stops the work. The LLM budget and a per-principal rate limit are on by default.
* Every request gets an `X-Request-ID` that appears on every log line it causes. `/metrics`
  follows Prometheus conventions (`_total` counters, seconds, HELP on every family) and needs a key.
* A replay bundle captures everything a task consumed; `ceap replay` re-runs it with an injected
  clock and id source and must reproduce the stored result byte for byte, which turns "it gave a
  different answer yesterday" into a diff.

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
11. **A plan that schedules its own approval, or the tool it's meant to gate.** (Stage 2) The
    harness strips any planner-supplied `HUMAN_APPROVAL` and appends its own; the planner is
    shown only read-only tools, so it cannot schedule `stage_orders` even if it tried.

## 17. Knowledge check

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

## 18. Stage 2: signal research

Everything above answers *"why did this execution behave the way it did?"* over a known window.
Stage 2 (since v0.3.0) answers a different question with the same architecture — *"does this
trading signal actually work, and should we trust it with money?"*:

> "Does 12-1 momentum work on this universe? Evaluate it out-of-sample with costs and propose
> whether to promote it."

The reused parts are load-bearing, not cosmetic: the same `AgentHarness` state machine, the same
policy engine, the same evidence model, the same `HUMAN_APPROVAL` step type. What's new is a
second set of deterministic analytics and a second agent roster.

**The research pipeline mirrors the TCA one exactly.** Universe/signal/backtest/portfolio tool
calls run first (their outputs become evidence, same as market data); Research, Alpha, Backtest
and Portfolio-risk agents interpret them (same pattern as Market/Execution/Quant); the Critic
computes a deterministic verdict — `assess_research` scores `PROMOTE`/`REJECT`/`INCONCLUSIVE` (the third since v0.8.0) with flags
(`NO_ALPHA`, `OOS_DECAY`, `COST_DRAG`, `CONCENTRATION`, `LIMIT_BREACH`) exactly the way
`attribute_causes` scores execution-quality causes — and caps any agent finding that contradicts
it; the Reporter writes the proposal and the same number/evidence-id audits run against it.

**What's genuinely new is the governance tail.** An investigation ends at a report. A research
proposal ends at a decision a human has to make, so the harness appends a `HUMAN_APPROVAL` step
after the critic — the approver sees the deterministic verdict and the *recomputed* target
portfolio, never the model's prose. If the caller also asked to stage paper orders (admin only,
requires `trading:execute`), a *second*, separately-decided approval gates the platform's only
non-read-only tool, `execution.stage_orders`. Declining the first approval fails the task with no
report; declining the second still produces the proposal, just without staged orders. Neither
approval step can be scheduled by the plan — the harness strips any planner-supplied
`HUMAN_APPROVAL` and appends its own, the same way it owns the critic/validation/finalise tail for
investigations (§7).

**The maths doesn't trust itself either.** `signal_statistics` uses one-day, non-overlapping ICs
specifically so the t-statistic isn't inflated by autocorrelated overlapping horizons — a subtle
enough error that it would have made every no-alpha scenario look significant. `backtest` charges
a transaction cost (half-spread plus square-root impact on ADV participation) and reports gross
*and* net Sharpe so cost drag is visible, not absorbed into a single number. `portfolio_risk`
*recomputes* the target portfolio from the signal at the report date rather than reading the
backtest's last weights — the same "don't trust a derived number, recompute it" instinct that
makes venue fill rate use accepted quantity in §5.

**The synthetic data plays the same trick as §13, one level up.** `HistoricalGenerator` embeds a
scenario's premium by computing the *same signal function* the analytics later measure it with —
so the generator and the measurement can't silently drift into disagreement — over a seeded
30-name, 20-year daily universe. *(0.4.0 changed this: ground truth now comes from the generator's
parameters, never from running the analytics, and fills happen at the next session instead of the
same close. Under that honest timing the reversal and mixed templates flip from `PROMOTE` to
`REJECT`/`COST_DRAG`; see the README table and the CHANGELOG.)* Ten templates × three seeds give 30
scenarios: real premia (`PROMOTE`), pure noise (`REJECT`/`NO_ALPHA`), a premium that vanishes
out-of-sample (`REJECT`/`OOS_DECAY`, called `OVERFIT` before 0.4.0), premia eaten by costs
(`REJECT`/`COST_DRAG`), a premium concentrated in four illiquid names (`REJECT`/`CONCENTRATION` +
`LIMIT_BREACH`), and near-threshold cases that sit right at a gate.

**Evaluated honestly, the same way as §14.** On the 0.3.x 21-scenario set (before the 0.4.0 re-baseline), with the mock model: 21/21 verdicts, 21/21 expected <!-- source: CHANGELOG.md -->
flags covered. Against a real model for the first time — `claude-haiku-4-5` as both planner and
reporter — verdict accuracy dropped to 19/21: five scenarios came back `INCOMPLETE_ANALYSIS` <!-- source: CHANGELOG.md -->
because the model's plan skipped steps the canonical plan always includes. That's the platform
working as designed (no crash, a conservative verdict, nothing unresolved) exposing a genuinely
weak planner, not a platform bug. Giving the planner a stronger model
(`CEAP_LLM_PLANNING_MODEL=claude-sonnet-5`) while keeping Haiku as the cheap narrator/critic fixed
it completely: 21/21 again. Running against a real model also caught two bugs the offline mock had
been silently hiding — a `temperature` parameter the installed SDK no longer accepts (every "live"
call had been falling back to the mock without raising), and a narrative audit stricter than a
real model's legitimate arithmetic. See `docs/evaluation/evaluation.md` and `CHANGELOG.md` 0.3.1
for the numbers; the lesson generalises past this project: *an offline mock proves the control
plane works, not that the model integration does — check both.*

On the 30-scenario set, with the mock model, `ceap evaluation-report` gives research verdict
accuracy <!-- metric: capability.research_verdict_accuracy -->0.8333 (25/30; evidence class: mock,
source `docs/evaluation/latest.json`). It was 29/30 in 0.7.1; v0.8.0 made the test harder on
purpose: the premia were recalibrated to published moments, the IC hurdle became t >= 3.0 for a
searched result, and a third verdict `INCONCLUSIVE` covers "the data cannot tell no edge from a
tradable edge". By class: 21/24 clear and 2/6 near-threshold, each at or above its a-priori bar <!-- source: CHANGELOG.md -->
(synthetic; `docs/quant/real-data-and-paper.md`). That number measures the deterministic pipeline; real-model
verdict accuracy on this set is UNKNOWN / NOT RUN LIVE (D15).

## 18a. Paper trading and the forward record

**The plain idea.** A backtest replays the past with rules decided after looking at it. A paper
record writes each day down on that day, before the next one exists, so it cannot be tuned after
the fact. It is still not trading: fills are simulated and the data are synthetic or free.

**Formally.** Each session the book runs: splits; financing `cash x (rf + s)`, where `s` is the
financing spread and applies only when cash is negative; borrow `|short value| x b_tier`; mark to
the close; then fills of yesterday's orders, at most `0.10 x ADV` shares, at cost
`|x| p (h + 0.6 sigma sqrt(|x|/ADV)) / 1e4`. Equity changes by exactly
`mark P&L + financing - borrow - costs`. After `T` days the Sharpe has standard error
`sqrt((1 + SR_d^2/2)/T)` (daily units). Telling a true Sharpe of 0 apart from an in-sample Sharpe `S`
<!-- source: tests/unit/paper/test_forward_and_ledger.py --> (alpha 0.05, power 0.8) needs `T = (1.96 + 0.84 sqrt(1 + s^2/2))^2 / s^2` days, where
`s = S/sqrt(252)`. For S = 1 that is 1980 sessions, about eight years.

**Worked example** (checked by `test_two_name_three_day_ledger_by_hand`). Start with $1,000,000.
Buy 1,000 AAA at $100 and short 500 BBB at $50. Both are 1 % of ADV, so the cost is
5 bps + 0.6 x 100 x 0.1 = 11 bps: $110 + $27.50. Cash is $924,862.50 and equity is $999,862.50.
Next day, at rf = 1 bp, cash earns $92.49 and the BBB borrow (10 % a year) costs $9.92. AAA rises to
102 and BBB falls to 49, so the marks gain $2,500. Equity is $1,002,445.07.

**Where this leaves the forward record today.** The record runs on synthetic data (feed
`synthetic` in `config/paper.json`), so it tests operations and controls, not profitability. The
scheduled job is enabled (repository variable `PAPER_DAILY_ENABLED=true`, set 2026-11-08) and the
first forward session is 2026-11-09. The real Stooq feed is written but disabled: `config/paper.json`
stays on the synthetic feed, no Stooq data is in the repository (the cache is git-ignored), it must refuse the unspent R5
pseudo-holdout (to 2025-12-31), and 12-1 momentum needs a year of history after that, so January
2027 is the earliest real-price start. Even then, at 1980 sessions for S = 1, the record cannot
settle a Sharpe question for years; read it for breaks, blocks and reconciliation, not for alpha.

## 18b. Running an execution, not only measuring it (0.8.0)

Sections 5 and 6 measure an execution after the fact. Up to 0.7.1 the synthetic market also gave
passive orders free fills at the touch and planted impact as a drift chosen in advance, so nothing
the platform did could move the price. The `ceap.execution` package (0.8.0) closes that gap, still on
synthetic data only:

* a limit order book where a resting order fills only when the market trades through the queue
  ahead of it, with latency and informed flow that makes passive fills adversely selected;
* impact caused by the bot's own fills (a propagator kernel), which can be fitted from markouts and
  compared with the truth the simulator used;
* one `Schedule` interface for TWAP, VWAP, POV and Almgren-Chriss, child-order logic, an auction
  uncross, and the tick, round-lot and LULD rules;
* a predicted-versus-realised cost loop that stores residuals, recalibrates weekly and raises a drift
  alert, and markout curves by venue with bootstrap intervals.

Start with [docs/execution/execution.md](docs/execution/execution.md) (plain idea, formal statement
and a worked example per part) and COOKBOOK recipes 44-48; the data flow is in
[docs/DIAGRAMS.md §15](docs/DIAGRAMS.md#15-execution-package-080). Not done yet: paper MOC caps, money
as integer micro-dollars, dividends and the sigma fat-finger band (0.9.0).

## 19. Further reading

* Perold, A. (1988). *The Implementation Shortfall: Paper versus Reality.* Journal of
  Portfolio Management.
* Almgren, R. & Chriss, N. (2000). *Optimal Execution of Portfolio Transactions.*
* Kissell, R. (2013). *The Science of Algorithmic Trading and Portfolio Management.*
* Harris, L. (2003). *Trading and Exchanges: Market Microstructure for Practitioners.*
* Model Context Protocol specification — https://modelcontextprotocol.io
* This repository: `docs/architecture/overview.md`, `docs/threat-model/threat-model.md`,
  `docs/evaluation/evaluation.md`, `docs/api/api.md`, `docs/DIAGRAMS.md`,
  `docs/SPECIFICATION.md`, `COOKBOOK.md`.
