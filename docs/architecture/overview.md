# Architecture Overview

## 1. Objective

Build an enterprise-style Agentic AI platform that can investigate, analyse and explain
cash-equity trading and market behaviour, starting with the MVP use case:

> "Analyse our AAPL execution between 14:00 and 15:00 and explain why implementation shortfall increased."

## 2. Guiding principle

```
LLM            = reasoning and orchestration assistance
Python analytics = deterministic computation
MCP            = capability interface
Harness        = control plane
Policy engine  = authority
Evidence       = auditability
```

The LLM writes the explanation; the numbers and evidence originate from deterministic systems.
Every layer below enforces a piece of that principle in code.

## 3. Layers

### 3.1 Domain (`ceap.domain`)

Framework-independent frozen dataclasses and abstract interfaces: `Task`, `Plan`, `PlanStep`,
`StepType`, `Agent`, `AgentContext`, `AgentResult`, `Tool`, `ToolMetadata`, `ToolRequest`,
`ToolResult`, `ToolRegistry`, `Evidence`, `EvidenceType`, `Finding`, `Quote`, `Trade`,
`OrderBookSnapshot`, `MarketStatistics`, `Order`, `Execution`, `ExecutionMetrics`,
`VenueStatistics`, `PolicyDecision`, `PolicyEngine`, `MarketDataRepository`,
`ExecutionRepository`, `ExecutionAnalytics`, `InvestigationReport`.

Nothing in this package imports MCP, an LLM SDK or a web framework.

**Contracts (0.4.0, Round R0).** The shapes later rounds write to are frozen first:
`TaskStatus` (adds `DEGRADED`, `INCOMPLETE_ANALYSIS`, `INTERRUPTED`), `ResultWarning`,
`Finding.evidence_refs` (`EvidenceRef{id, path, value}`), `HypothesisTable`, `LLMCallRecord`,
`ToolResult.schema_version`, `InvestigationReport.schema_version` and
`ApprovalRequest.content_hash` (in `ceap.policy.approvals`). All are additive with defaults; the
harness starts using them in Round R1. A CI job fails any later PR that changes a public
`ceap.domain` signature unless it carries the `contracts-change` label.

### 3.2 Analytics (`ceap.analytics`)

Pure functions over NumPy arrays: VWAP, TWAP, implementation shortfall (Perold, with opportunity
cost), slippage (arrival / interval VWAP / per-fill), effective spread, permanent and temporary
market impact, square-root impact estimate, realised and annualised volatility, price drift and
jump detection, displayed depth, imbalance, stale/crossed quote diagnostics, participation and
fill rate. `StandardExecutionAnalytics` composes them into `ExecutionMetrics` including per-venue
statistics; `calculate_market_statistics` produces `MarketStatistics`.

`attribute_causes` scores a fixed catalogue of causes (volatility, spreads, liquidity, venue,
technology, market data, large order, price movement) by comparing a window with its baseline.
Thresholds live in `Thresholds` and are documented in the evaluation guide.

Evaluation (0.6.0): `ceap.evaluation` runs the scenario sweeps; `ceap.evaluation_report` builds the
capability / safety / robustness / cost / live-only table and the regression history;
`ceap.golden` governs the frozen golden set; `ceap.drift` holds the drift monitors (cassettes, RAG
recall floor, golden version, model pins, SDK version). `scripts/check_docs.py` ties every
documented number to `docs/evaluation/latest.json`.

ML (0.6.0, R2B; see `docs/ml/README.md`): deterministic models fitted to synthetic data only and
labelled with the generator hash. `ceap.ml.anomaly` (CUSUM change points on engineering metrics),
`ceap.analytics.ml_signals` (ridge combination of the three signals with purged walk-forward CV,
every hyperparameter a ledger trial), `ceap.analytics.leakage` (leakage tests),
`ceap.analytics.calibration` (held-out isotonic calibration; shipped only if held-out Brier does
not get worse) and `ceap.ml.registry` (models loaded by content hash). None of these is an LLM.

Performance (0.6.0): `_slice_by_time` builds each dataset's sorted series and timestamp keys once
instead of on every tool call; one investigation went from about 10 s to about 3.4 s (measured
locally by the maintainer on one machine; not a benchmark).

