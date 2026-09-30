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

**Stage 2** adds a parallel, equally deterministic set for signal research: `signals` (a small
fixed library — momentum, reversal, low-vol), `signal_statistics` (rank IC via Spearman, t-stat,
information ratio, turnover, decay, quantile spread — one-day non-overlapping ICs so t-stats are
honest), `backtest` (event-driven daily loop, half-spread + square-root-impact cost model,
walk-forward in-sample/out-of-sample split), `portfolio_risk` (target portfolio *recomputed* from
the signal at the report date — never trusts the backtest's own weights — gross/net/HHI/beta/ADV
participation, limit checks, stress) and `research_assessment` (`assess_research`: a deterministic
`PROMOTE`/`REJECT` verdict plus flags — the Stage 2 analogue of `attribute_causes`).

### 3.3 Data (`ceap.data`)

`SyntheticMarketGenerator` produces a seeded, deterministic dataset per scenario: one-second NBBO
quotes, Poisson trade prints across five venues, ten-second five-level order books, a baseline and
a window VWAP parent order with 30-second child slices, fills with latency, engineering metrics and
logs per minute, deployments, positions and limits. Scenario effects are localised to the
investigation window so the preceding hour is always a clean baseline.

`DatasetStore` caches datasets; the in-memory repositories implement the domain repository
interfaces. `ceap.data.export` writes CSV/Parquet under `data/` for external tooling (DuckDB,
Polars).

**Stage 2**: `HistoricalGenerator` produces a seeded 30-name, four-year daily universe (`ceap.data
.historical`) — a factor model whose embedded premium is computed through the *same* signal code
that later measures it, so the generator and the analytics can never silently drift apart.
`HistoricalStore` mirrors `DatasetStore`. `ceap.data.research_scenarios` carries the 7-template
(× 3 seeds = 21-scenario) ground truth: expected verdict and flags per scenario.

### 3.4 MCP (`ceap.mcp`)

* `MCPServerDefinition` – decorator-based tool registration with JSON schemas derived from
  signatures, `readOnlyHint`, risk level and required capabilities; callable in-process or
  exported to `FastMCP` (`to_fastmcp()`, `run_stdio()`).
* Servers: `market_data`, `execution`, `risk`, `engineering`, `knowledge`, and (Stage 2)
  `research_data`, `alpha`, `backtest`. `execution.stage_orders` is the platform's only
  non-read-only tool (HIGH risk, `trading:execute`) — it builds *paper* orders from the
  recomputed target portfolio into an in-memory, idempotent `StagedOrderBook`; nothing is routed
  to a venue. `risk` gains `get_portfolio_exposure` / `check_portfolio_limits` /
  `calculate_portfolio_stress`, which recompute the target portfolio rather than trusting a
  backtest's output.
* `MCPClient` abstraction with `InProcessMCPClient` and `StdioMCPClient` (official SDK).
* `MCPToolAdapter` turns a discovered descriptor into a domain `Tool` and attaches an
  `Evidence` record — tool id, arguments, correlation id and a SHA-256 digest of the payload — to
  every successful call.
* `build_tool_registry` performs discovery and populates the `ToolRegistry`.

### 3.5 LLM (`ceap.llm`)

`LLMClient.complete(LLMRequest) -> LLMResponse`. `MockLLMClient` is deterministic and offline: a
canonical plan for planning requests, a templated narrative rendered from structured facts for
report requests, and a rule-based critique. `AnthropicLLMClient` adapts the Anthropic SDK.
`LLMRouter` selects a model per purpose and falls back to the mock if the primary client fails.
Prompts live in `prompts.py` and state the boundary explicitly.

### 3.6 Harness (`ceap.harness`)

`AgentHarness.execute(task, policy_context, cancellation)` runs the explicit state machine
(`HarnessState`, `TRANSITIONS`). `StepExecutor` is the only path to a tool: policy evaluation,
approval when required, retry with exponential backoff, per-step timeout bounded by the task
deadline, metrics and tracing, then `InvestigationMemory.record_tool`. Agents receive a
`HarnessToolInvoker` so their own tool calls take the same path.

Governance is enforced by the harness regardless of the plan: a critic step, an evidence
validation step and a finalise step are appended if absent; the critic is forced before
validation/finalisation; findings whose evidence does not resolve are dropped.

Consecutive independent `TOOL_CALL` steps are batched and executed concurrently with
`asyncio.TaskGroup` under a semaphore.

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
| Backtest *(Stage 2)* | does it survive costs out-of-sample? | BACKTEST findings (`PROFITABLE`/`OVERFIT`/`COST_DRAG`) |
| Portfolio risk *(Stage 2)* | is the target portfolio within limits? | RISK findings from the recomputed target portfolio |
| Critic | are the conclusions supported? | adjusted findings + critique (+ `assess_research` verdict for research tasks) |
| Reporter | write it up | `InvestigationReport` (investigation narrative, or a Stage 2 proposal with narrative audits) |

### 3.8 Policy (`ceap.policy`)

`RulePolicyEngine` evaluates ordered rules (deny list, read-only, required capabilities, argument
guards, symbol universe, risk level) and returns ALLOW / DENY / REQUIRE_APPROVAL with the rule
that fired. Roles map to capabilities in `permissions.py`. Approval gateways: automatic (dev),
queued (human decides through the API, with timeout) and deny.

### 3.9 RAG (`ceap.rag`)

Markdown documents are chunked by heading and paragraph, embedded with a dependency-free hashed
TF-IDF embedder and searched by cosine similarity. Exposed through the `knowledge` MCP server so
retrieved passages become DOCUMENT evidence.

### 3.10 Observability (`ceap.observability`)

`InMemoryTracer` records nested spans and events per investigation (returned by the API);
`OpenTelemetryTracer` mirrors them to an OTel SDK when configured. `MetricsRegistry` exposes
counters and histograms in Prometheus text format at `/metrics`. Logging is JSON lines.

### 3.11 API (`ceap.api`)

FastAPI application with API-key authentication, capability-based authorisation and asynchronous
investigation execution. See `docs/api/api.md`.

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
  `ceap.data.research_scenarios` and it joins the 21-scenario evaluation.