**Stage 2** adds a parallel, equally deterministic set for signal research: `signals` (a small
fixed library — momentum, reversal, low-vol), `signal_statistics` (rank IC via Spearman, t-stat,
information ratio, rank-autocorrelation turnover, decay, quantile spread — the gate is the
non-overlapping holding-period IC after the execution lag, with a Newey-West t alongside),
`backtest` (decision at the close, fill at t+1; weight drift; spread + square-root impact, tier
borrow, rf financing, Sharpe on excess returns; Reg SHO, weight and ADV caps logged; delisting
bounds; locked in-sample split), `trial_ledger` (append-only, server-side trial count),
`null_sweep` (until 0.7.1 it set the PROMOTE hurdle; since 0.8.0 the hurdle is a priori, t >= 3.0
for a searched result, and the 500-seed sweep measures its false-PROMOTE rate),
`profitability` (capacity sweep and profitability table), `portfolio_risk` (target portfolio *recomputed* from
the signal at the report date — never trusts the backtest's own weights — gross/net/HHI/beta/ADV
participation, limit checks over the out-of-sample history, beta ex-self with Vasicek shrinkage,
factor / squeeze / liquidation stress) and `research_assessment` (`assess_research`: a deterministic
`PROMOTE`/`REJECT`/`INCONCLUSIVE` verdict plus flags — the Stage 2 analogue of `attribute_causes`;
`INCONCLUSIVE` since 0.8.0). The 0.8.0 research toolkit (`adversarial`, `trial_accounting`,
`overfitting`, `risk_research`, `alpha_decay`) is described in `docs/research/README.md`.

**Execution (0.8.0, `ceap.execution`).** A separate package that *runs* an execution on synthetic
data rather than measuring one: limit order book simulator (`lob`), propagator impact (`impact`),
TWAP/VWAP/POV/Almgren-Chriss behind one `Schedule` (`schedule`), child orders (`child`), auctions
(`auction`), markouts (`markouts`), the predicted-versus-realised cost loop (`cost_loop`) and
trading rules (`rules`). It reuses the Perold IS from `ceap.analytics`. Guide:
`docs/execution/execution.md`; diagram: `docs/DIAGRAMS.md` §15.

### 3.3 Data (`ceap.data`)

`SyntheticMarketGenerator` produces a seeded, deterministic dataset per scenario: one-second NBBO
quotes, Poisson trade prints across five venues, ten-second five-level order books, a baseline and
a window VWAP parent order with 30-second child slices, fills with latency, engineering metrics and
logs per minute, deployments, positions and limits. Scenario effects are localised to the
investigation window so the preceding hour is always a clean baseline.

`DatasetStore` caches datasets; the in-memory repositories implement the domain repository
interfaces. `ceap.data.export` writes CSV/Parquet under `data/` for external tooling (DuckDB,
Polars).

**Stage 2**: `HistoricalGenerator` (`ceap.data.historical`, `realistic_v1`) produces a seeded
30-name, 20-year daily universe on the NYSE calendar (`ceap.data.calendar`): GARCH market with
fat tails, latent drift and transient premium components the signal code never touches,
volatility-dependent spreads and volume, late listings, delistings and a membership mask.
`ceap.data.research_scenarios` carries 10 templates x 3 seeds = 30 scenarios whose verdict and
flags are derived in closed form from generator parameters by `ceap.data.ground_truth`, never from
a run. The 0.3.x generator survives as `legacy_v03` for regression tests only.

### 3.4 MCP (`ceap.mcp`)

* `MCPServerDefinition` – decorator-based tool registration with JSON schemas derived from
  signatures, `readOnlyHint`, risk level and required capabilities; callable in-process or
  exported to `FastMCP` (`to_fastmcp()`, `run_stdio()`).
* Servers: `market_data`, `execution`, `risk`, `engineering`, `knowledge`, and (Stage 2)
  `research_data`, `alpha`, `backtest`. `execution.stage_orders` is the platform's only
  non-read-only tool (HIGH risk, `trading:execute`) — it builds *paper* orders from the
  recomputed target portfolio into an idempotent `StagedOrderBook` (persisted in SQLite and
  pre-trade checked since 0.8.0, and blocked by the DB kill switch); nothing is routed to a venue. `risk` gains `get_portfolio_exposure` / `check_portfolio_limits` /
  `calculate_portfolio_stress`, which recompute the target portfolio rather than trusting a
  backtest's output.
* `MCPClient` abstraction with `InProcessMCPClient` and `StdioMCPClient` (official SDK).
* `MCPToolAdapter` turns a discovered descriptor into a domain `Tool` and attaches an
  `Evidence` record — tool id, arguments, correlation id and a SHA-256 digest of the payload — to
  every successful call.
* `build_tool_registry` performs discovery and returns a `PinnedToolRegistry`: each server's tool
  catalogue is hashed at start-up and checked against `CEAP_MCP_CATALOGUE_PINS` (0.8.0).
* CPU-bound tools (`run_backtest`, `evaluate_signal`) declare `cpu_bound=True` and, under the API
  server, run in a killable process pool (`ceap.mcp.cpu_pool`, 0.8.0).

### 3.5 LLM (`ceap.llm`)

`LLMClient.complete(LLMRequest) -> LLMResponse`. `MockLLMClient` is deterministic and offline: a
canonical plan for planning requests, a templated narrative rendered from structured facts for
report requests, and a rule-based critique. `AnthropicLLMClient` adapts the Anthropic SDK.
`LLMRouter` selects a model per purpose and falls back to the mock if the primary client fails.
Since 0.8.0 the router also holds the per-task caps (calls, tokens, USD), on by default: each call
reserves its upper-bound local estimate under a lock before it is sent and settles to the actual
cost afterwards, so concurrent calls of one task cannot pass a check that only one of them fits. A
circuit breaker skips the primary model for a cooldown after N consecutive failures. The API adds a
per-principal token bucket (`ceap.api.ratelimit`, 429 with `Retry-After`) in front of the endpoints
that start tasks.
Prompts live in `prompts.py` and state the boundary explicitly.

Since 0.9.0 `ceap.llm.ollama.OllamaLLMClient` puts a local open-weight model behind the same
interface (Ollama's JSON-schema `format`, the same Pydantic validation, `provenance = local_model`).
It is used by the evaluation runners in `ceap.eval`, never by the platform's request path, and live
calls need `CEAP_LOCAL_MODEL=1`; `LocalCassette` records replies keyed by the canonical request
hash so CI replays them without Ollama. Every routed call also feeds
`ceap.observability.ai_slo.AISLOMonitor` (plan-valid, repair, fallback, untraceable-number rate and
p95 latency per purpose, with p-chart alerts).

### 3.6 Harness (`ceap.harness`)

`AgentHarness.execute(task, policy_context, cancellation)` runs the explicit state machine
(`HarnessState`, `TRANSITIONS`). `StepExecutor` is the only path to a tool: policy evaluation,
approval when required, retry with exponential backoff, per-step timeout bounded by the task
deadline, metrics and tracing, then `InvestigationMemory.record_tool`. Agents receive a
`HarnessToolInvoker` so their own tool calls take the same path.

The per-step timeout is an `asyncio.wait_for`, which only fires if the event loop is free. CPU work
therefore stays off the loop (0.8.0): request validation and dataset generation run in a worker
thread (`DatasetStore` locks per scenario key), and tools declared `cpu_bound=True` run their heavy
part in `ceap.mcp.cpu_pool`, a process pool whose workers are terminated when the awaiting step is
cancelled or times out. A CPU-heavy tool that does not declare `cpu_bound` would block the loop and
defeat both the timeout and cancellation, so new tools that compute for more than a few hundred
milliseconds should declare it.

Governance is enforced by the harness regardless of the plan: a critic step, an evidence
validation step and a finalise step are appended if absent; the critic is forced before
validation/finalisation; findings whose evidence does not resolve, or whose cited value does not
match the value at its path, are dropped. Since R3B the harness also adds the data steps of every
chosen agent (`agents/requirements.py`), applies the plan's enumerated scope (baseline, ranked
hypotheses), runs critic disputes as deterministic checks, re-plans once on a failure, builds the
hypothesis table, and reads and writes the bounded case memory (`harness/memory.py`, D10).

Consecutive independent `TOOL_CALL` steps are batched and executed concurrently with
`asyncio.TaskGroup` under a semaphore.

Since 0.9.0 `engine.py` is a facade (`AgentHarness.__init__` and `execute`); the rest lives in
mixins that share a typed `HarnessBase` (`base.py`): `planning.py`, `dispatch.py`, `approvals.py`,
`evidence.py`, with constants in `common.py`. `from ceap.harness.engine import ...` still works.
`durable.py` adds an opt-in run journal (`CEAP_DURABLE_RUNS=1`): each tool call has an idempotency
key `sha256(task | step | tool | canonical args)`, a re-run of the same task reads completed calls
back, and a human decision for the same content hash is reused. Recovery steps are in
`docs/RUNBOOK.md`.

### 3.7 Agents (`ceap.agents`)

| Agent | Question | Output |
|---|---|---|
| Planner | what should we investigate? | typed, sanitised `Plan` (research or investigation, by `Task.input["kind"]`) |
| Market | what happened in the market? | MARKET findings (volatility, spreads, depth, drift, feed quality) |
| Execution | what happened to our orders? | EXECUTION findings (IS, VWAP slippage, fill/participation, venue outliers, configuration consistency) |
| Quant | what does the data show and why? | ATTRIBUTION findings from `attribute_causes` |
| Risk | unusual exposure or limit usage? | RISK findings from `risk.check_limit` / `get_exposure` |
| Engineering | did technology behave normally? | TECHNOLOGY findings (latency ratio, SLO, deployments, error logs, feed gaps) |
| Research *(Stage 2)* | what is the hypothesis, and does the data cover it? | RESEARCH findings (hypothesis, coverage, regime) |
| Alpha *(Stage 2)* | is the signal predictive, does it persist out-of-sample? | SIGNAL findings (`ALPHA`/`NO_ALPHA`/`ROBUST`, turnover, decay) |
| Backtest *(Stage 2)* | does it survive costs out-of-sample? | BACKTEST findings (`PROFITABLE`/`OOS_DECAY`/`COST_DRAG`, cost lines, capacity, trial count) |
| Portfolio risk *(Stage 2)* | is the target portfolio within limits? | RISK findings from the recomputed target portfolio |
| Critic | are the conclusions supported? | adjusted findings + critique (+ `assess_research` verdict for research tasks) |
| Reporter | write it up | `InvestigationReport` (investigation narrative, or a Stage 2 proposal with narrative audits) |

Since 0.9.0 the critic also runs deterministic statement-versus-evidence checks
(`ceap.agents.critic_checks`) on every finding and records them in `assessment["flags"]`:
`contradicted` (the finding's own citations disagree), `numeric_mismatch` (the statement names a
metric the cited evidence carries, but no printed number agrees with it at the precision printed)
and `direction_mismatch` (the direction word disagrees with the sign of the cited change). Worked
example: "Implementation shortfall improved by 6.4 bps" citing `is_change_bps = 6.4` returns `direction_mismatch`, because shortfall is lower-is-better and the cited change is positive; "1.1 bps" agrees with 1.0864 because a printed number is read within its own rounding. A flagged finding has its confidence capped (`CHECK_CAPS` in `critic.py`: 0.4 for `contradicted`, 0.2 for the
other two); a flag never raises a confidence.

### 3.8 Policy (`ceap.policy`)

`RulePolicyEngine` evaluates ordered rules (deny list, untrusted MCP server, per-agent allowlist,
read-only, required capabilities, argument guards, JSON input schema, symbol universe, knowledge
classification, risk level) and returns ALLOW / DENY / REQUIRE_APPROVAL with the rule that fired.
Since 0.8.0 every symbol-bearing field is checked and the engine fails closed without a universe;
HIGH-risk tools always need a human approval, decided by a different named person who is not an
admin; each agent's calls carry its own allowlist (`agents.py`). Roles map to capabilities in
`permissions.py`. Approval gateways: automatic (dev), queued (human decides through the API, with
timeout; the content hash of the exact arguments is re-checked after the decision) and deny.
Diagram: `docs/DIAGRAMS.md` §13; OWASP mapping: `docs/threat-model/owasp-llm-top10.md`.

### 3.9 RAG (`ceap.rag`)

Since 0.6.0 (R3C): ten versioned, classified documents (nine under `data/reference/knowledge/`
plus `docs/quant/quant.md` via `manifest.json`) are chunked by a measured strategy (default
`section`, 65 chunks) and searched with Okapi BM25 (`ceap.rag.bm25`, stdlib) behind a minimum
support gate. `ceap.rag.query.retrieval_query` builds the query from the parsed question, symbol,
window and cause candidates, so the model never writes it; the planner pins the principal's
classifications and the policy rule `knowledge-classification` denies more. Retrieved passages are
DOCUMENT evidence, and the reporter's citations are checked span by span (`DOC` evidence with
document version and corpus hash). Held-out retrieval eval: `data/reference/knowledge/eval.json`,
`ceap.rag.evaluation`. The hashed TF-IDF embedder of 0.5.x was removed.

0.9.0 adds opt-in hybrid retrieval in `ceap.rag.dense` (bge-small embeddings, reciprocal rank fusion
with BM25, a MiniLM cross-encoder reranker, all local CPU models through the optional `fastembed`,
outputs cached by hash under `data/rag/`). BM25 stays the default; the measurement and the rule
behind that choice are in `docs/ai-agents/rag-hybrid-0.9.0.md`.

### 3.10 Observability (`ceap.observability`)

`InMemoryTracer` records nested spans and events per investigation (returned by the API);
`OpenTelemetryTracer` mirrors them to an OTel SDK when configured. `MetricsRegistry` exposes
counters and histograms in Prometheus text format at `/metrics`. Logging is JSON lines.

### 3.10a Utilities (`ceap.util`)

`canonical.py` turns text and JSON into canonical bytes before hashing (LF line endings, sorted
keys, UTC datetimes in one fixed format, NaN refused), so a hash computed on Windows equals the one
computed on Linux. `redact.py` removes emails, API keys, tokens, `name=value` secrets (`password=`,
`*_API_KEY=`), phone numbers, high-entropy opaque tokens and account-like numbers from
question text before it reaches a log, the task store or a prompt, and scrubs recorded HTTP
interactions (API key, request id, organisation id) before a cassette is committed.

### 3.10b Persistence (`ceap.persistence`)

SQLite from the standard library (decision D7). One `Database` owns one connection on one writer
thread; async callers use `arun`, so the event loop never blocks on disk and SQLite never sees two
writers. WAL and `busy_timeout` let `ceap db` read while the server writes. Schema versions are
`PRAGMA user_version`; `migrations/NNNN_*.sql` apply forward only, each in its own transaction, after
an online backup of the file. Tables: `tasks`, `results`, `approvals`, `staged_orders`, `audit_log`
(v1); `llm_calls`, `trial_ledger`, `idempotency_keys` (v2); the paper tables (v3); `hash_alg` columns,
`chain_epoch` and `kill_switch_events` (v4, 0.8.0). Repositories (`repositories.py`) are thin,
one per table. Startup recovery turns in-flight tasks into `INTERRUPTED` and pending approvals into
`EXPIRED`. The Postgres path stays open: repositories speak plain SQL and own no connection logic.

Integrity and durability (0.8.0). The audit log, the paper ledger and the paper day chains are
HMAC-SHA256 keyed with `CEAP_AUDIT_HMAC_KEY`, which never enters the database (`chainkey.py`). Each
row records its `hash_alg`. Rows from before migration 0004 verify as a legacy prefix, and its length
is sealed in `chain_epoch`. `anchor.py` writes chain heads to a git-committable file with a SHA-256
sidecar for OpenTimestamps, and `verify_chain(expected_head, expected_rows)` checks against it. That
is what catches a cut-off tail or a chain rebuilt by a key holder. `killswitch.py` keeps an
append-only, audited set/clear log that the paper job and `stage_orders` both read. File databases
use `synchronous=FULL`, `arun` has a timeout, and `Database.health()` (served on `/health`) reports
the writer queue and a write probe.

### 3.11 API (`ceap.api`)

FastAPI application with API-key authentication (salted digests, optional expiry),
capability-based authorisation with owner-scoped reads, `Idempotency-Key` handling, a pure-ASGI
middleware for the correlation id, HTTP metrics and the body limit, and asynchronous task execution
under a concurrency bound that a task releases while it waits for a human. See `docs/api/api.md`.

### 3.12 Replay (`ceap.harness.replay`)

A bundle records the request, tool outputs, redacted model I/O, approval decisions and a clock
seed. Replay swaps in recording-backed MCP, LLM and approval clients and injects the clock and id
source through context variables in `ceap.domain.common`, so the stored result is reproduced byte
for byte.

## 4. Data flow for the MVP question

```
question ─► parse symbol/window ─► validate against dataset coverage ─► Task
Task ─► Planner (LLM) ─► Plan ─► validation (structure + policy)
Plan ─► 15 tool calls in parallel batches (orders, fills, market stats, book depth, TCA metrics
        for window and baseline, venue stats, latency, deployments, logs, strategy config, runbooks)
     ─► Market / Execution / Engineering / Risk / Quant agents ─► findings + CALCULATION evidence
     ─► Critic ─► adjusted findings ─► evidence validation ─► Reporter (LLM narrative + audits)
     ─► InvestigationReport + trace + policy log
```

A full investigation with the mock LLM completes in about one second.

## 4a. Data flow for a Stage 2 research question

```
question ─► parse signal/dataset/window ─► validate against dataset coverage ─► Task(kind=research)
Task ─► Planner (LLM) ─► Plan ─► validation (structure + policy; symbol pinned to the task universe)
Plan ─► 7 tool calls (universe summary, signal statistics, backtest, portfolio exposure/limits/
        stress, research policy) ─► Research / Alpha / Backtest / Portfolio-risk agents ─► findings
     ─► Critic (assess_research: verdict + flags, evidence-recorded) ─► adjusted findings
     ─► evidence validation ─► HUMAN_APPROVAL (proposal) ─► [HUMAN_APPROVAL (staging) ─►
        execution.stage_orders] ─► Reporter (LLM proposal + audits)
     ─► InvestigationReport (kind=research, proposal) + trace + policy log
```

The two `HUMAN_APPROVAL` steps and the conditional `stage_orders` tail are appended by the harness,
never by the plan — see §3.6 and `docs/DIAGRAMS.md` §9.

## 5. Design decisions

* **Own harness abstraction** rather than an agent framework: the state machine, policy hooks and
  evidence model are the product; frameworks can be plugged in behind `Agent` / `Tool`.
* **Analytics live next to the data** (inside the execution MCP server) so agents receive metrics
  rather than shipping hundreds of thousands of quotes through the model context.
* **Two benchmarks**: implementation shortfall carries price-drift noise (~30 bps/hour at 10 %
  volatility); VWAP slippage is drift-neutral. Deterioration is declared on either.
* **The critic may only lower confidence** – a compromised or over-eager model cannot inflate
  certainty.
* **Number audit** on the narrative closes the last gap between "the model narrates" and "the
  numbers are deterministic".

## 6. Extending

* New data source: implement a repository, wrap it in an `MCPServerDefinition`, register it in
  `build_default_servers`; tools appear in the registry through discovery.
* New agent: subclass `BaseAgent`, return findings with evidence ids, add it to
  `Platform._agents` and `ALLOWED_AGENTS`.
* New cause: add to `Cause`, score it in `attribute_causes`, add a scenario template with ground
  truth, extend the evaluation.
* Stage 2 (research → alpha → backtest → risk → critic → human approval → optional paper order
  staging) runs on the same harness, policy engine, approval gateway and evidence model:
  `Task.input["kind"] == "research"` selects the research planner prompt, canonical plan, agents and
  the governance tail with its approval gates. New signal: add a `SignalSpec` to
  `ceap.analytics.signals`; new research scenario: add a template with ground truth to
  `ceap.data.research_scenarios` and it joins the research evaluation (30 scenarios since 0.4.0).

## Paper trading (RP)

```
 signal (rows <= D) -> target -> deltas -> QueuedApprovalGateway (paper-auto, audited)
                                              |
                                  staged_orders (PAPER_RESEARCH)
                                              |
 DailyFeed (synthetic | free daily) -> simulate_day(D+1): splits, financing, mark, fills, expiry
                                              |
          paper_ledger (HMAC chain) + paper_days (HMAC chain) + paper_forward_log | paper_simulated_log
                                              |
                       paper report / export (CSV, schema_version) -> forward-log branch
```

paper, synthetic or free daily data, not trading. One SQLite transaction per session. The kill
switch (DB-backed, audited, or `CEAP_PAPER_HALT`) is checked before anything is staged. The forward
and simulated logs keep plain SHA-256 links so the exported CSV verifies without a key.

Today the feed is `synthetic`. `ceap.paper.feeds_real` (feed `"stooq"`) reads the local Stooq
cache with R5 point-in-time membership, lagged 20-day ADV and the R5 spread schedule, and refuses
any row up to 2025-12-31 (the unspent R5 pseudo-holdout). `config/paper.json` stays on the
synthetic feed (the cache is git-ignored and R5 is VOID), so the Stooq feed is not enabled;
with the 12-month formation window, January 2027 is the earliest real-price start. The forward log
tests operations and controls, not profitability.

## 7. System diagram, lifecycles and layout

Moved here from the README in 0.9.0.


```
                         ┌──────────────────────┐
                         │        User          │  Trader / Quant / Dev
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │    API / Gateway     │  FastAPI · API keys · RBAC (ceap.api)
                         └──────────┬───────────┘
                                    ▼
              ┌─────────────────────────────────────────┐
              │             AGENT HARNESS               │  ceap.harness
              │  Planner → Plan validation → Steps      │  state machine · TaskGroup batching
              │  Policy · Approval · Retry · Timeout    │  cancellation · memory · tracing
              │  Critic → Evidence validation → Report  │  governance steps enforced
              └──────────────────┬──────────────────────┘
                                 ▼
  Market · Execution · Quant · Risk · Engineering · Critic · Reporter          (ceap.agents, investigation)
  Research · Alpha · Backtest · Portfolio risk · Critic · Reporter              (ceap.agents, research)
                                 ▼
                         ┌────────────────┐
                         │  ToolRegistry  │  MCPToolAdapter → MCPClient (in-process or stdio)
                         └───────┬────────┘
     ┌──────────────┬────────────┼─────────────┬──────────────┬──────────────┬───────────┬──────────┐
     ▼              ▼            ▼             ▼              ▼              ▼           ▼          ▼
 market_data    execution      risk       engineering     knowledge     research_data   alpha    backtest
 quotes/trades  orders/fills   positions  logs/metrics    runbooks/     universe        signals  event-driven
 order book     TCA metrics    limits     deployments     policy (RAG)  daily bars      rank IC  cost model
 statistics     venue stats    stress     latency                       regime          decay    walk-forward
                stage_orders*  portfolio                                                                   (ceap.mcp.*)
                                 ▼
                       ┌─────────────────────┐
                       │  Quant Analytics    │  VWAP · TWAP · IS · slippage · spreads · impact · liquidity · volatility
                       │  (deterministic)    │  attribution · signals · signal statistics · backtest · portfolio risk · assessment
                       └─────────────────────┘
 * the only non-read-only tool: HIGH risk, requires trading:execute and its own human approval; paper orders only
```

#### Investigation lifecycle

```
CREATED → PLANNING → VALIDATING_PLAN → EXECUTING ⇄ AWAITING_APPROVAL
        → CRITIQUING → VALIDATING_EVIDENCE → FINALISING → COMPLETED   (| FAILED | CANCELLED)
```

1. **Understand request** – symbol and window are parsed deterministically (`ceap.api.parsing`) so
   data scope is policy-checked before any model sees the text.
2. **Plan** – the Planner asks the LLM for a JSON plan restricted to the discovered tool catalogue;
   unknown tools/agents/arguments are stripped, and unusable output falls back to a canonical plan.
3. **Validate plan** – structure, tool existence and a policy pre-check of every tool call; a DENY
   fails fast before anything runs. Critic → validation → finalise are appended if missing.
4. **Execute** – consecutive independent tool calls run concurrently (`asyncio.TaskGroup`); each call
   passes policy → (approval) → retry/timeout → tool → memory → trace. Agents call tools only
   through the same path.
5. **Critique** – deterministic checks (evidence resolves, anomalies corroborated by attribution,
   cross-agent contradictions, thin evidence capped) plus an optional LLM critique that can only
   lower confidence.
6. **Validate evidence** – findings citing unknown evidence are dropped and recorded.
7. **Finalise** – the Reporter hands structured facts to the LLM, which names figures as
   placeholders (`{fact.key}`) that code renders; the narrative is then audited: every number must be
   traceable to the facts, every evidence id must exist and every policy claim needs its own verified
   citation. Prose that fails is replaced by the deterministic template narrative
   (`LLM_NARRATIVE_SUPPRESSED`), and the rejected text goes only to the audit log.

#### Research lifecycle (Stage 2)

```
… → CRITIQUING → VALIDATING_EVIDENCE → AWAITING_APPROVAL (proposal) → [AWAITING_APPROVAL (staging) → stage_orders]
  → FINALISING → COMPLETED
```

The same state machine; the differences are what the harness *owns*. `Task.input["kind"] == "research"`
selects the research planner prompt, the canonical plan (universe summary → signal statistics →
backtest → portfolio exposure / limits / stress → knowledge), the agents and the governance tail:

* **Research agent** states the hypothesis and checks coverage and regime; **Alpha agent** reads the
  rank-IC statistics (`ALPHA` / `NO_ALPHA` / `ROBUST`); **Backtest agent** reads the walk-forward
  backtest (`PROFITABLE`, `OOS_DECAY`, `COST_DRAG`, `CONCENTRATION`); **Portfolio risk agent** reads the
  *recomputed* target portfolio (`LIMIT_BREACH`, `STRESS_LOSS`). Every tool is called exactly once:
  plan, agents and harness build their arguments from one function (`research_tool_arguments`).
* The **critic** computes the deterministic assessment (`assess_research`: verdict + flags, recorded
  as evidence) and caps any finding that contradicts it, then the optional LLM critique may only
  lower confidence further.
* **Human approval** is appended by the harness, never by the plan: the approver sees the assessment
  and the target portfolio. A declined proposal fails the task with no report. If order staging was
  requested (admin only), a second approval shows the order preview; declining it skips staging and
  the proposal still completes. `execution.stage_orders` is idempotent and stages paper orders only.
* The **proposal** (HYPOTHESIS, SIGNAL STATISTICS, BACKTEST, RISK, VERDICT, ALTERNATIVE EXPLANATIONS,
  CRITIC, APPROVAL, STAGED ORDERS, EVIDENCE) passes the same number and evidence-id audits.


### Repository layout

```
src/ceap/
├── domain/        Task · Plan · PlanStep · Tool · ToolRequest/Result · Evidence · Finding · Order · Execution ·
│                  Quote · Trade · OrderBook · ExecutionMetrics · PolicyDecision · repositories · reports ·
│                  research (scope · signal statistics · backtest · portfolio risk · assessment)
├── analytics/     vwap · twap · implementation_shortfall · slippage · market_impact · liquidity · volatility ·
│                  market_statistics · execution_metrics (TCA) · attribution ·
│                  signals · signal_statistics · backtest · portfolio_risk · research_assessment
├── data/          synthetic market model (6 symbols, 5 venues), 10 scenario templates x 5 symbols = 50 scenarios;
│                  500-name synthetic research universe (0.9.0);
│                  historical (30-name, 20-year daily universe) · research_scenarios (10 templates x 3 seeds = 30)
├── mcp/           server definition (in-process + FastMCP export), client (in-process + stdio), adapter, registry,
│                  market_data/ execution/ risk/ engineering/ knowledge/ research_data/ alpha/ backtest/
│                  (each runnable: python -m ceap.mcp.<name>); execution/staging.py holds the paper order book
├── llm/           LLMClient · MockLLMClient (deterministic) · AnthropicLLMClient · OllamaLLMClient (local, 0.9.0) ·
│                  strict schemas · model pins ·
│                  versioned prompts (llm/prompts/*.md) · cassette recording
├── harness/       engine (AgentHarness facade) + mixins planning · dispatch · approvals · evidence (0.9.0) ·
│                  durable (run journal) · paper_gate · state_machine · executor · retry · cancellation · memory
├── agents/        planner · market · execution · quant · risk · engineering · critic (+ critic_checks) · reporter ·
│                  research · alpha · backtest · portfolio_risk
├── policy/        rule engine (schema + symbol-field checks) · RBAC permissions · per-agent allowlists ·
│                  approval gateways (auto / queued / deny, content-hash re-check)
├── rag/           BM25 (stdlib, default) · opt-in dense/RRF/rerank (0.9.0) · measured chunking · corpus hash · held-out eval
├── ml/            pre-trade cost model · anomaly · model registry · model inventory (deterministic, labelled synthetic)
├── execution/     LOB simulator · schedulers (TWAP/VWAP/POV/Almgren-Chriss) · child orders · auctions · impact ·
│                  markouts · predicted-vs-realised cost loop · tick/lot/LULD rules (0.8.0, synthetic)
├── studies/      preregistered real-data studies (E1 industry momentum, 0.9.0)
├── paper/         paper trading: feed, fills, hash-chained ledger, pre-trade checks, kill switch, forward log
├── eval/          per-agent evals, degraded clients, OOD golden split, corpus pins (0.8.0); local_model, injection,
│                  judge, challenger (0.9.0)
├── persistence/   SQLite (WAL, single writer, forward-only migrations) · HMAC chain keys · chain anchors
├── util/          canonical bytes for hashing (LF, sorted-key JSON, UTC datetimes) · redaction, cassette scrubbing
├── observability/ tracing (in-memory + OpenTelemetry) · metrics (Prometheus text) · JSON logging · AI SLOs (0.9.0)
├── api/           FastAPI app · routes · auth · schemas · request parsing
├── platform.py    composition root (InvestigationRequest · ResearchRequest)
├── evaluation.py  50-scenario execution evaluation · 30-scenario research evaluation
├── evaluation_report.py  capability · safety · robustness · cost · live-only rows (UNKNOWN / NOT RUN LIVE)
├── golden.py      frozen golden set (version + items hash)
├── drift.py       drift monitors (cassettes, RAG floors, golden version, model and SDK pins)
└── cli.py         ceap investigate | research | scenarios | research-scenarios | profitability | evaluate |
                   evaluation-report | tca-report | generate-data | serve | db | replay | paper
tests/             unit · repo · integration · mcp · agent · evaluation · adversarial · contract · properties · golden
data/reference/knowledge/   runbooks, TCA methodology, venue config, limits, policies (RAG corpus)
docs/              architecture · threat-model · api · evaluation (history.csv, latest.json) · ml · quant · ai-agents ·
                   execution · research · review
```

---

### MCP servers

Every server is defined once (`MCPServerDefinition`) and can be used in-process or exported to a
real `FastMCP` server over stdio:

```bash
python -m ceap.mcp.market_data     # stdio MCP server: get_quote, get_quotes, get_order_book, get_trades, get_market_statistics, ...
python -m ceap.mcp.execution       # get_parent_orders, get_child_orders, get_executions, get_execution_metrics, get_venue_statistics, ...
python -m ceap.mcp.risk            # get_position, get_exposure, check_limit, calculate_stress,
                                   # get_portfolio_exposure, check_portfolio_limits, calculate_portfolio_stress
python -m ceap.mcp.engineering     # search_logs, get_service_metrics, get_deployments, get_latency_metrics, get_services
python -m ceap.mcp.knowledge       # search_documents, get_document, list_documents (RAG)
python -m ceap.mcp.research_data   # get_universe, get_daily_bars, get_universe_summary
python -m ceap.mcp.alpha           # list_signals, evaluate_signal
python -m ceap.mcp.backtest        # run_backtest
```

Tool annotations carry `readOnlyHint`, a risk level and required capabilities, which the policy
engine reads. Every tool is read-only except `execution.stage_orders` (HIGH risk, `trading:execute`),
which the harness alone schedules, after two human approvals. `StdioMCPClient` drives these
subprocesses with the official `mcp` SDK; the test suite includes a real stdio round-trip.

